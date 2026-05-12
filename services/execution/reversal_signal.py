"""Early reversal warning signal for proactive exit management."""
import os
from typing import Dict, List, Tuple

from services.execution.position_metrics import price_from_locked_profit_pct


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


EARLY_REVERSAL_SENSITIVITY = _clamp(_env_float('EARLY_REVERSAL_SENSITIVITY', 1.10), 0.7, 1.35)
EARLY_REVERSAL_WARNING_THRESHOLD = _clamp(_env_float('EARLY_REVERSAL_WARNING_THRESHOLD', 0.68), 0.55, 0.88)
EARLY_REVERSAL_EXIT_THRESHOLD = _clamp(_env_float('EARLY_REVERSAL_EXIT_THRESHOLD', 0.84), 0.62, 0.95)
EARLY_REVERSAL_MIN_PEAK_PROFIT_PCT = _clamp(_env_float('EARLY_REVERSAL_MIN_PEAK_PROFIT_PCT', 0.012), 0.004, 0.03)
EARLY_REVERSAL_MIN_PULLBACK_ATR = _clamp(_env_float('EARLY_REVERSAL_MIN_PULLBACK_ATR', 0.35), 0.15, 1.20)
EARLY_REVERSAL_PROTECT_STOP_ATR_MULTIPLIER = _clamp(
    _env_float('EARLY_REVERSAL_PROTECT_STOP_ATR_MULTIPLIER', 0.75),
    0.30,
    1.60,
)
EARLY_REVERSAL_LOCKED_PROFIT_SHARE = _clamp(_env_float('EARLY_REVERSAL_LOCKED_PROFIT_SHARE', 0.55), 0.20, 0.85)


def _tail_is_weakening(side: str, tail: List[float]) -> bool:
    if not isinstance(tail, list) or len(tail) < 3:
        return False
    a, b, c = tail[-3], tail[-2], tail[-1]
    if side == 'long':
        return c < b < a
    return c > b > a


# _tail_is_slipping 与 _tail_is_weakening 逻辑相同，使用别名避免代码重复
_tail_is_slipping = _tail_is_weakening


def _volatility_ratio(indicators: Dict) -> float:
    if not indicators:
        return 0.0
    price = float(indicators.get('current_price', 0) or 0)
    atr = float(indicators.get('atr_14', 0) or 0)
    if price <= 0 or atr <= 0:
        return 0.0
    return atr / price


def _threshold_adjustments(sensitivity: float, tf_15m: Dict, tf_5m: Dict) -> Tuple[float, float, float]:
    volatility_ratio = max(_volatility_ratio(tf_15m), _volatility_ratio(tf_5m))
    sensitivity_shift = (1.0 - sensitivity) * 0.10
    volatility_shift = _clamp((volatility_ratio - 0.006) * 10.0, -0.04, 0.08)
    warning_threshold = _clamp(
        EARLY_REVERSAL_WARNING_THRESHOLD + sensitivity_shift + volatility_shift,
        0.55,
        0.90,
    )
    exit_threshold = _clamp(
        max(EARLY_REVERSAL_EXIT_THRESHOLD + sensitivity_shift + volatility_shift, warning_threshold + 0.08),
        0.64,
        0.95,
    )
    min_peak_profit_pct = _clamp(
        EARLY_REVERSAL_MIN_PEAK_PROFIT_PCT + max(volatility_shift, 0) * 0.05 + sensitivity_shift * 0.04,
        0.005,
        0.03,
    )
    return warning_threshold, exit_threshold, min_peak_profit_pct


def _score_reversal_timeframe(
    side: str,
    indicators: Dict,
    current_price: float,
    peak_price: float,
    sensitivity: float,
    min_pullback_atr: float,
) -> Tuple[float, List[str], Dict]:
    if not indicators:
        return 0.0, [], {}

    local_price = float(indicators.get('current_price', current_price) or current_price)
    current_price = local_price if local_price > 0 else float(current_price or 0)

    sma5 = float(indicators.get('sma_5', 0) or 0)
    sma7 = float(indicators.get('sma_7', 0) or 0)
    rsi = float(indicators.get('rsi_14', 50) or 50)
    macd_hist = float(indicators.get('macd_histogram', 0) or 0)
    macd_tail = indicators.get('macd_histogram_tail', [])
    close_tail = indicators.get('close_prices_tail', [])
    volume_tail = indicators.get('volume_tail', [])
    volume_ratio = float(indicators.get('volume_ratio', 0) or 0)
    bb_position = float(indicators.get('bb_position', 0.5) or 0.5)
    atr = float(indicators.get('atr_14', 0) or 0)
    recent_high = float(indicators.get('recent_high_20', current_price) or current_price)
    recent_low = float(indicators.get('recent_low_20', current_price) or current_price)

    atr = atr if atr > 0 else max(current_price * 0.0015, 1e-8)
    peak_reference = float(peak_price or current_price)
    extension_tolerance = max(0.004, 0.010 - (sensitivity - 1.0) * 0.003)
    score = 0.0
    reasons: List[str] = []

    if side == 'long':
        if recent_high > 0 and peak_reference >= recent_high * (1 - extension_tolerance):
            score += 0.18
            reasons.append('peak touched recent resistance')
        if bb_position >= 0.84 - (sensitivity - 1.0) * 0.05:
            score += 0.10
            reasons.append('price extended near upper band')

        pullback_atr = max(0.0, (peak_reference - current_price) / atr)
        if pullback_atr >= min_pullback_atr:
            score += min(0.24, 0.11 + (pullback_atr - min_pullback_atr) * 0.10)
            reasons.append(f'pullback expanded to {pullback_atr:.2f} ATR')

        if sma5 > 0 and current_price < sma5:
            score += 0.08
            reasons.append('price lost 5-period support')
        if sma7 > 0 and current_price < sma7:
            score += 0.06
            reasons.append('price slipped below 7-period trend')
        if macd_hist < 0:
            score += 0.10
            reasons.append('MACD histogram turned negative')
        elif _tail_is_weakening(side, macd_tail):
            score += 0.08
            reasons.append('MACD histogram is fading')
        if rsi < 58:
            score += 0.07
            reasons.append('RSI cooled after extension')
        if volume_ratio > 0 and volume_ratio < 0.95:
            score += 0.05
            reasons.append('volume faded below average')
        if _tail_is_slipping(side, volume_tail):
            score += 0.05
            reasons.append('recent volume is declining')
        if _tail_is_slipping(side, close_tail):
            score += 0.07
            reasons.append('recent closes are rolling over')
    else:
        if recent_low > 0 and peak_reference <= recent_low * (1 + extension_tolerance):
            score += 0.18
            reasons.append('peak touched recent support')
        if bb_position <= 0.16 + (sensitivity - 1.0) * 0.05:
            score += 0.10
            reasons.append('price extended near lower band')

        pullback_atr = max(0.0, (current_price - peak_reference) / atr)
        if pullback_atr >= min_pullback_atr:
            score += min(0.24, 0.11 + (pullback_atr - min_pullback_atr) * 0.10)
            reasons.append(f'bounce expanded to {pullback_atr:.2f} ATR')

        if sma5 > 0 and current_price > sma5:
            score += 0.08
            reasons.append('price reclaimed 5-period pressure line')
        if sma7 > 0 and current_price > sma7:
            score += 0.06
            reasons.append('price climbed above 7-period trend')
        if macd_hist > 0:
            score += 0.10
            reasons.append('MACD histogram turned positive')
        elif _tail_is_weakening(side, macd_tail):
            score += 0.08
            reasons.append('MACD histogram is fading bearish momentum')
        if rsi > 42:
            score += 0.07
            reasons.append('RSI rebounded after extension')
        if volume_ratio > 0 and volume_ratio < 0.95:
            score += 0.05
            reasons.append('volume faded below average')
        if _tail_is_slipping(side, volume_tail):
            score += 0.05
            reasons.append('recent volume is declining')
        if _tail_is_slipping(side, close_tail):
            score += 0.07
            reasons.append('recent closes are rolling over')

    return _clamp(score, 0.0, 1.0), reasons, {
        'atr': atr,
        'bb_position': bb_position,
        'rsi_14': rsi,
        'volume_ratio': volume_ratio,
    }


def _trend_context_adjustment(side: str, indicators: Dict) -> Tuple[float, str]:
    if not indicators:
        return 0.0, ''

    current_price = float(indicators.get('current_price', 0) or 0)
    sma5 = float(indicators.get('sma_5', 0) or 0)
    rsi = float(indicators.get('rsi_14', 50) or 50)
    macd_hist = float(indicators.get('macd_histogram', 0) or 0)
    macd_tail = indicators.get('macd_histogram_tail', [])

    if side == 'long':
        strong_trend = current_price > sma5 > 0 and rsi >= 60 and macd_hist > 0 and not _tail_is_weakening(side, macd_tail)
        weak_trend = (sma5 > 0 and current_price < sma5) and (rsi < 55 or macd_hist < 0 or _tail_is_weakening(side, macd_tail))
    else:
        strong_trend = sma5 > 0 and current_price < sma5 and rsi <= 40 and macd_hist < 0 and not _tail_is_weakening(side, macd_tail)
        weak_trend = (sma5 > 0 and current_price > sma5) and (rsi > 45 or macd_hist > 0 or _tail_is_weakening(side, macd_tail))

    if strong_trend:
        return -0.06, '1h trend still supports holding'
    if weak_trend:
        return 0.06, '1h trend is also weakening'
    return 0.0, ''


def build_reversal_exit_signal(
    side: str,
    entry_price: float,
    current_price: float,
    peak_price: float,
    current_profit_pct: float,
    peak_profit_pct: float,
    leverage: int,
    tf_15m: Dict,
    tf_5m: Dict,
    tf_1h: Dict = None,
    sensitivity: float = None,
) -> Dict:
    sensitivity = _clamp(float(sensitivity or EARLY_REVERSAL_SENSITIVITY), 0.7, 1.35)
    warning_threshold, exit_threshold, min_peak_profit_pct = _threshold_adjustments(sensitivity, tf_15m, tf_5m)
    min_pullback_atr = _clamp(
        EARLY_REVERSAL_MIN_PULLBACK_ATR + max(_volatility_ratio(tf_15m), _volatility_ratio(tf_5m), 0.0) * 18.0 - (sensitivity - 1.0) * 0.08,
        0.15,
        1.25,
    )

    result = {
        'eligible': peak_profit_pct >= min_peak_profit_pct,
        'warning_threshold': warning_threshold,
        'exit_threshold': exit_threshold,
        'score': 0.0,
        'score_15m': 0.0,
        'score_5m': 0.0,
        'signal': 'none',
        'should_exit': False,
        'should_tighten_stop': False,
        'suggested_stop_loss': None,
        'reasons': [],
        'details': {
            'sensitivity': sensitivity,
            'min_peak_profit_pct': min_peak_profit_pct,
            'min_pullback_atr': min_pullback_atr,
        }
    }
    if not result['eligible']:
        return result

    score_15m, reasons_15m, details_15m = _score_reversal_timeframe(
        side,
        tf_15m or {},
        current_price,
        peak_price,
        sensitivity,
        min_pullback_atr,
    )
    score_5m, reasons_5m, details_5m = _score_reversal_timeframe(
        side,
        tf_5m or {},
        current_price,
        peak_price,
        sensitivity,
        min_pullback_atr * 0.90,
    )
    context_adjustment, context_reason = _trend_context_adjustment(side, tf_1h or {})

    total_score = _clamp(score_15m * 0.58 + score_5m * 0.42 + context_adjustment, 0.0, 1.0)
    result['score'] = total_score
    result['score_15m'] = score_15m
    result['score_5m'] = score_5m
    result['details'].update({
        'timeframe_15m': details_15m,
        'timeframe_5m': details_5m,
        'context_adjustment': context_adjustment,
    })

    reasons: List[str] = []
    for item in reasons_15m[:3]:
        reasons.append(f'15m: {item}')
    for item in reasons_5m[:3]:
        reasons.append(f'5m: {item}')
    if context_reason:
        reasons.append(f'1h: {context_reason}')
    result['reasons'] = reasons

    score_confirms_reversal = score_15m >= warning_threshold * 0.95 and score_5m >= warning_threshold * 0.88

    if total_score >= exit_threshold and score_confirms_reversal and current_profit_pct > 0:
        result['signal'] = 'exit'
        result['should_exit'] = True
        result['should_tighten_stop'] = True
    elif total_score >= warning_threshold:
        result['signal'] = 'tighten_stop'
        result['should_tighten_stop'] = True

    atr_values = [
        float((tf_5m or {}).get('atr_14', 0) or 0),
        float((tf_15m or {}).get('atr_14', 0) or 0),
    ]
    atr = max([value for value in atr_values if value > 0] or [max(float(current_price or 0) * 0.0015, 1e-8)])
    locked_profit_pct = max(
        0.0,
        min(
            max(float(current_profit_pct or 0) * EARLY_REVERSAL_LOCKED_PROFIT_SHARE, 0.003),
            max(float(peak_profit_pct or 0) - 0.002, 0.0),
        ),
    )
    profit_lock_stop = price_from_locked_profit_pct(entry_price, side, leverage, locked_profit_pct)
    atr_stop = current_price - atr * EARLY_REVERSAL_PROTECT_STOP_ATR_MULTIPLIER if side == 'long' else current_price + atr * EARLY_REVERSAL_PROTECT_STOP_ATR_MULTIPLIER
    fee_break_even = entry_price * (1 + 0.001 / max(float(leverage or 1), 1.0)) if side == 'long' else entry_price * (1 - 0.001 / max(float(leverage or 1), 1.0))

    if side == 'long':
        result['suggested_stop_loss'] = max(fee_break_even, profit_lock_stop, atr_stop)
    else:
        result['suggested_stop_loss'] = min(fee_break_even, profit_lock_stop, atr_stop)

    return result
