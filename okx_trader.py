"""
OKX 模拟盘交易模块
"""
from okx import Account, Trade, MarketData
import config
import time

class OKXTrader:
    """OKX 模拟盘交易器"""

    def __init__(self):
        # 初始化 OKX 客户端
        self.account_api = Account.AccountAPI(
            config.OKX_API_KEY,
            config.OKX_API_SECRET,
            config.OKX_PASSPHRASE,
            False,  # debug=False
            config.OKX_FLAG  # '1'=模拟盘
        )
        self.trade_api = Trade.TradeAPI(
            config.OKX_API_KEY,
            config.OKX_API_SECRET,
            config.OKX_PASSPHRASE,
            False,
            config.OKX_FLAG
        )
        self.market_api = MarketData.MarketAPI(flag=config.OKX_FLAG)
        
        # 设置超时（如果 SDK 支持）
        try:
            # 尝试设置超时
            if hasattr(self.account_api, 'client'):
                # 检查是否可以设置超时
                pass
        except Exception as e:
            print(f"[INFO] 设置超时失败: {e}")

    def get_balance(self) -> dict:
        """
        获取账户余额
        添加重试机制，提高 API 调用稳定性
        """
        max_retries = 3
        retry_delay = 2  # 秒
        #设置延迟
        for attempt in range(max_retries):
            try:
                result = self.account_api.get_account_balance()
                if result.get('code') != '0':
                    error_msg = result.get('msg')
                    print(f"[ERROR] API 返回错误: {error_msg}")
                    if attempt < max_retries - 1 and 'temporarily unavailable' in error_msg.lower():
                        print(f"[ERROR] 暂时不可用，{retry_delay}秒后重试...")
                        continue
                    return {'error': error_msg}

                # 提取所有币种的余额
                details = result.get('data', [])[0].get('details', [])
                balances = {}
                total = 0
                available = 0
                frozen = 0

                for item in details:
                    ccy = item.get('ccy')
                    if not ccy:
                        continue

                    cash_bal = float(item.get('cashBal', 0))
                    frozen_bal = float(item.get('frozenBal', 0))
                    total_bal = cash_bal + frozen_bal

                    # 只记录有余额的币种
                    if cash_bal > 0 or frozen_bal > 0:
                        balances[ccy] = {
                            'total': total_bal,
                            'available': cash_bal,
                            'frozen': frozen_bal
                        }
                    # 累计 USDT 余额（假设所有币种都已转换为 USDT）
                    if ccy == 'USDT':
                        total += total_bal
                        available += cash_bal
                        frozen += frozen_bal
                # 保持向后兼容，同时返回新的结构
                result = {
                    'total': total,
                    'available': available,
                    'frozen': frozen,
                    'balances': balances,
                    'details': details  # 保留原始数据用于调试
                }
                # 打印格式化的余额信息
                print(f"[DEBUG] 获取余额成功: 总余额={total}, 币种数={len(balances)}")
                for ccy, balance in balances.items():
                    total_balance = balance['total'] - balance['frozen']
                    available_balance =total_balance-balance['frozen']
                    print(f"[DEBUG] 币种：{ccy};总余额：{total_balance};已被占用余额：{balance['frozen']};可用余额：{available_balance}")
                return result
            except Exception as e:
                error_msg = str(e)
                print(f"[ERROR] 获取余额异常: {error_msg}")
                if attempt < max_retries - 1 and 'connection' in error_msg.lower():
                    print(f"[ERROR] 网络异常，{retry_delay}秒后重试...")
                    continue
                return {'error': error_msg}

    def get_positions(self) -> list:
        """获取持仓信息"""
        try:
            # 查询账户配置
            try:
                config_result = self.account_api.get_account_config()
                if config_result.get('code') == '0' and config_result.get('data'):
                    config_data = config_result.get('data', [{}])[0]
                    print(f"[DEBUG] 账户配置: posMode={config_data.get('posMode')}, acctLv={config_data.get('acctLv')}")
            except Exception as e:
                print(f"[ERROR] 查询账户配置失败: {e}")

            # 查询所有支持的币种
            all_positions = []
            # 从配置文件获取交易对列表
            inst_ids = list(config.OKX_SYMBOLS.values())
            for inst_id in inst_ids:
                inst_result = self.account_api.get_positions(instId=inst_id)
                if inst_result.get('code') == '0':
                    data = inst_result.get('data', [])
                    if data:
                        for pos in data:
                            if float(pos.get('pos', 0)) != 0:
                                all_positions.append({
                                    'coin': pos.get('instId').split('-')[0],
                                    'inst_id': pos.get('instId'),
                                    'side': pos.get('posSide'),
                                    'size': float(pos.get('pos', 0)),
                                    'avg_price': float(pos.get('avgPx', 0)),
                                    'leverage': int(pos.get('lever', 1)),
                                    'unrealized_pnl': float(pos.get('upl', 0)),
                                    'mgnMode': pos.get('mgnMode', ''),
                                    'posId': pos.get('posId', '')
                                })
            # 打印持仓信息
            for idx, pos in enumerate(all_positions):
                print(f"[DEBUG] 最终持仓 #{idx}: instId={pos['inst_id']}, pos={pos['size']}")
            return all_positions
        except Exception as e:
            print(f"[ERROR] 获取持仓信息失败: {e}")
            return []

    def get_order_status(self, ord_id: str, inst_id: str) -> dict:
        """查询订单状态"""
        try:
            result = self.trade_api.get_order(
                instId=inst_id,
                ordId=ord_id
            )
            print(f"[DEBUG] 查询订单状态 ord_id={ord_id}, inst_id={inst_id}")
            #print(f"[DEBUG] 订单状态返回: {result}")
            return result
        except Exception as e:
            print(f"[ERROR] Get order status failed: {e}")
            return {'error': str(e)}

    def place_order(self, coin: str, side: str, quantity: float, price: float, leverage: int = 1, stop_loss: float = None, take_profit: float = None) -> dict:
        """
        下单
        side: 'buy' (做多) 或 'sell' (做空)
        quantity: 币数量（如 BTC 0.11 个）
        price: 当前价格（USDT）
        """
        try:
            inst_id = config.OKX_SYMBOLS.get(coin)
            if not inst_id:
                return {'error': f'Unsupported coin: {coin}'}

            print(f"[DEBUG] OKX下单参数: coin={coin}, inst_id={inst_id}, side={side}, quantity={quantity}, price={price}, leverage={leverage}")

            # OKX 永续合约每张面值（单位：USDT）
            contract_face_values = {
                'BTC': 100,
                'ETH': 10,
                'SOL': 10,
                'BNB': 1,
                'XRP': 1000,
                'DOGE': 10
            }
            face_value = contract_face_values.get(coin, 1)  # 默认 1 USDT/张

            # 计算合约张数：张数 = (币数量 × 当前价格) / 每张面值
            contract_value = quantity * price  # 订单总价值（USDT）
            contract_size = int(contract_value / face_value)  # 向下取整得到张数

            print(f"[DEBUG] 币数量: {quantity}, 当前价格: {price}, 订单价值: {contract_value} USDT")
            print(f"[DEBUG] 每张面值: {face_value} USDT, 计算得张数: {contract_size} 张")

            # 确保至少下单 1 张合约，满足 OKX 最小订单要求
            if contract_size < 1:
                contract_size = 1
                print(f"[DEBUG] 订单价值低于最小要求，自动调整为 1 张合约")

            # 设置杠杆
            if leverage > 1:
                set_lever_result = self.account_api.set_leverage(
                    instId=inst_id,
                    lever=str(leverage),
                    mgnMode='cross',  # 全仓
                    posSide='long' if side == 'buy' else 'short',  # 开平仓模式
                    ccy='USDT'
                )
                print(f"[DEBUG] 设置杠杆结果: {set_lever_result}")
                if set_lever_result.get('code') != '0':
                    print(f"[ERROR] Set leverage failed: {set_lever_result.get('msg')}")

            # 下单参数
            params = {
                'instId': inst_id,
                'tdMode': 'cross',  # 全仓
                'side': 'buy' if side == 'buy' else 'sell',
                'posSide': 'long' if side == 'buy' else 'short',  # 开平仓模式
                'ordType': 'market',  # 市价单
                'sz': str(contract_size),
                'ccy': 'USDT'
            }
            print(f"[DEBUG] 下单请求参数: {params}")

            result = self.trade_api.place_order(**params)
            print(f"[DEBUG] 下单返回结果: {result}")

            if result.get('code') != '0':
                print(f"[ERROR] 下单失败: code={result.get('code')}, msg={result.get('msg')}")
                return {
                    'success': False,
                    'error': result.get('msg')
                }

            return {
                'success': True,
                'ord_id': result.get('data', [{}])[0].get('ordId'),
                'message': f'Order placed: {side} {quantity} {coin} ({contract_size} contracts)'
            }
        except Exception as e:
            print(f"[ERROR] 下单异常: {e}")
            return {'success': False, 'error': str(e)}

    def close_position(self, coin: str = None, side: str = None, quantity: float = None, instId: str = None) -> dict:
        """
        平仓
        支持两种调用方式：
        1. close_position(coin, side, quantity) - 通过币种和数量平仓
        2. close_position(instId=instId) - 通过交易对平仓（自动查询持仓数量）
        """
        try:
            # 方式1：通过 coin 平仓
            if coin:
                inst_id = config.OKX_SYMBOLS.get(coin)
                if not inst_id:
                    return {'error': f'Unsupported coin: {coin}'}
                if not side:
                    return {'error': 'Side is required'}
                if quantity is None:
                    return {'error': 'Quantity is required'}

                # 平仓就是反向下单
                close_side = 'sell' if side == 'long' else 'buy'
                sz = str(quantity)
                params = {
                    'instId': inst_id,
                    'tdMode': 'cross',
                    'side': close_side,
                    'posSide': 'long' if side == 'long' else 'short',
                    'ordType': 'market',
                    'sz': sz
                }
                result = self.trade_api.place_order(**params)
                if result.get('code') != '0':
                    return {'success': False, 'error': result.get('msg')}
                return {
                    'success': True,
                    'ord_id': result.get('data', [{}])[0].get('ordId'),
                    'message': f'Position closed: {coin}'
                }

            # 方式2：通过 instId 平仓（自动查询持仓数量）
            elif instId:
                # 查询当前持仓
                inst_result = self.account_api.get_positions(instId=instId)
                if inst_result.get('code') != '0':
                    return {'success': False, 'error': inst_result.get('msg')}

                data = inst_result.get('data', [])
                for pos in data:
                    if float(pos.get('pos', 0)) != 0:
                        pos_side = pos.get('posSide')  # long 或 short
                        size = abs(int(pos.get('pos')))

                        # 平仓方向与持仓相反
                        close_side = 'sell' if pos_side == 'long' else 'buy'

                        params = {
                            'instId': instId,
                            'tdMode': 'cross',
                            'side': close_side,
                            'posSide': pos_side,  # 平仓时 posSide 保持与持仓一致
                            'ordType': 'market',
                            'sz': str(size)
                        }
                        result = self.trade_api.place_order(**params)
                        if result.get('code') != '0':
                            return {'success': False, 'error': result.get('msg')}
                        return {
                            'success': True,
                            'ord_id': result.get('data', [{}])[0].get('ordId'),
                            'message': f'Position closed: {instId}'
                        }

                return {'success': False, 'error': f'No position found for {instId}'}

            else:
                return {'success': False, 'error': 'Either coin or instId is required'}

        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_open_orders(self) -> list:
        """获取挂单"""
        try:
            result = self.trade_api.get_order_list(instType='SWAP')
            if result.get('code') != '0':
                return []
            return result.get('data', [])
        except Exception as e:
            print(f"[ERROR] Get orders failed: {e}")
            return []

    # 添加别名方法，兼容 trading_engine.py 的调用
    def get_account_balance(self):
        """获取账户余额（别名方法）"""
        return self.get_balance()

    def get_order_history(self, instType='SWAP', limit=100):
        """获取订单历史（别名方法）"""
        return self.get_open_orders()

    def get_account_config(self) -> dict:
        """查询账户配置"""
        try:
            result = self.account_api.get_account_config()
            print(f"[DEBUG] 账户配置: {result}")
            if result.get('code') == '0' and result.get('data'):
                config_data = result.get('data', [{}])[0]
                return {
                    'success': True,
                    'account_level': config_data.get('acctLv', ''),
                    'position_mode': config_data.get('posMode', ''),
                    'auto_loan': config_data.get('autoLoan', ''),
                }
            else:
                return {
                    'success': False,
                    'error': result.get('msg', 'Unknown error')
                }
        except Exception as e:
            print(f"[ERROR] Get account config failed: {e}")
            return {'success': False, 'error': str(e)}