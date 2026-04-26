"""
Market data module - Multi-source API integration with fallback
支持多数据源：Binance -> CoinGecko -> CoinCap -> CryptoCompare
增强缓存机制：内存缓存 + 文件持久化缓存
"""
import requests
import time
import json
import os
from typing import Dict, List, Optional
import config

class MarketDataFetcher:
    """Fetch real-time market data from multiple sources with fallback"""

    def __init__(self):
        # API URLs
        self.binance_base_url = config.BINANCE_API_URL
        self.coingecko_base_url = config.COINGECKO_API_URL
        self.coincap_base_url = "https://api.coincap.io/v2"
        self.cryptocompare_base_url = "https://min-api.cryptocompare.com/data"
        self.okx_base_url = "https://www.okx.com/api/v5"

        # Symbol mappings
        self.binance_symbols = config.BINANCE_SYMBOLS
        self.coingecko_mapping = config.COINGECKO_MAPPING
        self.coincap_mapping = {
            'BTC': 'bitcoin',
            'ETH': 'ethereum',
            'SOL': 'solana',
            'BNB': 'binance-coin',
            'XRP': 'xrp',
            'DOGE': 'dogecoin'
        }
        self.okx_symbols = {
            'BTC': 'BTC-USDT',
            'ETH': 'ETH-USDT',
            'SOL': 'SOL-USDT',
            'BNB': 'BNB-USDT',
            'XRP': 'XRP-USDT',
            'DOGE': 'DOGE-USDT'
        }

        # Cache settings
        self._cache = {}
        self._cache_time = {}
        self._cache_duration = config.MARKET_API_CACHE_DURATION
        self._stale_cache_duration = 3600 * 24  # 过期缓存保留24小时

        # 持久化缓存文件
        self._cache_file = 'market_data_cache.json'
        self._load_persistent_cache()

        # Rate limiting (针对不同API设置不同的限流)
        self._last_request_time = {}
        self._min_request_interval = {
            'binance': 0.5,      # Binance很稳定，0.5秒即可
            'coingecko': 10.0,   # CoinGecko免费版限流严重，10秒间隔
            'coincap': 2.0,      # CoinCap中等限流
            'cryptocompare': 2.0, # CryptoCompare中等限流
            'okx': 1.0           # OKX中等限流
        }
    
    def _rate_limit(self, source: str):
        """针对不同API的智能限流机制"""
        now = time.time()
        interval = self._min_request_interval.get(source, 2.0)

        if source in self._last_request_time:
            elapsed = now - self._last_request_time[source]
            if elapsed < interval:
                sleep_time = interval - elapsed
                # print(f"[DEBUG] Rate limiting {source}, sleeping {sleep_time:.1f}s")
                time.sleep(sleep_time)

        self._last_request_time[source] = time.time()

    def _load_persistent_cache(self):
        """从文件加载持久化缓存"""
        try:
            if os.path.exists(self._cache_file):
                with open(self._cache_file, 'r') as f:
                    data = json.load(f)
                    self._cache = data.get('cache', {})
                    self._cache_time = data.get('cache_time', {})
                    print(f"[INFO] Loaded {len(self._cache)} cached items from {self._cache_file}")
        except Exception as e:
            print(f"[WARN] Failed to load persistent cache: {e}")
            self._cache = {}
            self._cache_time = {}

    def _save_persistent_cache(self):
        """保存缓存到文件"""
        try:
            data = {
                'cache': self._cache,
                'cache_time': self._cache_time
            }
            with open(self._cache_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            print(f"[WARN] Failed to save persistent cache: {e}")

    def get_current_prices(self, coins: List[str]) -> Dict[str, float]:
        """Get current prices with multi-source fallback"""
        # Check cache
        cache_key = 'prices_' + '_'.join(sorted(coins))
        if cache_key in self._cache:
            if time.time() - self._cache_time[cache_key] < self._cache_duration:
                return self._cache[cache_key]

        # 1. 优先使用 OKX（如果启用）
        prices = None
        if config.TRADING_MODE == 'okx_demo':
            prices = self._get_prices_from_okx(coins)
            if prices and len(prices) == len(coins):
                self._cache[cache_key] = prices
                self._cache_time[cache_key] = time.time()
                return prices
        # 1. Try Binance (fastest, most reliable)
        prices = self._get_prices_from_binance(coins)
        if prices and len(prices) == len(coins):
            self._cache[cache_key] = prices
            self._cache_time[cache_key] = time.time()
            return prices

        # 2. Try CoinGecko (comprehensive, but rate limited)
        prices = self._get_prices_from_coingecko(coins)
        if prices and len(prices) == len(coins):
            self._cache[cache_key] = prices
            self._cache_time[cache_key] = time.time()
            return prices

        # 3. Try CoinCap (good free tier)
        prices = self._get_prices_from_coincap(coins)
        if prices and len(prices) == len(coins):
            self._cache[cache_key] = prices
            self._cache_time[cache_key] = time.time()
            return prices

        # 4. Try CryptoCompare (last resort)
        prices = self._get_prices_from_cryptocompare(coins)
        if prices and len(prices) == len(coins):
            self._cache[cache_key] = prices
            self._cache_time[cache_key] = time.time()
            return prices

        # All sources failed, return cached data if available (within 30 days - 只用真实数据)
        if cache_key in self._cache:
            cache_age = time.time() - self._cache_time[cache_key]
            if cache_age < 2592000:  # 30天内的真实缓存数据都可以用
                print(f"[WARN] All API sources failed, using cached real data ({cache_age/3600:.1f} hours old)")
                return self._cache[cache_key]

        # 禁止使用默认价格！返回空字典并记录错误
        print(f"[ERROR] Failed to get current prices - NO MOCK DATA ALLOWED!")
        print(f"[ERROR] All APIs failed and no cached data available. Please check network connection.")
        return {}  # 返回空字典，让调用方处理

    def _get_prices_from_binance(self, coins: List[str]) -> Optional[Dict[str, float]]:
        """Fetch prices from Binance API"""
        try:
            self._rate_limit('binance')

            symbols = [self.binance_symbols.get(coin) for coin in coins if coin in self.binance_symbols]
            if not symbols:
                return None

            symbols_param = '[' + ','.join([f'"{s}"' for s in symbols]) + ']'

            response = requests.get(
                f"{self.binance_base_url}/ticker/24hr",
                params={'symbols': symbols_param},
                timeout=5
            )
            response.raise_for_status()
            data = response.json()

            prices = {}
            for item in data:
                symbol = item['symbol']
                for coin, binance_symbol in self.binance_symbols.items():
                    if binance_symbol == symbol:
                        prices[coin] = {
                            'price': float(item['lastPrice']),
                            'change_24h': float(item['priceChangePercent'])
                        }
                        break

            return prices if prices else None

        except Exception as e:
            print(f"[WARN] Binance API failed: {e}")
            return None
    
    def _get_prices_from_coingecko(self, coins: List[str]) -> Optional[Dict[str, float]]:
        """Fetch prices from CoinGecko API"""
        try:
            self._rate_limit('coingecko')

            coin_ids = [self.coingecko_mapping.get(coin, coin.lower()) for coin in coins]

            response = requests.get(
                f"{self.coingecko_base_url}/simple/price",
                params={
                    'ids': ','.join(coin_ids),
                    'vs_currencies': 'usd',
                    'include_24hr_change': 'true'
                },
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            prices = {}
            for coin in coins:
                coin_id = self.coingecko_mapping.get(coin, coin.lower())
                if coin_id in data:
                    prices[coin] = {
                        'price': data[coin_id]['usd'],
                        'change_24h': data[coin_id].get('usd_24h_change', 0)
                    }

            return prices if prices else None

        except Exception as e:
            print(f"[WARN] CoinGecko API failed: {e}")
            return None

    def _get_prices_from_coincap(self, coins: List[str]) -> Optional[Dict[str, float]]:
        """Fetch prices from CoinCap API (free tier: 4000 credits/month)"""
        try:
            self._rate_limit('coincap')

            prices = {}
            for coin in coins:
                coin_id = self.coincap_mapping.get(coin, coin.lower())

                response = requests.get(
                    f"{self.coincap_base_url}/assets/{coin_id}",
                    timeout=5
                )
                response.raise_for_status()
                data = response.json()

                if 'data' in data:
                    asset = data['data']
                    prices[coin] = {
                        'price': float(asset['priceUsd']),
                        'change_24h': float(asset.get('changePercent24Hr', 0))
                    }

            return prices if prices else None

        except Exception as e:
            print(f"[WARN] CoinCap API failed: {e}")
            return None

    def _get_prices_from_cryptocompare(self, coins: List[str]) -> Optional[Dict[str, float]]:
        """Fetch prices from CryptoCompare API (free tier available)"""
        try:
            self._rate_limit('cryptocompare')

            # CryptoCompare uses coin symbols directly
            fsyms = ','.join(coins)

            response = requests.get(
                f"{self.cryptocompare_base_url}/pricemultifull",
                params={
                    'fsyms': fsyms,
                    'tsyms': 'USD'
                },
                timeout=5
            )
            response.raise_for_status()
            data = response.json()

            prices = {}
            if 'RAW' in data:
                for coin in coins:
                    if coin in data['RAW'] and 'USD' in data['RAW'][coin]:
                        coin_data = data['RAW'][coin]['USD']
                        prices[coin] = {
                            'price': float(coin_data['PRICE']),
                            'change_24h': float(coin_data.get('CHANGEPCT24HOUR', 0))
                        }

            return prices if prices else None

        except Exception as e:
            print(f"[WARN] CryptoCompare API failed: {e}")
            return None
    
    def _get_prices_from_okx(self, coins: List[str]) -> Optional[Dict[str, float]]:
        """从 OKX API 获取实时价格（优先使用）"""
        try:
            self._rate_limit('okx')
            
            # 获取所有产品的行情
            inst_ids = [self.okx_symbols.get(coin) for coin in coins if coin in self.okx_symbols]
            if not inst_ids:
                return None
            
            # 查询多个产品行情
            inst_type = 'SPOT'  # 现货
            response = requests.get(
                f"{self.okx_base_url}/market/tickers",
                params={
                    'instType': inst_type,
                    'instId': ','.join(inst_ids)
                },
                timeout=5
            )
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') != '0':
                print(f"[WARN] OKX API error: {data.get('msg')}")
                return None
            
            prices = {}
            for item in data.get('data', []):
                inst_id = item.get('instId')
                # 反向映射到币种
                for coin, okx_symbol in self.okx_symbols.items():
                    if okx_symbol == inst_id:
                        prices[coin] = {
                            'price': float(item.get('last', 0)),
                            'change_24h': float(item.get('open24h', 0))
                        }
                        break
            
            return prices if prices else None
        except Exception as e:
            print(f"[WARN] OKX API failed: {e}")
            return None

    def get_market_data(self, coin: str) -> Dict:
        """Get detailed market data from CoinGecko"""
        coin_id = self.coingecko_mapping.get(coin, coin.lower())
        
        try:
            response = requests.get(
                f"{self.coingecko_base_url}/coins/{coin_id}",
                params={'localization': 'false', 'tickers': 'false', 'community_data': 'false'},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            market_data = data.get('market_data', {})
            
            return {
                'current_price': market_data.get('current_price', {}).get('usd', 0),
                'market_cap': market_data.get('market_cap', {}).get('usd', 0),
                'total_volume': market_data.get('total_volume', {}).get('usd', 0),
                'price_change_24h': market_data.get('price_change_percentage_24h', 0),
                'price_change_7d': market_data.get('price_change_percentage_7d', 0),
                'high_24h': market_data.get('high_24h', {}).get('usd', 0),
                'low_24h': market_data.get('low_24h', {}).get('usd', 0),
            }
        except Exception as e:
            print(f"[ERROR] Failed to get market data for {coin}: {e}")
            return {}
    
    def get_historical_prices(self, coin: str, days: int = 7) -> List[Dict]:
        """Get historical prices with multi-source fallback"""
        # Check cache (延长缓存时间到6小时，减少API请求)
        cache_key = f'historical_{coin}_{days}'
        if cache_key in self._cache:
            cache_age = time.time() - self._cache_time[cache_key]
            if cache_age < 21600:  # 6小时缓存（原来1小时太短）
                return self._cache[cache_key]

        # Try Binance first (最稳定，无限流)
        prices = self._get_historical_from_binance(coin, days)
        if prices:
            self._cache[cache_key] = prices
            self._cache_time[cache_key] = time.time()
            self._save_persistent_cache()  # 保存到文件
            return prices

        # Try CoinGecko as fallback
        prices = self._get_historical_from_coingecko(coin, days)
        if prices:
            self._cache[cache_key] = prices
            self._cache_time[cache_key] = time.time()
            self._save_persistent_cache()  # 保存到文件
            return prices

        # Try CoinCap as fallback
        prices = self._get_historical_from_coincap(coin, days)
        if prices:
            self._cache[cache_key] = prices
            self._cache_time[cache_key] = time.time()
            self._save_persistent_cache()  # 保存到文件
            return prices

        # Return cached data if available (within 30 days - 只用真实数据)
        if cache_key in self._cache:
            cache_age = time.time() - self._cache_time[cache_key]
            if cache_age < 2592000:  # 30天内的真实缓存数据都可以用
                print(f"[WARN] Historical data API failed, using cached real data for {coin} ({cache_age/3600:.1f} hours old)")
                return self._cache[cache_key]

        # 禁止使用模拟数据！返回空列表并记录错误
        print(f"[ERROR] Failed to get historical prices for {coin} - NO MOCK DATA ALLOWED!")
        print(f"[ERROR] All APIs failed and no cached data available. Please check network connection.")
        return []  # 返回空列表，让调用方处理

    def _get_historical_from_binance(self, coin: str, days: int) -> Optional[List[Dict]]:
        """Fetch historical prices from Binance (最稳定的数据源)"""
        try:
            # Binance不需要rate limit，API很稳定
            symbol = self.binance_symbols.get(coin)
            if not symbol:
                return None

            # Binance Klines API
            # interval: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M
            interval = '1d' if days > 7 else '1h'
            limit = days if days > 7 else days * 24

            response = requests.get(
                f"{self.binance_base_url}/klines",
                params={
                    'symbol': symbol,
                    'interval': interval,
                    'limit': limit
                },
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            prices = []
            for kline in data:
                # Binance kline format: [timestamp, open, high, low, close, volume, ...]
                prices.append({
                    'timestamp': kline[0],  # 开盘时间
                    'price': float(kline[4])  # 收盘价
                })

            return prices if prices else None

        except Exception as e:
            print(f"[WARN] Binance historical data failed for {coin}: {e}")
            return None

    def _get_historical_from_coingecko(self, coin: str, days: int) -> Optional[List[Dict]]:
        """Fetch historical prices from CoinGecko"""
        try:
            self._rate_limit('coingecko')

            coin_id = self.coingecko_mapping.get(coin, coin.lower())

            response = requests.get(
                f"{self.coingecko_base_url}/coins/{coin_id}/market_chart",
                params={'vs_currency': 'usd', 'days': days},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            prices = []
            for price_data in data.get('prices', []):
                prices.append({
                    'timestamp': price_data[0],
                    'price': price_data[1]
                })

            return prices if prices else None

        except Exception as e:
            print(f"[WARN] CoinGecko historical data failed for {coin}: {e}")
            return None

    def _get_historical_from_coincap(self, coin: str, days: int) -> Optional[List[Dict]]:
        """Fetch historical prices from CoinCap"""
        try:
            self._rate_limit('coincap')

            coin_id = self.coincap_mapping.get(coin, coin.lower())

            # CoinCap uses intervals: m1, m5, m15, m30, h1, h2, h6, h12, d1
            interval = 'd1' if days > 7 else 'h1'

            response = requests.get(
                f"{self.coincap_base_url}/assets/{coin_id}/history",
                params={
                    'interval': interval
                },
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            prices = []
            if 'data' in data:
                for item in data['data'][-days*24:]:  # 取最近的数据
                    prices.append({
                        'timestamp': item['time'],
                        'price': float(item['priceUsd'])
                    })

            return prices if prices else None

        except Exception as e:
            print(f"[WARN] CoinCap historical data failed for {coin}: {e}")
            return None
    
    def calculate_technical_indicators(self, coin: str) -> Dict:
        """Calculate technical indicators"""
        historical = self.get_historical_prices(coin, days=30)
        return self.calculate_technical_indicators_from_history(historical)

    def calculate_technical_indicators_from_history(self, historical: List[Dict]) -> Dict:
        """Calculate technical indicators from a provided historical window."""

        if not historical or len(historical) < 14:
            return {}

        prices = [p['price'] for p in historical]

        # Simple Moving Average
        sma_7 = sum(prices[-7:]) / 7 if len(prices) >= 7 else prices[-1]
        sma_14 = sum(prices[-14:]) / 14 if len(prices) >= 14 else prices[-1]
        sma_30 = sum(prices[-30:]) / 30 if len(prices) >= 30 else prices[-1]

        # Exponential Moving Average
        ema_12 = self._calculate_ema(prices, 12)
        ema_26 = self._calculate_ema(prices, 26)

        # MACD
        macd_line = ema_12 - ema_26
        signal_line = self._calculate_ema([macd_line] * 9, 9)  # 简化版
        macd_histogram = macd_line - signal_line

        # RSI
        changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        gains = [c if c > 0 else 0 for c in changes]
        losses = [-c if c < 0 else 0 for c in changes]

        avg_gain = sum(gains[-14:]) / 14 if gains else 0
        avg_loss = sum(losses[-14:]) / 14 if losses else 0

        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        # Bollinger Bands
        sma_20 = sum(prices[-20:]) / 20 if len(prices) >= 20 else prices[-1]
        std_20 = self._calculate_std(prices[-20:]) if len(prices) >= 20 else 0
        bb_upper = sma_20 + (2 * std_20)
        bb_lower = sma_20 - (2 * std_20)

        # 价格位置（在布林带中的位置）
        bb_position = ((prices[-1] - bb_lower) / (bb_upper - bb_lower)) if (bb_upper - bb_lower) > 0 else 0.5

        return {
            'sma_7': sma_7,
            'sma_14': sma_14,
            'sma_30': sma_30,
            'ema_12': ema_12,
            'ema_26': ema_26,
            'macd': macd_line,
            'macd_signal': signal_line,
            'macd_histogram': macd_histogram,
            'rsi_14': rsi,
            'bb_upper': bb_upper,
            'bb_middle': sma_20,
            'bb_lower': bb_lower,
            'bb_position': bb_position,
            'current_price': prices[-1],
            'price_change_7d': ((prices[-1] - prices[0]) / prices[0]) * 100 if prices[0] > 0 else 0,
            'volatility': std_20 / sma_20 if sma_20 > 0 else 0
        }

    def _calculate_ema(self, prices: List[float], period: int) -> float:
        """计算指数移动平均"""
        if len(prices) < period:
            return prices[-1] if prices else 0

        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period

        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema

        return ema

    def _calculate_std(self, prices: List[float]) -> float:
        """计算标准差"""
        if not prices:
            return 0

        mean = sum(prices) / len(prices)
        variance = sum((p - mean) ** 2 for p in prices) / len(prices)
        return variance ** 0.5
