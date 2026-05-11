"""OKX API 封装，负责余额、持仓、下单以及原生止盈止损管理。"""
import copy
import json
import threading
import time
import uuid

import config
import httpx
import requests
from datetime import datetime, timezone
from okx import Account, MarketData, Trade
from okx import consts as okx_consts
from okx import utils as okx_utils
from okx.okxclient import OkxClient as _OkxClient


def _patch_okx_client_request() -> None:
    """Ensure OKX SDK responses are closed so the HTTP pool does not get exhausted."""
    if getattr(_OkxClient, '_codex_safe_request_patched', False):
        return

    def _safe_request(self, method, request_path, params):
        if method == okx_consts.GET:
            request_path = request_path + okx_utils.parse_params_to_str(params)
        timestamp = okx_utils.get_timestamp()
        if self.use_server_time:
            timestamp = self._get_timestamp()
        body = json.dumps(params) if method == okx_consts.POST else ""
        if self.API_KEY != '-1':
            sign = okx_utils.sign(
                okx_utils.pre_hash(timestamp, method, request_path, str(body), self.debug),
                self.API_SECRET_KEY
            )
            header = okx_utils.get_header(self.API_KEY, sign, timestamp, self.PASSPHRASE, self.flag, self.debug)
        else:
            header = okx_utils.get_header_no_sign(self.flag, self.debug)

        response = None
        try:
            if self.debug is True:
                from loguru import logger
                logger.debug(f'domain: {self.domain}')
                logger.debug(f'url: {request_path}')
                logger.debug(f'body:{body}')

            if method == okx_consts.GET:
                response = self.get(request_path, headers=header)
            elif method == okx_consts.POST:
                response = self.post(request_path, data=body, headers=header)
            else:
                raise ValueError(f'Unsupported method: {method}')

            return response.json()
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    def _safe_get_timestamp(self):
        request_path = okx_consts.API_URL + okx_consts.SERVER_TIMESTAMP_URL
        response = None
        try:
            response = self.get(request_path)
            if response.status_code == 200:
                ts = datetime.fromtimestamp(int(response.json()['data'][0]['ts']) / 1000.0, tz=timezone.utc)
                return ts.isoformat(timespec='milliseconds').replace('+00:00', 'Z')
            return ""
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    _OkxClient._request = _safe_request
    _OkxClient._get_timestamp = _safe_get_timestamp
    _OkxClient._codex_safe_request_patched = True


_patch_okx_client_request()

class OKXTrader:
    """OKX 模拟盘交易器"""

    BALANCE_CACHE_TTL_SECONDS = 20
    POSITIONS_CACHE_TTL_SECONDS = 15
    STALE_PRIVATE_DATA_MAX_AGE_SECONDS = 180

    TRANSIENT_ERROR_KEYWORDS = (
        'temporarily unavailable',
        'connection',
        'timed out',
        'pooltimeout',
        '10035',
        'Expecting value',
        'Remote end closed connection',
        'Connection aborted',
        'Connection reset',
        'SSLEOFError',
        'SSL:',
        'unexpected eof',
        'remote host closed'
    )

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
        self.account_api.timeout = 10.0
        self.trade_api.timeout = 10.0
        self.market_api.timeout = 10.0
        self._tune_http_client(self.account_api)
        self._tune_http_client(self.trade_api)
        self._tune_http_client(self.market_api)
        self._instrument_cache = {}
        self._debug_timestamps = {}
        self._runtime_cache = {}
        self._cache_lock = threading.Lock()
        self._api_lock = threading.Lock()
        
        # 设置超时（如果 SDK 支持）
        try:
            # 尝试设置超时
            if hasattr(self.account_api, 'client'):
                # 检查是否可以设置超时
                pass
        except Exception as e:
            print(f"[INFO] 设置超时失败: {e}")

    def _tune_http_client(self, client, max_connections: int = 20) -> None:
        """Tune the SDK httpx client to reduce pool exhaustion and flaky keepalive reuse."""
        try:
            client.timeout = httpx.Timeout(30.0, connect=10.0, read=20.0, write=20.0, pool=30.0)
        except Exception:
            pass

        try:
            transport = getattr(client, '_transport', None)
            pool = getattr(transport, '_pool', None) if transport is not None else None
            if pool is None:
                return

            if hasattr(pool, '_http2'):
                pool._http2 = False
            if hasattr(pool, '_http1'):
                pool._http1 = True
            pool._max_connections = max(int(getattr(pool, '_max_connections', 0) or 0), max_connections)
            pool._max_keepalive_connections = max(
                int(getattr(pool, '_max_keepalive_connections', 0) or 0),
                max(5, max_connections // 2)
            )
            if hasattr(pool, '_keepalive_expiry'):
                pool._keepalive_expiry = min(float(getattr(pool, '_keepalive_expiry', 5.0) or 5.0), 5.0)
        except Exception as e:
            print(f"[WARN] 调整 OKX HTTP 客户端失败: {e}")

    def _debug_log(self, key: str, message: str, interval_seconds: int = 180) -> None:
        """调试日志记录器，避免频繁打印"""
        now = time.time()
        last_logged = self._debug_timestamps.get(key, 0)
        if now - last_logged >= interval_seconds:
            self._debug_timestamps[key] = now
            print(message)

    def _format_exception(self, exc: Exception) -> str:
        message = str(exc).strip()
        if message:
            return message
        return repr(exc) or exc.__class__.__name__

    def _get_cached_runtime_value(self, key: str, ttl_seconds: int, force_refresh: bool = False):
        if force_refresh:
            return None

        with self._cache_lock:
            cached = self._runtime_cache.get(key)
            if not cached:
                return None

            age = time.time() - cached['timestamp']
            if age > ttl_seconds:
                return None

            return copy.deepcopy(cached['value'])

    def _get_stale_runtime_value(self, key: str):
        with self._cache_lock:
            cached = self._runtime_cache.get(key)
            if not cached:
                return None, None

            age = time.time() - cached['timestamp']
            if age > self.STALE_PRIVATE_DATA_MAX_AGE_SECONDS:
                return None, None

            return copy.deepcopy(cached['value']), age

    def _store_cached_runtime_value(self, key: str, value) -> None:
        with self._cache_lock:
            self._runtime_cache[key] = {
                'timestamp': time.time(),
                'value': copy.deepcopy(value)
            }

    def _invalidate_private_runtime_cache(self, *keys: str) -> None:
        keys = keys or ('balance', 'positions')
        with self._cache_lock:
            for key in keys:
                self._runtime_cache.pop(key, None)

    def get_contract_face_value(self, coin: str) -> float:
        """获取合约面值，单位为基础币"""
        inst_id = config.OKX_SYMBOLS.get(coin)
        if not inst_id:
            return 0.0
        instrument = self.get_instrument_spec(inst_id)
        return float(instrument.get('ctVal', 0))

    def get_instrument_spec(self, inst_id: str) -> dict:
        """获取合约规格"""
        if inst_id in self._instrument_cache:
            return self._instrument_cache[inst_id]

        response = requests.get(
            f'{config.OKX_API_URL}/public/instruments',
            params={'instType': 'SWAP', 'instId': inst_id},
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        if result.get('code') != '0' or not result.get('data'):
            raise ValueError(f'Failed to load instrument spec for {inst_id}: {result.get("msg", "unknown error")}')

        instrument = result['data'][0]
        self._instrument_cache[inst_id] = instrument
        return instrument

    def normalize_contracts(self, coin: str, contracts: float, round_up: bool = False) -> float:
        """归一化合约数量，符合OKX交易规则"""
        inst_id = config.OKX_SYMBOLS.get(coin)
        if not inst_id:
            raise ValueError(f'Unsupported coin: {coin}')

        instrument = self.get_instrument_spec(inst_id)
        lot_size = float(instrument.get('lotSz', 1) or 1)
        min_size = float(instrument.get('minSz', lot_size) or lot_size)
        contracts = float(contracts)

        if lot_size <= 0:
            raise ValueError(f'Invalid lot size for {coin}: {lot_size}')

        import math
        steps = contracts / lot_size
        normalized_steps = math.ceil(steps) if round_up else math.floor(steps)
        normalized = normalized_steps * lot_size

        if contracts > 0 and normalized < min_size:
            normalized = min_size

        precision = max(0, len(str(lot_size).split('.')[-1].rstrip('0'))) if '.' in str(lot_size) else 0
        return round(normalized, precision)

    def coin_quantity_to_contracts(self, coin: str, quantity: float, price: float, round_up: bool = False) -> int:
        """将基础币数量转换为合约数量"""
        face_value = self.get_contract_face_value(coin)
        if face_value <= 0:
            raise ValueError(f'Invalid contract value for {coin}: {face_value}')

        raw_contracts = float(quantity) / face_value
        return self.normalize_contracts(coin, raw_contracts, round_up=round_up)

    def contracts_to_coin_quantity(self, coin: str, contracts: float, price: float) -> float:
        """将合约数量转换为基础币数量"""
        return float(contracts) * self.get_contract_face_value(coin)

    def contracts_to_notional_usdt(self, coin: str, contracts: float, price: float) -> float:
        """将合约数量转换为USDT价值"""
        return self.contracts_to_coin_quantity(coin, contracts, price) * float(price)

    def get_balance(self, force_refresh: bool = False, allow_stale: bool = False) -> dict:
        """
        获取账户余额
        添加重试机制，提高 API 调用稳定性
        """
        cached_value = self._get_cached_runtime_value(
            'balance',
            self.BALANCE_CACHE_TTL_SECONDS,
            force_refresh=force_refresh
        )
        if cached_value is not None:
            return cached_value

        max_retries = 3
        last_error = ''
        retry_delay = 2  # 秒
        #设置延迟
        with self._api_lock:
            for attempt in range(max_retries):
                try:
                    result = self.account_api.get_account_balance()
                    if not isinstance(result, dict):
                        raise ValueError(f'余额响应类型异常: {type(result)}')
                    if result.get('code') != '0':
                        api_code = result.get('code')
                        error_msg = (result.get('msg') or '').strip() or f'OKX API error code={api_code}'
                        last_error = error_msg
                        print(f"[ERROR] API 返回错误: {error_msg}")
                        if attempt < max_retries - 1 and error_msg and any(k.lower() in error_msg.lower() for k in self.TRANSIENT_ERROR_KEYWORDS):
                            print(f"[ERROR] 暂时不可用，{retry_delay}秒后重试...")
                            time.sleep(retry_delay)
                            continue
                        break

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
                    self._debug_log('balance_summary', f"[DEBUG] 获取余额成功: 总余额={total}, 币种数={len(balances)}")
                    self._store_cached_runtime_value('balance', result)
                    for ccy, balance in balances.items():
                        total_balance = balance['total'] - balance['frozen']
                        available_balance =total_balance-balance['frozen']
                        self._debug_log(
                            f'balance_detail:{ccy}',
                            f"[DEBUG] 币种：{ccy};总余额：{total_balance};已被占用余额：{balance['frozen']};可用余额：{available_balance}"
                        )
                    return result
                except Exception as e:
                    error_msg = self._format_exception(e)
                    last_error = error_msg
                    print(f"[ERROR] 获取余额异常: {error_msg}")
                    if attempt < max_retries - 1 and any(k.lower() in error_msg.lower() for k in self.TRANSIENT_ERROR_KEYWORDS):
                        print(f"[ERROR] 网络异常，{retry_delay}秒后重试...")
                        time.sleep(retry_delay)
                        continue
                    break

        if allow_stale:
            stale_value, age = self._get_stale_runtime_value('balance')
            if stale_value is not None:
                stale_value['_stale'] = True
                stale_value['_stale_reason'] = last_error
                stale_value['_stale_age_seconds'] = round(age, 1)
                self._debug_log(
                    'balance_stale_fallback',
                    f"[WARN] OKX balance fallback to cached snapshot from {age:.1f}s ago: {last_error}",
                    interval_seconds=30
                )
                return stale_value

        return {'error': last_error or 'Unknown balance error'}

    def get_positions(self, force_refresh: bool = False, allow_stale: bool = False) -> list:
        """获取持仓信息"""
        cached_value = self._get_cached_runtime_value(
            'positions',
            self.POSITIONS_CACHE_TTL_SECONDS,
            force_refresh=force_refresh
        )
        if cached_value is not None:
            return cached_value

        max_retries = 3
        retry_delay = 2
        last_error = ''
        with self._api_lock:
            for attempt in range(max_retries):
                try:
                # 查询账户配置
                    try:
                        config_result = self.account_api.get_account_config()
                        if not isinstance(config_result, dict):
                            raise ValueError(f'Unexpected account config response type: {type(config_result)}')
                        if config_result.get('code') == '0' and config_result.get('data'):
                            config_data = config_result.get('data', [{}])[0]
                            self._debug_log(
                                'account_config_summary',
                                f"[DEBUG] 账户配置: posMode={config_data.get('posMode')}, acctLv={config_data.get('acctLv')}"
                            )
                    except Exception as e:
                        print(f"[ERROR] 查询账户配置失败: {e}")

                    all_positions = []
                    inst_ids = list(config.OKX_SYMBOLS.values())
                    for inst_id in inst_ids:
                        inst_result = self.account_api.get_positions(instId=inst_id)
                        if not isinstance(inst_result, dict):
                            raise ValueError(f'Unexpected positions response type: {type(inst_result)}')
                        if inst_result.get('code') == '0':
                            data = inst_result.get('data', [])
                            if data:
                                for pos in data:
                                    if float(pos.get('pos', 0)) != 0:
                                        coin = pos.get('instId').split('-')[0]
                                        contracts = float(pos.get('pos', 0))
                                        avg_price = float(pos.get('avgPx', 0))
                                        instrument = self.get_instrument_spec(pos.get('instId'))
                                        all_positions.append({
                                            'coin': coin,
                                            'inst_id': pos.get('instId'),
                                            'side': pos.get('posSide'),
                                            'size': contracts,
                                            'contracts': contracts,
                                            'avg_price': avg_price,
                                            'coin_quantity': self.contracts_to_coin_quantity(coin, contracts, avg_price),
                                            'face_value': self.get_contract_face_value(coin),
                                            'lot_size': float(instrument.get('lotSz', 1) or 1),
                                            'min_size': float(instrument.get('minSz', 1) or 1),
                                            'notional_usdt': self.contracts_to_notional_usdt(coin, contracts, avg_price),
                                            'leverage': int(pos.get('lever', 1)),
                                            'unrealized_pnl': float(pos.get('upl', 0)),
                                            'mgnMode': pos.get('mgnMode', ''),
                                            'posId': pos.get('posId', '')
                                        })
                    for idx, pos in enumerate(all_positions):
                        self._debug_log(
                            f'final_position:{pos["inst_id"]}:{idx}',
                            f"[DEBUG] 最终持仓 #{idx}: instId={pos['inst_id']}, pos={pos['size']}"
                        )
                    self._store_cached_runtime_value('positions', all_positions)
                    return all_positions
                except Exception as e:
                    error_msg = self._format_exception(e)
                    last_error = error_msg
                    print(f"[ERROR] 获取持仓信息失败: {error_msg}")
                    if attempt < max_retries - 1 and any(k.lower() in error_msg.lower() for k in self.TRANSIENT_ERROR_KEYWORDS):
                        print(f"[ERROR] 持仓接口异常，{retry_delay}秒后重试...")
                        time.sleep(retry_delay)
                        continue
                    break

        if allow_stale:
            stale_value, age = self._get_stale_runtime_value('positions')
            if stale_value is not None:
                self._debug_log(
                    'positions_stale_fallback',
                    f"[WARN] OKX positions fallback to cached snapshot from {age:.1f}s ago: {last_error}",
                    interval_seconds=30
                )
                return stale_value

        return []

    def get_order_status(self, ord_id: str, inst_id: str) -> dict:
        """查询订单状态"""
        try:
            with self._api_lock:
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

    def place_native_risk_order(self, coin: str, side: str, contracts: float,
                                stop_loss: float = None, take_profit: float = None,
                                trigger_px_type: str = 'mark') -> dict:
        """Place exchange-native TP/SL algo orders for an existing position."""
        try:
            if stop_loss is None and take_profit is None:
                return {'success': True, 'message': 'No TP/SL requested'}

            inst_id = config.OKX_SYMBOLS.get(coin)
            if not inst_id:
                return {'success': False, 'error': f'Unsupported coin: {coin}'}

            close_side = 'sell' if side == 'long' else 'buy'
            algo_cl_ord_id = f"risk{coin}{'L' if side == 'long' else 'S'}{uuid.uuid4().hex[:20]}"

            def build_params(include_algo_cl_ord_id: bool = True):
                payload = {
                    'instId': inst_id,
                    'tdMode': 'cross',
                    'side': close_side,
                    'ordType': 'conditional',
                    'sz': str(contracts),
                    'posSide': side,
                    'reduceOnly': 'true',
                    'cxlOnClosePos': 'true',
                }
                if include_algo_cl_ord_id:
                    payload['algoClOrdId'] = algo_cl_ord_id
                if take_profit is not None:
                    payload['tpTriggerPx'] = str(take_profit)
                    payload['tpOrdPx'] = '-1'
                    payload['tpTriggerPxType'] = trigger_px_type
                if stop_loss is not None:
                    payload['slTriggerPx'] = str(stop_loss)
                    payload['slOrdPx'] = '-1'
                    payload['slTriggerPxType'] = trigger_px_type
                return payload

            with self._api_lock:
                params = build_params(include_algo_cl_ord_id=True)
                result = self.trade_api.place_algo_order(**params)
                if result.get('code') != '0' and 'algoClOrdId' in (result.get('msg') or ''):
                    params = build_params(include_algo_cl_ord_id=False)
                    result = self.trade_api.place_algo_order(**params)

            if result.get('code') != '0':
                return {'success': False, 'error': result.get('msg'), 'raw': result}

            data = result.get('data', [{}])[0]
            return {
                'success': True,
                'algo_id': data.get('algoId'),
                'algo_cl_ord_id': algo_cl_ord_id if params.get('algoClOrdId') else data.get('algoClOrdId'),
                'message': f'Native TP/SL placed for {coin}'
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def amend_native_risk_order(self, coin: str, algo_id: str = None, algo_cl_ord_id: str = None,
                                contracts: float = None, stop_loss: float = None,
                                take_profit: float = None, trigger_px_type: str = 'mark') -> dict:
        """Amend exchange-native TP/SL algo order."""
        try:
            inst_id = config.OKX_SYMBOLS.get(coin)
            if not inst_id:
                return {'success': False, 'error': f'Unsupported coin: {coin}'}
            if not algo_id and not algo_cl_ord_id:
                return {'success': False, 'error': 'algo_id or algo_cl_ord_id is required'}

            params = {
                'instId': inst_id,
                'algoId': algo_id or '',
                'algoClOrdId': algo_cl_ord_id or '',
            }
            if contracts is not None:
                params['newSz'] = str(contracts)
            if take_profit is not None:
                params['newTpTriggerPx'] = str(take_profit)
                params['newTpOrdPx'] = '-1'
                params['newTpTriggerPxType'] = trigger_px_type
            if stop_loss is not None:
                params['newSlTriggerPx'] = str(stop_loss)
                params['newSlOrdPx'] = '-1'
                params['newSlTriggerPxType'] = trigger_px_type

            with self._api_lock:
                result = self.trade_api.amend_algo_order(**params)
            if result.get('code') != '0':
                return {'success': False, 'error': result.get('msg'), 'raw': result}
            return {'success': True, 'message': f'Native TP/SL amended for {coin}'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def cancel_native_risk_order(self, coin: str, algo_id: str = None, algo_cl_ord_id: str = None) -> dict:
        """取消本地风险订单"""
        try:
            inst_id = config.OKX_SYMBOLS.get(coin)
            if not inst_id:
                return {'success': False, 'error': f'Unsupported coin: {coin}'}
            if not algo_id and not algo_cl_ord_id:
                return {'success': True, 'message': 'No native TP/SL order to cancel'}

            params = [{
                'instId': inst_id,
                'algoId': algo_id or '',
                'algoClOrdId': algo_cl_ord_id or ''
            }]
            with self._api_lock:
                result = self.trade_api.cancel_algo_order(params)
            if result.get('code') == '1':
                data = result.get('data', []) or []
                for item in data:
                    if item.get('sCode') == '51400':
                        return {
                            'success': True,
                            'message': f'Native TP/SL already inactive for {coin}',
                            'already_inactive': True,
                            'raw': result
                        }
            if result.get('code') != '0':
                return {'success': False, 'error': result.get('msg'), 'raw': result}
            return {'success': True, 'message': f'Native TP/SL canceled for {coin}'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

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

            # OKX 永续合约每张合约价值（单位：标的币）
            face_value = self.get_contract_face_value(coin)

            # 计算合约张数：张数 = 币数量 / 每张合约价值
            contract_value = quantity * price  # 订单总价值（USDT）
            contract_size = self.coin_quantity_to_contracts(coin, quantity, price)  # 按交易所规格对齐得到张数

            print(f"[DEBUG] 币数量: {quantity}, 当前价格: {price}, 订单价值: {contract_value} USDT")
            print(f"[DEBUG] 每张合约价值: {face_value} {coin}, 计算得张数: {contract_size} 张")

            # 设置杠杆
            if leverage > 1:
                with self._api_lock:
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

            with self._api_lock:
                result = self.trade_api.place_order(**params)
            print(f"[DEBUG] 下单返回结果: {result}")

            if result.get('code') != '0':
                print(f"[ERROR] 下单失败: code={result.get('code')}, msg={result.get('msg')}")
                return {
                    'success': False,
                    'error': result.get('msg')
                }

            self._invalidate_private_runtime_cache('balance', 'positions')
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
                with self._api_lock:
                    result = self.trade_api.place_order(**params)
                if result.get('code') != '0':
                    return {'success': False, 'error': result.get('msg')}
                self._invalidate_private_runtime_cache('balance', 'positions')
                return {
                    'success': True,
                    'ord_id': result.get('data', [{}])[0].get('ordId'),
                    'message': f'Position closed: {coin}'
                }

            # 方式2：通过 instId 平仓（自动查询持仓数量）
            elif instId:
                # 查询当前持仓
                with self._api_lock:
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
                        with self._api_lock:
                            result = self.trade_api.place_order(**params)
                        if result.get('code') != '0':
                            return {'success': False, 'error': result.get('msg')}
                        self._invalidate_private_runtime_cache('balance', 'positions')
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
            with self._api_lock:
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
            with self._api_lock:
                result = self.account_api.get_account_config()
            self._debug_log('account_config_raw', f"[DEBUG] 账户配置: {result}")
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

    def get_recent_closed_position(self, coin: str, side: str, after_ms: int = None) -> dict:
        """获取最近平仓持仓，优先返回晚于 after_ms 的最新一条。"""
        try:
            with self._api_lock:
                result = self.account_api.get_positions_history(instType='SWAP')
            if not isinstance(result, dict) or result.get('code') != '0':
                return {}

            target_inst_id = config.OKX_SYMBOLS.get(coin)
            target_direction = 'long' if side == 'long' else 'short'

            candidates = []
            for item in result.get('data', []):
                if item.get('instId') != target_inst_id or item.get('posSide') != target_direction:
                    continue

                ts_raw = item.get('uTime') or item.get('cTime') or 0
                try:
                    ts_ms = int(ts_raw)
                except Exception:
                    ts_ms = 0

                if after_ms is not None and ts_ms < int(after_ms):
                    continue

                candidates.append((ts_ms, item))

            if not candidates:
                return {}

            candidates.sort(key=lambda pair: pair[0], reverse=True)
            return candidates[0][1]
        except Exception as e:
            print(f"[ERROR] 获取历史平仓持仓失败: {e}")
            return {}
