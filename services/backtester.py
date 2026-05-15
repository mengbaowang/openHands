"""Historical backtester that replays the current AI decision flow on 5m candles."""
from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Callable, Dict, List, Optional

import config
from services.execution.position_metrics import (
    ROUND_TRIP_FEE_PCT,
    PEAK_DRAWDOWN_CLOSE_RATIO,
    PEAK_PROFIT_ACTIVATION_PCT,
    build_position_metrics,
    calculate_peak_drawdown_stop,
)
from services.execution.reversal_signal import EARLY_REVERSAL_SENSITIVITY, build_reversal_exit_signal
from services.execution_service import ExecutionService


class _BacktestRuleHelper(ExecutionService):
    """Reuse current execution/risk heuristics without requiring OKX or DB access."""

    def __init__(self):
        self.model_id = 0
        self.db = None
        self._debug_callback = None
        self.okx_trader = None
        self.peak_profit_activation_pct = PEAK_PROFIT_ACTIVATION_PCT
        self.peak_drawdown_close_ratio = PEAK_DRAWDOWN_CLOSE_RATIO
        self.early_reversal_sensitivity = EARLY_REVERSAL_SENSITIVITY
        self._backtest_now = None

    def _debug_log(self, message: str) -> None:
        return None

    def _position_age_minutes(self, opened_at: str = None) -> float:
        if not opened_at or self._backtest_now is None:
            return 0.0
        try:
            opened = datetime.strptime(opened_at, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            return max(0.0, (self._backtest_now - opened).total_seconds() / 60.0)
        except Exception:
            return 0.0


class Backtester:
    """Replay market history and the current AI decision format on simulated positions."""

    BASE_TIMEFRAME = '5m'
    BASE_INTERVAL_SECONDS = 300
    ENTRY_FEE_PCT = ROUND_TRIP_FEE_PCT / 2

    def __init__(self, db, market_fetcher, ai_trader):
        self.db = db
        self.market_fetcher = market_fetcher
        self.ai_trader = ai_trader
        self.rule_helper = _BacktestRuleHelper()
        self._snapshot_cache: Dict[tuple[str, int], Dict] = {}
        self._series_cache: Dict[str, Dict] = {}
        self._progress_callback = None

    def run_backtest(self, model_config: Dict, start_date: str, end_date: str,
                     initial_capital: float = 10000,
                     decision_interval_seconds: Optional[int] = None,
                     risk_interval_seconds: Optional[int] = None,
                     max_ai_calls: int = 2000,
                     mode: str = 'candidate_ai',
                     progress_callback: Optional[Callable[[float, int, int, str], None]] = None) -> Dict:
        if mode not in {'full_ai', 'candidate_ai', 'fast_rule'}:
            raise ValueError(f'Unsupported backtest mode: {mode}')
        self._progress_callback = progress_callback
        start_dt, end_dt = self._parse_date_range(start_date, end_date)
        effective_decision_seconds = self._normalize_interval(
            decision_interval_seconds or config.AI_DECISION_INTERVAL
        )
        effective_risk_seconds = self._normalize_interval(
            risk_interval_seconds or config.RISK_CHECK_INTERVAL
        )
        cycle_seconds = self.BASE_INTERVAL_SECONDS

        timeline = self._build_timeline(start_dt, end_dt, cycle_seconds)
        decision_cycle_total = sum(1 for ts in timeline if self._is_cycle_boundary(ts, start_dt, effective_decision_seconds))
        if mode == 'full_ai' and decision_cycle_total > max_ai_calls:
            raise ValueError(
                f"AI回测需要 {decision_cycle_total} 次模型调用，超过当前上限 {max_ai_calls}。"
                f"请缩短时间范围，或把 decision_interval_seconds 调大到更高周期后再跑。"
            )

        self._emit_progress(1, 0, len(timeline), '加载历史数据中')
        self._prepare_historical_series(start_dt, end_dt)
        portfolio = {
            'cash': float(initial_capital),
            'positions': [],
            'total_value': float(initial_capital),
            'positions_value': 0.0,
            'realized_pnl': 0.0,
            'unrealized_pnl': 0.0,
            'frozen_margin': 0.0,
        }
        trades: List[Dict] = []
        daily_values: List[Dict] = []
        decision_logs: List[Dict] = []
        ai_call_count = 0
        skipped_ai_cycles = 0
        candidate_cycles = 0
        cache_hits = 0

        for index, ts in enumerate(timeline, start=1):
            current_dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            current_prices = self._build_price_snapshot(ts)
            if not current_prices:
                continue

            current_portfolio = self._refresh_portfolio_state(portfolio, current_prices, ts)
            if self._is_cycle_boundary(ts, start_dt, effective_risk_seconds):
                risk_results = self._run_risk_cycle(portfolio, current_prices, ts)
                if risk_results:
                    trades.extend(risk_results)
                    current_portfolio = self._refresh_portfolio_state(portfolio, current_prices, ts)

            if self._is_cycle_boundary(ts, start_dt, effective_decision_seconds):
                market_state = self._build_market_state(ts)
                if market_state:
                    decisions = {}
                    raw_response = ''
                    from_cache = False
                    if mode == 'fast_rule':
                        decisions = self._generate_rule_decisions(
                            portfolio,
                            current_portfolio,
                            market_state,
                        )
                    else:
                        should_query_ai = mode == 'full_ai' or self._should_consider_ai_decision(
                            current_portfolio,
                            market_state,
                        )
                        if should_query_ai:
                            candidate_cycles += 1
                            account_info = self._build_account_info(initial_capital, current_portfolio, current_dt)
                            decisions, raw_response, from_cache = self._resolve_ai_decision(
                                model_config,
                                mode,
                                market_state,
                                current_portfolio,
                                account_info,
                                max_ai_calls=max_ai_calls,
                                ai_call_count=ai_call_count,
                            )
                            if from_cache:
                                cache_hits += 1
                            else:
                                ai_call_count += 1
                        else:
                            skipped_ai_cycles += 1
                    decision_logs.append({
                        'timestamp': current_dt.strftime('%Y-%m-%d %H:%M:%S'),
                        'decisions': decisions or {},
                        'raw_response': raw_response[:2000] if raw_response else '',
                        'mode': mode,
                        'from_cache': from_cache if mode != 'fast_rule' else False,
                    })
                    execution_results = self._execute_decisions(
                        portfolio,
                        decisions or {},
                        market_state,
                        ts
                    )
                    if execution_results:
                        trades.extend(execution_results)
                        current_portfolio = self._refresh_portfolio_state(portfolio, current_prices, ts)

            if not daily_values or daily_values[-1]['date'] != current_dt.strftime('%Y-%m-%d'):
                daily_values.append({
                    'date': current_dt.strftime('%Y-%m-%d'),
                    'total_value': current_portfolio['total_value'],
                    'cash': current_portfolio['cash'],
                    'positions_value': current_portfolio['positions_value'],
                    'positions_count': len(current_portfolio['positions']),
                })
            else:
                daily_values[-1].update({
                    'total_value': current_portfolio['total_value'],
                    'cash': current_portfolio['cash'],
                    'positions_value': current_portfolio['positions_value'],
                    'positions_count': len(current_portfolio['positions']),
                })
            if index == 1 or index % 12 == 0 or index == len(timeline):
                self._emit_progress(
                    5 + (index / max(len(timeline), 1)) * 90,
                    index,
                    len(timeline),
                    f'回放中：{current_dt.strftime("%Y-%m-%d %H:%M")} · AI调用 {ai_call_count}'
                )

        final_prices = self._build_price_snapshot(timeline[-1]) if timeline else {}
        final_portfolio = self._refresh_portfolio_state(portfolio, final_prices, timeline[-1] if timeline else None)
        metrics = self._calculate_backtest_metrics(
            trades=trades,
            daily_values=daily_values,
            initial_capital=float(initial_capital),
            final_portfolio=final_portfolio,
            ai_cycle_count=ai_call_count if mode != 'fast_rule' else 0,
            risk_cycle_count=sum(1 for ts in timeline if self._is_cycle_boundary(ts, start_dt, effective_risk_seconds)),
        )
        self._emit_progress(100, len(timeline), len(timeline), '回测完成')

        return {
            'start_date': start_date,
            'end_date': end_date,
            'initial_capital': float(initial_capital),
            'final_value': final_portfolio['total_value'],
            'total_return': metrics['total_return'],
            'settings': {
                'requested_decision_interval_seconds': decision_interval_seconds or config.AI_DECISION_INTERVAL,
                'requested_risk_interval_seconds': risk_interval_seconds or config.RISK_CHECK_INTERVAL,
                'effective_decision_interval_seconds': effective_decision_seconds,
                'effective_risk_interval_seconds': effective_risk_seconds,
                'base_timeframe': self.BASE_TIMEFRAME,
                'max_ai_calls': max_ai_calls,
                'mode': mode,
            },
            'summary': {
                'decision_cycles': decision_cycle_total,
                'ai_calls': ai_call_count,
                'cache_hits': cache_hits,
                'candidate_cycles': candidate_cycles,
                'skipped_ai_cycles': skipped_ai_cycles,
                'risk_cycles': metrics['risk_cycle_count'],
                'trade_records': len(trades),
                'entry_count': metrics['entry_count'],
                'exit_count': metrics['exit_count'],
                'open_positions': len(final_portfolio['positions']),
            },
            'trades': trades,
            'daily_values': daily_values,
            'decision_logs': decision_logs,
            'metrics': metrics,
        }

    def _emit_progress(self, progress: float, current_step: int, total_steps: int, message: str) -> None:
        if self._progress_callback:
            self._progress_callback(progress, current_step, total_steps, message)

    def _resolve_ai_decision(self, model_config: Dict, mode: str, market_state: Dict, current_portfolio: Dict,
                             account_info: Dict, max_ai_calls: int, ai_call_count: int) -> tuple[Dict, str, bool]:
        fingerprint = self._build_decision_fingerprint(model_config, mode, market_state, current_portfolio, account_info)
        model_id = int(model_config.get('model_id') or 0)
        if model_id:
            cached = self.db.get_backtest_decision_cache(model_id, mode, fingerprint)
            if cached:
                try:
                    return json.loads(cached.get('decision_json') or '{}') or {}, cached.get('raw_response') or '', True
                except Exception:
                    pass

        if ai_call_count + 1 > max_ai_calls:
            raise ValueError(
                f"本次回测在 {mode} 模式下需要超过 {max_ai_calls} 次AI调用。"
                f"请提高周期、缩短区间，或切换到快速模式。"
            )

        decisions, raw_response = self.ai_trader.make_decision(
            market_state,
            deepcopy(current_portfolio),
            account_info
        )
        if model_id:
            self.db.upsert_backtest_decision_cache(
                model_id,
                mode,
                fingerprint,
                json.dumps(decisions or {}, ensure_ascii=False),
                raw_response or ''
            )
        return decisions, raw_response, False

    def _build_decision_fingerprint(self, model_config: Dict, mode: str, market_state: Dict,
                                    current_portfolio: Dict, account_info: Dict) -> str:
        payload = {
            'mode': mode,
            'model_name': model_config.get('model_name'),
            'system_prompt': model_config.get('system_prompt') or '',
            'account_info': {
                'initial_capital': round(float(account_info.get('initial_capital', 0) or 0), 2),
                'total_return': round(float(account_info.get('total_return', 0) or 0), 4),
            },
            'portfolio': {
                'cash': round(float(current_portfolio.get('cash', 0) or 0), 2),
                'positions': [
                    {
                        'coin': pos.get('coin'),
                        'side': pos.get('side'),
                        'quantity': round(float(pos.get('quantity', 0) or 0), 6),
                        'avg_price': round(float(pos.get('avg_price', 0) or 0), 4),
                        'leverage': int(pos.get('leverage', 1) or 1),
                        'stop_loss': round(float(pos.get('stop_loss', 0) or 0), 4) if pos.get('stop_loss') is not None else None,
                        'take_profit': round(float(pos.get('take_profit', 0) or 0), 4) if pos.get('take_profit') is not None else None,
                        'setup_class': pos.get('setup_class') or '',
                        'management_stage': int(pos.get('management_stage', 0) or 0),
                    }
                    for pos in current_portfolio.get('positions', [])
                ],
            },
            'market': {
                coin: {
                    'price': round(float(data.get('price', 0) or 0), 4),
                    'timeframes': {
                        timeframe: {
                            key: self._normalize_indicator_value(value)
                            for key, value in (indicators or {}).items()
                            if key in {
                                'current_price', 'sma_5', 'sma_7', 'sma_14', 'sma_30',
                                'rsi_14', 'macd', 'macd_histogram', 'atr_14',
                                'volume_ratio', 'recent_high_20', 'recent_low_20'
                            }
                        }
                        for timeframe, indicators in (data.get('timeframes') or {}).items()
                    }
                }
                for coin, data in market_state.items()
            }
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(encoded.encode('utf-8')).hexdigest()

    def _normalize_indicator_value(self, value):
        if isinstance(value, (int, float)):
            return round(float(value), 6)
        return value

    def _parse_date_range(self, start_date: str, end_date: str) -> tuple[datetime, datetime]:
        start_dt = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(tzinfo=timezone.utc) + timedelta(days=1) - timedelta(minutes=5)
        if end_dt < start_dt:
            raise ValueError('结束日期不能早于开始日期')
        return start_dt, end_dt

    def _normalize_interval(self, interval_seconds: int) -> int:
        interval_seconds = max(int(interval_seconds or self.BASE_INTERVAL_SECONDS), self.BASE_INTERVAL_SECONDS)
        remainder = interval_seconds % self.BASE_INTERVAL_SECONDS
        if remainder:
            interval_seconds += self.BASE_INTERVAL_SECONDS - remainder
        return interval_seconds

    def _build_timeline(self, start_dt: datetime, end_dt: datetime, cycle_seconds: int) -> List[int]:
        start_ts = int(start_dt.timestamp())
        end_ts = int(end_dt.timestamp())
        aligned_start = start_ts - (start_ts % cycle_seconds)
        if aligned_start < start_ts:
            aligned_start += cycle_seconds
        timeline = []
        current = aligned_start
        while current <= end_ts:
            timeline.append(current * 1000)
            current += cycle_seconds
        return timeline

    def _is_cycle_boundary(self, ts_ms: int, start_dt: datetime, interval_seconds: int) -> bool:
        delta_seconds = int(ts_ms / 1000 - int(start_dt.timestamp()))
        return delta_seconds >= 0 and delta_seconds % interval_seconds == 0

    def _prepare_historical_series(self, start_dt: datetime, end_dt: datetime) -> None:
        self._snapshot_cache = {}
        self._series_cache = {}
        pad_candles = self.market_fetcher.TIMEFRAME_CONFIG['1h']['points'] * (3600 // self.BASE_INTERVAL_SECONDS)
        padded_start = start_dt - timedelta(seconds=pad_candles * self.BASE_INTERVAL_SECONDS)
        start_ts_ms = int(padded_start.timestamp() * 1000)
        end_ts_ms = int(end_dt.timestamp() * 1000)

        for coin in config.SUPPORTED_COINS:
            base = self.market_fetcher.get_historical_candles_range(
                coin,
                self.BASE_TIMEFRAME,
                start_ts_ms=start_ts_ms,
                end_ts_ms=end_ts_ms
            )
            base = [c for c in base if c.get('timestamp') is not None]
            base.sort(key=lambda item: item['timestamp'])
            if not base:
                continue

            tf_15m = self._aggregate_candles(base, interval_ms=15 * 60 * 1000)
            tf_1h = self._aggregate_candles(base, interval_ms=60 * 60 * 1000)
            self._series_cache[coin] = {
                '5m': base,
                '15m': tf_15m,
                '1h': tf_1h,
                '5m_ts': [c['timestamp'] for c in base],
                '15m_ts': [c['timestamp'] for c in tf_15m],
                '1h_ts': [c['timestamp'] for c in tf_1h],
            }

    def _aggregate_candles(self, candles: List[Dict], interval_ms: int) -> List[Dict]:
        grouped: Dict[int, List[Dict]] = {}
        for candle in candles:
            bucket = candle['timestamp'] // interval_ms
            grouped.setdefault(bucket, []).append(candle)

        aggregated = []
        expected_count = interval_ms // (self.BASE_INTERVAL_SECONDS * 1000)
        for bucket in sorted(grouped.keys()):
            chunk = sorted(grouped[bucket], key=lambda item: item['timestamp'])
            if len(chunk) < expected_count:
                continue
            aggregated.append({
                'timestamp': chunk[-1]['timestamp'],
                'open': chunk[0]['open'],
                'high': max(item['high'] for item in chunk),
                'low': min(item['low'] for item in chunk),
                'close': chunk[-1]['close'],
                'price': chunk[-1]['close'],
                'volume': sum(float(item.get('volume', 0) or 0) for item in chunk),
            })
        return aggregated

    def _build_price_snapshot(self, ts_ms: int) -> Dict[str, Dict]:
        snapshot = {}
        for coin, series in self._series_cache.items():
            idx = bisect_right(series['5m_ts'], ts_ms)
            if idx <= 0:
                continue
            candle = series['5m'][idx - 1]
            snapshot[coin] = {
                'price': candle['close'],
                'change_24h': 0.0,
                'high': candle['high'],
                'low': candle['low'],
                'timestamp': candle['timestamp'],
            }
        snapshot['__timeframes__'] = {
            coin: self._get_coin_timeframes(coin, ts_ms)
            for coin in self._series_cache
        }
        snapshot['__candles__'] = {
            coin: self._get_latest_candle(coin, '5m', ts_ms)
            for coin in self._series_cache
        }
        return snapshot

    def _build_market_state(self, ts_ms: int) -> Dict:
        state = {}
        for coin, series in self._series_cache.items():
            idx = bisect_right(series['5m_ts'], ts_ms)
            if idx <= 0:
                continue
            latest = series['5m'][idx - 1]
            timeframes = self._get_coin_timeframes(coin, ts_ms)
            state[coin] = {
                'price': latest['close'],
                'change_24h': 0.0,
                'timeframes': timeframes,
                'indicators': timeframes.get('1h', {}),
            }
        return state

    def _get_coin_timeframes(self, coin: str, ts_ms: int) -> Dict:
        cache_key = (coin, ts_ms)
        if cache_key in self._snapshot_cache:
            return self._snapshot_cache[cache_key]

        series = self._series_cache.get(coin)
        if not series:
            return {}

        timeframes = {}
        limits = {
            '5m': self.market_fetcher.TIMEFRAME_CONFIG['5m']['points'],
            '15m': self.market_fetcher.TIMEFRAME_CONFIG['15m']['points'],
            '1h': self.market_fetcher.TIMEFRAME_CONFIG['1h']['points'],
        }
        for timeframe in ('5m', '15m', '1h'):
            ts_list = series[f'{timeframe}_ts']
            candles = series[timeframe]
            idx = bisect_right(ts_list, ts_ms)
            if idx <= 0:
                timeframes[timeframe] = {}
                continue
            start_idx = max(0, idx - limits[timeframe])
            timeframes[timeframe] = self.market_fetcher.calculate_technical_indicators_from_history(
                candles[start_idx:idx]
            )

        self._snapshot_cache[cache_key] = timeframes
        return timeframes

    def _get_latest_candle(self, coin: str, timeframe: str, ts_ms: int) -> Optional[Dict]:
        series = self._series_cache.get(coin)
        if not series:
            return None
        ts_list = series[f'{timeframe}_ts']
        candles = series[timeframe]
        idx = bisect_right(ts_list, ts_ms)
        if idx <= 0:
            return None
        return candles[idx - 1]

    def _refresh_portfolio_state(self, portfolio: Dict, current_prices: Dict, ts_ms: Optional[int]) -> Dict:
        total_value = float(portfolio['cash'])
        positions_value = 0.0
        unrealized_pnl = 0.0
        timeframes_map = current_prices.get('__timeframes__', {})
        current_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) if ts_ms else None
        self.rule_helper._backtest_now = current_dt

        for position in portfolio['positions']:
            coin = position['coin']
            if coin not in current_prices:
                continue
            current_price = float(current_prices[coin]['price'])
            side = position['side']
            if side == 'long':
                gross_unrealized = (current_price - position['avg_price']) * position['quantity']
            else:
                gross_unrealized = (position['avg_price'] - current_price) * position['quantity']

            metrics = build_position_metrics(
                position['avg_price'],
                current_price,
                side,
                int(position['leverage']),
                stored_peak_price=position.get('peak_price'),
                stored_peak_profit_pct=position.get('peak_profit_pct'),
            )
            position['current_price'] = current_price
            position['pnl'] = gross_unrealized
            position['peak_price'] = metrics['peak_price']
            position['peak_profit_pct'] = metrics['peak_profit_pct']
            position['last_profit_pct'] = metrics['current_profit_pct']
            position['holding_minutes'] = self.rule_helper._position_age_minutes(position.get('opened_at'))
            position['timeframes'] = timeframes_map.get(coin, {})
            position_value = position.get('margin_used', 0.0) + gross_unrealized
            position['value'] = position_value
            positions_value += position_value
            unrealized_pnl += gross_unrealized
            total_value += position_value

        portfolio['positions_value'] = positions_value
        portfolio['unrealized_pnl'] = unrealized_pnl
        portfolio['total_value'] = total_value
        return deepcopy(portfolio)

    def _build_account_info(self, initial_capital: float, portfolio: Dict, current_dt: datetime) -> Dict:
        total_value = float(portfolio['total_value'])
        total_return = ((total_value - initial_capital) / initial_capital * 100) if initial_capital else 0.0
        return {
            'current_time': current_dt.strftime('%Y-%m-%d %H:%M:%S'),
            'initial_capital': float(initial_capital),
            'total_return': total_return,
        }

    def _should_consider_ai_decision(self, portfolio: Dict, market_state: Dict) -> bool:
        if portfolio.get('positions'):
            return True

        for coin, market_context in market_state.items():
            timeframes = market_context.get('timeframes', {}) or {}
            price = float(market_context.get('price', 0) or 0)
            if price <= 0:
                continue
            confidence = 0.66
            for side in ('long', 'short'):
                stop_loss, take_profit = self.rule_helper._resolve_risk_targets(side, price)
                guardrails = self.rule_helper._evaluate_entry_guardrails(
                    side,
                    price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    timeframes=timeframes,
                    confidence=confidence,
                )
                setup_class = guardrails.get('setup_class') or self.rule_helper._classify_setup_class(
                    side,
                    confidence,
                    price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    timeframes=timeframes,
                )
                if setup_class in {'A', 'B', 'C'} and not guardrails.get('hard_block'):
                    return True
        return False

    def _generate_rule_decisions(self, portfolio: Dict, current_portfolio: Dict, market_state: Dict) -> Dict:
        decisions = {}
        for coin, market_context in market_state.items():
            if self._find_position(portfolio, coin):
                continue

            price = float(market_context.get('price', 0) or 0)
            timeframes = market_context.get('timeframes', {}) or {}
            if price <= 0:
                continue

            long_stop, long_target = self.rule_helper._resolve_risk_targets('long', price)
            long_class = self.rule_helper._classify_setup_class(
                'long', 0.70, price, stop_loss=long_stop, take_profit=long_target, timeframes=timeframes
            )
            long_guardrails = self.rule_helper._evaluate_entry_guardrails(
                'long', price, stop_loss=long_stop, take_profit=long_target, timeframes=timeframes, confidence=0.70
            )
            if long_class in {'A', 'B', 'C'} and not long_guardrails.get('hard_block'):
                quantity = self._estimate_rule_quantity(current_portfolio, price, 3, 0.06)
                if quantity > 0:
                    decisions[coin] = {
                        'signal': 'buy_to_enter',
                        'quantity': quantity,
                        'leverage': 3,
                        'stop_loss': long_stop,
                        'profit_target': long_target,
                        'confidence': 0.70,
                    }
                    continue

            short_stop, short_target = self.rule_helper._resolve_risk_targets('short', price)
            short_class = self.rule_helper._classify_setup_class(
                'short', 0.66, price, stop_loss=short_stop, take_profit=short_target, timeframes=timeframes
            )
            short_guardrails = self.rule_helper._evaluate_entry_guardrails(
                'short', price, stop_loss=short_stop, take_profit=short_target, timeframes=timeframes, confidence=0.66
            )
            if short_class in {'A', 'B'} and not short_guardrails.get('hard_block'):
                quantity = self._estimate_rule_quantity(current_portfolio, price, 3, 0.05)
                if quantity > 0:
                    decisions[coin] = {
                        'signal': 'sell_to_enter',
                        'quantity': quantity,
                        'leverage': 3,
                        'stop_loss': short_stop,
                        'profit_target': short_target,
                        'confidence': 0.66,
                    }
        return decisions

    def _estimate_rule_quantity(self, portfolio: Dict, price: float, leverage: int, margin_ratio: float) -> float:
        available_cash = float(portfolio.get('cash', 0) or 0)
        if available_cash <= 0 or price <= 0:
            return 0.0
        margin_budget = available_cash * margin_ratio
        return max(0.0, margin_budget * leverage / price)

    def _run_risk_cycle(self, portfolio: Dict, current_prices: Dict, ts_ms: int) -> List[Dict]:
        results = []
        candle_map = current_prices.get('__candles__', {})
        positions = list(portfolio['positions'])
        current_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        self.rule_helper._backtest_now = current_dt

        for position in positions:
            coin = position['coin']
            live_position = self._find_position(portfolio, coin)
            if not live_position or coin not in current_prices:
                continue

            candle = candle_map.get(coin)
            intrabar_result = self._check_intrabar_risk(portfolio, live_position, candle, current_dt)
            if intrabar_result:
                results.append(intrabar_result)
                continue

            current_price = float(current_prices[coin]['price'])
            side = live_position['side']
            timeframes = current_prices.get('__timeframes__', {}).get(coin, {})
            current_profit_pct = float(live_position.get('last_profit_pct') or 0)
            peak_profit_pct = float(live_position.get('peak_profit_pct') or 0)
            drawdown_ratio = 0.0
            if peak_profit_pct > 0:
                drawdown_ratio = max(0.0, (peak_profit_pct - current_profit_pct) / peak_profit_pct)

            time_stop_signal = self.rule_helper._evaluate_time_stop(
                side=side,
                current_profit_pct=current_profit_pct,
                opened_at=live_position.get('opened_at'),
                setup_class=str(live_position.get('setup_class') or ''),
                timeframes=timeframes,
            )
            if time_stop_signal.get('should_exit'):
                results.append(self._close_position(
                    portfolio, live_position, current_price, current_dt, signal='close_position',
                    reason='time_stop'
                ))
                continue

            quality_exit_signal = self.rule_helper._evaluate_quality_exit(
                side=side,
                entry_price=live_position['avg_price'],
                current_price=current_price,
                current_profit_pct=current_profit_pct,
                take_profit=live_position.get('take_profit'),
                setup_class=str(live_position.get('setup_class') or ''),
                management_stage=int(live_position.get('management_stage') or 0),
                timeframes=timeframes,
            )
            if quality_exit_signal.get('should_reduce'):
                reduce_qty = float(live_position['quantity']) * float(quality_exit_signal.get('reduce_ratio') or 0)
                if reduce_qty > 0:
                    live_position['management_stage'] = quality_exit_signal.get('next_stage', live_position.get('management_stage', 0))
                    results.append(self._close_position(
                        portfolio, live_position, current_price, current_dt,
                        quantity=reduce_qty, signal='reduce_position',
                        reason='quality_exit'
                    ))
                    continue
            suggested_quality_stop = quality_exit_signal.get('suggested_stop_loss')
            if suggested_quality_stop is not None:
                self._improve_stop_loss(live_position, suggested_quality_stop)

            failed_trade_signal = self.rule_helper._evaluate_failed_trade_exit(
                side=side,
                entry_price=live_position['avg_price'],
                current_price=current_price,
                stop_loss=live_position.get('stop_loss'),
                current_profit_pct=current_profit_pct,
                timeframes=timeframes,
            )
            if failed_trade_signal.get('should_exit'):
                results.append(self._close_position(
                    portfolio, live_position, current_price, current_dt, signal='close_position',
                    reason='failed_trade_exit'
                ))
                continue

            reversal_signal = build_reversal_exit_signal(
                side=side,
                entry_price=live_position['avg_price'],
                current_price=current_price,
                peak_price=float(live_position.get('peak_price') or live_position['avg_price']),
                current_profit_pct=current_profit_pct,
                peak_profit_pct=peak_profit_pct,
                leverage=int(live_position['leverage']),
                tf_15m=timeframes.get('15m', {}),
                tf_5m=timeframes.get('5m', {}),
                tf_1h=timeframes.get('1h', {}),
                sensitivity=self.rule_helper.early_reversal_sensitivity,
            )
            if reversal_signal.get('should_exit'):
                results.append(self._close_position(
                    portfolio, live_position, current_price, current_dt, signal='close_position',
                    reason='early_reversal'
                ))
                continue
            suggested_reversal_stop = reversal_signal.get('suggested_stop_loss')
            if reversal_signal.get('should_tighten_stop') and suggested_reversal_stop is not None:
                self._improve_stop_loss(live_position, suggested_reversal_stop)

            weak_15m = self.rule_helper._timeframe_is_weakening(side, timeframes.get('15m', {}))
            weak_5m = self.rule_helper._timeframe_is_weakening(side, timeframes.get('5m', {}))
            if peak_profit_pct >= self.rule_helper.EARLY_PROFIT_PROTECTION_PCT and weak_15m and weak_5m:
                break_even_stop = self.rule_helper._get_break_even_stop(
                    live_position['avg_price'],
                    side,
                    int(live_position['leverage']),
                )
                self._improve_stop_loss(live_position, break_even_stop)

            if peak_profit_pct >= self.rule_helper.peak_profit_activation_pct and drawdown_ratio >= self.rule_helper.peak_drawdown_close_ratio:
                results.append(self._close_position(
                    portfolio, live_position, current_price, current_dt, signal='close_position',
                    reason='peak_drawdown'
                ))
                continue

            locked_profit_pct, desired_stop_loss = calculate_peak_drawdown_stop(
                live_position['avg_price'],
                peak_profit_pct,
                side,
                int(live_position['leverage']),
            )
            if desired_stop_loss is not None and locked_profit_pct > float(live_position.get('trailing_tier') or 0):
                if self._improve_stop_loss(live_position, desired_stop_loss):
                    live_position['trailing_tier'] = locked_profit_pct

        return results

    def _check_intrabar_risk(self, portfolio: Dict, position: Dict, candle: Optional[Dict], current_dt: datetime) -> Optional[Dict]:
        if not candle:
            return None
        stop_loss = position.get('stop_loss')
        take_profit = position.get('take_profit')
        side = position['side']
        low = float(candle.get('low', candle.get('close', 0)) or 0)
        high = float(candle.get('high', candle.get('close', 0)) or 0)

        if side == 'long':
            if stop_loss is not None and low <= float(stop_loss):
                return self._close_position(portfolio, position, float(stop_loss), current_dt, signal='fixed_stop', reason='stop_loss')
            if take_profit is not None and high >= float(take_profit):
                return self._close_position(portfolio, position, float(take_profit), current_dt, signal='take_profit', reason='take_profit')
        else:
            if stop_loss is not None and high >= float(stop_loss):
                return self._close_position(portfolio, position, float(stop_loss), current_dt, signal='fixed_stop', reason='stop_loss')
            if take_profit is not None and low <= float(take_profit):
                return self._close_position(portfolio, position, float(take_profit), current_dt, signal='take_profit', reason='take_profit')
        return None

    def _execute_decisions(self, portfolio: Dict, decisions: Dict, market_state: Dict, ts_ms: int) -> List[Dict]:
        results = []
        current_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        for coin, decision in (decisions or {}).items():
            if not isinstance(decision, dict) or coin not in market_state:
                continue
            signal = str(decision.get('signal') or 'hold')
            if signal == 'hold':
                continue
            try:
                if signal == 'buy_to_enter':
                    result = self._open_position(portfolio, coin, 'long', decision, market_state[coin], current_dt)
                elif signal == 'sell_to_enter':
                    result = self._open_position(portfolio, coin, 'short', decision, market_state[coin], current_dt)
                elif signal in {'sell_to_close', 'buy_to_close', 'close_position'}:
                    position = self._find_position(portfolio, coin)
                    result = self._close_position(
                        portfolio,
                        position,
                        float(market_state[coin]['price']),
                        current_dt,
                        signal=signal,
                    ) if position else None
                elif signal == 'reduce_position':
                    position = self._find_position(portfolio, coin)
                    result = self._close_position(
                        portfolio,
                        position,
                        float(market_state[coin]['price']),
                        current_dt,
                        quantity=float(decision.get('quantity', 0) or 0),
                        signal='reduce_position',
                    ) if position else None
                elif signal == 'increase_position':
                    result = self._increase_position(portfolio, coin, decision, market_state[coin], current_dt)
                else:
                    result = {
                        'timestamp': current_dt.strftime('%Y-%m-%d %H:%M:%S'),
                        'coin': coin,
                        'signal': signal,
                        'error': f'Unsupported signal: {signal}',
                    }
                if result:
                    results.append(result)
            except Exception as exc:
                results.append({
                    'timestamp': current_dt.strftime('%Y-%m-%d %H:%M:%S'),
                    'coin': coin,
                    'signal': signal,
                    'error': str(exc),
                })
        return results

    def _open_position(self, portfolio: Dict, coin: str, side: str, decision: Dict,
                       market_context: Dict, current_dt: datetime) -> Optional[Dict]:
        if self._find_position(portfolio, coin):
            return None

        quantity = float(decision.get('quantity', 0) or 0)
        if quantity <= 0:
            return None
        leverage = int(decision.get('leverage', 1) or 1)
        price = float(market_context['price'])
        stop_loss, take_profit = self.rule_helper._resolve_risk_targets(
            side,
            price,
            decision.get('stop_loss'),
            decision.get('profit_target') or decision.get('take_profit'),
        )
        guardrails = self.rule_helper._evaluate_entry_guardrails(
            side,
            price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timeframes=market_context.get('timeframes', {}),
            confidence=float(decision.get('confidence') or 0.60),
        )
        if guardrails.get('hard_block'):
            return {
                'timestamp': current_dt.strftime('%Y-%m-%d %H:%M:%S'),
                'coin': coin,
                'signal': 'rejected',
                'reason': '; '.join(guardrails.get('reasons', [])[:3]),
            }

        entry_ratio_cap, _ = self.rule_helper._get_entry_margin_ratio(
            decision,
            {coin: market_context},
            coin,
            side
        )
        max_margin_allowed = float(portfolio['cash']) * entry_ratio_cap
        required_margin = (quantity * price) / leverage
        if required_margin > max_margin_allowed and max_margin_allowed > 0:
            quantity = max_margin_allowed * leverage / price
            required_margin = (quantity * price) / leverage
        if quantity <= 0:
            return None

        entry_fee = quantity * price * self.ENTRY_FEE_PCT
        if required_margin + entry_fee > float(portfolio['cash']):
            return {
                'timestamp': current_dt.strftime('%Y-%m-%d %H:%M:%S'),
                'coin': coin,
                'signal': 'rejected',
                'reason': 'insufficient cash',
            }

        setup_class = self.rule_helper._classify_setup_class(
            side,
            float(decision.get('confidence') or 0),
            price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timeframes=market_context.get('timeframes', {}),
        )
        opened_at = current_dt.strftime('%Y-%m-%d %H:%M:%S')
        position = {
            'coin': coin,
            'side': side,
            'quantity': quantity,
            'avg_price': price,
            'leverage': leverage,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'entry_fee': entry_fee,
            'margin_used': required_margin,
            'peak_price': price,
            'peak_profit_pct': 0.0,
            'last_profit_pct': 0.0,
            'opened_at': opened_at,
            'setup_class': setup_class,
            'entry_confidence': float(decision.get('confidence') or 0),
            'management_stage': 0,
            'trailing_tier': 0.0,
        }
        portfolio['cash'] -= (required_margin + entry_fee)
        portfolio['positions'].append(position)

        should_abort, reasons = self.rule_helper._should_abort_post_fill_entry(
            side,
            price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timeframes=market_context.get('timeframes', {}),
        )
        trade = {
            'timestamp': opened_at,
            'coin': coin,
            'signal': 'buy_to_enter' if side == 'long' else 'sell_to_enter',
            'side': side,
            'quantity': quantity,
            'price': price,
            'leverage': leverage,
            'pnl': 0.0,
            'gross_pnl': 0.0,
            'fee': entry_fee,
            'setup_class': setup_class,
        }
        if should_abort:
            close_trade = self._close_position(
                portfolio,
                position,
                price,
                current_dt,
                signal='close_position',
                reason='post_fill_abort:' + '; '.join(reasons[:3]),
            )
            return close_trade
        return trade

    def _increase_position(self, portfolio: Dict, coin: str, decision: Dict,
                           market_context: Dict, current_dt: datetime) -> Optional[Dict]:
        position = self._find_position(portfolio, coin)
        if not position or position['side'] != 'long':
            return None

        quantity = float(decision.get('quantity', 0) or 0)
        if quantity <= 0:
            return None
        leverage = int(decision.get('leverage', position['leverage']) or position['leverage'])
        price = float(market_context['price'])
        stop_loss, take_profit = self.rule_helper._resolve_risk_targets(
            'long',
            price,
            decision.get('stop_loss') if decision.get('stop_loss') is not None else position.get('stop_loss'),
            decision.get('profit_target') or decision.get('take_profit') or position.get('take_profit'),
        )
        required_margin = (quantity * price) / leverage
        entry_fee = quantity * price * self.ENTRY_FEE_PCT
        if required_margin + entry_fee > float(portfolio['cash']):
            return None

        portfolio['cash'] -= (required_margin + entry_fee)
        total_quantity = position['quantity'] + quantity
        position['avg_price'] = (
            (position['avg_price'] * position['quantity']) + (price * quantity)
        ) / total_quantity
        position['quantity'] = total_quantity
        position['margin_used'] += required_margin
        position['entry_fee'] += entry_fee
        position['leverage'] = leverage
        position['stop_loss'] = stop_loss
        position['take_profit'] = take_profit

        return {
            'timestamp': current_dt.strftime('%Y-%m-%d %H:%M:%S'),
            'coin': coin,
            'signal': 'increase_position',
            'side': 'long',
            'quantity': quantity,
            'price': price,
            'leverage': leverage,
            'pnl': 0.0,
            'gross_pnl': 0.0,
            'fee': entry_fee,
        }

    def _close_position(self, portfolio: Dict, position: Optional[Dict], close_price: float,
                        current_dt: datetime, quantity: Optional[float] = None,
                        signal: str = 'close_position', reason: str = '') -> Optional[Dict]:
        if not position:
            return None

        close_quantity = float(position['quantity'] if quantity is None else min(position['quantity'], quantity))
        if close_quantity <= 0:
            return None

        side = position['side']
        close_ratio = close_quantity / position['quantity']
        allocated_margin = float(position.get('margin_used', 0.0)) * close_ratio
        allocated_entry_fee = float(position.get('entry_fee', 0.0)) * close_ratio
        close_fee = close_quantity * close_price * self.ENTRY_FEE_PCT

        if side == 'long':
            gross_pnl = (close_price - position['avg_price']) * close_quantity
        else:
            gross_pnl = (position['avg_price'] - close_price) * close_quantity
        net_pnl = gross_pnl - allocated_entry_fee - close_fee

        portfolio['cash'] += allocated_margin + gross_pnl - close_fee
        portfolio['realized_pnl'] += net_pnl

        position['quantity'] -= close_quantity
        position['margin_used'] -= allocated_margin
        position['entry_fee'] -= allocated_entry_fee

        if position['quantity'] <= 1e-12:
            portfolio['positions'] = [p for p in portfolio['positions'] if p is not position]

        return {
            'timestamp': current_dt.strftime('%Y-%m-%d %H:%M:%S'),
            'coin': position['coin'],
            'signal': signal,
            'side': side,
            'quantity': close_quantity,
            'price': close_price,
            'leverage': int(position['leverage']),
            'pnl': net_pnl,
            'gross_pnl': gross_pnl,
            'fee': allocated_entry_fee + close_fee,
            'reason': reason,
        }

    def _find_position(self, portfolio: Dict, coin: str) -> Optional[Dict]:
        for position in portfolio['positions']:
            if position['coin'] == coin:
                return position
        return None

    def _improve_stop_loss(self, position: Dict, new_stop_loss: float) -> bool:
        if new_stop_loss is None:
            return False
        current_stop = position.get('stop_loss')
        if current_stop is None:
            position['stop_loss'] = float(new_stop_loss)
            return True
        if position['side'] == 'long' and float(new_stop_loss) > float(current_stop):
            position['stop_loss'] = float(new_stop_loss)
            return True
        if position['side'] == 'short' and float(new_stop_loss) < float(current_stop):
            position['stop_loss'] = float(new_stop_loss)
            return True
        return False

    def _calculate_backtest_metrics(self, trades: List[Dict], daily_values: List[Dict],
                                    initial_capital: float, final_portfolio: Dict,
                                    ai_cycle_count: int, risk_cycle_count: int) -> Dict:
        final_value = float(final_portfolio['total_value'])
        total_return = ((final_value - initial_capital) / initial_capital * 100) if initial_capital else 0.0

        realized_trades = [t for t in trades if t.get('signal') in {'close_position', 'sell_to_close', 'buy_to_close', 'reduce_position', 'fixed_stop', 'take_profit'} and 'pnl' in t]
        entry_trades = [t for t in trades if t.get('signal') in {'buy_to_enter', 'sell_to_enter', 'increase_position'}]
        wins = [t for t in realized_trades if float(t.get('pnl', 0) or 0) > 0]
        losses = [t for t in realized_trades if float(t.get('pnl', 0) or 0) < 0]
        total_fees = sum(float(t.get('fee', 0) or 0) for t in trades)
        total_net_pnl = sum(float(t.get('pnl', 0) or 0) for t in realized_trades)
        total_gross_pnl = sum(float(t.get('gross_pnl', 0) or 0) for t in realized_trades)

        returns = []
        for idx in range(1, len(daily_values)):
            previous = float(daily_values[idx - 1]['total_value'] or 0)
            current = float(daily_values[idx]['total_value'] or 0)
            if previous > 0:
                returns.append((current - previous) / previous)
        sharpe_ratio = 0.0
        if returns:
            avg_return = sum(returns) / len(returns)
            variance = sum((value - avg_return) ** 2 for value in returns) / len(returns)
            std_return = variance ** 0.5
            if std_return > 0:
                sharpe_ratio = avg_return / std_return * (252 ** 0.5)

        gross_profit = sum(float(t.get('pnl', 0) or 0) for t in wins)
        gross_loss_abs = abs(sum(float(t.get('pnl', 0) or 0) for t in losses))
        profit_factor = (gross_profit / gross_loss_abs) if gross_loss_abs > 0 else None

        return {
            'total_return': total_return,
            'final_value': final_value,
            'max_drawdown': self._calculate_max_drawdown([float(day['total_value']) for day in daily_values]),
            'sharpe_ratio': sharpe_ratio,
            'decision_cycle_count': ai_cycle_count,
            'risk_cycle_count': risk_cycle_count,
            'entry_count': len(entry_trades),
            'exit_count': len(realized_trades),
            'win_rate': (len(wins) / len(realized_trades) * 100) if realized_trades else 0.0,
            'winning_trades': len(wins),
            'losing_trades': len(losses),
            'total_net_pnl': total_net_pnl,
            'total_gross_pnl': total_gross_pnl,
            'total_fees': total_fees,
            'avg_pnl_per_exit': (total_net_pnl / len(realized_trades)) if realized_trades else 0.0,
            'profit_factor': profit_factor,
            'best_trade': max((float(t.get('pnl', 0) or 0) for t in realized_trades), default=0.0),
            'worst_trade': min((float(t.get('pnl', 0) or 0) for t in realized_trades), default=0.0),
            'coin_stats': self._build_coin_stats(realized_trades),
        }

    def _build_coin_stats(self, trades: List[Dict]) -> List[Dict]:
        stats: Dict[str, Dict] = {}
        for trade in trades:
            coin = trade.get('coin')
            if not coin:
                continue
            stats.setdefault(coin, {
                'coin': coin,
                'trades': 0,
                'wins': 0,
                'losses': 0,
                'net_pnl': 0.0,
                'gross_pnl': 0.0,
                'fees': 0.0,
            })
            item = stats[coin]
            pnl = float(trade.get('pnl', 0) or 0)
            item['trades'] += 1
            item['net_pnl'] += pnl
            item['gross_pnl'] += float(trade.get('gross_pnl', 0) or 0)
            item['fees'] += float(trade.get('fee', 0) or 0)
            if pnl > 0:
                item['wins'] += 1
            elif pnl < 0:
                item['losses'] += 1

        result = []
        for item in stats.values():
            item['win_rate'] = (item['wins'] / item['trades'] * 100) if item['trades'] else 0.0
            result.append(item)
        result.sort(key=lambda row: row['net_pnl'], reverse=True)
        return result

    def _calculate_max_drawdown(self, values: List[float]) -> float:
        if not values:
            return 0.0
        peak = values[0]
        max_drawdown = 0.0
        for value in values:
            peak = max(peak, value)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - value) / peak)
        return max_drawdown
