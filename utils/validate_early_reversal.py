"""Replay baseline vs early-reversal exits on recent OKX candles."""
from __future__ import annotations

import argparse
import bisect
import os
import sys
from collections import Counter, defaultdict
from statistics import mean
from typing import Dict, List, Tuple

import requests

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from market_data import MarketDataFetcher
from services.execution.position_metrics import (
    PEAK_DRAWDOWN_CLOSE_RATIO,
    PEAK_PROFIT_ACTIVATION_PCT,
    build_position_metrics,
    calculate_peak_drawdown_stop,
)
from services.execution.reversal_signal import build_reversal_exit_signal


OKX_BASE_URL = 'https://www.okx.com/api/v5'
EARLY_PROFIT_PROTECTION_PCT = 0.01
DEFAULT_STOP_LOSS_PCT = 0.03
DEFAULT_LEVERAGE = 3


def fetch_okx_candles(inst_id: str, total_bars: int) -> List[Dict]:
    bars: List[Dict] = []
    after = None
    seen = set()

    while len(bars) < total_bars:
        params = {
            'instId': inst_id,
            'bar': '5m',
            'limit': min(300, total_bars - len(bars)),
        }
        if after:
            params['after'] = after

        response = requests.get(f'{OKX_BASE_URL}/market/history-candles', params=params, timeout=10)
        response.raise_for_status()
        payload = response.json()
        chunk = payload.get('data', [])
        if payload.get('code') != '0' or not chunk:
            break

        added = 0
        for row in reversed(chunk):
            timestamp = int(row[0])
            if timestamp in seen:
                continue
            seen.add(timestamp)
            bars.append({
                'timestamp': timestamp,
                'open': float(row[1]),
                'high': float(row[2]),
                'low': float(row[3]),
                'close': float(row[4]),
                'volume': float(row[5]),
            })
            added += 1

        oldest = min(int(row[0]) for row in chunk)
        if added == 0 or after == oldest:
            break
        after = oldest

    bars.sort(key=lambda item: item['timestamp'])
    return bars[-total_bars:]


def aggregate_candles(candles: List[Dict], minutes: int) -> List[Dict]:
    if minutes == 5:
        return list(candles)

    interval_ms = minutes * 60 * 1000
    buckets: Dict[int, Dict] = {}
    ordered_keys: List[int] = []

    for candle in candles:
        bucket = candle['timestamp'] - (candle['timestamp'] % interval_ms)
        existing = buckets.get(bucket)
        if not existing:
            existing = {
                'timestamp': bucket,
                'open': candle['open'],
                'high': candle['high'],
                'low': candle['low'],
                'close': candle['close'],
                'volume': candle['volume'],
            }
            buckets[bucket] = existing
            ordered_keys.append(bucket)
        else:
            existing['high'] = max(existing['high'], candle['high'])
            existing['low'] = min(existing['low'], candle['low'])
            existing['close'] = candle['close']
            existing['volume'] += candle['volume']

    return [buckets[key] for key in sorted(ordered_keys)]


def candle_timestamps(candles: List[Dict]) -> List[int]:
    return [item['timestamp'] for item in candles]


def window_until(candles: List[Dict], timestamps: List[int], current_ts: int, limit: int) -> List[Dict]:
    idx = bisect.bisect_right(timestamps, current_ts)
    start = max(0, idx - limit)
    return candles[start:idx]


def timeframe_is_weakening(side: str, indicators: Dict) -> bool:
    if not indicators:
        return False

    current_price = float(indicators.get('current_price', 0) or 0)
    sma5 = float(indicators.get('sma_5', 0) or 0)
    rsi = float(indicators.get('rsi_14', 50) or 50)
    macd_hist = float(indicators.get('macd_histogram', 0) or 0)
    macd_tail = indicators.get('macd_histogram_tail', [])

    signals = 0
    if side == 'long':
        if rsi < 55:
            signals += 1
        if macd_hist < 0 or tail_is_weakening(side, macd_tail):
            signals += 1
        if sma5 > 0 and current_price < sma5:
            signals += 1
    else:
        if rsi > 45:
            signals += 1
        if macd_hist > 0 or tail_is_weakening(side, macd_tail):
            signals += 1
        if sma5 > 0 and current_price > sma5:
            signals += 1

    return signals >= 2


def tail_is_weakening(side: str, tail: List[float]) -> bool:
    if not isinstance(tail, list) or len(tail) < 3:
        return False
    a, b, c = tail[-3], tail[-2], tail[-1]
    if side == 'long':
        return c < b < a
    return c > b > a


def get_break_even_stop(entry_price: float, side: str, leverage: int) -> float:
    fee_adjustment = 0.001 / max(float(leverage or 1), 1.0)
    if side == 'long':
        return entry_price * (1 + fee_adjustment)
    return entry_price * (1 - fee_adjustment)


def default_stop_loss(entry_price: float, side: str) -> float:
    if side == 'long':
        return entry_price * (1 - DEFAULT_STOP_LOSS_PCT)
    return entry_price * (1 + DEFAULT_STOP_LOSS_PCT)


def stop_crossed(side: str, current_price: float, stop_loss: float) -> bool:
    if stop_loss is None:
        return False
    if side == 'long':
        return current_price <= stop_loss
    return current_price >= stop_loss


def candidate_entries(
    fetcher: MarketDataFetcher,
    coin: str,
    candles_5m: List[Dict],
    candles_15m: List[Dict],
    candles_1h: List[Dict],
    max_events: int,
) -> List[Dict]:
    ts_15m = candle_timestamps(candles_15m)
    ts_1h = candle_timestamps(candles_1h)
    entries: List[Dict] = []
    last_entry_index = -999

    for idx in range(360, len(candles_5m) - 96):
        if idx - last_entry_index < 24:
            continue

        current = candles_5m[idx]
        current_ts = current['timestamp']
        window_5m = candles_5m[max(0, idx - 120):idx + 1]
        window_15m = window_until(candles_15m, ts_15m, current_ts, 120)
        window_1h = window_until(candles_1h, ts_1h, current_ts, 60)
        if len(window_5m) < 40 or len(window_15m) < 30 or len(window_1h) < 30:
            continue

        ind_5m = fetcher.calculate_technical_indicators_from_history(window_5m)
        ind_15m = fetcher.calculate_technical_indicators_from_history(window_15m)
        ind_1h = fetcher.calculate_technical_indicators_from_history(window_1h)
        if not ind_5m or not ind_15m or not ind_1h:
            continue

        prev_high_20 = max(item['high'] for item in window_5m[-21:-1])
        prev_low_20 = min(item['low'] for item in window_5m[-21:-1])
        current_price = current['close']

        long_setup = (
            current_price >= prev_high_20 * 0.9995 and
            ind_1h['ema_12'] > ind_1h['ema_26'] and
            ind_15m['ema_12'] > ind_15m['ema_26'] and
            ind_15m['sma_5'] >= ind_15m['sma_14'] and
            ind_5m['macd_histogram'] > 0 and
            ind_5m['volume_ratio'] >= 0.80
        )
        short_setup = (
            current_price <= prev_low_20 * 1.0005 and
            ind_1h['ema_12'] < ind_1h['ema_26'] and
            ind_15m['ema_12'] < ind_15m['ema_26'] and
            ind_15m['sma_5'] <= ind_15m['sma_14'] and
            ind_5m['macd_histogram'] < 0 and
            ind_5m['volume_ratio'] >= 0.80
        )

        if not long_setup and not short_setup:
            continue

        entries.append({
            'coin': coin,
            'index': idx,
            'timestamp': current_ts,
            'entry_price': current_price,
            'side': 'long' if long_setup else 'short',
        })
        last_entry_index = idx
        if len(entries) >= max_events:
            break

    return entries


def simulate_exit(
    fetcher: MarketDataFetcher,
    event: Dict,
    candles_5m: List[Dict],
    candles_15m: List[Dict],
    candles_1h: List[Dict],
    mode: str,
    leverage: int = DEFAULT_LEVERAGE,
    max_hold_bars: int = 96,
) -> Dict:
    side = event['side']
    entry_price = event['entry_price']
    peak_price = entry_price
    peak_profit_pct = 0.0
    trailing_tier = 0.0
    stop_loss = default_stop_loss(entry_price, side)

    ts_15m = candle_timestamps(candles_15m)
    ts_1h = candle_timestamps(candles_1h)
    exit_index = min(len(candles_5m) - 1, event['index'] + max_hold_bars)
    exit_reason = 'time_exit'
    exit_price = candles_5m[exit_index]['close']

    for idx in range(event['index'] + 1, exit_index + 1):
        candle = candles_5m[idx]
        current_price = candle['close']

        if stop_crossed(side, current_price, stop_loss):
            exit_index = idx
            exit_price = stop_loss
            exit_reason = 'stop_loss'
            break

        window_5m = candles_5m[max(0, idx - 120):idx + 1]
        window_15m = window_until(candles_15m, ts_15m, candle['timestamp'], 120)
        window_1h = window_until(candles_1h, ts_1h, candle['timestamp'], 60)
        ind_5m = fetcher.calculate_technical_indicators_from_history(window_5m)
        ind_15m = fetcher.calculate_technical_indicators_from_history(window_15m)
        ind_1h = fetcher.calculate_technical_indicators_from_history(window_1h)
        if not ind_5m or not ind_15m or not ind_1h:
            continue

        metrics = build_position_metrics(
            entry_price,
            current_price,
            side,
            leverage,
            stored_peak_price=peak_price,
            stored_peak_profit_pct=peak_profit_pct,
        )
        peak_price = metrics['peak_price']
        peak_profit_pct = metrics['peak_profit_pct']
        current_profit_pct = metrics['current_profit_pct']
        drawdown_ratio = metrics['drawdown_ratio']

        if mode == 'enhanced':
            reversal_signal = build_reversal_exit_signal(
                side=side,
                entry_price=entry_price,
                current_price=current_price,
                peak_price=peak_price,
                current_profit_pct=current_profit_pct,
                peak_profit_pct=peak_profit_pct,
                leverage=leverage,
                tf_15m=ind_15m,
                tf_5m=ind_5m,
                tf_1h=ind_1h,
            )
            if reversal_signal.get('should_exit'):
                exit_index = idx
                exit_price = current_price
                exit_reason = 'early_reversal_exit'
                break
            if reversal_signal.get('should_tighten_stop'):
                candidate = reversal_signal.get('suggested_stop_loss')
                if candidate is not None:
                    if side == 'long':
                        stop_loss = max(stop_loss, candidate)
                    else:
                        stop_loss = min(stop_loss, candidate)

        weak_15m = timeframe_is_weakening(side, ind_15m)
        weak_5m = timeframe_is_weakening(side, ind_5m)

        if peak_profit_pct >= EARLY_PROFIT_PROTECTION_PCT and weak_15m and weak_5m:
            break_even_stop = get_break_even_stop(entry_price, side, leverage)
            if ((side == 'long' and current_price <= break_even_stop) or
                    (side == 'short' and current_price >= break_even_stop) or
                    current_profit_pct <= 0):
                exit_index = idx
                exit_price = current_price
                exit_reason = 'early_profit_protection_close'
                break
            if side == 'long':
                stop_loss = max(stop_loss, break_even_stop)
            else:
                stop_loss = min(stop_loss, break_even_stop)

        if peak_profit_pct >= PEAK_PROFIT_ACTIVATION_PCT and drawdown_ratio >= PEAK_DRAWDOWN_CLOSE_RATIO:
            exit_index = idx
            exit_price = current_price
            exit_reason = 'peak_drawdown_close'
            break

        locked_profit_pct, desired_stop_loss = calculate_peak_drawdown_stop(
            entry_price,
            peak_profit_pct,
            side,
            leverage,
        )
        if desired_stop_loss is not None and locked_profit_pct > trailing_tier:
            trailing_tier = locked_profit_pct
            if side == 'long':
                stop_loss = max(stop_loss, desired_stop_loss)
            else:
                stop_loss = min(stop_loss, desired_stop_loss)

        exit_price = current_price

    final_metrics = build_position_metrics(
        entry_price,
        exit_price,
        side,
        leverage,
        stored_peak_price=peak_price,
        stored_peak_profit_pct=peak_profit_pct,
    )
    realized_profit_pct = final_metrics['current_profit_pct']
    realized_peak_profit_pct = final_metrics['peak_profit_pct']

    return {
        'coin': event['coin'],
        'side': side,
        'entry_index': event['index'],
        'exit_index': exit_index,
        'bars_held': exit_index - event['index'],
        'entry_price': entry_price,
        'exit_price': exit_price,
        'peak_profit_pct': realized_peak_profit_pct,
        'realized_profit_pct': realized_profit_pct,
        'profit_giveback_pct': max(0.0, realized_peak_profit_pct - realized_profit_pct),
        'exit_reason': exit_reason,
    }


def summarize(results: List[Tuple[Dict, Dict]]) -> Dict:
    rows = []
    by_coin = defaultdict(list)
    reasons = Counter()
    improved = 0
    worsened = 0

    for baseline, enhanced in results:
        giveback_saved = baseline['profit_giveback_pct'] - enhanced['profit_giveback_pct']
        pnl_delta = enhanced['realized_profit_pct'] - baseline['realized_profit_pct']
        exit_lead_bars = baseline['bars_held'] - enhanced['bars_held']
        row = {
            'coin': baseline['coin'],
            'side': baseline['side'],
            'baseline_giveback_pct': baseline['profit_giveback_pct'],
            'enhanced_giveback_pct': enhanced['profit_giveback_pct'],
            'giveback_saved_pct': giveback_saved,
            'baseline_profit_pct': baseline['realized_profit_pct'],
            'enhanced_profit_pct': enhanced['realized_profit_pct'],
            'pnl_delta_pct': pnl_delta,
            'exit_lead_bars': exit_lead_bars,
            'baseline_reason': baseline['exit_reason'],
            'enhanced_reason': enhanced['exit_reason'],
        }
        rows.append(row)
        by_coin[baseline['coin']].append(row)
        reasons[enhanced['exit_reason']] += 1
        if giveback_saved > 0.001:
            improved += 1
        elif giveback_saved < -0.001:
            worsened += 1

    summary = {
        'trades': len(rows),
        'improved_trades': improved,
        'worsened_trades': worsened,
        'avg_baseline_giveback_pct': mean([row['baseline_giveback_pct'] for row in rows]) if rows else 0.0,
        'avg_enhanced_giveback_pct': mean([row['enhanced_giveback_pct'] for row in rows]) if rows else 0.0,
        'avg_giveback_saved_pct': mean([row['giveback_saved_pct'] for row in rows]) if rows else 0.0,
        'avg_baseline_profit_pct': mean([row['baseline_profit_pct'] for row in rows]) if rows else 0.0,
        'avg_enhanced_profit_pct': mean([row['enhanced_profit_pct'] for row in rows]) if rows else 0.0,
        'avg_pnl_delta_pct': mean([row['pnl_delta_pct'] for row in rows]) if rows else 0.0,
        'avg_exit_lead_bars': mean([row['exit_lead_bars'] for row in rows]) if rows else 0.0,
        'enhanced_exit_reasons': dict(reasons),
        'per_coin': {},
    }

    for coin, coin_rows in by_coin.items():
        summary['per_coin'][coin] = {
            'trades': len(coin_rows),
            'avg_giveback_saved_pct': mean([row['giveback_saved_pct'] for row in coin_rows]),
            'avg_pnl_delta_pct': mean([row['pnl_delta_pct'] for row in coin_rows]),
            'avg_exit_lead_bars': mean([row['exit_lead_bars'] for row in coin_rows]),
        }

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description='Validate early reversal exit signal on recent OKX candles.')
    parser.add_argument('--coins', nargs='+', default=['BTC', 'ETH', 'SOL', 'BNB', 'ZEC'])
    parser.add_argument('--bars', type=int, default=960, help='Number of 5m candles to fetch per coin.')
    parser.add_argument('--max-events', type=int, default=12, help='Maximum candidate entries per coin.')
    args = parser.parse_args()

    fetcher = MarketDataFetcher()
    inst_map = fetcher.okx_symbols

    paired_results: List[Tuple[Dict, Dict]] = []
    for coin in args.coins:
        inst_id = inst_map.get(coin)
        if not inst_id:
            print(f'[WARN] Skip {coin}: missing OKX instrument mapping')
            continue

        candles_5m = fetch_okx_candles(inst_id, args.bars)
        if len(candles_5m) < 480:
            print(f'[WARN] Skip {coin}: insufficient candles ({len(candles_5m)})')
            continue

        candles_15m = aggregate_candles(candles_5m, 15)
        candles_1h = aggregate_candles(candles_5m, 60)
        events = candidate_entries(fetcher, coin, candles_5m, candles_15m, candles_1h, args.max_events)
        print(f'[INFO] {coin}: fetched {len(candles_5m)} x 5m candles, generated {len(events)} candidate trades')

        for event in events:
            baseline = simulate_exit(fetcher, event, candles_5m, candles_15m, candles_1h, mode='baseline')
            enhanced = simulate_exit(fetcher, event, candles_5m, candles_15m, candles_1h, mode='enhanced')
            paired_results.append((baseline, enhanced))

    summary = summarize(paired_results)
    print('\n=== Early Reversal Validation Summary ===')
    print(f"Trades tested: {summary['trades']}")
    print(f"Improved trades: {summary['improved_trades']}")
    print(f"Worsened trades: {summary['worsened_trades']}")
    print(f"Avg baseline giveback: {summary['avg_baseline_giveback_pct'] * 100:.2f}%")
    print(f"Avg enhanced giveback: {summary['avg_enhanced_giveback_pct'] * 100:.2f}%")
    print(f"Avg giveback saved: {summary['avg_giveback_saved_pct'] * 100:.2f}%")
    print(f"Avg baseline realized profit: {summary['avg_baseline_profit_pct'] * 100:.2f}%")
    print(f"Avg enhanced realized profit: {summary['avg_enhanced_profit_pct'] * 100:.2f}%")
    print(f"Avg realized profit delta: {summary['avg_pnl_delta_pct'] * 100:.2f}%")
    print(f"Avg earlier exit lead: {summary['avg_exit_lead_bars']:.2f} bars")
    print(f"Enhanced exit reasons: {summary['enhanced_exit_reasons']}")

    if summary['per_coin']:
        print('\nPer coin:')
        for coin, item in summary['per_coin'].items():
            print(
                f"  {coin}: trades={item['trades']}, "
                f"giveback_saved={item['avg_giveback_saved_pct'] * 100:.2f}%, "
                f"pnl_delta={item['avg_pnl_delta_pct'] * 100:.2f}%, "
                f"exit_lead={item['avg_exit_lead_bars']:.2f} bars"
            )


if __name__ == '__main__':
    main()
