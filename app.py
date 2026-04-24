from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
import time
import threading
from datetime import datetime
import os

from trading_engine import TradingEngine
from market_data import MarketDataFetcher
from ai_trader import AITrader
from database import Database
from services.risk_manager import RiskManager
from services.backtester import Backtester
from services.performance_analyzer import PerformanceAnalyzer
from utils.auth import hash_password, verify_password, login_required, get_current_user_id, set_current_user, clear_current_user
from utils.timezone import get_current_utc_time_str, get_current_beijing_time_str, utc_to_beijing
import config

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', '8XRxYeeymuCa2URjWcg6AIKPo')
CORS(app, supports_credentials=True)

# 版本号用于缓存清理 - 使用当前时间戳
import time
APP_VERSION = str(int(time.time()))

# 添加全局模板变量
@app.context_processor
def inject_version():
    return {'app_version': APP_VERSION}

# 设置缓存控制头
@app.after_request
def after_request(response):
    # 对静态资源设置较短的缓存时间
    if request.endpoint == 'static':
        response.headers['Cache-Control'] = 'public, max-age=300'  # 5分钟
    # 对API响应禁用缓存
    elif request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    # 对HTML页面设置较短缓存
    elif response.content_type.startswith('text/html'):
        response.headers['Cache-Control'] = 'no-cache, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
    return response

db = Database(config.DATABASE_PATH)
market_fetcher = MarketDataFetcher()
risk_manager = RiskManager(db)
performance_analyzer = PerformanceAnalyzer(db)
backtester = None  # 延迟初始化
trading_engines = {}
auto_trading = config.AUTO_TRADING

# ============ Helper Functions ============

def _create_trading_engine(model_id: int) -> TradingEngine:
    """
    创建TradingEngine实例（DRY原则：消除重复代码）

    Args:
        model_id: 模型ID

    Returns:
        TradingEngine实例

    Raises:
        Exception: 模型不存在或创建失败
    """
    model = db.get_model(model_id)
    if not model:
        raise Exception(f"Model {model_id} not found")

    return TradingEngine(
        model_id=model_id,
        db=db,
        market_fetcher=market_fetcher,
        ai_trader=AITrader(
            api_key=model['api_key'],
            api_url=model['api_url'],
            model_name=model['model_name'],
            system_prompt=model.get('system_prompt')  # 传递自定义prompt
        )
    )

def _get_current_market_prices():
    """
    获取当前市场价格（DRY原则：消除重复代码）

    Returns:
        dict: {coin: price} 或空字典（如果所有API都失败且无缓存）
    """
    try:
        prices_data = market_fetcher.get_current_prices(config.SUPPORTED_COINS)
        if not prices_data:
            # 所有API都失败且无缓存，返回空字典
            print(f'[ERROR] 未找到市场价格数据 - 所有API都失败且无缓存')
            return {}
        return {coin: prices_data[coin]['price'] for coin in prices_data if coin in prices_data}
    except Exception as e:
        print(f'[ERROR] 获取市场价格失败: {e}')
        import traceback
        traceback.print_exc()
        return {}

def _check_model_ownership(model_id: int, user_id: int) -> bool:
    """
    检查模型是否属于当前用户

    Args:
        model_id: 模型ID
        user_id: 用户ID

    Returns:
        是否拥有该模型
    """
    model = db.get_model(model_id)
    if not model:
        return False
    return model.get('user_id') == user_id

@app.route('/image/<path:filename>')
def serve_image(filename):
    """提供image目录下的静态文件"""
    from flask import send_from_directory
    return send_from_directory('image', filename)

@app.route('/')
def index():
    """主页（公开）"""
    return render_template('home.html')

@app.route('/login')
def login_page():
    """登录页面"""
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    """仪表板（需要登录）"""
    return render_template('dashboard.html')

# ============ Authentication APIs ============

@app.route('/api/auth/register', methods=['POST'])
def register():
    """用户注册"""
    data = request.json
    username = data.get('username')
    password = data.get('password')
    email = data.get('email')

    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400

    # 检查用户名是否已存在
    existing_user = db.get_user_by_username(username)
    if existing_user:
        return jsonify({'error': '用户名已存在'}), 400

    # 创建用户
    password_hash = hash_password(password)
    user_id = db.create_user(username, password_hash, email)

    # 自动登录
    set_current_user(user_id, username)

    return jsonify({
        'message': '注册成功',
        'user': {
            'id': user_id,
            'username': username,
            'email': email
        }
    })

@app.route('/api/auth/login', methods=['POST'])
def login():
    """用户登录"""
    data = request.json
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400

    # 验证用户
    user = db.get_user_by_username(username)
    if not user or not verify_password(user['password_hash'], password):
        return jsonify({'error': '用户名或密码错误'}), 401

    # 设置Session
    set_current_user(user['id'], user['username'])

    return jsonify({
        'message': '登录成功',
        'user': {
            'id': user['id'],
            'username': user['username'],
            'email': user.get('email')
        }
    })

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """用户登出"""
    clear_current_user()
    return jsonify({'message': '登出成功'})

@app.route('/api/auth/me', methods=['GET'])
@app.route('/api/user/info', methods=['GET'])
def get_current_user():
    """获取当前登录用户信息"""
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({'error': 'Not logged in'}), 401

    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    # 转换created_at为东八区时间
    created_at = user.get('created_at')
    if created_at:
        created_at = utc_to_beijing(created_at)

    return jsonify({
        'id': user['id'],
        'username': user['username'],
        'email': user.get('email'),
        'created_at': created_at
    })

# ============ Model APIs ============

@app.route('/api/models', methods=['GET'])
@login_required
def get_models():
    """获取当前用户的模型列表"""
    user_id = get_current_user_id()
    models = db.get_all_models(user_id=user_id)
    return jsonify(models)

@app.route('/api/models/<int:model_id>', methods=['GET'])
@login_required
def get_model(model_id):
    """获取单个模型详情"""
    user_id = get_current_user_id()

    # 检查权限
    if not _check_model_ownership(model_id, user_id):
        return jsonify({'error': '无权访问此模型'}), 403

    model = db.get_model(model_id)
    if not model:
        return jsonify({'error': '模型不存在'}), 404

    return jsonify(model)

@app.route('/api/models', methods=['POST'])
@login_required
def add_model():


    # 根据 TRADING_MODE 决定初始资金
    if config.TRADING_MODE == 'okx_demo':
        # OKX 模式：从 OKX API 获取初始余额
        try:
            from okx_trader import OKXTrader
            okx_trader = OKXTrader()
            balance_data = okx_trader.get_balance()
            if balance_data and 'error' not in balance_data:
                # 计算所有币种的总价值
                balances = balance_data.get('balances', {})
                initial_capital = 0.0
                for ccy, bal in balances.items():
                    initial_capital += bal.get('total', 0)
                print(f"[INFO] OKX 模式：使用 OKX 余额作为初始资金: {initial_capital}")
            else:
                # 如果获取失败，使用用户输入的值
                initial_capital = float(data.get('initial_capital', 10000))
                print(f"[WARN] OKX 余额获取失败，使用用户输入值: {initial_capital}")
        except Exception as e:
            print(f"[ERROR] 获取 OKX 余额失败: {e}")
            initial_capital = float(data.get('initial_capital', 10000))
    else:
        # 模拟模式：使用用户输入的初始资金
        initial_capital = float(data.get('initial_capital', 10000))


    """创建新模型（需要登录）"""
    user_id = get_current_user_id()
    data = request.json
    model_id = db.add_model(
        user_id=user_id,
        name=data['name'],
        api_key=data['api_key'],
        api_url=data['api_url'],
        model_name=data['model_name'],
        initial_capital=initial_capital,
        system_prompt=data.get('system_prompt'))

    try:
        trading_engines[model_id] = _create_trading_engine(model_id)
        print(f"[INFO] 模型 {model_id} ({data['name']}) 初始化成功")
    except Exception as e:
        print(f"[ERROR] 初始化模型 {model_id} 失败: {e}")

    return jsonify({'id': model_id, 'message': 'Model added successfully'})

@app.route('/api/models/<int:model_id>', methods=['PUT'])
@login_required
def update_model(model_id):
    """更新模型（只允许更新system_prompt）"""
    user_id = get_current_user_id()

    # 检查权限
    if not _check_model_ownership(model_id, user_id):
        return jsonify({'error': '无权操作此模型'}), 403

    try:
        data = request.get_json()

        # 只允许更新system_prompt
        if 'system_prompt' not in data:
            return jsonify({'error': '缺少system_prompt参数'}), 400

        # 检查是否有其他字段（防止F12修改参数漏洞）
        allowed_fields = {'system_prompt'}
        if not set(data.keys()).issubset(allowed_fields):
            return jsonify({'error': '只允许修改交易策略'}), 400

        system_prompt = data['system_prompt']

        # 更新数据库
        db.update_model_prompt(model_id, system_prompt)

        # 重新创建trading engine（使用新的prompt）
        if model_id in trading_engines:
            del trading_engines[model_id]
        trading_engines[model_id] = _create_trading_engine(model_id)

        print(f"[INFO] 模型 {model_id} 更新成功")
        return jsonify({'message': 'Model updated successfully'})
    except Exception as e:
        print(f"[ERROR] 更新模型 {model_id} 失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/models/<int:model_id>', methods=['DELETE'])
@login_required
def delete_model(model_id):
    """删除模型（需要登录且拥有该模型）"""
    user_id = get_current_user_id()

    # 检查权限
    if not _check_model_ownership(model_id, user_id):
        return jsonify({'error': '无权操作此模型'}), 403

    try:
        model = db.get_model(model_id)
        model_name = model['name'] if model else f"ID-{model_id}"

        db.delete_model(model_id)
        if model_id in trading_engines:
            del trading_engines[model_id]

        print(f"[INFO] 模型 {model_id} ({model_name}) 删除成功")
        return jsonify({'message': 'Model deleted successfully'})
    except Exception as e:
        print(f"[ERROR] 删除模型 {model_id} 失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/models/<int:model_id>/portfolio', methods=['GET'])
@login_required
def get_portfolio(model_id):
    """获取投资组合（需要登录且拥有该模型）"""
    user_id = get_current_user_id()

    # 检查权限
    if not _check_model_ownership(model_id, user_id):
        return jsonify({'error': '无权访问此模型'}), 403

    current_prices = _get_current_market_prices()

    portfolio = db.get_portfolio(model_id, current_prices)
    account_value = db.get_account_value_history(model_id, limit=100)

    return jsonify({
        'portfolio': portfolio,
        'account_value_history': account_value
    })

@app.route('/api/models/<int:model_id>/trades', methods=['GET'])
@login_required
def get_trades(model_id):
    """获取交易记录（需要登录且拥有该模型）"""
    user_id = get_current_user_id()

    # 检查权限
    if not _check_model_ownership(model_id, user_id):
        return jsonify({'error': '无权访问此模型'}), 403

    limit = request.args.get('limit', 50, type=int)
    trades = db.get_trades(model_id, limit=limit)

    # 转换时间为东八区
    for trade in trades:
        if 'timestamp' in trade:
            trade['timestamp'] = utc_to_beijing(trade['timestamp'])

    return jsonify(trades)

@app.route('/api/models/<int:model_id>/conversations', methods=['GET'])
@login_required
def get_conversations(model_id):
    """获取AI对话记录（需要登录且拥有该模型）"""
    user_id = get_current_user_id()

    # 检查权限
    if not _check_model_ownership(model_id, user_id):
        return jsonify({'error': '无权访问此模型'}), 403

    limit = request.args.get('limit', 20, type=int)
    conversations = db.get_conversations(model_id, limit=limit)

    # 过滤掉空响应（AI调用失败的记录）
    valid_conversations = []
    for conv in conversations:
        # 转换时间为东八区
        if 'timestamp' in conv:
            conv['timestamp'] = utc_to_beijing(conv['timestamp'])

        # 过滤掉空响应
        ai_response = conv.get('ai_response', '')
        if ai_response and ai_response.strip() not in ['{}', '']:
            valid_conversations.append(conv)

    return jsonify(valid_conversations)

@app.route('/api/models/<int:model_id>/risk', methods=['GET'])
@login_required
def get_risk_metrics(model_id):
    """获取风险指标（需要登录且拥有该模型）"""
    user_id = get_current_user_id()

    # 检查权限
    if not _check_model_ownership(model_id, user_id):
        return jsonify({'error': '无权访问此模型'}), 403

    current_prices = _get_current_market_prices()
    portfolio = db.get_portfolio(model_id, current_prices)
    risk_metrics = risk_manager.get_risk_metrics(model_id, portfolio)
    return jsonify(risk_metrics)

@app.route('/api/backtest', methods=['POST'])
def run_backtest():
    """运行回测"""
    global backtester

    data = request.json
    model_config = {
        'api_key': data.get('api_key'),
        'api_url': data.get('api_url'),
        'model_name': data.get('model_name')
    }
    start_date = data.get('start_date')
    end_date = data.get('end_date')
    initial_capital = data.get('initial_capital', 10000)

    # 延迟初始化backtester
    if backtester is None:
        ai_trader = AITrader(
            api_key=model_config['api_key'],
            api_url=model_config['api_url'],
            model_name=model_config['model_name']
        )
        backtester = Backtester(db, market_fetcher, ai_trader)

    try:
        result = backtester.run_backtest(
            model_config, start_date, end_date, initial_capital
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/models/<int:model_id>/performance', methods=['GET'])
@login_required
def get_performance(model_id):
    """获取绩效分析（需要登录且拥有该模型）"""
    user_id = get_current_user_id()

    # 检查权限
    if not _check_model_ownership(model_id, user_id):
        return jsonify({'error': '无权访问此模型'}), 403

    try:
        performance = performance_analyzer.analyze_performance(model_id)
        return jsonify(performance)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/user/analytics', methods=['GET'])
@login_required
def get_user_analytics():
    """获取当前用户所有模型的详细分析数据（Dashboard绩效分析页面）"""
    user_id = get_current_user_id()

    try:
        models = db.get_all_models(user_id=user_id)

        overall_stats = []
        advanced_analytics = []

        for model in models:
            model_id = model['id']

            # 获取最新账户价值
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT total_value FROM account_values
                WHERE model_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
            ''', (model_id,))
            row = cursor.fetchone()

            if not row:
                conn.close()
                continue

            total_value = row['total_value']
            initial_capital = model['initial_capital']
            total_pnl = total_value - initial_capital
            return_pct = (total_pnl / initial_capital) * 100

            # 获取交易统计
            cursor.execute('''
                SELECT
                    COUNT(*) as trade_count,
                    SUM(CASE WHEN signal = 'buy_to_enter' OR signal = 'sell_to_enter' THEN price * quantity * 0.001 ELSE 0 END) as total_fees,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as win_count,
                    MAX(pnl) as biggest_win,
                    MIN(pnl) as biggest_loss
                FROM trades
                WHERE model_id = ?
            ''', (model_id,))
            trade_stats = cursor.fetchone()
            conn.close()

            trade_count = trade_stats['trade_count'] or 0
            win_count = trade_stats['win_count'] or 0
            win_rate = (win_count / trade_count * 100) if trade_count > 0 else 0
            total_fees = trade_stats['total_fees'] or 0
            biggest_win = trade_stats['biggest_win'] or 0
            biggest_loss = trade_stats['biggest_loss'] or 0

            # 获取详细绩效分析
            try:
                performance = performance_analyzer.analyze_performance(model_id)
                risk_metrics = performance.get('risk_metrics', {})
                trading_stats = performance.get('trading_stats', {})

                sharpe_ratio = risk_metrics.get('sharpe_ratio', 0)
                sortino_ratio = risk_metrics.get('sortino_ratio', 0)
                calmar_ratio = risk_metrics.get('calmar_ratio', 0)
                max_drawdown = risk_metrics.get('max_drawdown', 0)
                volatility = risk_metrics.get('volatility', 0)
                avg_win = trading_stats.get('avg_win', 0)
                avg_loss = trading_stats.get('avg_loss', 0)
                profit_factor = trading_stats.get('profit_factor', 0)
            except:
                sharpe_ratio = 0
                sortino_ratio = 0
                calmar_ratio = 0
                max_drawdown = 0
                volatility = 0
                avg_win = 0
                avg_loss = 0
                profit_factor = 0

            # Overall Stats数据
            overall_stats.append({
                'model_id': model_id,
                'model_name': model['name'],
                'return_pct': return_pct,
                'total_value': total_value,
                'total_pnl': total_pnl,
                'fees': total_fees,
                'win_rate': win_rate,
                'biggest_win': biggest_win,
                'biggest_loss': biggest_loss,
                'sharpe': sharpe_ratio,
                'trades': trade_count
            })

            # Advanced Analytics数据
            advanced_analytics.append({
                'model_id': model_id,
                'model_name': model['name'],
                'sharpe': sharpe_ratio,
                'sortino': sortino_ratio,
                'calmar': calmar_ratio,
                'max_drawdown': max_drawdown,
                'volatility': volatility,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'profit_factor': profit_factor,
                'trades': trade_count
            })

        # 按收益率排序
        overall_stats.sort(key=lambda x: x['return_pct'], reverse=True)
        advanced_analytics.sort(key=lambda x: x['sharpe'], reverse=True)

        return jsonify({
            'overall_stats': overall_stats,
            'advanced_analytics': advanced_analytics
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/dashboard/top-coins', methods=['GET'])
def get_top_coins():
    """获取顶部币种价格栏数据（公开API）"""
    try:
        prices = _get_current_market_prices()

        # 如果没有价格数据，返回错误
        if not prices:
            return jsonify({'error': 'Market data unavailable - all APIs failed and no cache'}), 503

        result = []
        for coin in config.SUPPORTED_COINS:
            price = prices.get(coin, 0)
            if price == 0:
                # 跳过没有价格的币种
                continue
            # 计算24小时涨跌幅（这里简化处理，实际应该从历史数据计算）
            change_24h = (hash(coin) % 20 - 10) / 100  # 模拟数据，实际应该从API获取
            result.append({
                'symbol': coin,
                'price': price,
                'change_24h': change_24h
            })

        if not result:
            return jsonify({'error': 'No market data available'}), 503

        return jsonify(result)
    except Exception as e:
        print(f'[ERROR] 获取顶部币种价格栏数据失败，异常信息: {e}')
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/dashboard/total-stats', methods=['GET'])
def get_total_stats():
    """获取全平台总统计数据（公开API）"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()

        # 获取所有模型的最新总价值
        cursor.execute('''
            SELECT m.id, m.initial_capital, av.total_value
            FROM models m
            LEFT JOIN (
                SELECT model_id, total_value
                FROM account_values
                WHERE (model_id, timestamp) IN (
                    SELECT model_id, MAX(timestamp)
                    FROM account_values
                    GROUP BY model_id
                )
            ) av ON m.id = av.model_id
        ''')

        models = cursor.fetchall()
        conn.close()

        total_value = 0
        total_pnl = 0

        for model in models:
            if model['total_value']:
                total_value += model['total_value']
                total_pnl += (model['total_value'] - model['initial_capital'])

        return jsonify({
            'total_value': total_value,
            'total_pnl': total_pnl
        })
    except Exception as e:
        print(f'[ERROR] 获取全平台总统计数据失败，异常信息: {e}')
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/dashboard/detailed-leaderboard', methods=['GET'])
def get_detailed_leaderboard():
    """获取详细排行榜数据（公开API）"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()

        # 获取所有模型及其统计数据
        cursor.execute('''
            SELECT
                m.id,
                m.name,
                m.initial_capital,
                av.total_value,
                COUNT(DISTINCT t.id) as trade_count,
                SUM(CASE WHEN t.signal = 'buy_to_enter' OR t.signal = 'sell_to_enter' THEN t.price * t.quantity * 0.001 ELSE 0 END) as total_fees,
                SUM(CASE WHEN t.pnl > 0 THEN 1 ELSE 0 END) as win_count,
                MAX(t.pnl) as biggest_win,
                MIN(t.pnl) as biggest_loss
            FROM models m
            LEFT JOIN (
                SELECT model_id, total_value
                FROM account_values
                WHERE (model_id, timestamp) IN (
                    SELECT model_id, MAX(timestamp)
                    FROM account_values
                    GROUP BY model_id
                )
            ) av ON m.id = av.model_id
            LEFT JOIN trades t ON m.id = t.model_id
            GROUP BY m.id
        ''')

        models = cursor.fetchall()
        conn.close()

        leaderboard = []
        for model in models:
            if not model['total_value']:
                continue

            total_value = model['total_value']
            initial_capital = model['initial_capital']
            total_pnl = total_value - initial_capital
            return_pct = (total_pnl / initial_capital) * 100

            trade_count = model['trade_count'] or 0
            win_count = model['win_count'] or 0
            win_rate = (win_count / trade_count * 100) if trade_count > 0 else 0

            leaderboard.append({
                'id': model['id'],
                'name': model['name'],
                'total_value': total_value,
                'return_pct': return_pct,
                'total_pnl': total_pnl,
                'fees': model['total_fees'] or 0,
                'win_rate': win_rate,
                'biggest_win': model['biggest_win'] or 0,
                'biggest_loss': model['biggest_loss'] or 0,
                'sharpe': 0,  # 简化处理，实际需要计算
                'trades': trade_count
            })

        # 按收益率排序（从高到低）
        leaderboard.sort(key=lambda x: x['return_pct'], reverse=True)

        # 只返回前100名
        leaderboard = leaderboard[:100]

        return jsonify(leaderboard)
    except Exception as e:
        print(f'[ERROR] 获取详细排行榜数据失败，异常信息: {e}')
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/dashboard/advanced-analytics', methods=['GET'])
def get_advanced_analytics():
    """获取高级分析数据（公开API）- 包含Sharpe、Sortino、Calmar等指标"""
    try:
        models = db.get_all_models()
        analytics = []

        for model in models:
            model_id = model['id']

            # 获取最新账户价值
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT total_value FROM account_values
                WHERE model_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
            ''', (model_id,))
            row = cursor.fetchone()

            if not row:
                conn.close()
                continue

            total_value = row['total_value']

            # 获取交易统计
            cursor.execute('''
                SELECT COUNT(*) as trade_count
                FROM trades
                WHERE model_id = ?
            ''', (model_id,))
            trade_stats = cursor.fetchone()
            conn.close()

            trade_count = trade_stats['trade_count'] or 0

            # 获取详细绩效分析
            try:
                performance = performance_analyzer.analyze_performance(model_id)
                risk_metrics = performance.get('risk_metrics', {})
                trading_stats = performance.get('trading_stats', {})

                sharpe_ratio = risk_metrics.get('sharpe_ratio', 0)
                sortino_ratio = risk_metrics.get('sortino_ratio', 0)
                calmar_ratio = risk_metrics.get('calmar_ratio', 0)
                max_drawdown = risk_metrics.get('max_drawdown', 0)
                volatility = risk_metrics.get('volatility', 0)
                avg_win = trading_stats.get('avg_win', 0)
                avg_loss = trading_stats.get('avg_loss', 0)
                profit_factor = trading_stats.get('profit_factor', 0)
            except:
                sharpe_ratio = 0
                sortino_ratio = 0
                calmar_ratio = 0
                max_drawdown = 0
                volatility = 0
                avg_win = 0
                avg_loss = 0
                profit_factor = 0

            analytics.append({
                'id': model_id,
                'name': model['name'],
                'sharpe': sharpe_ratio,
                'sortino': sortino_ratio,
                'calmar': calmar_ratio,
                'max_drawdown': max_drawdown,
                'volatility': volatility,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'profit_factor': profit_factor,
                'trades': trade_count
            })

        # 按夏普比率排序（从高到低）
        analytics.sort(key=lambda x: x['sharpe'], reverse=True)

        # 只返回前100名
        analytics = analytics[:100]

        return jsonify(analytics)
    except Exception as e:
        print(f'[ERROR] 获取高级分析数据失败，异常信息: {e}')
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/dashboard/performance-chart', methods=['GET'])
def get_performance_chart():
    """获取收益曲线图数据（公开API）- 前10名+倒数2名模型 + BTC基准"""
    try:
        # 获取时间筛选参数
        time_filter = request.args.get('timeFilter', 'all')
        print(f'[DEBUG] 时间筛选参数: {time_filter}')

        # 计算时间范围
        from datetime import datetime, timedelta
        current_time = datetime.now()

        if time_filter == '1d':
            start_time = current_time - timedelta(days=1)
        elif time_filter == '1w':
            start_time = current_time - timedelta(weeks=1)
        elif time_filter == '1m':
            start_time = current_time - timedelta(days=30)
        elif time_filter == '3m':
            start_time = current_time - timedelta(days=90)
        else:  # 'all'
            # 系统实际运行时间：2025-10-21 13:00:00（东八区）
            start_time = datetime(2025, 10, 21, 13, 0, 0) - timedelta(hours=8)  # 转换为UTC

        # 转换为UTC时间字符串用于数据库查询
        start_time_utc_str = start_time.strftime('%Y-%m-%d %H:%M:%S')
        print(f'[DEBUG] 查询开始时间（UTC）: {start_time_utc_str}')

        # 获取排行榜前10名+倒数2名
        models = db.get_all_models()
        leaderboard = []

        for model in models:
            # 从account_values表获取最新的total_value
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT total_value FROM account_values
                WHERE model_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
            ''', (model['id'],))
            row = cursor.fetchone()
            conn.close()

            if not row:
                continue

            total_value = row['total_value']
            total_return = ((total_value - model['initial_capital']) / model['initial_capital']) * 100

            leaderboard.append({
                'model_id': model['id'],
                'model_name': model['name'],
                'total_value': total_value,
                'total_return': total_return,
                'initial_capital': model['initial_capital']  # 添加初始资金
            })

        # 按收益率排序，取前10名+倒数2名
        leaderboard.sort(key=lambda x: x['total_return'], reverse=True)

        # 选择要显示的模型：前10名 + 倒数2名
        selected_models = []
        total_models = len(leaderboard)

        if total_models <= 12:
            # 如果总数不超过12个，显示全部
            selected_models = leaderboard
        else:
            # 前10名
            selected_models.extend(leaderboard[:10])
            # 倒数2名（避免重复）
            selected_models.extend(leaderboard[-2:])

        top_models = selected_models

        # 获取每个模型的历史账户价值数据
        result = []
        conn = db.get_connection()

        for model in top_models:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT timestamp, total_value
                FROM account_values
                WHERE model_id = ? AND timestamp >= ?
                ORDER BY timestamp ASC
            ''', (model['model_id'], start_time_utc_str))

            history = cursor.fetchall()
            print(f'[DEBUG] 模型 {model["model_name"]} (ID:{model["model_id"]}): 过滤后数据点数量: {len(history)}')

            data_points = []
            for row in history:
                # 将UTC时间转换为东八区时间
                beijing_time = utc_to_beijing(row['timestamp'])
                data_points.append({
                    'time': beijing_time,
                    'value': row['total_value']
                })

            result.append({
                'model_id': model['model_id'],
                'model_name': model['model_name'],
                'initial_capital': model['initial_capital'],  # 添加初始资金
                'data': data_points
            })

        # 添加BTC基准线（从2025-10-21开始，假设初始10000美元买入BTC并持有）
        # 获取当前东八区时间用于显示
        current_time = get_current_beijing_time_str()

        # 获取BTC当前价格（只使用真实数据）
        try:
            btc_prices = market_fetcher.get_current_prices(['BTC'])
            if not btc_prices or 'BTC' not in btc_prices:
                print('[ERROR] 无法获取BTC价格，跳过BTC基准线')
                btc_current_price = None
            else:
                btc_current_price = btc_prices.get('BTC', {}).get('price')
        except Exception as e:
            print(f'[ERROR] 获取BTC价格失败，异常信息: {e}')
            btc_current_price = None

        # 获取BTC历史价格（从base_time开始，只使用真实数据）
        btc_historical_data = []
        if btc_current_price:
            try:
                btc_historical = market_fetcher.get_historical_prices('BTC', days=30)
                if btc_historical and len(btc_historical) > 0:
                    btc_historical_data = btc_historical
                else:
                    print('[WARN] 无法获取BTC历史数据，无法计算BTC基准线收益')
            except Exception as e:
                print(f'[ERROR] 获取BTC历史数据失败，异常信息: {e}')

        # 只有在有真实BTC数据时才添加BTC基准线
        if btc_current_price and btc_historical_data:
            # 过滤BTC历史数据，只保留系统运行时间之后的数据
            filtered_btc_data = []
            for hist_point in btc_historical_data:
                # timestamp可能是整数（Unix时间戳毫秒）或字符串
                timestamp = hist_point['timestamp']
                if isinstance(timestamp, int):
                    # Unix时间戳（毫秒）转换为datetime
                    hist_time_utc = datetime.fromtimestamp(timestamp / 1000)
                else:
                    # 字符串格式
                    hist_time_utc = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')

                if hist_time_utc >= start_time:
                    filtered_btc_data.append(hist_point)

            if not filtered_btc_data:
                print(f'[WARN] 无法获取在 {start_time_utc_str} 之后的BTC历史数据')
            else:
                # 使用过滤后的最早价格作为初始价格
                btc_initial_price = filtered_btc_data[0]['price']

                # 计算BTC持有收益
                # 假设初始10000美元全部买入BTC
                btc_quantity = 10000 / btc_initial_price

                # 构建BTC基准线数据点（只包含系统运行时间之后的数据）
                btc_baseline_data = []
                for hist_point in filtered_btc_data:
                    btc_value = btc_quantity * hist_point['price']
                    # 转换时间为东八区
                    timestamp = hist_point['timestamp']
                    if isinstance(timestamp, int):
                        # Unix时间戳（毫秒）转换为UTC字符串
                        hist_time_utc_str = datetime.fromtimestamp(timestamp / 1000).strftime('%Y-%m-%d %H:%M:%S')
                        hist_time_beijing = utc_to_beijing(hist_time_utc_str)
                    else:
                        hist_time_beijing = utc_to_beijing(timestamp)

                    btc_baseline_data.append({
                        'time': hist_time_beijing,
                        'value': btc_value
                    })

                # 添加当前时间点
                btc_current_value = btc_quantity * btc_current_price
                btc_baseline_data.append({
                    'time': current_time,
                    'value': btc_current_value
                })

                result.append({
                    'model_id': 'BTC_BASELINE',
                    'model_name': 'BTC基准',
                    'data': btc_baseline_data
                })
        else:
            print('[WARN] 无法获取BTC历史数据，无法计算BTC基准线收益')

        conn.close()

        return jsonify(result)
    except Exception as e:
        print(f'[ERROR] 获取性能图表失败，异常信息: {e}')
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/dashboard/recent-trades', methods=['GET'])
def get_recent_trades():
    """获取最近交易动态（公开API）"""
    try:
        limit = int(request.args.get('limit', 100))

        conn = db.get_connection()
        cursor = conn.cursor()

        # 获取最近的交易记录，包含模型名称
        cursor.execute('''
            SELECT
                t.id,
                t.model_id,
                m.name as model_name,
                t.coin,
                t.signal,
                t.quantity,
                t.price,
                t.leverage,
                t.pnl,
                t.timestamp
            FROM trades t
            JOIN models m ON t.model_id = m.id
            ORDER BY t.timestamp DESC
            LIMIT ?
        ''', (limit,))

        trades = []
        for row in cursor.fetchall():
            # 将UTC时间转换为东八区时间
            beijing_time = utc_to_beijing(row['timestamp'])
            trades.append({
                'id': row['id'],
                'model_id': row['model_id'],
                'model_name': row['model_name'],
                'coin': row['coin'],
                'action': row['signal'],  # 映射signal到action
                'quantity': row['quantity'],
                'price': row['price'],
                'leverage': row['leverage'],
                'pnl': row['pnl'],
                'created_at': beijing_time  # 映射timestamp到created_at，并转换为东八区
            })

        conn.close()
        return jsonify(trades)
    except Exception as e:
        print(f'[ERROR] 获取最近交易动态失败，异常信息: {e}')
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/leaderboard', methods=['GET'])
def get_leaderboard():
    """获取排行榜（公开API，不需要登录）- 增强版，支持多维度排序"""
    sort_by = request.args.get('sort_by', 'returns')

    # 获取所有模型
    all_models = db.get_all_models()
    current_prices = _get_current_market_prices()

    leaderboard = []
    for model in all_models:
        portfolio = db.get_portfolio(model['id'], current_prices)

        # 获取用户名
        user = db.get_user_by_id(model.get('user_id'))
        username = user['username'] if user else 'Unknown'

        # 计算收益率
        account_value = portfolio.get('total_value', model['initial_capital'])
        returns = ((account_value - model['initial_capital']) / model['initial_capital']) * 100

        # 计算胜率
        trades = db.get_trades(model['id'], limit=100)
        winning_trades = [t for t in trades if t['pnl'] > 0]
        win_rate = (len(winning_trades) / len(trades)) if trades else 0

        # 计算夏普比率（简化版）
        if len(trades) > 1:
            returns_list = [t['pnl'] for t in trades]
            avg_return = sum(returns_list) / len(returns_list)
            std_return = (sum((r - avg_return) ** 2 for r in returns_list) / len(returns_list)) ** 0.5
            sharpe_ratio = (avg_return / std_return) if std_return > 0 else 0
        else:
            sharpe_ratio = 0

        # 计算最大回撤
        max_drawdown = risk_manager._calculate_max_drawdown(model['id'])

        leaderboard.append({
            'model_id': model['id'],
            'model_name': model['name'],
            'username': username,
            'total_value': account_value,
            'total_return': returns,
            'sharpe_ratio': sharpe_ratio,
            'win_rate': win_rate,
            'max_drawdown': max_drawdown,
            'total_trades': len(trades)
        })

    # 排序
    if sort_by == 'returns':
        leaderboard.sort(key=lambda x: x['total_return'], reverse=True)
    elif sort_by == 'sharpe':
        leaderboard.sort(key=lambda x: x['sharpe_ratio'], reverse=True)
    elif sort_by == 'win_rate':
        leaderboard.sort(key=lambda x: x['win_rate'], reverse=True)
    elif sort_by == 'drawdown':
        leaderboard.sort(key=lambda x: x['max_drawdown'])

    return jsonify(leaderboard)

@app.route('/api/market/prices', methods=['GET'])
def get_market_prices():
    """获取市场价格（公开API）"""
    prices = market_fetcher.get_current_prices(config.SUPPORTED_COINS)
    return jsonify(prices)

@app.route('/api/market/historical/<coin>', methods=['GET'])
def get_historical_prices(coin):
    """获取历史价格数据"""
    days = request.args.get('days', 30, type=int)
    try:
        historical = market_fetcher.get_historical_prices(coin, days=days)
        return jsonify(historical)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/models/<int:model_id>/execute', methods=['POST'])
@login_required
def execute_trading(model_id):
    """执行交易（需要登录且拥有该模型）"""
    user_id = get_current_user_id()

    # 检查权限
    if not _check_model_ownership(model_id, user_id):
        return jsonify({'error': '无权操作此模型'}), 403

    if model_id not in trading_engines:
        try:
            trading_engines[model_id] = _create_trading_engine(model_id)
        except Exception as e:
            return jsonify({'error': str(e)}), 404

    try:
        result = trading_engines[model_id].execute_trading_cycle()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def trading_loop():
    print("[INFO] 交易循环已启动")
    
    while auto_trading:
        try:
            if not trading_engines:
                time.sleep(30) # 等待30秒，确保模型初始化完成
                continue
            
            print(f"[CYCLE] {get_current_beijing_time_str()}")  # 日志显示东八区时间
            print(f"[INFO] 当前交易模型数量: {len(trading_engines)}")
            
            for model_id, engine in list(trading_engines.items()):
                try:
                    print(f"\n[EXEC] 执行模型交易 {model_id}")
                    result = engine.execute_trading_cycle()
                    
                    if result.get('success'):
                        print(f"[OK] 模型 {model_id} 执行成功")
                        if result.get('executions'):
                            for exec_result in result['executions']:
                                signal = exec_result.get('signal', 'unknown')
                                coin = exec_result.get('coin', 'unknown')
                                msg = exec_result.get('message', '')
                                if signal != 'hold':
                                    print(f"  [TRADE] {coin}: {msg}")
                    else:
                        error = result.get('error', 'Unknown error')
                        print(f"[WARN] 模型 {model_id} 执行失败: {error}")
                        
                except Exception as e:
                    print(f"[ERROR] 模型 {model_id} 异常: {e}")
                    import traceback
                    print(traceback.format_exc())
                    continue
            print(f"[SLEEP] 等待三分钟进行下次交易")
            
            trading_interval = int(os.getenv('TRADING_INTERVAL', '180'))
            time.sleep(trading_interval)
            
        except Exception as e:
            print(f"\n[CRITICAL] 交易循环异常: {e}")
            import traceback
            print(traceback.format_exc())
            print("[RETRY] 60秒后重试\n")
            time.sleep(60) # 等待60秒，确保模型执行完成
    
    print("[INFO] 交易循环已停止")



def init_trading_engines():
    try:
        models = db.get_all_models()

        if not models:
            print("[WARN] 未找到交易模型")
            return

        print(f"\n[INIT] 初始化交易引擎...")
        for model in models:
            model_id = model['id']
            model_name = model['name']

            try:
                trading_engines[model_id] = _create_trading_engine(model_id)
                print(f"  [OK] 模型 {model_id} ({model_name}) 初始化成功")
            except Exception as e:
                print(f"  [ERROR] 模型 {model_id} ({model_name}): {e}")
                continue

        print(f"[INFO] 初始化交易引擎成功，共 {len(trading_engines)} 个引擎\n")

    except Exception as e:
        print(f"[ERROR] 初始化交易引擎失败: {e}\n")

if __name__ == '__main__':
    db.init_db()
    print("启动交易平台")
    
    init_trading_engines()
    
    if auto_trading:
        trading_thread = threading.Thread(target=trading_loop, daemon=True)
        trading_thread.start()
    
    print(f"Server: http://localhost:{config.PORT}")

    app.run(debug=config.DEBUG, host=config.HOST, port=config.PORT, use_reloader=False)
