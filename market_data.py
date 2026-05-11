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
        self.okx_symbols = {
            'BTC': 'BTC-USDT',
            'ETH': 'ETH-USDT',
            'SOL': 'SOL-USDT',
            'BNB': 'BNB-USDT',
            'XRP': 'XRP-USDT',
            'DOGE': 'DOGE-USDT',
            'ZEC': 'ZEC-USDT',
        }
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

    def get_current_prices(self, coins: List[str]) -> Dict[str, float]:
        cache_key = 'prices_' + '_'.join(sorted(coins))
        if cache_key in self._cache and time.time() - self._cache_time[cache_key] < self._cache_duration:
            return self._cache[cache_key]

        prices = self._get_prices_from_okx(coins)
        if prices and len(prices) == len(coins):
            self._cache[cache_key] = prices
            self._cache_time[cache_key] = time.time()
            return prices

        if cache_key in self._cache and time.time() - self._cache_time[cache_key] < 2592000:
            return self._cache[cache_key]

        return {}

    def _get_prices_from_okx(self, coins: List[str]) -> Optional[Dict[str, float]]:
        try:
            self._rate_limit('okx')
            response = requests.get(
                f"{self.okx_base_url}/market/tickers",
                params={'instType': 'SWAP'},
                timeout=8
            )
            response.raise_for_status()
            data = response.json()
            if data.get('code') != '0' or not data.get('data'):
                return None

            ticker_map = {item.get('instId'): item for item in data.get('data', []) if item.get('instId')}
            prices = {}
            for coin in coins:
                inst_id = self.okx_symbols.get(coin)
                item = ticker_map.get(inst_id)
                if not item:
                    continue
                open_24h = float(item.get('open24h', 0) or 0)
                last_price = float(item.get('last', 0) or 0)
                change_24h = ((last_price - open_24h) / open_24h * 100) if open_24h > 0 else 0
                prices[coin] = {'price': last_price, 'change_24h': change_24h}
            return prices if prices else None
        except Exception:
            return None

    def _get_historical_from_okx(self, coin: str, days: int) -> Optional[List[Dict]]:
        try:
            self._rate_limit('okx')
            inst_id = self.okx_symbols.get(coin)
            if not inst_id:
                return None
            bar = '1Dutc' if days > 7 else '1H'
            limit = min(days if days > 7 else days * 24, 300)
            response = requests.get(
                f"{self.okx_base_url}/market/history-candles",
                params={'instId': inst_id, 'bar': bar, 'limit': limit},
                timeout=8
            )
            response.raise_for_status()
            data = response.json()
            if data.get('code') != '0':
                return None
            return [{'timestamp': int(c[0]), 'price': float(c[4])} for c in reversed(data.get('data', []))]
        except Exception:
            return None

    def _get_candles_from_okx(self, coin: str, timeframe: str, limit: int) -> Optional[List[Dict]]:
        try:
            self._rate_limit('okx')
            inst_id = self.okx_symbols.get(coin)
            tf_config = self.TIMEFRAME_CONFIG.get(timeframe)
            if not inst_id or not tf_config:
                return None
            response = requests.get(
                f"{self.okx_base_url}/market/history-candles",
                params={'instId': inst_id, 'bar': tf_config['okx'], 'limit': min(limit, 300)},
                timeout=8
            )
            response.raise_for_status()
            data = response.json()
            if data.get('code') != '0':
                return None
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
        except Exception:
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
