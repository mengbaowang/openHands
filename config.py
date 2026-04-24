"""
AI交易器 - 统一配置管理
遵循DRY和OCP原则，所有配置集中管理
使用 .env 文件管理环境变量，避免配置混乱
"""
import os
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()

# ============ 服务器配置 ============
HOST = os.getenv('HOST', '0.0.0.0')
PORT = int(os.getenv('PORT', 35008))
DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'

# ============ 数据库配置 ============
DATABASE_PATH = os.getenv('DATABASE_PATH', 'trading_bot.db')

# ============ 交易配置 ============
AUTO_TRADING = os.getenv('AUTO_TRADING', 'True').lower() == 'true'
TRADING_INTERVAL = int(os.getenv('TRADING_INTERVAL', 180))  # 秒

# 支持的币种列表（统一管理，遵循OCP原则）
SUPPORTED_COINS = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE']

# Binance交易对映射
BINANCE_SYMBOLS = {
    'BTC': 'BTCUSDT',
    'ETH': 'ETHUSDT',
    'SOL': 'SOLUSDT',
    'BNB': 'BNBUSDT',
    'XRP': 'XRPUSDT',
    'DOGE': 'DOGEUSDT'
}

# CoinGecko币种映射
COINGECKO_MAPPING = {
    'BTC': 'bitcoin',
    'ETH': 'ethereum',
    'SOL': 'solana',
    'BNB': 'binancecoin',
    'XRP': 'ripple',
    'DOGE': 'dogecoin'
}
# OKX交易对映射（新增）
OKX_SYMBOLS = {
    'BTC': 'BTC-USDT-SWAP',  # 永续合约
    'ETH': 'ETH-USDT-SWAP',
    'SOL': 'SOL-USDT-SWAP',
    'BNB': 'BNB-USDT-SWAP',
    'XRP': 'XRP-USDT-SWAP',
    'DOGE': 'DOGE-USDT-SWAP'
}

# 交易模式切换 simulation(模拟交易） | okx_demo(OKX模拟盘）
TRADING_MODE = os.getenv('TRADING_MODE', 'simulation')
# ============ 市场数据配置 ============
MARKET_API_CACHE_DURATION = 5  # 秒
BINANCE_API_URL = 'https://api.binance.com/api/v3'
COINGECKO_API_URL = 'https://api.coingecko.com/api/v3'
OKX_API_URL = 'https://www.okx.com/api/v5'


# OKX API 配置
OKX_API_KEY = os.getenv('OKX_API_KEY', 'cfdb8f59-d2a4-4be1-8675-6f16e19e22de')
OKX_API_SECRET = os.getenv('OKX_API_SECRET', '293C6B25518060439A29E0ABBE1CD2E9')
OKX_PASSPHRASE = os.getenv('OKX_PASSPHRASE', 'Mengbaowang666.')
OKX_FLAG = '1'  # 1=模拟盘, 0=实盘
# ============ 前端刷新频率 ============
MARKET_REFRESH_INTERVAL = 5000  # 毫秒（修复了原来的错误35008）
PORTFOLIO_REFRESH_INTERVAL = 10000  # 毫秒

# ============ 风险管理配置 ============
# 单笔交易最大风险比例
MAX_RISK_PER_TRADE = 0.05  # 5%

# 单币种最大持仓比例
MAX_POSITION_RATIO = 0.30  # 30%

# 最大总杠杆倍数
MAX_TOTAL_LEVERAGE = 3.0

# 杠杆范围
MIN_LEVERAGE = 1
MAX_LEVERAGE = 20

# 最大回撤警告阈值
MAX_DRAWDOWN_WARNING = 0.15  # 15%
MAX_DRAWDOWN_CRITICAL = 0.25  # 25%

# ============ 日志配置 ============
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
LOG_FILE = 'trading_bot.log'

# ============ API限流配置 ============
RATE_LIMIT_ENABLED = True
RATE_LIMIT_DEFAULT = "100 per minute"
RATE_LIMIT_TRADING = "10 per minute"

# ============ WebSocket配置 ============
WEBSOCKET_ENABLED = True
WEBSOCKET_PING_INTERVAL = 25
WEBSOCKET_PING_TIMEOUT = 60
