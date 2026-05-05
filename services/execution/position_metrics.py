"""可复用的持仓收益与移动止损计算函数。"""
from typing import Dict, Optional


ROUND_TRIP_FEE_PCT = 0.001
PEAK_PROFIT_ACTIVATION_PCT = 0.03
PEAK_DRAWDOWN_CLOSE_RATIO = 0.20


def calculate_net_profit_pct(entry_price: float, current_price: float, side: str, leverage: int) -> float:
    if entry_price <= 0:
        return 0.0
    if side == 'long':
        gross_pct = (current_price - entry_price) / entry_price
    else:
        gross_pct = (entry_price - current_price) / entry_price
    leveraged_pct = gross_pct * float(leverage or 1)
    return leveraged_pct - ROUND_TRIP_FEE_PCT


def calculate_peak_price(entry_price: float, current_price: float, side: str, stored_peak_price: Optional[float] = None) -> float:
    peak_price = stored_peak_price if stored_peak_price is not None else entry_price
    if side == 'long':
        return max(peak_price, current_price)
    return min(peak_price, current_price)


def price_from_locked_profit_pct(entry_price: float, side: str, leverage: int, locked_profit_pct: float) -> float:
    effective_leverage = float(leverage or 1)
    underlying_move = locked_profit_pct / effective_leverage
    if side == 'long':
        return entry_price * (1 + underlying_move)
    return entry_price * (1 - underlying_move)


def calculate_peak_drawdown_stop(entry_price: float, peak_profit_pct: float, side: str, leverage: int):
    if peak_profit_pct < 0.03:
        return 0.0, None
    if peak_profit_pct < 0.05:
        locked_profit_pct = max(0.005, peak_profit_pct - 0.015)
    elif peak_profit_pct < 0.08:
        locked_profit_pct = max(0.0, peak_profit_pct - 0.02)
    elif peak_profit_pct < 0.15:
        locked_profit_pct = max(0.0, peak_profit_pct - 0.03)
    else:
        locked_profit_pct = max(0.0, peak_profit_pct - 0.05)
    return locked_profit_pct, price_from_locked_profit_pct(entry_price, side, leverage, locked_profit_pct)


def build_position_metrics(entry_price: float, current_price: float, side: str, leverage: int,
                           stored_peak_price: Optional[float] = None,
                           stored_peak_profit_pct: Optional[float] = None) -> Dict:
    current_profit_pct = calculate_net_profit_pct(entry_price, current_price, side, leverage)
    peak_price = calculate_peak_price(entry_price, current_price, side, stored_peak_price)
    peak_profit_from_price = calculate_net_profit_pct(entry_price, peak_price, side, leverage)
    peak_profit_pct = max(float(stored_peak_profit_pct or 0), peak_profit_from_price, current_profit_pct)
    drawdown_ratio = 0.0
    if peak_profit_pct > 0:
        drawdown_ratio = max(0.0, (peak_profit_pct - current_profit_pct) / peak_profit_pct)
    return {
        'current_profit_pct': current_profit_pct,
        'peak_price': peak_price,
        'peak_profit_pct': peak_profit_pct,
        'drawdown_ratio': drawdown_ratio,
    }
