"""Contract-based reversal strategy backtester without martingale."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List


class EventReversalFuturesStrategyBacktester:
    """Reverse after 3 same-color closed candles and manage with fixed SL/TP."""

    def __init__(self, market_fetcher):
        self.market_fetcher = market_fetcher

    def run_backtest(self, start_date: str, end_date: str, initial_capital: float,
                     params: Dict | None = None,
                     progress_callback: Callable[[float, int, int, str], None] | None = None) -> Dict:
        params = params or {}
        coin = str(params.get('coin') or 'BTC')
        timeframe = str(params.get('timeframe') or '15m')
        leverage = max(1, int(params.get('leverage', 2)))
        risk_pct = max(0.0005, float(params.get('risk_pct', 0.005)))
        stop_loss_pct = max(0.0005, float(params.get('stop_loss_pct', 0.003)))
        take_profit_pct = max(stop_loss_pct * 0.5, float(params.get('take_profit_pct', 0.006)))
        max_hold_bars = max(1, int(params.get('max_hold_bars', 3)))
        fee_rate = max(0.0, float(params.get('fee_rate', 0.0005)))
        slippage_rate = max(0.0, float(params.get('slippage_rate', 0.0002)))
        trend_filter_enabled = bool(params.get('trend_filter_enabled', True))
        trend_rsi_threshold = max(50.0, float(params.get('trend_rsi_threshold', 60.0)))
        trend_lookback = max(30, int(params.get('trend_lookback', 30)))

        start_dt = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(tzinfo=timezone.utc) + timedelta(days=1) - timedelta(minutes=1)
        start_ts_ms = int((start_dt - timedelta(days=5)).timestamp() * 1000)
        end_ts_ms = int(end_dt.timestamp() * 1000)
        candles = self.market_fetcher.get_historical_candles_range(coin, timeframe, start_ts_ms, end_ts_ms)
        candles = [candle for candle in candles if int(candle['timestamp']) >= int(start_dt.timestamp() * 1000)]

        balance = float(initial_capital)
        trades: List[Dict] = []
        daily_values: List[Dict] = []
        wins = 0
        losses = 0
        total_fees = 0.0
        total_signals = max(0, len(candles) - (3 + max_hold_bars))
        idx = 3
        trend_filtered = 0
        pattern_candidates = 0
        exit_reason_stats = {
            'stop_loss': {'count': 0, 'net_pnl': 0.0},
            'take_profit': {'count': 0, 'net_pnl': 0.0},
            'time_exit': {'count': 0, 'net_pnl': 0.0},
        }

        while idx < len(candles) - 1:
            signal_candle = candles[idx]
            signal_dt = datetime.fromtimestamp(int(signal_candle['timestamp']) / 1000, tz=timezone.utc)
            if progress_callback and (idx == 3 or idx % 50 == 0 or idx >= len(candles) - 2):
                progress_callback(
                    5 + (idx / max(len(candles), 1)) * 90,
                    idx - 2,
                    total_signals,
                    f'合约反转回放中：{signal_dt.strftime("%Y-%m-%d %H:%M")}'
                )

            recent = candles[idx - 3:idx]
            colors = [self._candle_color(candle) for candle in recent]
            if 'flat' in colors or len(set(colors)) != 1:
                self._record_daily_value(daily_values, signal_dt, balance)
                idx += 1
                continue

            pattern_candidates += 1
            side = 'short' if colors[0] == 'bull' else 'long'
            if trend_filter_enabled and self._is_strong_trend(candles, idx, side, trend_lookback, trend_rsi_threshold):
                trend_filtered += 1
                self._record_daily_value(daily_values, signal_dt, balance)
                idx += 1
                continue

            entry_candle = candles[idx]
            raw_entry = float(entry_candle['open'])
            entry_price = raw_entry * (1 + slippage_rate) if side == 'long' else raw_entry * (1 - slippage_rate)
            risk_usdt = balance * risk_pct
            stop_distance = entry_price * stop_loss_pct
            if stop_distance <= 0:
                idx += 1
                continue
            quantity = risk_usdt / stop_distance
            notional = quantity * entry_price
            margin_required = notional / leverage
            if margin_required > balance:
                quantity = balance * leverage / entry_price
                notional = quantity * entry_price
                margin_required = notional / leverage
            if quantity <= 0 or margin_required <= 0:
                idx += 1
                continue

            if side == 'long':
                stop_price = entry_price * (1 - stop_loss_pct)
                take_profit_price = entry_price * (1 + take_profit_pct)
            else:
                stop_price = entry_price * (1 + stop_loss_pct)
                take_profit_price = entry_price * (1 - take_profit_pct)

            exit_info = self._simulate_exit(
                candles=candles,
                start_index=idx,
                side=side,
                entry_price=entry_price,
                stop_price=stop_price,
                take_profit_price=take_profit_price,
                max_hold_bars=max_hold_bars,
                slippage_rate=slippage_rate,
            )
            exit_price = exit_info['exit_price']
            exit_ts = exit_info['exit_timestamp']
            exit_reason = exit_info['exit_reason']
            gross_pnl = (exit_price - entry_price) * quantity if side == 'long' else (entry_price - exit_price) * quantity
            fees = (entry_price * quantity + exit_price * quantity) * fee_rate
            pnl = gross_pnl - fees
            total_fees += fees
            balance += pnl
            won = pnl > 0
            if won:
                wins += 1
            else:
                losses += 1
            if exit_reason in exit_reason_stats:
                exit_reason_stats[exit_reason]['count'] += 1
                exit_reason_stats[exit_reason]['net_pnl'] += pnl

            trades.append({
                'timestamp': signal_dt.strftime('%Y-%m-%d %H:%M:%S'),
                'coin': coin,
                'signal': side,
                'quantity': quantity,
                'price': entry_price,
                'exit_price': exit_price,
                'leverage': leverage,
                'pnl': pnl,
                'gross_pnl': gross_pnl,
                'fee': fees,
                'exit_reason': exit_reason,
                'holding_bars': exit_info['bars_held'],
                'won': won,
            })
            self._record_daily_value(daily_values, datetime.fromtimestamp(exit_ts / 1000, tz=timezone.utc), balance)
            idx = max(idx + 1, exit_info['exit_index'])

        if progress_callback:
            progress_callback(100, total_signals, total_signals, '合约反转回测完成')

        final_value = balance
        total_return = ((final_value - initial_capital) / initial_capital * 100) if initial_capital else 0.0
        win_rate = (wins / (wins + losses) * 100) if (wins + losses) else 0.0
        metrics = {
            'total_return': total_return,
            'final_value': final_value,
            'total_net_pnl': final_value - initial_capital,
            'total_fees': total_fees,
            'win_rate': win_rate,
            'entry_count': len(trades),
            'winning_trades': wins,
            'losing_trades': losses,
            'max_drawdown': self._calculate_max_drawdown([row['total_value'] for row in daily_values]),
            'coin_stats': [{
                'coin': coin,
                'trades': len(trades),
                'win_rate': win_rate,
                'net_pnl': final_value - initial_capital,
                'fees': total_fees,
            }],
            'pattern_candidates': pattern_candidates,
            'trend_filtered': trend_filtered,
            'executed_entries': len(trades),
            'exit_reason_stats': exit_reason_stats,
        }

        return {
            'start_date': start_date,
            'end_date': end_date,
            'initial_capital': float(initial_capital),
            'final_value': final_value,
            'total_return': total_return,
            'settings': {
                'strategy': 'event_reversal_futures',
                'coin': coin,
                'interval': timeframe,
                'leverage': leverage,
                'risk_pct': risk_pct,
                'stop_loss_pct': stop_loss_pct,
                'take_profit_pct': take_profit_pct,
                'max_hold_bars': max_hold_bars,
                'fee_rate': fee_rate,
                'slippage_rate': slippage_rate,
                'trend_filter_enabled': trend_filter_enabled,
                'trend_rsi_threshold': trend_rsi_threshold,
                'trend_lookback': trend_lookback,
            },
            'summary': {
                'decision_cycles': total_signals,
                'entry_count': len(trades),
                'exit_count': len(trades),
                'winning_trades': wins,
                'losing_trades': losses,
            },
            'trades': trades,
            'daily_values': daily_values,
            'decision_logs': [],
            'metrics': metrics,
        }

    def _is_strong_trend(self, candles: List[Dict], index: int, side: str, trend_lookback: int, trend_rsi_threshold: float) -> bool:
        if index < trend_lookback:
            return False
        window = candles[index - trend_lookback:index]
        closes = [float(candle['close']) for candle in window]
        if len(closes) < trend_lookback:
            return False
        current_close = closes[-1]
        sma = sum(closes) / len(closes)
        rsi = self._calculate_rsi(closes, min(14, len(closes) - 1))
        if side == 'short':
            return current_close > sma and rsi >= trend_rsi_threshold
        return current_close < sma and rsi <= (100 - trend_rsi_threshold)

    def _calculate_rsi(self, closes: List[float], period: int) -> float:
        if len(closes) <= period:
            return 50.0
        gains = []
        losses = []
        for idx in range(1, len(closes)):
            change = closes[idx] - closes[idx - 1]
            gains.append(max(change, 0.0))
            losses.append(max(-change, 0.0))
        avg_gain = sum(gains[-period:]) / period if period > 0 else 0.0
        avg_loss = sum(losses[-period:]) / period if period > 0 else 0.0
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _simulate_exit(self, candles: List[Dict], start_index: int, side: str, entry_price: float,
                       stop_price: float, take_profit_price: float, max_hold_bars: int,
                       slippage_rate: float) -> Dict:
        exit_index = min(len(candles) - 1, start_index + max_hold_bars)
        for idx in range(start_index, min(len(candles), start_index + max_hold_bars + 1)):
            candle = candles[idx]
            high = float(candle['high'])
            low = float(candle['low'])
            close = float(candle['close'])

            if side == 'long':
                if low <= stop_price:
                    return {
                        'exit_price': stop_price * (1 - slippage_rate),
                        'exit_timestamp': int(candle['timestamp']),
                        'exit_reason': 'stop_loss',
                        'bars_held': idx - start_index + 1,
                        'exit_index': idx + 1,
                    }
                if high >= take_profit_price:
                    return {
                        'exit_price': take_profit_price * (1 - slippage_rate),
                        'exit_timestamp': int(candle['timestamp']),
                        'exit_reason': 'take_profit',
                        'bars_held': idx - start_index + 1,
                        'exit_index': idx + 1,
                    }
            else:
                if high >= stop_price:
                    return {
                        'exit_price': stop_price * (1 + slippage_rate),
                        'exit_timestamp': int(candle['timestamp']),
                        'exit_reason': 'stop_loss',
                        'bars_held': idx - start_index + 1,
                        'exit_index': idx + 1,
                    }
                if low <= take_profit_price:
                    return {
                        'exit_price': take_profit_price * (1 + slippage_rate),
                        'exit_timestamp': int(candle['timestamp']),
                        'exit_reason': 'take_profit',
                        'bars_held': idx - start_index + 1,
                        'exit_index': idx + 1,
                    }

            exit_index = idx + 1
            last_close = close
            last_ts = int(candle['timestamp'])

        return {
            'exit_price': last_close * (1 - slippage_rate if side == 'long' else 1 + slippage_rate),
            'exit_timestamp': last_ts,
            'exit_reason': 'time_exit',
            'bars_held': max_hold_bars,
            'exit_index': exit_index,
        }

    def _candle_color(self, candle: Dict) -> str:
        open_price = float(candle.get('open', 0) or 0)
        close_price = float(candle.get('close', 0) or 0)
        if close_price > open_price:
            return 'bull'
        if close_price < open_price:
            return 'bear'
        return 'flat'

    def _record_daily_value(self, daily_values: List[Dict], signal_dt: datetime, balance: float) -> None:
        date_str = signal_dt.strftime('%Y-%m-%d')
        if not daily_values or daily_values[-1]['date'] != date_str:
            daily_values.append({
                'date': date_str,
                'total_value': balance,
                'cash': balance,
                'positions_value': 0.0,
                'positions_count': 0,
            })
        else:
            daily_values[-1]['total_value'] = balance
            daily_values[-1]['cash'] = balance

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
