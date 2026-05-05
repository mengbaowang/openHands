# 项目结构

## 核心运行
- `app.py`：Flask 入口、路由、登录注册、模型管理和页面渲染。
- `trading_engine.py`：编排层，负责取数、调用 AI，并把决策交给执行层。
- `services/execution_service.py`：OKX 执行层，负责下单、平仓、风控同步和持仓状态管理。
- `ai_trader.py`：提示词组装、LLM 调用与响应解析。
- `market_data.py`：多源行情获取、缓存与技术指标计算。
- `database.py`：SQLite 持久化，保存用户、模型、交易、持仓、对话和账户历史。
- `config.py`：统一配置与环境变量入口。

## 交易与分析服务
- `okx_trader.py`：OKX API 封装，提供余额、持仓、下单和原生 TP/SL。
- `services/execution/position_metrics.py`：持仓收益、峰值利润与移动止损计算。
- `services/exchanges/okx_adapter.py`：OKX 适配器的稳定导入入口。
- `services/risk_manager.py`：风险评分、持仓预警与回撤监控。
- `services/backtester.py`：历史回测。
- `services/performance_analyzer.py`：绩效分析与风险报表。

## 工具层
- `utils/auth.py`：登录鉴权、Session 管理和密码工具。
- `utils/logger.py`：运行时日志与按日滚动。
- `utils/timezone.py`：UTC 与北京时间转换。
- `utils/exceptions.py`：项目自定义异常。

## 前端
- `templates/home.html`：公共首页。
- `templates/login.html`：登录页。
- `templates/dashboard.html`：登录后的控制台。
- `templates/index.html`：备用首页模板。
- `static/app.js`：控制台前端逻辑。
- `static/home.js`：公共首页前端逻辑。
- `static/auth.js`：登录注册前端逻辑。
- `static/style.css`：全站样式。

## 数据与部署
- `trading_bot.db`：SQLite 数据库。
- `market_data_cache.json`：行情缓存文件。
- `trading_bot.log` / `logs/*`：运行日志。
- `Dockerfile` / `docker-compose.yml`：容器部署。
- `.env`：本地环境变量覆盖文件。
