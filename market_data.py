"""行情获取、缓存管理与技术指标计算模块。"""
import json
import os
import time
from typing import Dict, List, Optional

import requests

import config


class MarketDataFetcher:
    """Fetch market data from OKX with cache fallback."""

    TIMEFRAME_CONFIG = {
        '5m': {'okx': '5m', 'cache_seconds': 300, 'points': 120},
        '15m': {'okx': '15m', 'cache_seconds': 900, 'points': 120},
        '1h': {'okx': '1H', 'cache_seconds': 3600, 'points': 240},
        '4h': {'okx': '4H', 'cache_seconds': 14400, 'points': 180},
        '1d': {'okx': '1Dutc', 'cache_seconds': 21600, 'points': 90},
    }

    def __init__(self):
        self.okx_base_url = config.OKX_API_URL
        self.binance_futures_base_url = 'https://fapi.binance.com'
        self.okx_spot_symbols = {
            'BTC': 'BTC-USDT',
            'ETH': 'ETH-USDT',
            'SOL': 'SOL-USDT',
            'BNB': 'BNB-USDT',
            'XRP': 'XRP-USDT',
            'DOGE': 'DOGE-USDT',
            'ZEC': 'ZEC-USDT',
        }
        self.okx_swap_symbols = dict(config.OKX_SYMBOLS)
        self.okx_symbols = self.okx_spot_symbols
        self._cache = {}
        self._cache_time = {}
        self._cache_duration = config.MARKET_API_CACHE_DURATION
        self._cache_file = 'market_data_cache.json'
        self._load_persistent_cache()
        self._last_request_time = {}
        self._min_request_interval = {'okx': 2.5}

    def _rate_limit(self, source: str):
        now = time.time()
        interval = self._min_request_interval.get(source, 2.0)
        if source in self._last_request_time:
            elapsed = now - self._last_request_time[source]
            if elapsed < interval:
                time.sleep(interval - elapsed)
        self._last_request_time[source] = time.time()

    def _load_persistent_cache(self):
        try:
            if os.path.exists(self._cache_file):
                with open(self._cache_file, 'r') as f:
                    data = json.load(f)
                    self._cache = data.get('cache', {})
                    self._cache_time = data.get('cache_time', {})
        except Exception:
            self._cache = {}
            self._cache_time = {}

    def _save_persistent_cache(self):
        try:
            with open(self._cache_file, 'w') as f:
                json.dump({'cache': self._cache, 'cache_time': self._cache_time}, f)
        except Exception:
            pass

    def _get_inst_candidates(self, coin: str) -> List[str]:
        candidates = []
        for inst_id in (
            self.okx_swap_symbols.get(coin),
            self.okx_spot_symbols.get(coin),
        ):
            if inst_id and inst_id not in candidates:
                candidates.append(inst_id)
        return candidates

    def _request_okx_json(self, path: str, params: Dict, timeout: int = 8,
                          retries: int = 2, rate_limit_source: str = 'okx') -> Optional[Dict]:
        last_error = None
        for attempt in range(retries + 1):
            try:
                self._rate_limit(rate_limit_source)
                response = requests.get(
                    f"{self.okx_base_url}{path}",
                    params=params,
                    timeout=timeout
                )
                response.raise_for_status()
                data = response.json()
                if data.get('code') == '0':
                    return data
                last_error = data.get('msg') or f"code={data.get('code')}"
            except Exception as e:
                last_error = str(e)
            if attempt < retries:
                time.sleep(min(1.5 * (attempt + 1), 3.0))
        return None

    def get_current_prices(self, coins: List[str]) -> Dict[str, Dict[str, float]]:
        cache_key = 'prices_' + '_'.join(sorted(coins))
        if cache_key in self._cache and time.time() - self._cache_time[cache_key] < self._cache_duration:
            return self._cache[cache_key]

        prices = self._get_prices_from_okx(coins)
        if prices:
            self._cache[cache_key] = prices
            self._cache_time[cache_key] = time.time()
            self._save_persistent_cache()
            return prices

        if cache_key in self._cache and time.time() - self._cache_time[cache_key] < 2592000:
            cached = dict(self._cache[cache_key])
            if prices:
                cached.update(prices)
            return cached

        return prices or {}

    def _ticker_to_price_payload(self, item: Dict) -> Optional[Dict[str, float]]:
        if not item:
            return None
        last_price = float(item.get('last', 0) or 0)
        if last_price <= 0:
            return None
        open_24h = float(item.get('open24h', 0) or 0)
        change_24h = ((last_price - open_24h) / open_24h * 100) if open_24h > 0 else 0
        return {'price': last_price, 'change_24h': change_24h}

    def _fallback_price_from_cached_candles(self, coin: str) -> Optional[Dict[str, float]]:
        candle_keys = [
            f'candles_{coin}_5m_{self.TIMEFRAME_CONFIG["5m"]["points"]}',
            f'candles_{coin}_15m_{self.TIMEFRAME_CONFIG["15m"]["points"]}',
            f'candles_{coin}_1h_{self.TIMEFRAME_CONFIG["1h"]["points"]}',
        ]
        latest_candle = None
        latest_ts = -1
        for key in candle_keys:
            candles = self._cache.get(key) or []
            if candles:
                candidate = candles[-1]
                ts = int(candidate.get('timestamp', 0) or 0)
                if ts > latest_ts:
                    latest_candle = candidate
                    latest_ts = ts
        if not latest_candle:
            return None
        price = float(latest_candle.get('close', latest_candle.get('price', 0)) or 0)
        if price <= 0:
            return None
        return {'price': price, 'change_24h': 0}

    def _get_prices_from_okx(self, coins: List[str]) -> Optional[Dict[str, Dict[str, float]]]:
        prices: Dict[str, Dict[str, float]] = {}
        swap_tickers = self._request_okx_json('/market/tickers', {'instType': 'SWAP'}, timeout=8, retries=1)
        ticker_map = {
            item.get('instId'): item
            for item in (swap_tickers or {}).get('data', [])
            if item.get('instId')
        }

        for coin in coins:
            for inst_id in self._get_inst_candidates(coin):
                item = ticker_map.get(inst_id)
                if item:
                    payload = self._ticker_to_price_payload(item)
                    if payload:
                        prices[coin] = payload
                        break
            if coin in prices:
                continue

            for inst_id in self._get_inst_candidates(coin):
                ticker = self._request_okx_json('/market/ticker', {'instId': inst_id}, timeout=8, retries=1)
                data = (ticker or {}).get('data', [])
                payload = self._ticker_to_price_payload(data[0] if data else {})
                if payload:
                    prices[coin] = payload
                    break

            if coin not in prices:
                cached_payload = self._fallback_price_from_cached_candles(coin)
                if cached_payload:
                    prices[coin] = cached_payload

        return prices if prices else None

    def _get_historical_from_okx(self, coin: str, days: int) -> Optional[List[Dict]]:
        bar = '1Dutc' if days > 7 else '1H'
        limit = min(days if days > 7 else days * 24, 300)
        for inst_id in self._get_inst_candidates(coin):
            data = self._request_okx_json(
                '/market/history-candles',
                {'instId': inst_id, 'bar': bar, 'limit': limit},
                timeout=8,
                retries=1
            )
            if data and data.get('data'):
                return [{'timestamp': int(c[0]), 'price': float(c[4])} for c in reversed(data.get('data', []))]
        return None

    def _get_candles_from_okx(self, coin: str, timeframe: str, limit: int) -> Optional[List[Dict]]:
        tf_config = self.TIMEFRAME_CONFIG.get(timeframe)
        if not tf_config:
            return None
        for inst_id in self._get_inst_candidates(coin):
            data = self._request_okx_json(
                '/market/history-candles',
                {'instId': inst_id, 'bar': tf_config['okx'], 'limit': min(limit, 300)},
                timeout=8,
                retries=1
            )
            if data and data.get('data'):
                return [
                    {
                        'timestamp': int(c[0]),
                        'open': float(c[1]),
                        'high': float(c[2]),
                        'low': float(c[3]),
                        'close': float(c[4]),
                        'price': float(c[4]),
                        'volume': float(c[5]),
                    }
                    for c in reversed(data.get('data', []))
                ]
        return None

    def _get_candles_page_from_okx(self, coin: str, timeframe: str, limit: int,
                                   after: int = None, before: int = None) -> Optional[List[Dict]]:
        tf_config = self.TIMEFRAME_CONFIG.get(timeframe)
        if not tf_config:
            return None

        for inst_id in self._get_inst_candidates(coin):
            params = {
                'instId': inst_id,
                'bar': tf_config['okx'],
                'limit': min(limit, 300),
            }
            if after is not None:
                params['after'] = int(after)
            if before is not None:
                params['before'] = int(before)

            data = self._request_okx_json(
                '/market/history-candles',
                params,
                timeout=12,
                retries=1
            )
            if data and data.get('data'):
                return [
                    {
                        'timestamp': int(c[0]),
                        'open': float(c[1]),
                        'high': float(c[2]),
                        'low': float(c[3]),
                        'close': float(c[4]),
                        'price': float(c[4]),
                        'volume': float(c[5]),
                    }
                    for c in data.get('data', [])
                ]
        return None

    def get_market_data(self, coin: str) -> Dict:
        try:
            inst_id = self.okx_symbols.get(coin)
            if not inst_id:
                return {}
            self._rate_limit('okx')
            response = requests.get(
                f"{self.okx_base_url}/market/ticker",
                params={'instId': inst_id},
                timeout=8
            )
            response.raise_for_status()
            data = response.json()
            if data.get('code') != '0' or not data.get('data'):
                return {}
            ticker = data['data'][0]
            return {
                'current_price': float(ticker.get('last', 0)),
                'market_cap': 0,
                'total_volume': float(ticker.get('volCcy24h', 0)),
                'price_change_24h': 0,
                'price_change_7d': 0,
                'high_24h': float(ticker.get('high24h', 0)),
                'low_24h': float(ticker.get('low24h', 0)),
            }
        except Exception:
            return {}

    def get_historical_prices(self, coin: str, days: int = 7) -> List[Dict]:
        cache_key = f'historical_{coin}_{days}'
        if cache_key in self._cache and time.time() - self._cache_time[cache_key] < 21600:
            return self._cache[cache_key]
        prices = self._get_historical_from_okx(coin, days)
        if prices:
            self._cache[cache_key] = prices
            self._cache_time[cache_key] = time.time()
            self._save_persistent_cache()
            return prices
        if cache_key in self._cache and time.time() - self._cache_time[cache_key] < 2592000:
            return self._cache[cache_key]
        return []

    def get_historical_candles(self, coin: str, timeframe: str = '1h', limit: int = None) -> List[Dict]:
        tf_config = self.TIMEFRAME_CONFIG.get(timeframe)
        if not tf_config:
            raise ValueError(f'Unsupported timeframe: {timeframe}')
        candle_limit = limit or tf_config['points']
        cache_key = f'candles_{coin}_{timeframe}_{candle_limit}'
        if cache_key in self._cache and time.time() - self._cache_time[cache_key] < tf_config['cache_seconds']:
            return self._cache[cache_key]
        candles = self._get_candles_from_okx(coin, timeframe, candle_limit)
        if candles:
            self._cache[cache_key] = candles
            self._cache_time[cache_key] = time.time()
            self._save_persistent_cache()
            return candles
        if cache_key in self._cache and time.time() - self._cache_time[cache_key] < 2592000:
            return self._cache[cache_key]
        return []

    def get_historical_candles_range(self, coin: str, timeframe: str,
                                     start_ts_ms: int, end_ts_ms: int,
                                     page_limit: int = 300) -> List[Dict]:
        tf_config = self.TIMEFRAME_CONFIG.get(timeframe)
        if not tf_config:
            raise ValueError(f'Unsupported timeframe: {timeframe}')
        if start_ts_ms > end_ts_ms:
            return []

        cache_key = f'candles_range_{coin}_{timeframe}_{int(start_ts_ms)}_{int(end_ts_ms)}'
        if cache_key in self._cache and time.time() - self._cache_time[cache_key] < 21600:
            return self._cache[cache_key]

        pages = []
        seen_timestamps = set()
        cursor_after = int(end_ts_ms) + 1
        min_start = int(start_ts_ms)

        while True:
            page = self._get_candles_page_from_okx(
                coin,
                timeframe,
                limit=page_limit,
                after=cursor_after
            )
            if not page:
                break

            added = 0
            oldest_ts = None
            for candle in page:
                ts = int(candle['timestamp'])
                oldest_ts = ts if oldest_ts is None else min(oldest_ts, ts)
                if ts in seen_timestamps:
                    continue
                seen_timestamps.add(ts)
                if min_start <= ts <= end_ts_ms:
                    pages.append(candle)
                    added += 1

            if oldest_ts is None or oldest_ts <= min_start:
                break
            if len(page) < page_limit:
                break

            cursor_after = oldest_ts
            if added == 0 and oldest_ts > min_start:
                # Prevent endless pagination if the server keeps repeating the same page.
                break

        pages.sort(key=lambda item: item['timestamp'])
        self._cache[cache_key] = pages
        self._cache_time[cache_key] = time.time()
        return pages

    def get_binance_index_price_klines(self, pair: str, interval: str,
                                       start_ts_ms: int, end_ts_ms: int,
                                       limit: int = 1500) -> List[Dict]:
        cache_key = f'binance_index_{pair}_{interval}_{int(start_ts_ms)}_{int(end_ts_ms)}'
        if cache_key in self._cache and time.time() - self._cache_time[cache_key] < 21600:
            return self._cache[cache_key]

        results = []
        seen = set()
        cursor = int(start_ts_ms)
        while cursor <= end_ts_ms:
            try:
                response = requests.get(
                    f'{self.binance_futures_base_url}/fapi/v1/indexPriceKlines',
                    params={
                        'pair': pair,
                        'interval': interval,
                        'startTime': cursor,
                        'endTime': int(end_ts_ms),
                        'limit': min(int(limit), 1500),
                    },
                    timeout=12
                )
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, list) or not data:
                    break

                page_added = 0
                last_open_time = None
                for item in data:
                    open_time = int(item[0])
                    close_time = int(item[6])
                    last_open_time = open_time
                    if open_time in seen:
                        continue
                    seen.add(open_time)
                    results.append({
                        'timestamp': close_time,
                        'open_time': open_time,
                        'open': float(item[1]),
                        'high': float(item[2]),
                        'low': float(item[3]),
                        'close': float(item[4]),
                        'price': float(item[4]),
                    })
                    page_added += 1

                if page_added == 0 or last_open_time is None:
                    break
                cursor = last_open_time + 1
                if len(data) < min(int(limit), 1500):
                    break
            except Exception:
                break

        results.sort(key=lambda item: item['timestamp'])
        self._cache[cache_key] = results
        self._cache_time[cache_key] = time.time()
        return results

    def calculate_technical_indicators(self, coin: str) -> Dict:
        candles = self.get_historical_candles(coin, timeframe='1h', limit=self.TIMEFRAME_CONFIG['1h']['points'])
        return self.calculate_technical_indicators_from_history(candles)

    def get_multi_timeframe_indicators(self, coin: str) -> Dict:
        result = {}
        for timeframe in ('1h', '15m', '5m'):
            candles = self.get_historical_candles(coin, timeframe=timeframe, limit=self.TIMEFRAME_CONFIG[timeframe]['points'])
            result[timeframe] = self.calculate_technical_indicators_from_history(candles)
        return result

    def calculate_technical_indicators_from_history(self, historical: List[Dict]) -> Dict:
        if not historical or len(historical) < 14:
            return {}

        prices = [float(p.get('close', p.get('price', 0))) for p in historical]
        highs = [float(p.get('high', p.get('close', p.get('price', 0)))) for p in historical]
        lows = [float(p.get('low', p.get('close', p.get('price', 0)))) for p in historical]
        volumes = [float(p.get('volume', 0)) for p in historical]

        sma_5 = sum(prices[-5:]) / 5 if len(prices) >= 5 else prices[-1]
        sma_7 = sum(prices[-7:]) / 7 if len(prices) >= 7 else prices[-1]
        sma_14 = sum(prices[-14:]) / 14 if len(prices) >= 14 else prices[-1]
        sma_30 = sum(prices[-30:]) / 30 if len(prices) >= 30 else prices[-1]

        ema_12 = self._calculate_ema(prices, 12)
        ema_26 = self._calculate_ema(prices, 26)
        macd_line = ema_12 - ema_26
        macd_series = [self._calculate_ema(prices[:idx + 1], 12) - self._calculate_ema(prices[:idx + 1], 26) for idx in range(len(prices))]
        signal_line = self._calculate_ema(macd_series, 9)
        macd_histogram = macd_line - signal_line

        changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        gains = [c if c > 0 else 0 for c in changes]
        losses = [-c if c < 0 else 0 for c in changes]
        avg_gain = sum(gains[-14:]) / 14 if gains else 0
        avg_loss = sum(losses[-14:]) / 14 if losses else 0
        rsi = 100 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss)))

        sma_20 = sum(prices[-20:]) / 20 if len(prices) >= 20 else prices[-1]
        std_20 = self._calculate_std(prices[-20:]) if len(prices) >= 20 else 0
        bb_upper = sma_20 + (2 * std_20)
        bb_lower = sma_20 - (2 * std_20)
        bb_position = ((prices[-1] - bb_lower) / (bb_upper - bb_lower)) if (bb_upper - bb_lower) > 0 else 0.5

        atr = self._calculate_atr(highs, lows, prices, 14)
        avg_volume_20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else (sum(volumes) / len(volumes) if volumes else 0)
        recent_volumes = volumes[-3:] if len(volumes) >= 3 else volumes
        volume_ratio = (volumes[-1] / avg_volume_20) if avg_volume_20 > 0 and volumes else 0
        recent_high = max(highs[-20:]) if len(highs) >= 20 else max(highs)
        recent_low = min(lows[-20:]) if len(lows) >= 20 else min(lows)

        return {
            'sma_7': sma_7, 'sma_5': sma_5, 'sma_14': sma_14, 'sma_30': sma_30,
            'ema_12': ema_12, 'ema_26': ema_26, 'macd': macd_line, 'macd_signal': signal_line,
            'macd_histogram': macd_histogram, 'rsi_14': rsi, 'bb_upper': bb_upper, 'bb_middle': sma_20,
            'bb_lower': bb_lower, 'bb_position': bb_position, 'current_price': prices[-1],
            'price_change_7d': ((prices[-1] - prices[0]) / prices[0]) * 100 if prices[0] > 0 else 0,
            'volatility': std_20 / sma_20 if sma_20 > 0 else 0, 'atr_14': atr, 'volume': volumes[-1] if volumes else 0,
            'volume_avg_20': avg_volume_20, 'volume_ratio': volume_ratio, 'recent_high_20': recent_high,
            'recent_low_20': recent_low, 'close_prices_tail': prices[-5:],
            'macd_histogram_tail': [value - signal_line for value in (macd_series[-5:] if len(macd_series) >= 5 else macd_series)],
            'volume_tail': recent_volumes,
        }

    def _calculate_ema(self, prices: List[float], period: int) -> float:
        if len(prices) < period:
            return prices[-1] if prices else 0
        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def _calculate_std(self, prices: List[float]) -> float:
        if not prices:
            return 0
        mean = sum(prices) / len(prices)
        variance = sum((p - mean) ** 2 for p in prices) / len(prices)
        return variance ** 0.5

    def _calculate_atr(self, highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        if not highs or not lows or not closes:
            return 0
        true_ranges = []
        for idx in range(1, len(closes)):
            high = highs[idx]
            low = lows[idx]
            prev_close = closes[idx - 1]
            true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        if not true_ranges:
            return 0
        window = true_ranges[-period:] if len(true_ranges) >= period else true_ranges
        return sum(window) / len(window)
