from datetime import datetime
from typing import Dict
import json
import config
from utils.timezone import get_current_utc_time_str, get_current_beijing_time_str

# 在顶部导入
if config.TRADING_MODE == 'okx_demo':
    from okx_trader import OKXTrader

# 延迟导入 OKXTrader，仅在 OKX 模式下使用
okx_trader = None
if config.TRADING_MODE == 'okx_demo':
    try:
        from okx_trader import OKXTrader
        okx_trader = OKXTrader()
        print(f"[INFO] OKX交易器已初始化，模式：{config.TRADING_MODE}")
    except Exception as e:
        print(f"[ERROR] OKX交易器初始化失败：{e}")
        import traceback
        traceback.print_exc()
        config.TRADING_MODE = 'simulation'


class TradingEngine:
    def __init__(self, model_id: int, db, market_fetcher, ai_trader):
        self.model_id = model_id
        self.db = db
        self.market_fetcher = market_fetcher
        self.ai_trader = ai_trader
        self.coins = config.SUPPORTED_COINS
        self.trading_mode = config.TRADING_MODE
        
        # 根据模式初始化交易器
        if config.TRADING_MODE == 'okx_demo':
            self.okx_trader = OKXTrader()
            print(f"[INFO] 模型 {model_id} 已初始化，模式：{config.TRADING_MODE}")
        else:
            self.okx_trader = None

    def _validate_quantity(self, quantity: float, coin: str) -> None:
        """验证交易数量"""
        if not isinstance(quantity, (int, float)):
            raise ValueError(f"Invalid quantity type: {type(quantity)}")
        if quantity <= 0:
            raise ValueError(f"Quantity must be positive, got {quantity}")
        if quantity > 1000:  # 防止异常大的数量
            raise ValueError(f"Quantity too large: {quantity}")

    def _validate_leverage(self, leverage: int) -> None:
        """验证杠杆倍数"""
        if not isinstance(leverage, int):
            raise ValueError(f"Leverage must be integer, got {type(leverage)}")
        if leverage < config.MIN_LEVERAGE or leverage > config.MAX_LEVERAGE:
            raise ValueError(f"Leverage must be between {config.MIN_LEVERAGE} and {config.MAX_LEVERAGE}, got {leverage}")

    def execute_trading_cycle(self) -> Dict:
        try:
            market_state = self._get_market_state()
            current_prices = {coin: market_state[coin]['price'] for coin in market_state}
            
            # 根据 TRADING_MODE 获取投资组合
            if config.TRADING_MODE == 'okx_demo':
                portfolio = self._get_okx_portfolio(current_prices)
            else:
                portfolio = self.db.get_portfolio(self.model_id, current_prices)
            
            # 检查止盈止损（优先执行）
            if config.TRADING_MODE == 'okx_demo':
                stop_results = self._check_stop_loss_take_profit_okx(portfolio, current_prices)
            else:
                stop_results = self._check_stop_loss_take_profit(portfolio, current_prices)
            
            # AI决策（返回决策和原始响应）
            account_info = self._build_account_info(portfolio)
            decisions, raw_response = self.ai_trader.make_decision(
                market_state, portfolio, account_info
            )
            
            # 打印 AI 决策详细信息
            # print(f'[DEBUG] Model {self.model_id}: AI 决策详情')
            # print(f'[DEBUG] 决策数量: {len(decisions) if decisions else 0}')
            if decisions:
                for coin, decision in decisions.items():
                    print(f'[DEBUG] 模型 {self.model_id} 决策: {coin}: {decision}')
            #print(f'[DEBUG] 模型 {self.model_id} 原始响应长度: {len(raw_response) if raw_response else 0}')

            # 只有在AI返回有效决策时才存储对话记录
            if decisions and len(decisions) > 0:
                self.db.add_conversation(
                    self.model_id,
                    user_prompt=self._format_prompt(market_state, portfolio, account_info),
                    ai_response=json.dumps(decisions, ensure_ascii=False),
                    cot_trace=raw_response[:2000] if raw_response else ''
                )
                print(f'[INFO] 模型 {self.model_id} AI 决策已存储，包含 ({len(decisions)} 个币种)')
            else:
                print(f'[WARN] 模型 {self.model_id} AI 决策为空，跳过对话记录存储')
            
            # 根据 TRADING_MODE 执行交易
            if config.TRADING_MODE == 'okx_demo':
                execution_results = self._execute_decisions_okx(decisions, market_state, portfolio)
            else:
                execution_results = self._execute_decisions(decisions, market_state, portfolio)
            
            # 合并止盈止损结果
            all_results = stop_results + execution_results
            
            # 更新账户价值
            if config.TRADING_MODE == 'okx_demo':
                self._record_okx_account_value(portfolio)
                updated_portfolio = portfolio
            else:
                updated_portfolio = self.db.get_portfolio(self.model_id, current_prices)
                self.db.record_account_value(
                    self.model_id,
                    updated_portfolio['total_value'],
                    updated_portfolio['cash'],
                    updated_portfolio['positions_value']
                )
            
            return {
                'success': True,
                'decisions': decisions,
                'executions': all_results,
                'portfolio': updated_portfolio
            }
        except Exception as e:
            print(f"[ERROR] Trading cycle failed (Model {self.model_id}): {e}")
            import traceback
            print(traceback.format_exc())
            return {'success': False, 'error': str(e)}

    def _get_okx_portfolio(self, current_prices: Dict) -> Dict:
        """从 OKX 获取真实持仓"""
        balance = self.okx_trader.get_balance()
        if 'error' in balance:
            print(f"[WARN] 模型 {self.model_id} 获取余额失败: {balance['error']}")
            balance = {'total': 10000, 'available': 10000, 'frozen': 0, 'details': []}
        
        details = balance.get('details', [])
        
        # 1. 计算账户总值（所有币种的等值 USDT 之和）
        total_value = 0
        usdt_available = 0
        frozen_margin = 0
        
        for item in details:
            ccy = item.get('ccy')
            eq_usd = float(item.get('eqUsd', 0))
            
            if eq_usd > 0:
                total_value += eq_usd
            
            if ccy == 'USDT':
                usdt_available = float(item.get('availEq', 0))  # USDT 可用
                frozen_margin = float(item.get('frozenBal', 0))  # 保证金占用
        
        # 2. 现金价值 = USDT 可用余额
        cash = usdt_available
        
        # 3. 获取合约持仓
        positions = self.okx_trader.get_positions()
        if 'error' in positions:
            positions = []
        
        positions_value = 0
        pos_details = []
        
        for pos in positions:
            coin = pos['coin']
            if coin in current_prices:
                position_quantity = pos.get('coin_quantity', 0.0)
                position_value = pos.get('notional_usdt', position_quantity * current_prices[coin])
                positions_value += position_value
                
                db_pos = self.db.get_position(self.model_id, coin, pos['side'])
                pos_details.append({
                    'coin': coin,
                    'side': pos['side'],
                    'quantity': position_quantity,
                    'contracts': pos.get('contracts', pos.get('size')),
                    'avg_price': pos['avg_price'],
                    'leverage': pos['leverage'],
                    'stop_loss': db_pos.get('stop_loss') if db_pos else None,
                    'take_profit': db_pos.get('take_profit') if db_pos else None,
                    'value': position_value
                })
        
        print(f"[DEBUG] 账户总值: {total_value:.2f} USDT")
        print(f"[DEBUG] 现金价值: {cash:.2f} USDT")
        print(f"[DEBUG] 已用保证金: {frozen_margin:.2f} USDT")
        
        return {
            'cash': cash,                           # 现金 = USDT 可用
            'positions': pos_details,                # 合约持仓
            'total_value': total_value,              # 账户总值 = 所有资产
            'positions_value': positions_value,      # 合约持仓价值
            'frozen_margin': frozen_margin,          # 已用保证金
            'wallet_balances': balance.get('balances', {})
        }

    def _check_stop_loss_take_profit_okx(self, portfolio: Dict, current_prices: Dict) -> list:
        """OKX 模式下的止盈止损检查（支持阶梯式移动止损）"""
        
        # ========== 阶梯式移动止损配置 ==========
        # 格式: (盈利阈值, 止损距离 entry_price 的百分比)
        # 例如: (0.03, 0.01) 表示盈利 3% 时，止损在成本价的 99%（即保留 1% 缓冲）
        TRAILING_STEPS = [
            (0.03, 0.01),   # 盈利 3%  → 止损在 entry × 0.99（1% 缓冲）
            (0.05, 0.02),   # 盈利 5%  → 止损在 entry × 0.98（2% 缓冲）
            (0.10, 0.03),   # 盈利 10% → 止损在 entry × 0.97（3% 缓冲）
            (0.15, 0.05),   # 盈利 15% → 止损在 entry × 0.95（5% 缓冲）
            (0.20, 0.00),   # 盈利 20% → 止损在 entry × 1.00（成本价，100% 保住利润）
        ]
        # ========================================
        
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
            
            # ===== 计算当前盈利百分比 =====
            if side == 'long':
                profit_pct = (current_price - entry_price) / entry_price
            else:
                profit_pct = (entry_price - current_price) / entry_price
            
            # ===== 计算阶梯止损价 =====
            trailing_stop_loss = None
            current_tier = None
            
            for threshold, buffer in TRAILING_STEPS:
                if profit_pct >= threshold:
                    if side == 'long':
                        trailing_stop_loss = entry_price * (1 - buffer)
                    else:
                        trailing_stop_loss = entry_price * (1 + buffer)
                    current_tier = threshold
                    buffer_pct = int(buffer * 100)
            
            # 打印移动止损信息（只在跨过新台阶时）
            if trailing_stop_loss and current_tier:
                print(f"[DEBUG] {coin} 当前盈利 {profit_pct*100:.1f}%, 止损阶梯 {current_tier*100:.0f}%: ${trailing_stop_loss:.2f}")
            
            # ===== 检查是否触发止盈止损 =====
            should_close = False
            reason = ''
            triggered_by = ''
            
            # 1. 检查阶梯止损
            if trailing_stop_loss:
                if side == 'long' and current_price <= trailing_stop_loss:
                    should_close = True
                    reason = f'阶梯止损触发 ({current_tier*100:.0f}%盈利阶梯, ${current_price:.2f} <= ${trailing_stop_loss:.2f})'
                    triggered_by = 'trailing_stop'
                elif side == 'short' and current_price >= trailing_stop_loss:
                    should_close = True
                    reason = f'阶梯止损触发 ({current_tier*100:.0f}%盈利阶梯, ${current_price:.2f} >= ${trailing_stop_loss:.2f})'
                    triggered_by = 'trailing_stop'
            
            # 2. 检查固定止损（如果盈利未达到任何阶梯）
            if not should_close and stop_loss:
                if side == 'long' and current_price <= stop_loss:
                    should_close = True
                    reason = f'止损触发 (固定, ${current_price:.2f} <= ${stop_loss:.2f})'
                    triggered_by = 'fixed_stop'
                elif side == 'short' and current_price >= stop_loss:
                    should_close = True
                    reason = f'止损触发 (固定, ${current_price:.2f} >= ${stop_loss:.2f})'
                    triggered_by = 'fixed_stop'
            
            # 3. 检查止盈
            if take_profit and not should_close:
                if side == 'long' and current_price >= take_profit:
                    should_close = True
                    reason = f'止盈触发 (${current_price:.2f} >= ${take_profit:.2f})'
                    triggered_by = 'take_profit'
                elif side == 'short' and current_price <= take_profit:
                    should_close = True
                    reason = f'止盈触发 (${current_price:.2f} <= ${take_profit:.2f})'
                    triggered_by = 'take_profit'
            
            # ===== 执行平仓 =====
            if should_close:
                close_contracts = position.get('contracts', position.get('size', 0))
                okx_result = self.okx_trader.close_position(coin, side, close_contracts)
                
                if okx_result.get('success'):
                    quantity = position.get('quantity', position.get('coin_quantity', 0))
                    if side == 'long':
                        pnl = (current_price - entry_price) * quantity
                    else:
                        pnl = (entry_price - current_price) * quantity
                    
                    self.db.close_position(self.model_id, coin, side)
                    self.db.add_trade(
                        self.model_id, coin, triggered_by, quantity,
                        current_price, position['leverage'], side, pnl=pnl
                    )
                    
                    results.append({
                        'coin': coin,
                        'signal': triggered_by,
                        'reason': reason,
                        'quantity': quantity,
                        'price': current_price,
                        'pnl': pnl,
                        'message': f'{coin} {reason}, P&L: ${pnl:.2f} (OKX)'
                    })
        
        return results

    def _execute_decisions_okx(self, decisions: Dict, market_state: Dict, portfolio: Dict) -> list:
        """执行 OKX 决策"""
        print(f"[DEBUG] 模型 {self.model_id} ===== _execute_decisions_okx 开始 =====")
        print(f"[DEBUG] 模型 {self.model_id} 决策数量: {len(decisions)}")
        
        results = []
        supported_coins = config.SUPPORTED_COINS
        
        for coin, decision in decisions.items():
            # print(f"[DEBUG] 模型 {self.model_id} ===== 开始处理 {coin} =====")
            
            # 检查是否在支持列表中
            if coin not in supported_coins:
                print(f"[DEBUG] 模型 {self.model_id} {coin} 不在支持列表中，跳过")
                continue
            
            # 验证决策格式
            if not isinstance(decision, dict) or 'signal' not in decision:
                print(f"[DEBUG] 模型 {self.model_id} {coin} 决策格式错误: {decision}，跳过")
                continue
            
            signal = decision.get('signal')
            print(f"[DEBUG] 模型 {self.model_id} {coin} 信号: {signal}")
            
            try:
                if signal == 'buy_to_enter':
                    print(f"[DEBUG] 模型 {self.model_id} {coin} 准备调用 _execute_buy_okx...")
                    result = self._execute_buy_okx(coin, decision, market_state, portfolio)
                    print(f"[DEBUG] 模型 {self.model_id} {coin} _execute_buy_okx 返回: {result}")
                    results.append(result)
                    
                # 卖出信号处理
                elif signal == 'sell_to_enter':
                    print(f"[DEBUG] 模型 {self.model_id} {coin} 准备调用 _execute_sell_okx...")
                    result = self._execute_sell_okx(coin, decision, market_state, portfolio)
                    print(f"[DEBUG] 模型 {self.model_id} {coin} _execute_sell_okx 返回: {result}")
                    results.append(result)
                elif signal == 'hold':
                    # print(f"[DEBUG] 模型 {self.model_id} {coin} 持仓")
                    results.append({
                        'coin': coin,
                        'signal': 'hold',
                        'message': 'Hold position (OKX)'
                    })
                    
                elif signal == 'sell_to_close':
                    # 全部平仓（平多）
                    print(f"[DEBUG] 模型 {self.model_id} {coin} 准备全部平仓...")
                    result = self._execute_close_okx(coin, decision, market_state, portfolio, close_all=True)
                    print(f"[DEBUG] 模型 {self.model_id} {coin} _execute_close_okx 返回: {result}")
                    results.append(result)

                elif signal == 'reduce_position':
                    # 部分平仓
                    print(f"[DEBUG] 模型 {self.model_id} {coin} 准备部分平仓...")
                    result = self._execute_close_okx(coin, decision, market_state, portfolio, close_all=False)
                    print(f"[DEBUG] 模型 {self.model_id} {coin} _execute_close_okx 返回: {result}")
                    results.append(result)

                elif signal == 'increase_position':
                    # 部分加仓
                    print(f"[DEBUG] 模型 {self.model_id} {coin} 准备部分加仓...")
                    result = self._execute_add_okx(coin, decision, market_state, portfolio)
                    print(f"[DEBUG] 模型 {self.model_id} {coin} _execute_add_okx 返回: {result}")
                    results.append(result)

                elif signal == 'buy_to_close':
                    # 全部平仓（平空）
                    print(f"[DEBUG] 模型 {self.model_id} {coin} 准备全部平仓（空）...")
                    result = self._execute_close_okx(coin, decision, market_state, portfolio, close_all=True)
                    print(f"[DEBUG] 模型 {self.model_id} {coin} _execute_close_okx 返回: {result}")
                    results.append(result)
                    
                else:
                    print(f"[DEBUG] 模型 {self.model_id} {coin} 信号 '{signal}' 不支持，跳过")
                    results.append({
                        'coin': coin,
                        'signal': signal,
                        'error': f'Unsupported signal: {signal}'
                    })    
                # elif signal == 'hold':
                #     print(f"[DEBUG] 模型 {self.model_id} {coin} 持仓")
                #     results.append({
                #         'coin': coin,
                #         'signal': 'hold',
                #         'message': 'Hold position (OKX)'
                #     })
                # else:
                #     print(f"[DEBUG] 模型 {self.model_id} {coin} 信号 '{signal}' 不支持，跳过")
                #     results.append({
                #         'coin': coin,
                #         'signal': signal,
                #         'error': f'Unsupported signal: {signal}'
                #     })
            except Exception as e:
                print(f"[DEBUG] 模型 {self.model_id} {coin} 执行失败: {e}")
                results.append({
                    'coin': coin,
                    'signal': signal,
                    'error': f'Execution failed: {str(e)}'
                })
        
        print(f"[DEBUG] 模型 {self.model_id} ===== _execute_decisions_okx 结束，共处理 {len(results)} 个币种 =====")
        return results

    def _execute_buy_okx(self, coin: str, decision: Dict, market_state: Dict, portfolio: Dict) -> Dict:
        """OKX 做多"""
        quantity = float(decision.get('quantity', 0))
        leverage = int(decision.get('leverage', 1))
        stop_loss = decision.get('stop_loss')
        take_profit = decision.get('profit_target') or decision.get('take_profit')
        
        # 获取当前价格
        current_price = market_state[coin]['price']
        
        # 验证输入
        self._validate_quantity(quantity, coin)
        self._validate_leverage(leverage)
        
        # 只检查 OKX 合约持仓，不检查钱包余额
        print(f"[DEBUG] 模型 {self.model_id} {coin} 检查 OKX 合约持仓...")
        okx_positions = self.okx_trader.get_positions()
        print(f"[DEBUG] 模型 {self.model_id} {coin} OKX 合约持仓: {okx_positions}")
        
        for pos in okx_positions:
            if pos['coin'] == coin:
                return {'coin': coin, 'error': f'OKX 已有 {coin} 合约持仓'}
        
        print(f"[DEBUG] 模型 {self.model_id} {coin} 无持仓，准备下单")
        
        # 检查余额是否足够
        balance = self.okx_trader.get_balance()
        if 'error' in balance:
            return {'coin': coin, 'error': f'Failed to get balance: {balance["error"]}'}
        
        available_balance = balance.get('available', 0)
        required_margin = (quantity * current_price) / leverage
        print(f"[DEBUG] 模型 {self.model_id} {coin} 可用余额: {available_balance}, 需要保证金: {required_margin}")
        
        if available_balance < required_margin:
            return {
                'coin': coin,
                'error': f'Insufficient balance: need {required_margin:.2f}, have {available_balance:.2f}'
            }
        
        # 调用 OKX 下单（传入当前价格）
        print(f"[DEBUG] 模型 {self.model_id} {coin} 调用 OKX 下单...")
        okx_result = self.okx_trader.place_order(
            coin,
            'buy',
            quantity,
            current_price,  # 添加当前价格参数
            leverage,
            stop_loss,
            take_profit
        )
        print(f"[DEBUG] 模型 {self.model_id} {coin} OKX 下单结果: {okx_result}")
        
        if okx_result.get('success'):
             # 查询订单状态
            print(f"[DEBUG] 模型 {self.model_id} 查询订单状态...")
            inst_id = config.OKX_SYMBOLS[coin]
            order_status = self.okx_trader.get_order_status(
                okx_result.get('ord_id'),
                inst_id
            )
            #print(f"[DEBUG] 模型 {self.model_id} {coin} 订单状态: {order_status}")
            # 同步记录到本地数据库
            self.db.update_position(
                self.model_id,
                coin,
                quantity,
                current_price,
                leverage,
                'long',
                stop_loss,
                take_profit
            )
            self.db.add_trade(
                self.model_id,
                coin,
                'buy_to_enter',
                quantity,
                current_price,
                leverage,
                'long',
                pnl=0
            )
            return {
                'coin': coin,
                'signal': 'buy_to_enter',
                'okx_order_id': okx_result.get('ord_id'),
                'message': okx_result.get('message', f'(OKX) Long {quantity:.4f} {coin}')
            }
        else:
            return {
                'coin': coin,
                'error': f'OKX order failed: {okx_result.get("error", "Unknown error")}'
            }

    def _execute_sell_okx(self, coin: str, decision: Dict, market_state: Dict, portfolio: Dict) -> Dict:
        """OKX 做空"""
        quantity = float(decision.get('quantity', 0))
        leverage = int(decision.get('leverage', 1))
        stop_loss = decision.get('stop_loss')
        take_profit = decision.get('profit_target') or decision.get('take_profit')
        
        # 获取当前价格
        current_price = market_state[coin]['price']
        
        # 验证输入
        self._validate_quantity(quantity, coin)
        self._validate_leverage(leverage)
        
        # 只检查 OKX 合约持仓，不检查钱包余额
        print(f"[DEBUG] {coin} 检查 OKX 合约持仓...")
        okx_positions = self.okx_trader.get_positions()
        print(f"[DEBUG] {coin} OKX 合约持仓: {okx_positions}")
        
        for pos in okx_positions:
            if pos['coin'] == coin:
                return {'coin': coin, 'error': f'OKX 已有 {coin} 合约持仓'}
        
        print(f"[DEBUG] {coin} 无持仓，准备做空")
        
        # 检查余额是否足够
        balance = self.okx_trader.get_balance()
        if 'error' in balance:
            return {'coin': coin, 'error': f'Failed to get balance: {balance["error"]}'}
        
        available_balance = balance.get('available', 0)
        required_margin = (quantity * current_price) / leverage
        print(f"[DEBUG] {coin} 可用余额: {available_balance}, 需要保证金: {required_margin}")
        
        if available_balance < required_margin:
            return {
                'coin': coin,
                'error': f'Insufficient balance: need {required_margin:.2f}, have {available_balance:.2f}'
            }
        
        # 调用 OKX 下单（传入当前价格）
        print(f"[DEBUG] {coin} 调用 OKX 做空...")
        okx_result = self.okx_trader.place_order(
            coin,
            'sell',
            quantity,
            current_price,  # 添加当前价格参数
            leverage,
            stop_loss,
            take_profit
        )
        print(f"[DEBUG] {coin} OKX 做空结果: {okx_result}")
        
        if okx_result.get('success'):
             # 查询订单状态
            inst_id = config.OKX_SYMBOLS[coin]
            order_status = self.okx_trader.get_order_status(
                okx_result.get('ord_id'),
                inst_id
            )
            print(f"[DEBUG] 订单状态: {order_status}")
            # 同步记录到本地数据库
            self.db.update_position(
                self.model_id,
                coin,
                quantity,
                current_price,
                leverage,
                'short',
                stop_loss,
                take_profit
            )
            self.db.add_trade(
                self.model_id,
                coin,
                'sell_to_enter',
                quantity,
                current_price,
                leverage,
                'short',
                pnl=0
            )
            return {
                'coin': coin,
                'signal': 'sell_to_enter',
                'okx_order_id': okx_result.get('ord_id'),
                'message': okx_result.get('message', f'(OKX) Short {quantity:.4f} {coin}')
            }
        else:
            return {
                'coin': coin,
                'error': f'OKX order failed: {okx_result.get("error", "Unknown error")}'
            }

    def _execute_close_okx(self, coin: str, decision: Dict, market_state: Dict, portfolio: Dict, close_all: bool = True) -> Dict:
        """OKX 平仓（支持全部/部分平仓）"""
        position = None
        
        # 从 OKX 获取持仓
        positions = self.okx_trader.get_positions()
        if positions is None:
            positions = []
        
        for pos in positions:
            if pos['coin'] == coin:
                position = pos
                break
        
        if not position:
            return {'coin': coin, 'error': 'Position not found in OKX'}
        
        current_contracts = position.get('contracts', position['size'])
        current_quantity = position.get('coin_quantity', 0.0)
        ai_quantity = float(decision.get('quantity', 0))
        
        # 根据 close_all 参数决定平仓数量
        if close_all:
            close_contracts = current_contracts
            close_quantity = current_quantity
            print(f"[DEBUG] {coin} 全部平仓: {close_contracts} contracts ({close_quantity})")
        else:
            # 部分平仓
            if ai_quantity <= 0:
                return {'coin': coin, 'error': 'Invalid quantity for partial close'}
            requested_contracts = self.okx_trader.coin_quantity_to_contracts(
                coin,
                ai_quantity,
                market_state[coin]['price'],
                round_up=True
            )
            if requested_contracts > current_contracts:
                close_contracts = current_contracts  # 不能超过持仓
                close_quantity = current_quantity
            else:
                close_contracts = requested_contracts
                close_quantity = min(
                    current_quantity,
                    self.okx_trader.contracts_to_coin_quantity(
                        coin,
                        close_contracts,
                        market_state[coin]['price']
                    )
                )
            print(f"[DEBUG] {coin} 部分平仓: {close_contracts} / {current_contracts} contracts")
        
        # 调用 OKX 平仓
        okx_result = self.okx_trader.close_position(
            coin, position['side'], close_contracts
        )
        
        if okx_result.get('success'):
            current_price = market_state[coin]['price']
            if position['side'] == 'long':
                pnl = (current_price - position['avg_price']) * close_quantity
            else:
                pnl = (position['avg_price'] - current_price) * close_quantity
            
            # 更新数据库
            if close_contracts >= current_contracts:
                # 全部平仓
                self.db.close_position(self.model_id, coin, position['side'])
                self.db.add_trade(
                    self.model_id, coin, 'close_position', close_quantity,
                    current_price, position['leverage'], position['side'], pnl=pnl
                )
                message = f'(OKX) Close all {coin}, P&L: ${pnl:.2f}'
            else:
                # 部分平仓
                remaining_position = self.db.reduce_position(
                    self.model_id, coin, close_quantity, position['side']
                )
                remaining = remaining_position['quantity'] if remaining_position else 0
                self.db.add_trade(
                    self.model_id, coin, 'reduce_position', close_quantity,
                    current_price, position['leverage'], position['side'], pnl=pnl
                )
                message = f'(OKX) Reduce {coin} by {close_quantity}, remaining {remaining}, P&L: ${pnl:.2f}'
            
            return {
                'coin': coin,
                'signal': 'close_position' if close_all else 'reduce_position',
                'okx_order_id': okx_result.get('ord_id'),
                'pnl': pnl,
                'message': message
            }
        else:
            print(f"[DEBUG] {coin} 平仓失败: {okx_result.get('error', 'Unknown error')}")
            return {
                'coin': coin,
                'error': f'OKX close failed: {okx_result.get("error", "Unknown error")}'
            }

    def _execute_add_okx(self, coin: str, decision: Dict, market_state: Dict, portfolio: Dict) -> Dict:
        """OKX 部分加仓"""
        quantity = float(decision.get('quantity', 0))
        leverage = int(decision.get('leverage', 1))
        stop_loss = decision.get('stop_loss')
        take_profit = decision.get('profit_target') or decision.get('take_profit')
        current_price = market_state[coin]['price']
        
        self._validate_quantity(quantity, coin)
        self._validate_leverage(leverage)
        
        # 检查现有持仓
        positions = self.okx_trader.get_positions()
        if positions is None:
            positions = []
        
        existing_position = None
        for pos in positions:
            if pos['coin'] == coin:
                existing_position = pos
                break
        
        if not existing_position:
            return {'coin': coin, 'error': 'No existing position to add to'}
        
        # 只能对同方向加仓
        if existing_position['side'] != 'long':
            return {'coin': coin, 'error': f'{coin} 已有空仓，不能做多加仓'}
        
        print(f"[DEBUG] {coin} 已有持仓 {existing_position['size']}，准备加仓 {quantity}...")
        
        # 检查余额
        balance = self.okx_trader.get_balance()
        if 'error' in balance:
            return {'coin': coin, 'error': f'Failed to get balance: {balance["error"]}'}
        
        available_balance = balance.get('available', 0)
        required_margin = (quantity * current_price) / leverage
        
        if available_balance < required_margin:
            return {
                'coin': coin,
                'error': f'Insufficient balance: need {required_margin:.2f}, have {available_balance:.2f}'
            }
        
        # 调用 OKX 加仓
        okx_result = self.okx_trader.place_order(
            coin, 'buy', quantity, current_price, leverage, stop_loss, take_profit
        )
        
        if okx_result.get('success'):
            position_state = self.db.upsert_position_delta(
                self.model_id, coin, quantity, current_price, leverage, 'long',
                stop_loss, take_profit
            )
            self.db.add_trade(
                self.model_id, coin, 'increase_position', quantity, current_price,
                leverage, 'long', pnl=0
            )
            return {
                'coin': coin,
                'signal': 'increase_position',
                'okx_order_id': okx_result.get('ord_id'),
                'message': f'(OKX) Add to long {coin} +{quantity:.4f}, total {position_state["quantity"]}'
            }
        else:
            return {
                'coin': coin,
                'error': f'OKX order failed: {okx_result.get("error", "Unknown error")}'
            }

    def _record_okx_account_value(self, portfolio: Dict):
        """记录 OKX 账户价值"""
        balance = self.okx_trader.get_balance()
        self.db.record_account_value(
            self.model_id,
            portfolio['total_value'],
            balance.get('available', portfolio['cash']),
            portfolio['positions_value']
        )

    # ============ 通用方法（模拟和OKX共享） ============
    def _check_stop_loss_take_profit(self, portfolio: Dict, current_prices: Dict) -> list:
        """
        检查止盈止损条件，自动平仓（模拟模式）
        """
        results = []
        for position in portfolio['positions']:
            coin = position['coin']
            if coin not in current_prices:
                continue
            
            current_price = current_prices[coin]
            stop_loss = position.get('stop_loss')
            take_profit = position.get('take_profit')
            side = position['side']
            
            should_close = False
            reason = ''
            
            # 检查止损
            if stop_loss:
                if side == 'long' and current_price <= stop_loss:
                    should_close = True
                    reason = f'止损触发 (${current_price:.2f} <= ${stop_loss:.2f})'
                elif side == 'short' and current_price >= stop_loss:
                    should_close = True
                    reason = f'止损触发 (${current_price:.2f} >= ${stop_loss:.2f})'
            
            # 检查止盈
            if take_profit and not should_close:
                if side == 'long' and current_price >= take_profit:
                    should_close = True
                    reason = f'止盈触发 (${current_price:.2f} >= ${take_profit:.2f})'
                elif side == 'short' and current_price <= take_profit:
                    should_close = True
                    reason = f'止盈触发 (${current_price:.2f} <= ${take_profit:.2f})'
            
            if should_close:
                # 执行平仓
                quantity = position['quantity']
                entry_price = position['avg_price']
                if side == 'long':
                    pnl = (current_price - entry_price) * quantity * position['leverage']
                else:
                    pnl = (entry_price - current_price) * quantity * position['leverage']
                
                self.db.close_position(self.model_id, coin, side)
                self.db.add_trade(
                    self.model_id, coin, 'auto_close',
                    quantity, current_price,
                    position['leverage'], side, pnl=pnl
                )
                
                results.append({
                    'coin': coin,
                    'signal': 'auto_close',
                    'reason': reason,
                    'quantity': quantity,
                    'price': current_price,
                    'pnl': pnl,
                    'message': f'{coin} {reason}, P&L: ${pnl:.2f}'
                })
        
        return results

    def _get_market_state(self) -> Dict:
        market_state = {}
        prices = self.market_fetcher.get_current_prices(self.coins)
        for coin in self.coins:
            if coin in prices:
                market_state[coin] = prices[coin].copy()
                indicators = self.market_fetcher.calculate_technical_indicators(coin)
                market_state[coin]['indicators'] = indicators
        return market_state

    def _build_account_info(self, portfolio: Dict) -> Dict:
        model = self.db.get_model(self.model_id)
        initial_capital = model['initial_capital']
        total_value = portfolio['total_value']
        total_return = ((total_value - initial_capital) / initial_capital) * 100
        return {
            'current_time': get_current_beijing_time_str(),
            'total_return': total_return,
            'initial_capital': initial_capital
        }

    def _format_prompt(self, market_state: Dict, portfolio: Dict, account_info: Dict) -> str:
        return f"Market State: {len(market_state)} coins, Portfolio: {len(portfolio['positions'])} positions"

    def _execute_decisions(self, decisions: Dict, market_state: Dict, portfolio: Dict) -> list:
        """执行交易决策（模拟模式）"""
        results = []
        for coin, decision in decisions.items():
            if coin not in self.coins:
                continue
            
            signal = decision.get('signal', '').lower()
            try:
                if signal == 'buy_to_enter':
                    result = self._execute_buy(coin, decision, market_state, portfolio)
                elif signal == 'sell_to_enter':
                    result = self._execute_sell(coin, decision, market_state, portfolio)
                elif signal in {'close_position', 'sell_to_close', 'buy_to_close'}:
                    result = self._execute_close(coin, decision, market_state, portfolio)
                elif signal == 'reduce_position':
                    result = self._execute_reduce(coin, decision, market_state, portfolio)
                elif signal == 'increase_position':
                    result = self._execute_increase(coin, decision, market_state, portfolio)
                elif signal == 'hold':
                    result = {'coin': coin, 'signal': 'hold', 'message': 'Hold position'}
                else:
                    result = {'coin': coin, 'error': f'Unknown signal: {signal}'}
                results.append(result)
            except Exception as e:
                results.append({'coin': coin, 'error': str(e)})
        return results

    def _execute_buy(self, coin: str, decision: Dict, market_state: Dict,
                    portfolio: Dict) -> Dict:
        try:
            quantity = float(decision.get('quantity', 0))
            leverage = int(decision.get('leverage', 1))
            price = market_state[coin]['price']

            # 输入验证
            self._validate_quantity(quantity, coin)
            self._validate_leverage(leverage)

            # OKX模式：调用OKX API开多仓
            if self.trading_mode == 'okx_demo' and okx_trader:
                result = okx_trader.place_order(
                    coin=coin,
                    side='buy',  # 开多仓
                    quantity=quantity,
                    price=price,
                    leverage=leverage
                )

                if result.get('success'):
                    # 获取止盈止损价格
                    stop_loss = decision.get('stop_loss')
                    take_profit = decision.get('profit_target') or decision.get('take_profit')

                    # 本地数据库记录止盈止损设置
                    self.db.update_position(
                        self.model_id, coin, quantity, price, leverage, 'long',
                        stop_loss=stop_loss, take_profit=take_profit
                    )
                    self.db.add_trade(
                        self.model_id, coin, 'buy_to_enter', quantity,
                        price, leverage, 'long', pnl=0
                    )

                    return {
                        'coin': coin,
                        'signal': 'buy_to_enter',
                        'quantity': quantity,
                        'price': price,
                        'leverage': leverage,
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,
                        'message': result.get('message', f'Long {quantity:.4f} {coin} @ ${price:.2f} (OKX)')
                    }
                else:
                    return {'coin': coin, 'error': result.get('error', 'OKX order failed')}
            else:
                # 模拟模式：本地数据库开仓
                required_margin = (quantity * price) / leverage 
                if required_margin > portfolio['cash']:
                    return {'coin': coin, 'error': 'Insufficient cash'}

                # 获取止盈止损价格
                stop_loss = decision.get('stop_loss')
                take_profit = decision.get('profit_target') or decision.get('take_profit')

                position_state = self.db.upsert_position_delta(
                    self.model_id, coin, quantity, price, leverage, 'long',
                    stop_loss=stop_loss, take_profit=take_profit
                )
                self.db.add_trade(
                    self.model_id, coin, 'buy_to_enter', quantity,
                    price, leverage, 'long', pnl=0
                )

                return {
                    'coin': coin,
                    'signal': 'buy_to_enter',
                    'quantity': position_state['quantity'],
                    'price': price,
                    'leverage': leverage,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'message': f'Long {quantity:.4f} {coin} @ ${price:.2f}'
                }

        except (ValueError, TypeError) as e:
            return {'coin': coin, 'error': f'Validation failed: {str(e)}'}

    def _execute_sell(self, coin: str, decision: Dict, market_state: Dict,
                     portfolio: Dict) -> Dict:
        try:
            quantity = float(decision.get('quantity', 0))
            leverage = int(decision.get('leverage', 1))
            price = market_state[coin]['price']

            # 输入验证
            self._validate_quantity(quantity, coin)
            self._validate_leverage(leverage)

            # OKX模式：调用OKX API开空仓
            if self.trading_mode == 'okx_demo' and okx_trader:
                result = okx_trader.place_order(
                    coin=coin,
                    side='sell',  # 开空仓
                    quantity=quantity,
                    price=price,
                    leverage=leverage
                )

                if result.get('success'):
                    # 获取止盈止损价格
                    stop_loss = decision.get('stop_loss')
                    take_profit = decision.get('profit_target') or decision.get('take_profit')

                    # 本地数据库记录止盈止损设置
                    self.db.update_position(
                        self.model_id, coin, quantity, price, leverage, 'short',
                        stop_loss=stop_loss, take_profit=take_profit
                    )
                    self.db.add_trade(
                        self.model_id, coin, 'sell_to_enter', quantity,
                        price, leverage, 'short', pnl=0
                    )

                    return {
                        'coin': coin,
                        'signal': 'sell_to_enter',
                        'quantity': quantity,
                        'price': price,
                        'leverage': leverage,
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,
                        'message': result.get('message', f'Short {quantity:.4f} {coin} @ ${price:.2f} (OKX)')
                    }
                else:
                    return {'coin': coin, 'error': result.get('error', 'OKX order failed')}
            else:
                # 模拟模式：本地数据库开仓
                required_margin = (quantity * price) / leverage
                if required_margin > portfolio['cash']:
                    return {'coin': coin, 'error': 'Insufficient cash'}

                # 获取止盈止损价格
                stop_loss = decision.get('stop_loss')
                take_profit = decision.get('profit_target') or decision.get('take_profit')

                position_state = self.db.upsert_position_delta(
                    self.model_id, coin, quantity, price, leverage, 'short',
                    stop_loss=stop_loss, take_profit=take_profit
                )
                self.db.add_trade(
                    self.model_id, coin, 'sell_to_enter', quantity,
                    price, leverage, 'short', pnl=0
                )

                return {
                    'coin': coin,
                    'signal': 'sell_to_enter',
                    'quantity': position_state['quantity'],
                    'price': price,
                    'leverage': leverage,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'message': f'Short {quantity:.4f} {coin} @ ${price:.2f}'
                }

        except (ValueError, TypeError) as e:
            return {'coin': coin, 'error': f'Validation failed: {str(e)}'}

    def _execute_close(self, coin: str, decision: Dict, market_state: Dict, portfolio: Dict) -> Dict:
        """模拟平仓"""
        position = None
        for pos in portfolio['positions']:
            if pos['coin'] == coin:
                position = pos
                break
        
        if not position:
            return {'coin': coin, 'error': 'Position not found'}
        
        current_price = market_state[coin]['price']
        entry_price = position['avg_price']
        quantity = position['quantity']
        side = position['side']
        
        if side == 'long':
            pnl = (current_price - entry_price) * quantity * position['leverage']
        else:
            pnl = (entry_price - current_price) * quantity * position['leverage']
        
        self.db.close_position(self.model_id, coin, side)
        self.db.add_trade(
            self.model_id, coin, 'close_position',
            quantity, current_price,
            position['leverage'], side, pnl=pnl
        )
        
        return {
            'coin': coin,
            'signal': 'close_position',
            'quantity': quantity,
            'price': current_price,
            'pnl': pnl,
            'message': f'Close {coin}, P&L: ${pnl:.2f}'
        }

    def _execute_reduce(self, coin: str, decision: Dict, market_state: Dict, portfolio: Dict) -> Dict:
        """模拟部分平仓"""
        position = None
        for pos in portfolio['positions']:
            if pos['coin'] == coin:
                position = pos
                break

        if not position:
            return {'coin': coin, 'error': 'Position not found'}

        reduce_quantity = float(decision.get('quantity', 0))
        if reduce_quantity <= 0:
            return {'coin': coin, 'error': 'Invalid quantity for partial close'}

        current_price = market_state[coin]['price']
        close_quantity = min(position['quantity'], reduce_quantity)
        entry_price = position['avg_price']
        side = position['side']

        if side == 'long':
            pnl = (current_price - entry_price) * close_quantity * position['leverage']
        else:
            pnl = (entry_price - current_price) * close_quantity * position['leverage']

        remaining_position = self.db.reduce_position(self.model_id, coin, close_quantity, side)
        remaining = remaining_position['quantity'] if remaining_position else 0
        self.db.add_trade(
            self.model_id, coin, 'reduce_position',
            close_quantity, current_price,
            position['leverage'], side, pnl=pnl
        )

        return {
            'coin': coin,
            'signal': 'reduce_position',
            'quantity': close_quantity,
            'remaining_quantity': remaining,
            'price': current_price,
            'pnl': pnl,
            'message': f'Reduce {coin} by {close_quantity:.4f}, remaining {remaining:.4f}, P&L: ${pnl:.2f}'
        }

    def _execute_increase(self, coin: str, decision: Dict, market_state: Dict, portfolio: Dict) -> Dict:
        """模拟部分加仓（当前仅支持已有多仓加仓）"""
        position = None
        for pos in portfolio['positions']:
            if pos['coin'] == coin:
                position = pos
                break

        if not position:
            return {'coin': coin, 'error': 'No existing position to add to'}
        if position['side'] != 'long':
            return {'coin': coin, 'error': 'Increase position is only supported for existing long positions'}

        quantity = float(decision.get('quantity', 0))
        leverage = int(decision.get('leverage', position['leverage']))
        price = market_state[coin]['price']

        self._validate_quantity(quantity, coin)
        self._validate_leverage(leverage)

        required_margin = (quantity * price) / leverage
        if required_margin > portfolio['cash']:
            return {'coin': coin, 'error': 'Insufficient cash'}

        stop_loss = decision.get('stop_loss')
        take_profit = decision.get('profit_target') or decision.get('take_profit')
        position_state = self.db.upsert_position_delta(
            self.model_id, coin, quantity, price, leverage, 'long',
            stop_loss=stop_loss, take_profit=take_profit
        )
        self.db.add_trade(
            self.model_id, coin, 'increase_position', quantity,
            price, leverage, 'long', pnl=0
        )

        return {
            'coin': coin,
            'signal': 'increase_position',
            'quantity': quantity,
            'total_quantity': position_state['quantity'],
            'price': price,
            'leverage': leverage,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'message': f'Add to long {coin} +{quantity:.4f}, total {position_state["quantity"]:.4f}'
        }
