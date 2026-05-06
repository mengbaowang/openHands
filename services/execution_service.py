"""基于 OKX 的执行层，负责下单、平仓与持仓状态管理。"""
import time
from typing import Dict

from services.execution.position_metrics import (
    PEAK_DRAWDOWN_CLOSE_RATIO,
    PEAK_PROFIT_ACTIVATION_PCT,
    build_position_metrics,
    calculate_peak_drawdown_stop,
)
from services.exchanges.okx_adapter import OKXTrader


class ExecutionService:
    """OKX-backed execution layer responsible for order routing, exits, and position state."""

    MAX_QUANTITY_BY_COIN = {
        'BTC': 10,
        'ETH': 500,
        'SOL': 50000,
        'BNB': 5000,
        'XRP': 5000000,
        'DOGE': 50000000,
    }
    DEFAULT_STOP_LOSS_PCT = 0.03
    DEFAULT_TAKE_PROFIT_PCT = 0.06
    EARLY_PROFIT_PROTECTION_PCT = 0.015

    def __init__(self, model_id: int, db, debug_log=None):
        self.model_id = model_id
        self.db = db
        self._debug_callback = debug_log
        self.okx_trader = OKXTrader()
        self.peak_profit_activation_pct = PEAK_PROFIT_ACTIVATION_PCT
        self.peak_drawdown_close_ratio = PEAK_DRAWDOWN_CLOSE_RATIO

    def _debug_log(self, message: str) -> None:
        if self._debug_callback:
            self._debug_callback(message)

    def get_portfolio(self, current_prices: Dict) -> Dict:
        self._reconcile_closed_positions(current_prices)
        return self._get_okx_portfolio(current_prices)

    def check_stop_loss_take_profit(self, portfolio: Dict, current_prices: Dict) -> list:
        return self._check_stop_loss_take_profit_okx(portfolio, current_prices)

    def execute_decisions(self, decisions: Dict, market_state: Dict, portfolio: Dict) -> list:
        return self._execute_decisions_okx(decisions, market_state, portfolio)

    def record_account_value(self, portfolio: Dict) -> None:
        balance = self.okx_trader.get_balance()
        self.db.record_account_value(
            self.model_id,
            portfolio['total_value'],
            balance.get('available', portfolio['cash']),
            portfolio['positions_value']
        )

    def _reconcile_closed_positions(self, current_prices: Dict) -> None:
        """Backfill local close trades when OKX positions are gone but DB rows still exist."""
        open_rows = self.db.get_open_portfolio_rows(self.model_id)
        if not open_rows:
            return

        okx_positions = self.okx_trader.get_positions() or []
        active_keys = {(pos['coin'], pos['side']) for pos in okx_positions if pos}

        for row in open_rows:
            key = (row['coin'], row['side'])
            if key in active_keys:
                continue

            closed_snapshot = self.okx_trader.get_recent_closed_position(row['coin'], row['side'])
            close_price = float(closed_snapshot.get('closeAvgPx', 0) or 0)
            current_price = close_price or current_prices.get(row['coin'], row['avg_price'])
            quantity = float(row['quantity'])
            leverage = int(row['leverage'])
            if closed_snapshot and closed_snapshot.get('realizedPnl') not in (None, ''):
                pnl = float(closed_snapshot.get('realizedPnl', 0) or 0)
            else:
                if row['side'] == 'long':
                    pnl = (current_price - row['avg_price']) * quantity
                else:
                    pnl = (row['avg_price'] - current_price) * quantity

            trade_signal = 'sell_to_close' if row['side'] == 'long' else 'buy_to_close'
            self._debug_log(
                f"对账发现 {row['coin']} {row['side']} 已在 OKX 平仓，自动补记交易记录，价格 {current_price:.4f}"
            )
            timestamp = None
            if closed_snapshot and closed_snapshot.get('uTime'):
                try:
                    timestamp = time.strftime(
                        '%Y-%m-%d %H:%M:%S',
                        time.gmtime(int(closed_snapshot['uTime']) / 1000)
                    )
                except Exception:
                    timestamp = None
            self.db.add_trade(
                self.model_id,
                row['coin'],
                trade_signal,
                quantity,
                current_price,
                leverage,
                row['side'],
                pnl=pnl,
                timestamp=timestamp
            )
            self.db.close_position(self.model_id, row['coin'], row['side'])

    def _update_db_position_metrics(self, coin: str, position: Dict, metrics: Dict, db_pos: Dict = None) -> None:
        db_pos = db_pos or {}
        self.db.update_position(
            self.model_id,
            coin,
            position.get('quantity', position.get('coin_quantity', 0.0)),
            position['avg_price'],
            int(position['leverage']),
            position['side'],
            position.get('stop_loss'),
            position.get('take_profit'),
            entry_ord_id=db_pos.get('entry_ord_id'),
            okx_risk_algo_id=db_pos.get('okx_risk_algo_id'),
            okx_risk_algo_cl_ord_id=db_pos.get('okx_risk_algo_cl_ord_id'),
            trailing_tier=db_pos.get('trailing_tier', 0),
            peak_price=metrics['peak_price'],
            peak_profit_pct=metrics['peak_profit_pct'],
            last_profit_pct=metrics['current_profit_pct']
        )

    def _resolve_risk_targets(self, side: str, reference_price: float, stop_loss: float = None, take_profit: float = None):
        if stop_loss is None:
            stop_loss = reference_price * (1 - self.DEFAULT_STOP_LOSS_PCT) if side == 'long' else reference_price * (1 + self.DEFAULT_STOP_LOSS_PCT)
        if take_profit is None:
            take_profit = reference_price * (1 + self.DEFAULT_TAKE_PROFIT_PCT) if side == 'long' else reference_price * (1 - self.DEFAULT_TAKE_PROFIT_PCT)
        return stop_loss, take_profit

    def _estimate_rr(self, side: str, current_price: float, stop_loss: float = None, take_profit: float = None) -> float:
        if stop_loss is None or take_profit is None:
            return 0.0
        if side == 'long':
            reward = take_profit - current_price
            risk = current_price - stop_loss
        else:
            reward = current_price - take_profit
            risk = stop_loss - current_price
        if risk <= 0:
            return 0.0
        return reward / risk

    def _macd_tail_is_weakening(self, side: str, tail) -> bool:
        if not isinstance(tail, list) or len(tail) < 3:
            return False
        a, b, c = tail[-3], tail[-2], tail[-1]
        if side == 'long':
            return c < b < a
        return c > b > a

    def _timeframe_is_weakening(self, side: str, indicators: Dict) -> bool:
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
            if macd_hist < 0 or self._macd_tail_is_weakening(side, macd_tail):
                signals += 1
            if sma5 > 0 and current_price < sma5:
                signals += 1
        else:
            if rsi > 45:
                signals += 1
            if macd_hist > 0 or self._macd_tail_is_weakening(side, macd_tail):
                signals += 1
            if sma5 > 0 and current_price > sma5:
                signals += 1

        return signals >= 2

    def _get_break_even_stop(self, entry_price: float, side: str, leverage: int) -> float:
        fee_adjustment = 0.001 / max(float(leverage or 1), 1.0)
        if side == 'long':
            return entry_price * (1 + fee_adjustment)
        return entry_price * (1 - fee_adjustment)

    def _get_entry_margin_ratio(self, decision: Dict, market_state: Dict, coin: str, side: str) -> tuple[float, list[str]]:
        confidence = float(decision.get('confidence') or 0)
        if confidence >= 0.75:
            ratio = 0.10
        elif confidence >= 0.60:
            ratio = 0.08
        elif confidence >= 0.50:
            ratio = 0.06
        else:
            ratio = 0.04

        reasons = []
        timeframes = market_state[coin].get('timeframes', {})
        tf_1h = timeframes.get('1h', {})
        tf_15m = timeframes.get('15m', {})
        current_price = market_state[coin]['price']

        if side == 'long':
            near_extreme = (
                (tf_1h.get('recent_high_20') and current_price >= float(tf_1h.get('recent_high_20')) * 0.995) or
                (tf_15m.get('recent_high_20') and current_price >= float(tf_15m.get('recent_high_20')) * 0.995)
            )
            overheated = float(tf_1h.get('rsi_14', 0) or 0) >= 70 or float(tf_15m.get('rsi_14', 0) or 0) >= 70
        else:
            near_extreme = (
                (tf_1h.get('recent_low_20') and current_price <= float(tf_1h.get('recent_low_20')) * 1.005) or
                (tf_15m.get('recent_low_20') and current_price <= float(tf_15m.get('recent_low_20')) * 1.005)
            )
            overheated = float(tf_1h.get('rsi_14', 100) or 100) <= 30 or float(tf_15m.get('rsi_14', 100) or 100) <= 30

        rr = self._estimate_rr(
            side,
            current_price,
            decision.get('stop_loss'),
            decision.get('profit_target') or decision.get('take_profit')
        )

        if near_extreme:
            ratio *= 0.65
            reasons.append('接近短线极值位')
        if overheated:
            ratio *= 0.75
            reasons.append('趋势偏热/偏冷')
        if 0 < rr < 2.2:
            ratio *= 0.75
            reasons.append('风险回报比仅勉强达标')

        return max(ratio, 0.02), reasons

    def _sync_okx_native_risk_order(self, coin: str, side: str, contracts: float,
                                    stop_loss: float = None, take_profit: float = None,
                                    db_pos: Dict = None) -> Dict:
        algo_id = db_pos.get('okx_risk_algo_id') if db_pos else None
        algo_cl_ord_id = db_pos.get('okx_risk_algo_cl_ord_id') if db_pos else None

        if stop_loss is None and take_profit is None:
            if algo_id or algo_cl_ord_id:
                cancel_result = self.okx_trader.cancel_native_risk_order(coin, algo_id, algo_cl_ord_id)
                if not cancel_result.get('success'):
                    self._debug_log(f"{coin} 撤销原生 TP/SL 失败: {cancel_result}")
                return {'success': cancel_result.get('success', True), 'algo_id': None, 'algo_cl_ord_id': None}
            return {'success': True, 'algo_id': None, 'algo_cl_ord_id': None}

        if algo_id or algo_cl_ord_id:
            amend_result = self.okx_trader.amend_native_risk_order(
                coin,
                algo_id=algo_id,
                algo_cl_ord_id=algo_cl_ord_id,
                contracts=contracts,
                stop_loss=stop_loss,
                take_profit=take_profit
            )
            if amend_result.get('success'):
                return {'success': True, 'algo_id': algo_id, 'algo_cl_ord_id': algo_cl_ord_id, 'action': 'amended'}
            self._debug_log(f"{coin} 修改原生 TP/SL 失败，尝试重建: {amend_result}")
            self.okx_trader.cancel_native_risk_order(coin, algo_id, algo_cl_ord_id)

        place_result = self.okx_trader.place_native_risk_order(
            coin,
            side=side,
            contracts=contracts,
            stop_loss=stop_loss,
            take_profit=take_profit
        )
        return {
            'success': place_result.get('success', False),
            'algo_id': place_result.get('algo_id'),
            'algo_cl_ord_id': place_result.get('algo_cl_ord_id'),
            'action': 'placed',
            'error': place_result.get('error')
        }

    def _validate_quantity(self, quantity: float, coin: str) -> None:
        if not isinstance(quantity, (int, float)):
            raise ValueError(f"Invalid quantity type: {type(quantity)}")
        if quantity <= 0:
            raise ValueError(f"Quantity must be positive, got {quantity}")
        max_quantity = self.MAX_QUANTITY_BY_COIN.get(coin, 1000000)
        if quantity > max_quantity:
            raise ValueError(f"Quantity too large for {coin}: {quantity} > {max_quantity}")

    def _validate_leverage(self, leverage: int) -> None:
        import config
        if not isinstance(leverage, int):
            raise ValueError(f"Leverage must be integer, got {type(leverage)}")
        if leverage < config.MIN_LEVERAGE or leverage > config.MAX_LEVERAGE:
            raise ValueError(f"Leverage must be between {config.MIN_LEVERAGE} and {config.MAX_LEVERAGE}, got {leverage}")

    def _get_okx_portfolio(self, current_prices: Dict) -> Dict:
        balance = self.okx_trader.get_balance()
        if 'error' in balance:
            self._debug_log(f"获取余额失败，降级使用默认账户: {balance['error']}")
            balance = {'total': 10000, 'available': 10000, 'frozen': 0, 'details': []}

        details = balance.get('details', [])
        total_value = 0
        usdt_available = 0
        frozen_margin = 0
        for item in details:
            ccy = item.get('ccy')
            eq_usd = float(item.get('eqUsd', 0))
            if eq_usd > 0:
                total_value += eq_usd
            if ccy == 'USDT':
                usdt_available = float(item.get('availEq', 0))
                frozen_margin = float(item.get('frozenBal', 0))

        positions = self.okx_trader.get_positions()
        if isinstance(positions, dict) and 'error' in positions:
            positions = []

        positions_value = 0
        pos_details = []
        unrealized_pnl = 0
        trades = self.db.get_trades(self.model_id, limit=100000)
        realized_pnl = sum(trade.get('pnl', 0) for trade in trades)

        for pos in positions or []:
            coin = pos['coin']
            if coin not in current_prices:
                continue

            current_price = current_prices[coin]
            position_quantity = pos.get('coin_quantity', 0.0)
            position_value = pos.get('notional_usdt', position_quantity * current_price)
            positions_value += position_value
            if pos['side'] == 'long':
                pos_pnl = (current_price - pos['avg_price']) * position_quantity
            else:
                pos_pnl = (pos['avg_price'] - current_price) * position_quantity
            unrealized_pnl += pos_pnl

            db_pos = self.db.get_position(self.model_id, coin, pos['side'])
            metrics = build_position_metrics(
                pos['avg_price'],
                current_price,
                pos['side'],
                int(pos['leverage']),
                stored_peak_price=db_pos.get('peak_price') if db_pos else None,
                stored_peak_profit_pct=db_pos.get('peak_profit_pct') if db_pos else None
            )
            pos_details.append({
                'coin': coin,
                'side': pos['side'],
                'quantity': position_quantity,
                'contracts': pos.get('contracts', pos.get('size')),
                'avg_price': pos['avg_price'],
                'current_price': current_price,
                'leverage': pos['leverage'],
                'pnl': pos_pnl,
                'stop_loss': db_pos.get('stop_loss') if db_pos else None,
                'take_profit': db_pos.get('take_profit') if db_pos else None,
                'peak_price': metrics['peak_price'],
                'peak_profit_pct': metrics['peak_profit_pct'],
                'last_profit_pct': metrics['current_profit_pct'],
                'value': position_value
            })

        return {
            'cash': usdt_available,
            'positions': pos_details,
            'total_value': total_value,
            'positions_value': positions_value,
            'realized_pnl': realized_pnl,
            'unrealized_pnl': unrealized_pnl,
            'frozen_margin': frozen_margin,
            'wallet_balances': balance.get('balances', {})
        }

    def _check_stop_loss_take_profit_okx(self, portfolio: Dict, current_prices: Dict) -> list:
        results = []
        for position in portfolio['positions']:
            coin = position['coin']
            if coin not in current_prices:
                continue

            current_price = current_prices[coin]
            entry_price = position['avg_price']
            side = position['side']
            stop_loss = position.get('stop_loss')
            take_profit = position.get('take_profit')

            db_pos = self.db.get_position(self.model_id, coin, side) or {}
            metrics = build_position_metrics(
                entry_price, current_price, side, int(position['leverage']),
                stored_peak_price=db_pos.get('peak_price'),
                stored_peak_profit_pct=db_pos.get('peak_profit_pct')
            )
            current_profit_pct = metrics['current_profit_pct']
            peak_profit_pct = metrics['peak_profit_pct']
            drawdown_ratio = metrics['drawdown_ratio']
            peak_price = metrics['peak_price']
            timeframes = current_prices.get('__timeframes__', {})
            coin_timeframes = timeframes.get(coin, {})

            self._debug_log(f"{coin} 净浮盈 {current_profit_pct*100:.2f}%, 峰值净浮盈 {peak_profit_pct*100:.2f}%, 峰值价 {peak_price:.2f}")

            weak_15m = self._timeframe_is_weakening(side, coin_timeframes.get('15m', {}))
            weak_5m = self._timeframe_is_weakening(side, coin_timeframes.get('5m', {}))

            if peak_profit_pct >= self.EARLY_PROFIT_PROTECTION_PCT and weak_15m and weak_5m:
                break_even_stop = self._get_break_even_stop(entry_price, side, int(position['leverage']))
                below_break_even = (
                    (side == 'long' and current_price <= break_even_stop) or
                    (side == 'short' and current_price >= break_even_stop) or
                    current_profit_pct <= 0
                )

                if below_break_even:
                    close_result = self._execute_close_okx(
                        coin,
                        {'signal': 'sell_to_close' if side == 'long' else 'buy_to_close', 'quantity': position.get('quantity', 0)},
                        {coin: {'price': current_price}},
                        portfolio,
                        close_all=True
                    )
                    close_result['reason'] = (
                        f'早期利润保护触发：峰值净浮盈 {peak_profit_pct*100:.2f}% 后，15M/5M 同时转弱，'
                        f'且当前价格已跌破保本附近（保本位 ${break_even_stop:.2f}）'
                    )
                    results.append(close_result)
                    continue

                improved_break_even = (
                    stop_loss is None or
                    (side == 'long' and break_even_stop > stop_loss) or
                    (side == 'short' and break_even_stop < stop_loss)
                )
                if improved_break_even:
                    risk_sync = self._sync_okx_native_risk_order(
                        coin,
                        side=side,
                        contracts=position.get('contracts', position.get('size', 0)),
                        stop_loss=break_even_stop,
                        take_profit=take_profit,
                        db_pos=db_pos
                    )
                    if risk_sync.get('success'):
                        self.db.update_position(
                            self.model_id,
                            coin,
                            position.get('quantity', position.get('coin_quantity', 0.0)),
                            entry_price,
                            position['leverage'],
                            side,
                            break_even_stop,
                            take_profit,
                            entry_ord_id=db_pos.get('entry_ord_id'),
                            okx_risk_algo_id=risk_sync.get('algo_id'),
                            okx_risk_algo_cl_ord_id=risk_sync.get('algo_cl_ord_id'),
                            trailing_tier=max(float(db_pos.get('trailing_tier') or 0), 0.005),
                            peak_price=peak_price,
                            peak_profit_pct=peak_profit_pct,
                            last_profit_pct=current_profit_pct
                        )
                        results.append({
                            'coin': coin,
                            'signal': 'move_stop_loss',
                            'price': current_price,
                            'message': f'{coin} 达到早期利润保护条件，止损上移至保本价 ${break_even_stop:.2f}'
                        })
                        stop_loss = break_even_stop

            if peak_profit_pct >= self.peak_profit_activation_pct and drawdown_ratio >= self.peak_drawdown_close_ratio:
                close_result = self._execute_close_okx(
                    coin,
                    {'signal': 'sell_to_close' if side == 'long' else 'buy_to_close', 'quantity': position.get('quantity', 0)},
                    {coin: {'price': current_price}},
                    portfolio,
                    close_all=True
                )
                close_result['reason'] = (
                    f'峰值净浮盈回撤达到{self.peak_drawdown_close_ratio*100:.0f}% '
                    f'(峰值 {peak_profit_pct*100:.2f}% -> 当前 {current_profit_pct*100:.2f}%)'
                )
                results.append(close_result)
                continue

            locked_profit_pct, desired_stop_loss = calculate_peak_drawdown_stop(
                entry_price, peak_profit_pct, side, int(position['leverage'])
            )
            current_tier = float(db_pos.get('trailing_tier') or 0)

            if desired_stop_loss is not None:
                improved = (
                    stop_loss is None or
                    (side == 'long' and desired_stop_loss > stop_loss) or
                    (side == 'short' and desired_stop_loss < stop_loss)
                )
                if improved and locked_profit_pct > current_tier:
                    risk_sync = self._sync_okx_native_risk_order(
                        coin,
                        side=side,
                        contracts=position.get('contracts', position.get('size', 0)),
                        stop_loss=desired_stop_loss,
                        take_profit=take_profit,
                        db_pos=db_pos
                    )
                    if risk_sync.get('success'):
                        self.db.update_position(
                            self.model_id,
                            coin,
                            position.get('quantity', position.get('coin_quantity', 0.0)),
                            entry_price,
                            position['leverage'],
                            side,
                            desired_stop_loss,
                            take_profit,
                            entry_ord_id=db_pos.get('entry_ord_id'),
                            okx_risk_algo_id=risk_sync.get('algo_id'),
                            okx_risk_algo_cl_ord_id=risk_sync.get('algo_cl_ord_id'),
                            trailing_tier=locked_profit_pct,
                            peak_price=peak_price,
                            peak_profit_pct=peak_profit_pct,
                            last_profit_pct=current_profit_pct
                        )
                        results.append({
                            'coin': coin,
                            'signal': 'move_stop_loss',
                            'price': current_price,
                            'message': f'{coin} 已将交易所止损上移到 ${desired_stop_loss:.2f}'
                        })
                        continue
            elif stop_loss is not None or take_profit is not None:
                risk_sync = self._sync_okx_native_risk_order(
                    coin,
                    side=side,
                    contracts=position.get('contracts', position.get('size', 0)),
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    db_pos=db_pos
                )
                if risk_sync.get('success') and (risk_sync.get('algo_id') or risk_sync.get('algo_cl_ord_id')):
                    self.db.update_position(
                        self.model_id,
                        coin,
                        position.get('quantity', position.get('coin_quantity', 0.0)),
                        entry_price,
                        position['leverage'],
                        side,
                        stop_loss,
                        take_profit,
                        entry_ord_id=db_pos.get('entry_ord_id'),
                        okx_risk_algo_id=risk_sync.get('algo_id'),
                        okx_risk_algo_cl_ord_id=risk_sync.get('algo_cl_ord_id'),
                        trailing_tier=current_tier,
                        peak_price=peak_price,
                        peak_profit_pct=peak_profit_pct,
                        last_profit_pct=current_profit_pct
                    )
                    continue

            self._update_db_position_metrics(coin, position, metrics, db_pos=db_pos)
        return results

    def _execute_decisions_okx(self, decisions: Dict, market_state: Dict, portfolio: Dict) -> list:
        results = []
        for coin, decision in decisions.items():
            if not isinstance(decision, dict) or 'signal' not in decision:
                continue
            signal = decision.get('signal')
            try:
                if signal == 'buy_to_enter':
                    result = self._execute_buy_okx(coin, decision, market_state)
                elif signal == 'sell_to_enter':
                    result = self._execute_sell_okx(coin, decision, market_state)
                elif signal == 'sell_to_close':
                    result = self._execute_close_okx(coin, decision, market_state, portfolio, close_all=True)
                elif signal == 'buy_to_close':
                    result = self._execute_close_okx(coin, decision, market_state, portfolio, close_all=True)
                elif signal == 'reduce_position':
                    result = self._execute_close_okx(coin, decision, market_state, portfolio, close_all=False)
                elif signal == 'increase_position':
                    result = self._execute_add_okx(coin, decision, market_state)
                elif signal == 'hold':
                    result = {'coin': coin, 'signal': 'hold', 'message': 'Hold position (OKX)'}
                else:
                    result = {'coin': coin, 'signal': signal, 'error': f'Unsupported signal: {signal}'}
                results.append(result)
            except Exception as e:
                results.append({'coin': coin, 'signal': signal, 'error': f'Execution failed: {str(e)}'})
        return results

    def _execute_buy_okx(self, coin: str, decision: Dict, market_state: Dict) -> Dict:
        import config
        quantity = float(decision.get('quantity', 0))
        leverage = int(decision.get('leverage', 1))
        stop_loss = decision.get('stop_loss')
        take_profit = decision.get('profit_target') or decision.get('take_profit')
        current_price = market_state[coin]['price']
        self._validate_quantity(quantity, coin)
        self._validate_leverage(leverage)
        stop_loss, take_profit = self._resolve_risk_targets('long', current_price, stop_loss, take_profit)

        for pos in self.okx_trader.get_positions():
            if pos['coin'] == coin:
                return {'coin': coin, 'error': f'OKX 已有 {coin} 合约持仓'}

        balance = self.okx_trader.get_balance()
        if 'error' in balance:
            return {'coin': coin, 'error': f'Failed to get balance: {balance["error"]}'}
        required_margin = (quantity * current_price) / leverage
        entry_ratio_cap, cap_reasons = self._get_entry_margin_ratio(decision, market_state, coin, 'long')
        max_margin_allowed = balance.get('available', 0) * entry_ratio_cap
        if required_margin > max_margin_allowed and max_margin_allowed > 0:
            adjusted_quantity = max_margin_allowed * leverage / current_price
            quantity = max(0.0, adjusted_quantity)
            self._debug_log(
                f"{coin} 仓位因{','.join(cap_reasons) if cap_reasons else '风险预算'}收缩，新的下单数量 {quantity:.4f}"
            )
            self._validate_quantity(quantity, coin)
            required_margin = (quantity * current_price) / leverage
        if balance.get('available', 0) < required_margin:
            return {'coin': coin, 'error': f'Insufficient balance: need {required_margin:.2f}, have {balance.get("available", 0):.2f}'}

        okx_result = self.okx_trader.place_order(coin, 'buy', quantity, current_price, leverage, stop_loss, take_profit)
        if not okx_result.get('success'):
            return {'coin': coin, 'error': f'OKX order failed: {okx_result.get("error", "Unknown error")}'}

        entry_ord_id = okx_result.get('ord_id')
        self.okx_trader.get_order_status(entry_ord_id, config.OKX_SYMBOLS[coin])
        actual_position = self._get_okx_position_after_order(coin, 'long')
        actual_quantity = actual_position.get('coin_quantity', quantity) if actual_position else quantity
        actual_contracts = actual_position.get('contracts', self.okx_trader.coin_quantity_to_contracts(coin, quantity, current_price)) if actual_position else self.okx_trader.coin_quantity_to_contracts(coin, quantity, current_price)
        avg_price = actual_position.get('avg_price', current_price) if actual_position else current_price
        risk_sync = self._sync_okx_native_risk_order(coin, side='long', contracts=actual_contracts, stop_loss=stop_loss, take_profit=take_profit)
        self.db.update_position(
            self.model_id, coin, actual_quantity, avg_price, leverage, 'long',
            stop_loss, take_profit,
            entry_ord_id=entry_ord_id,
            okx_risk_algo_id=risk_sync.get('algo_id'),
            okx_risk_algo_cl_ord_id=risk_sync.get('algo_cl_ord_id'),
            trailing_tier=0, peak_price=avg_price, peak_profit_pct=0, last_profit_pct=0
        )
        self.db.add_trade(self.model_id, coin, 'buy_to_enter', actual_quantity, avg_price, leverage, 'long', pnl=0)
        return {
            'coin': coin,
            'signal': 'buy_to_enter',
            'okx_order_id': entry_ord_id,
            'message': okx_result.get('message', f'(OKX) Long {actual_quantity:.4f} {coin}'),
            'warning': None if risk_sync.get('success') else f'Native TP/SL setup failed: {risk_sync.get("error")}'
        }

    def _execute_sell_okx(self, coin: str, decision: Dict, market_state: Dict) -> Dict:
        import config
        quantity = float(decision.get('quantity', 0))
        leverage = int(decision.get('leverage', 1))
        stop_loss = decision.get('stop_loss')
        take_profit = decision.get('profit_target') or decision.get('take_profit')
        current_price = market_state[coin]['price']
        self._validate_quantity(quantity, coin)
        self._validate_leverage(leverage)
        stop_loss, take_profit = self._resolve_risk_targets('short', current_price, stop_loss, take_profit)

        for pos in self.okx_trader.get_positions():
            if pos['coin'] == coin:
                return {'coin': coin, 'error': f'OKX 已有 {coin} 合约持仓'}

        balance = self.okx_trader.get_balance()
        if 'error' in balance:
            return {'coin': coin, 'error': f'Failed to get balance: {balance["error"]}'}
        required_margin = (quantity * current_price) / leverage
        entry_ratio_cap, cap_reasons = self._get_entry_margin_ratio(decision, market_state, coin, 'short')
        max_margin_allowed = balance.get('available', 0) * entry_ratio_cap
        if required_margin > max_margin_allowed and max_margin_allowed > 0:
            adjusted_quantity = max_margin_allowed * leverage / current_price
            quantity = max(0.0, adjusted_quantity)
            self._debug_log(
                f"{coin} 仓位因{','.join(cap_reasons) if cap_reasons else '风险预算'}收缩，新的下单数量 {quantity:.4f}"
            )
            self._validate_quantity(quantity, coin)
            required_margin = (quantity * current_price) / leverage
        if balance.get('available', 0) < required_margin:
            return {'coin': coin, 'error': f'Insufficient balance: need {required_margin:.2f}, have {balance.get("available", 0):.2f}'}

        okx_result = self.okx_trader.place_order(coin, 'sell', quantity, current_price, leverage, stop_loss, take_profit)
        if not okx_result.get('success'):
            return {'coin': coin, 'error': f'OKX order failed: {okx_result.get("error", "Unknown error")}'}

        entry_ord_id = okx_result.get('ord_id')
        self.okx_trader.get_order_status(entry_ord_id, config.OKX_SYMBOLS[coin])
        actual_position = self._get_okx_position_after_order(coin, 'short')
        actual_quantity = actual_position.get('coin_quantity', quantity) if actual_position else quantity
        actual_contracts = actual_position.get('contracts', self.okx_trader.coin_quantity_to_contracts(coin, quantity, current_price)) if actual_position else self.okx_trader.coin_quantity_to_contracts(coin, quantity, current_price)
        avg_price = actual_position.get('avg_price', current_price) if actual_position else current_price
        risk_sync = self._sync_okx_native_risk_order(coin, side='short', contracts=actual_contracts, stop_loss=stop_loss, take_profit=take_profit)
        self.db.update_position(
            self.model_id, coin, actual_quantity, avg_price, leverage, 'short',
            stop_loss, take_profit,
            entry_ord_id=entry_ord_id,
            okx_risk_algo_id=risk_sync.get('algo_id'),
            okx_risk_algo_cl_ord_id=risk_sync.get('algo_cl_ord_id'),
            trailing_tier=0, peak_price=avg_price, peak_profit_pct=0, last_profit_pct=0
        )
        self.db.add_trade(self.model_id, coin, 'sell_to_enter', actual_quantity, avg_price, leverage, 'short', pnl=0)
        return {
            'coin': coin,
            'signal': 'sell_to_enter',
            'okx_order_id': entry_ord_id,
            'message': okx_result.get('message', f'(OKX) Short {actual_quantity:.4f} {coin}'),
            'warning': None if risk_sync.get('success') else f'Native TP/SL setup failed: {risk_sync.get("error")}'
        }

    def _execute_close_okx(self, coin: str, decision: Dict, market_state: Dict, portfolio: Dict, close_all: bool = True) -> Dict:
        position = None
        positions = self.okx_trader.get_positions() or []
        for pos in positions:
            if pos['coin'] == coin:
                position = pos
                break
        if not position:
            return {'coin': coin, 'error': 'Position not found in OKX'}

        current_contracts = position.get('contracts', position['size'])
        current_quantity = position.get('coin_quantity', 0.0)
        ai_quantity = float(decision.get('quantity', 0))
        db_pos = self.db.get_position(self.model_id, coin, position['side'])

        if close_all:
            close_contracts = current_contracts
        else:
            if ai_quantity <= 0:
                return {'coin': coin, 'error': 'Invalid quantity for partial close'}
            requested_contracts = self.okx_trader.coin_quantity_to_contracts(coin, ai_quantity, market_state[coin]['price'], round_up=True)
            close_contracts = current_contracts if requested_contracts >= current_contracts else requested_contracts

        okx_result = self.okx_trader.close_position(coin, position['side'], close_contracts)
        if not okx_result.get('success'):
            return {'coin': coin, 'error': f'OKX close failed: {okx_result.get("error", "Unknown error")}'}

        current_price = market_state[coin]['price']
        remaining_position = self._get_okx_position_after_order(coin, position['side'], previous_contracts=current_contracts)
        remaining_contracts = remaining_position.get('contracts', remaining_position['size']) if remaining_position else 0
        remaining_quantity = remaining_position.get('coin_quantity', 0.0) if remaining_position else 0.0
        actual_closed_quantity = max(0.0, current_quantity - remaining_quantity)
        if actual_closed_quantity <= 0:
            return {'coin': coin, 'error': f'OKX close order accepted but position did not decrease for {coin}'}

        if position['side'] == 'long':
            pnl = (current_price - position['avg_price']) * actual_closed_quantity
        else:
            pnl = (position['avg_price'] - current_price) * actual_closed_quantity

        if remaining_contracts <= 0 or remaining_quantity <= 0:
            self._sync_okx_native_risk_order(coin, side=position['side'], contracts=0, stop_loss=None, take_profit=None, db_pos=db_pos)
            self.db.close_position(self.model_id, coin, position['side'])
            self.db.add_trade(self.model_id, coin, 'close_position', actual_closed_quantity, current_price, position['leverage'], position['side'], pnl=pnl)
            result_signal = 'sell_to_close' if position['side'] == 'long' else 'buy_to_close'
            message = f'(OKX) Close all {coin}, P&L: ${pnl:.2f}'
        else:
            risk_sync = self._sync_okx_native_risk_order(
                coin, side=position['side'], contracts=remaining_contracts,
                stop_loss=db_pos.get('stop_loss') if db_pos else None,
                take_profit=db_pos.get('take_profit') if db_pos else None,
                db_pos=db_pos
            )
            self.db.update_position(
                self.model_id, coin, remaining_quantity, remaining_position['avg_price'], remaining_position['leverage'], position['side'],
                db_pos.get('stop_loss') if db_pos else None,
                db_pos.get('take_profit') if db_pos else None,
                entry_ord_id=db_pos.get('entry_ord_id') if db_pos else None,
                okx_risk_algo_id=risk_sync.get('algo_id') if risk_sync else db_pos.get('okx_risk_algo_id') if db_pos else None,
                okx_risk_algo_cl_ord_id=risk_sync.get('algo_cl_ord_id') if risk_sync else db_pos.get('okx_risk_algo_cl_ord_id') if db_pos else None,
                trailing_tier=db_pos.get('trailing_tier') if db_pos else 0,
                peak_price=db_pos.get('peak_price') if db_pos else remaining_position['avg_price'],
                peak_profit_pct=db_pos.get('peak_profit_pct') if db_pos else 0,
                last_profit_pct=db_pos.get('last_profit_pct') if db_pos else 0
            )
            self.db.add_trade(self.model_id, coin, 'reduce_position', actual_closed_quantity, current_price, position['leverage'], position['side'], pnl=pnl)
            result_signal = 'reduce_position'
            message = f'(OKX) Reduce {coin} by {actual_closed_quantity}, remaining {remaining_quantity}, P&L: ${pnl:.2f}'

        return {
            'coin': coin,
            'signal': result_signal,
            'okx_order_id': okx_result.get('ord_id'),
            'pnl': pnl,
            'closed_quantity': actual_closed_quantity,
            'remaining_quantity': remaining_quantity,
            'message': message
        }

    def _get_okx_position_after_order(self, coin: str, side: str, previous_contracts: float = None,
                                      retries: int = 5, delay_seconds: float = 0.5) -> Dict:
        last_position = None
        for attempt in range(retries):
            positions = self.okx_trader.get_positions() or []
            matching_position = None
            for pos in positions:
                if pos['coin'] == coin and pos['side'] == side:
                    matching_position = pos
                    break
            if previous_contracts is None:
                if matching_position or attempt == retries - 1:
                    return matching_position
                last_position = matching_position
                time.sleep(delay_seconds)
                continue
            remaining_contracts = matching_position.get('contracts', matching_position['size']) if matching_position else 0
            if remaining_contracts < previous_contracts or attempt == retries - 1:
                return matching_position
            last_position = matching_position
            time.sleep(delay_seconds)
        return last_position

    def _execute_add_okx(self, coin: str, decision: Dict, market_state: Dict) -> Dict:
        quantity = float(decision.get('quantity', 0))
        leverage = int(decision.get('leverage', 1))
        stop_loss = decision.get('stop_loss')
        take_profit = decision.get('profit_target') or decision.get('take_profit')
        current_price = market_state[coin]['price']
        self._validate_quantity(quantity, coin)
        self._validate_leverage(leverage)

        positions = self.okx_trader.get_positions() or []
        existing_position = None
        for pos in positions:
            if pos['coin'] == coin:
                existing_position = pos
                break
        if not existing_position:
            return {'coin': coin, 'error': 'No existing position to add to'}
        if existing_position['side'] != 'long':
            return {'coin': coin, 'error': f'{coin} 已有空仓，不能做多加仓'}

        stop_loss, take_profit = self._resolve_risk_targets('long', current_price, stop_loss, take_profit)
        balance = self.okx_trader.get_balance()
        if 'error' in balance:
            return {'coin': coin, 'error': f'Failed to get balance: {balance["error"]}'}
        required_margin = (quantity * current_price) / leverage
        if balance.get('available', 0) < required_margin:
            return {'coin': coin, 'error': f'Insufficient balance: need {required_margin:.2f}, have {balance.get("available", 0):.2f}'}

        okx_result = self.okx_trader.place_order(coin, 'buy', quantity, current_price, leverage, stop_loss, take_profit)
        if not okx_result.get('success'):
            return {'coin': coin, 'error': f'OKX order failed: {okx_result.get("error", "Unknown error")}'}

        db_pos = self.db.get_position(self.model_id, coin, 'long') or {}
        previous_contracts = existing_position.get('contracts', existing_position['size'])
        actual_position = self._get_okx_position_after_order(coin, 'long', previous_contracts=previous_contracts)
        total_quantity = actual_position.get('coin_quantity', existing_position.get('coin_quantity', 0.0) + quantity) if actual_position else existing_position.get('coin_quantity', 0.0) + quantity
        total_contracts = actual_position.get('contracts', previous_contracts) if actual_position else previous_contracts
        avg_price = actual_position.get('avg_price', current_price) if actual_position else current_price
        effective_stop_loss = stop_loss if stop_loss is not None else db_pos.get('stop_loss')
        effective_take_profit = take_profit if take_profit is not None else db_pos.get('take_profit')
        risk_sync = self._sync_okx_native_risk_order(
            coin, side='long', contracts=total_contracts,
            stop_loss=effective_stop_loss, take_profit=effective_take_profit, db_pos=db_pos
        )
        self.db.update_position(
            self.model_id, coin, total_quantity, avg_price, leverage, 'long',
            effective_stop_loss, effective_take_profit,
            entry_ord_id=okx_result.get('ord_id'),
            okx_risk_algo_id=risk_sync.get('algo_id'),
            okx_risk_algo_cl_ord_id=risk_sync.get('algo_cl_ord_id'),
            trailing_tier=0,
            peak_price=max(float(db_pos.get('peak_price') or avg_price), avg_price),
            peak_profit_pct=float(db_pos.get('peak_profit_pct') or 0),
            last_profit_pct=float(db_pos.get('last_profit_pct') or 0)
        )
        self.db.add_trade(self.model_id, coin, 'increase_position', quantity, current_price, leverage, 'long', pnl=0)
        return {
            'coin': coin,
            'signal': 'increase_position',
            'okx_order_id': okx_result.get('ord_id'),
            'message': f'(OKX) Add to long {coin} +{quantity:.4f}, total {total_quantity}',
            'warning': None if risk_sync.get('success') else f'Native TP/SL update failed: {risk_sync.get("error")}'
        }
