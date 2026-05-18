"""BTCUSDT index event-reversal strategy backtester."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List


class EventReversalStrategyBacktester:
    """Backtest 3-same-color candle reversal logic for BTCUSDT index."""

    DEFAULT_STAKES = [7, 13, 30, 66, 142]

    def __init__(self, market_fetcher):
        self.market_fetcher = market_fetcher

    def run_backtest(self, start_date: str, end_date: str, initial_capital: float,
                     params: Dict | None = None,
                     progress_callback: Callable[[float, int, int, str], None] | None = None) -> Dict:
        params = params or {}
        pair = str(params.get('pair') or 'BTCUSDT')
        interval = str(params.get('interval') or '1m')
        payout_ratio = float(params.get('payout_ratio', 0.92))
        cooldown_signals = max(0, int(params.get('cooldown_signals', 3)))
        stakes = params.get('stakes') or self.DEFAULT_STAKES
        stakes = [float(value) for value in stakes][:5]

        start_dt = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(tzinfo=timezone.utc) + timedelta(days=1) - timedelta(minutes=1)
        start_ts_ms = int((start_dt - timedelta(minutes=10)).timestamp() * 1000)
        end_ts_ms = int(end_dt.timestamp() * 1000)
        candles = self.market_fetcher.get_binance_index_price_klines(pair, interval, start_ts_ms, end_ts_ms)
        candles = [candle for candle in candles if int(candle['timestamp']) >= int(start_dt.timestamp() * 1000)]

        balance = float(initial_capital)
        ladder_index = 0
        cooldown_remaining = 0
        trades: List[Dict] = []
        ladder_rounds: List[Dict] = []
        daily_values: List[Dict] = []
        total_signals = max(0, len(candles) - 4)
        wins = 0
        losses = 0
        total_fees = 0.0
        max_losing_streak = 0
        current_losing_streak = 0
        current_round_steps: List[Dict] = []
        round_id = 1
        pattern_candidates = 0
        preorder_armed = 0
        cooldown_rejected = 0
        balance_rejected = 0

        for idx in range(3, len(candles) - 1):
            current = candles[idx]
            next_candle = candles[idx + 1]
            signal_ts = int(current['timestamp'])
            signal_dt = datetime.fromtimestamp(signal_ts / 1000, tz=timezone.utc)

            if progress_callback and (idx == 3 or idx % 120 == 0 or idx == len(candles) - 2):
                progress_callback(
                    5 + (idx / max(len(candles), 1)) * 90,
                    idx - 2,
                    total_signals,
                    f'事件反转回放中：{signal_dt.strftime("%Y-%m-%d %H:%M")}'
                )

            colors = [self._candle_color(candles[idx - 2]), self._candle_color(candles[idx - 1]), self._candle_color(current)]
            if 'flat' in colors or len(set(colors)) != 1:
                self._record_daily_value(daily_values, signal_dt, balance)
                continue

            pattern_candidates += 1
            preorder_armed += 1

            if cooldown_remaining > 0:
                cooldown_remaining -= 1
                cooldown_rejected += 1
                self._record_daily_value(daily_values, signal_dt, balance)
                continue

            direction = 'DOWN' if colors[0] == 'bull' else 'UP'
            expected_color = 'bear' if direction == 'DOWN' else 'bull'
            level_before_trade = min(ladder_index, len(stakes) - 1) + 1
            stake = stakes[level_before_trade - 1]
            if balance < stake:
                balance_rejected += 1
                self._record_daily_value(daily_values, signal_dt, balance)
                continue

            result_color = self._candle_color(next_candle)
            won = result_color == expected_color
            fee = 0.0
            gross_pnl = stake * payout_ratio if won else -stake
            pnl = gross_pnl - fee
            total_fees += fee
            balance += pnl
            assigned_round_id = round_id
            round_step = {
                'timestamp': signal_dt.strftime('%Y-%m-%d %H:%M:%S'),
                'level': level_before_trade,
                'direction': direction,
                'stake': stake,
                'gross_pnl': gross_pnl,
                'fee': fee,
                'pnl': pnl,
                'won': won,
                'result_color': result_color,
                'trigger_color': colors[0],
                'round_id': assigned_round_id,
            }
            current_round_steps.append(round_step)

            if won:
                wins += 1
                ladder_index = 0
                current_losing_streak = 0
                ladder_rounds.append(self._build_round_record(round_id, current_round_steps, final_status='win'))
                current_round_steps = []
                round_id += 1
            else:
                losses += 1
                current_losing_streak += 1
                max_losing_streak = max(max_losing_streak, current_losing_streak)
                if ladder_index >= len(stakes) - 1:
                    ladder_index = 0
                    cooldown_remaining = cooldown_signals
                    ladder_rounds.append(self._build_round_record(round_id, current_round_steps, final_status='max_loss_cooldown'))
                    current_round_steps = []
                    round_id += 1
                else:
                    ladder_index += 1

            trades.append({
                'timestamp': signal_dt.strftime('%Y-%m-%d %H:%M:%S'),
                'coin': pair,
                'signal': direction,
                'quantity': stake,
                'price': float(next_candle['close']),
                'pnl': pnl,
                'gross_pnl': gross_pnl,
                'fee': fee,
                'ladder_level': level_before_trade,
                'won': won,
                'pattern': colors[0],
                'round_id': assigned_round_id,
            })
            self._record_daily_value(daily_values, signal_dt, balance)

        if progress_callback:
            progress_callback(100, total_signals, total_signals, '事件反转回测完成')

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
            'max_drawdown': self._calculate_max_drawdown([row['total_value'] for row in daily_values]),
            'coin_stats': [{
                'coin': pair,
                'trades': len(trades),
                'win_rate': win_rate,
                'net_pnl': final_value - initial_capital,
                'fees': total_fees,
            }],
            'winning_trades': wins,
            'losing_trades': losses,
            'max_losing_streak': max_losing_streak,
            'ladder_rounds': ladder_rounds,
            'high_risk_rounds_4plus': sum(1 for item in ladder_rounds if int(item.get('levels_used', 0) or 0) >= 4),
            'high_risk_rounds_5plus': sum(1 for item in ladder_rounds if int(item.get('levels_used', 0) or 0) >= 5),
            'execution_simulation': {
                'pattern_candidates': pattern_candidates,
                'preorder_armed': preorder_armed,
                'final_confirmed_entries': len(trades),
                'recheck_rejected': cooldown_rejected + balance_rejected,
                'cooldown_rejected': cooldown_rejected,
                'balance_rejected': balance_rejected,
            },
        }

        return {
            'start_date': start_date,
            'end_date': end_date,
            'initial_capital': float(initial_capital),
            'final_value': final_value,
            'total_return': total_return,
            'settings': {
                'strategy': 'event_reversal',
                'pair': pair,
                'interval': interval,
                'payout_ratio': payout_ratio,
                'cooldown_signals': cooldown_signals,
                'stakes': stakes,
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

    def _build_round_record(self, round_id: int, steps: List[Dict], final_status: str) -> Dict:
        total_stake = sum(float(step.get('stake', 0) or 0) for step in steps)
        total_fee = sum(float(step.get('fee', 0) or 0) for step in steps)
        total_pnl = sum(float(step.get('pnl', 0) or 0) for step in steps)
        return {
            'round_id': round_id,
            'steps': list(steps),
            'final_status': final_status,
            'levels_used': len(steps),
            'total_stake': total_stake,
            'total_fee': total_fee,
            'total_pnl': total_pnl,
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
