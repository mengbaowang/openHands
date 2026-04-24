# AI交易器

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/flask-3.0+-green.svg)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

🚀 **专业级AI驱动的加密货币交易平台**

基于大语言模型的智能交易决策系统，支持自定义交易策略、实时市场数据、专业级绩效分析和实时交易大屏。

**🌐 在线体验**：[https://trade.easy2ai.com](https://trade.easy2ai.com)

> 本项目基于 [chadyi/AITradeGame](https://github.com/chadyi/AITradeGame) 开发，增加了用户系统、、智能AI响应解析器等功能。

---

## ✨ 核心特性

### 🎯 用户系统
- 🔐 **完整的用户认证** - 注册/登录，Session管理，密码加密
- 👤 **数据隔离** - 每个用户的模型和交易数据完全独立
- 🔒 **权限控制** - 基于装饰器的路由保护，确保数据安全
- 🏠 **公开主页** - 未登录用户可查看实时行情和排行榜

### 🤖 AI交易系统
- 🧠 **自定义交易策略** - 每个模型支持独立的Prompt，定义个性化交易风格
- 🎯 **智能决策** - 基于15+技术指标的AI分析和决策
- 🔍 **智能响应解析** - 5层解析策略，支持JSON、Markdown、思维链、纯文本等多种AI响应格式
- 📊 **决策透明化** - 详细的reasoning字段，展示AI的思考过程
- 🔄 **自动交易** - 每3分钟自动执行交易循环
- 💰 **杠杆交易** - 支持1-20x杠杆，做多/做空双向交易
- 🔁 **自动重试** - AI调用失败自动重试（最多3次）

### 📊 实时交易大屏
- 📈 **顶部价格栏** - 实时显示BTC、ETH、SOL、BNB、DOGE、XRP价格和涨跌幅
- 📉 **收益曲线图** - ECharts绘制前6名模型+BTC基准的实时收益曲线
- 🔄 **交易动态** - 右侧滚动显示最新100条交易记录
- 🏆 **模型卡片** - 底部展示排行榜前6名的详细指标
- ⚡ **自动刷新** - 每5秒自动更新所有数据

### 📈 技术分析
- 📊 **15+技术指标** - SMA、EMA、MACD、RSI、布林带、ATR、ADX、CCI、威廉指标等
- 🎯 **止盈止损** - 自动风险控制，保护收益，限制损失
- 📉 **风险管理** - 实时风险评分、仓位管理、最大回撤监控
- 💹 **K线图** - 支持6种币种的历史K线图和成交量

### 🏆 绩效分析
- 📊 **专业指标** - 夏普比率、Sortino比率、Calmar比率
- 📈 **多维度排行榜** - 按收益率、夏普比率、胜率、回撤排序
- 🔍 **回测系统** - 用历史数据测试策略表现
- 📉 **月度分析** - 每月收益统计和币种贡献度

### 🎨 用户体验
- 🌙 **暗黑模式** - 一键切换主题，自动保存偏好
- 📱 **响应式设计** - 完美适配手机、平板、桌面设备
- 🎯 **专业UI** - Bloomberg/TradingView级别的视觉效果
- ⚡ **高性能** - ECharts高性能渲染，流畅无卡顿

## 🛠️ 技术栈

- **后端**：Python 3.9+ / Flask 3.0
- **前端**：原生 JavaScript / ECharts 5.4.3
- **数据库**：SQLite
- **AI 接口**：OpenAI 兼容格式（支持 OpenAI、DeepSeek、Claude、Kimi 等）
- **实时通信**：WebSocket (Flask-SocketIO)
- **API 限流**：Flask-Limiter
- **部署**：Docker / Docker Compose / Gunicorn

## 🚀 快速开始

### 方式一：本地运行

#### 1. 克隆项目

```bash
git clone https://github.com/yourusername/AITradeGame.git
cd AITradeGame
```

#### 2. 安装依赖

```bash
pip install -r requirements.txt
```

#### 3. 配置环境变量（可选）

```bash
cp .env.example .env
# 编辑 .env 文件，设置 SECRET_KEY 等配置
```

#### 4. 启动服务器

```bash
python app.py
```

#### 5. 访问平台

打开浏览器访问：`http://localhost:35008`

---

### 方式二：Docker部署（推荐）

#### 1. 克隆项目

```bash
git clone https://github.com/yourusername/AITradeGame.git
cd AITradeGame
```

#### 2. 配置环境变量

```bash
cp .env.example .env
```

#### 3. 启动容器

```bash
docker-compose up -d
```

#### 4. 查看日志

```bash
docker-compose logs -f
```

#### 5. 访问平台

打开浏览器访问：`http://localhost:35008`

#### 6. 停止服务

```bash
docker-compose down
```

---

### 页面说明

- **公开主页** (`/`) - 实时交易大屏
  - 顶部币种价格栏
  - 收益曲线图（前6名模型 + BTC基准）
  - 实时交易动态
  - 排行榜前6名模型卡片

- **登录页面** (`/login`) - 注册/登录
  - Tab切换注册和登录表单
  - 密码加密存储

- **交易仪表板** (`/dashboard`) - 登录后的主界面
  - 创建和管理交易模型
  - 查看投资组合和交易历史
  - 查看AI对话和决策过程
  - 查看风险指标和K线图

### 注册账号

#### 方式一：普通注册

1. 点击右下角"Login / Register"按钮
2. 切换到"注册"标签
3. 填写信息：
   - 用户名（至少3位）
   - 密码（至少6位）
   - 邮箱（可选）
4. 点击"注册"按钮
5. 自动跳转到交易仪表板

### 创建交易模型

登录后，点击"添加模型"按钮，填写：

#### 基本信息
- **模型名称** - 例如：GPT-4保守型
- **API密钥** - 你的AI模型API密钥
- **API地址** - 例如：https://api.openai.com
- **模型标识** - 例如：gpt-4
- **初始资金** - 默认10000美元

#### 交易策略（可选）
自定义AI的交易策略Prompt，例如：
```
你是一个保守型交易员，专注于长期稳定收益。

交易规则：
1. 只在RSI<30时买入，RSI>70时卖出
2. 每笔交易风险不超过1%
3. 使用1-3x杠杆
4. 严格执行止损，保护本金
5. 不追涨杀跌，耐心等待机会

请根据市场数据做出理性决策。
```

**提示**：如果不填写，系统会使用默认的专业交易策略。

## ⚙️ 配置

### 环境变量

复制 `.env.example` 为 `.env` 并根据实际情况修改：

```bash
# 安全配置（生产环境必须修改）
SECRET_KEY=change-this-to-a-random-secret-key-in-production

# 服务器配置
HOST=0.0.0.0
PORT=35008
DEBUG=False

# 数据库配置
DATABASE_PATH=trading_bot.db


# 交易配置
AUTO_TRADING=True
TRADING_INTERVAL=180
```

**生成安全的SECRET_KEY**：
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## 📁 项目结构

```
AI交易器/
├── app.py                      # Flask应用主程序（900+行）
├── config.py                   # 统一配置管理
├── trading_engine.py           # 交易逻辑引擎
├── ai_trader.py                # AI集成模块（智能响应解析器）
├── database.py                 # 数据持久化层（用户、模型、交易、账户）
├── market_data.py              # 市场数据接口（Binance + CoinGecko + CoinCap）
├── services/                   # 业务服务层
│   ├── risk_manager.py         # 风险管理器（风险评分、仓位控制）
│   ├── backtester.py           # 回测系统（历史数据测试）
│   └── performance_analyzer.py # 绩效分析（夏普、Sortino、Calmar）
├── utils/                      # 工具模块
│   ├── auth.py                 # 用户认证（登录验证、权限控制、OAuth）
│   ├── timezone.py             # 时区处理（UTC ↔ 东八区）
│   ├── logger.py               # 日志系统
│   └── exceptions.py           # 自定义异常
├── static/                     # 前端资源
│   ├── app.js                  # 仪表板逻辑（1000+行）
│   ├── home.js                 # 主页逻辑（实时交易大屏）
│   ├── auth.js                 # 登录/注册逻辑
│   └── style.css               # 全局样式（1500+行）
├── templates/                  # HTML模板
│   ├── home.html               # 公开主页（实时交易大屏）
│   ├── login.html              # 登录/注册页面
│   └── dashboard.html          # 交易仪表板
├── Dockerfile                  # Docker镜像构建文件
├── docker-compose.yml          # Docker编排配置
├── .env.example                # 环境变量示例
├── requirements.txt            # Python依赖
├── CHANGELOG.md                # 版本更新日志
└── README.md                   # 项目文档
```

## 🤖 支持的 AI 模型

兼容 OpenAI 格式的 API：
- **OpenAI** - gpt-4, gpt-4-turbo, gpt-3.5-turbo
- **DeepSeek** - deepseek-chat, deepseek-coder
- **Claude** - claude-3-opus, claude-3-sonnet（通过 OpenRouter）
- **Kimi** - moonshot-v1-8k, moonshot-v1-32k
- **Qwen** - qwen-turbo, qwen-plus, qwen-max
- **其他** - 任何兼容OpenAI API格式的模型

### AI响应解析

本项目实现了**5层智能解析策略**，能够处理各种AI响应格式：

1. **思维链标签提取** - 支持 `<think>...</think>` 格式
2. **Markdown代码块** - 支持 ` ```json ... ``` ` 格式
3. **直接JSON解析** - 标准JSON格式
4. **正则表达式提取** - 从复杂文本中提取JSON
5. **智能文本分析** - 从纯文本中提取交易决策

即使AI返回格式不标准，系统也能智能提取有用信息，大大提高了兼容性和成功率。

## 🚀 使用方法

1. **启动服务器**
   ```bash
   python app.py
   ```

2. **添加 AI 模型**
   - 访问 `http://localhost:35008`
   - 点击"添加模型"
   - 填写模型配置（名称、API Key、API URL、模型名称、初始资金）

3. **自动交易**
   - 系统每 3 分钟自动执行一次交易循环
   - AI 分析市场数据并做出决策
   - 自动检查止盈止损条件

4. **监控与分析**
   - 实时查看投资组合
   - 查看交易历史和 AI 对话
   - 查看风险评分和警告
   - 多维度排行榜对比

## ⚙️ 配置说明

所有配置在 `config.py` 中统一管理：

```python
# 服务器配置
PORT = 35008
HOST = '0.0.0.0'

# 支持的币种
SUPPORTED_COINS = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE']

# 风险管理
MAX_RISK_PER_TRADE = 0.05      # 单笔交易最大风险 5%
MAX_POSITION_RATIO = 0.30       # 单币种最大持仓 30%
MAX_LEVERAGE = 20               # 最大杠杆倍数
MAX_DRAWDOWN_WARNING = 0.15     # 回撤警告阈值 15%
```

## 📊 API 接口

### 用户认证（需要登录）
- `POST /api/auth/register` - 用户注册
- `POST /api/auth/login` - 用户登录
- `POST /api/auth/logout` - 用户登出
- `GET /api/auth/me` - 获取当前用户信息

### 模型管理（需要登录）
- `GET /api/models` - 获取当前用户的所有模型
- `POST /api/models` - 创建新模型（支持自定义Prompt）
- `DELETE /api/models/<id>` - 删除模型（仅限所有者）

### 交易与数据（需要登录）
- `GET /api/models/<id>/portfolio` - 获取投资组合
- `GET /api/models/<id>/trades` - 获取交易历史
- `GET /api/models/<id>/conversations` - 获取AI对话记录
- `GET /api/models/<id>/risk` - 获取风险指标
- `GET /api/models/<id>/performance` - 获取绩效分析
- `POST /api/models/<id>/execute` - 手动执行交易
- `POST /api/backtest` - 回测交易策略

### 市场数据（公开API）
- `GET /api/market/prices` - 获取实时市场价格
- `GET /api/market/historical/<coin>` - 获取历史K线数据
- `GET /api/leaderboard?sort_by=returns` - 排行榜（支持returns/sharpe/win_rate/drawdown）

### 实时交易大屏（公开API）
- `GET /api/dashboard/top-coins` - 获取顶部币种价格栏数据
- `GET /api/dashboard/performance-chart` - 获取收益曲线图数据（前6名+BTC基准）
- `GET /api/dashboard/recent-trades` - 获取最近交易动态（最新100条）

## 🎯 使用场景

### 1. AI交易策略测试
- 测试不同的AI模型（GPT-4、Claude、DeepSeek等）的交易能力
- 对比不同Prompt策略的表现
- 验证技术指标的有效性

### 2. 量化交易学习
- 学习技术指标的使用
- 理解风险管理的重要性
- 掌握回测和绩效分析方法

### 3. 多模型对比
- 创建多个模型，使用不同的策略
- 通过排行榜对比表现
- 找到最优的交易策略

### 4. 教育演示
- 展示AI在金融领域的应用
- 演示量化交易的基本流程
- 可视化交易数据和绩效指标

---

## 🔧 高级功能

### 自定义交易策略

每个模型支持独立的Prompt，可以定义：
- **交易风格**：保守型、激进型、平衡型
- **风险偏好**：低风险、中风险、高风险
- **技术指标偏好**：RSI、MACD、布林带等
- **仓位管理**：固定仓位、动态仓位
- **止盈止损规则**：百分比、ATR倍数

### 回测系统

使用历史数据测试策略：
```bash
POST /api/backtest
{
  "model_id": 1,
  "start_date": "2024-01-01",
  "end_date": "2024-12-31",
  "initial_capital": 10000
}
```

返回：
- 总收益率
- 胜率
- 最大回撤
- 夏普比率
- 交易次数

### 绩效分析

专业级指标：
- **夏普比率** - 风险调整后收益
- **Sortino比率** - 只考虑下行风险
- **Calmar比率** - 收益/最大回撤
- **月度绩效** - 每月收益统计
- **币种贡献** - 各币种对总收益的贡献

---

## ⚠️ 注意事项

### 重要提示
- ✅ 这是一个**交易平台**（仅限纸面交易，不涉及真实资金）
- ✅ 需要有效的AI模型API密钥（OpenAI、DeepSeek等）
- ✅ 需要互联网连接以获取实时市场数据
- ⚠️ AI决策仅供参考，不构成投资建议
- ⚠️ 请勿用于实盘交易，风险自负

### 生产环境部署

#### Docker部署（推荐）

1. **克隆项目**
   ```bash
   git clone https://github.com/yourusername/AITradeGame.git
   cd AITradeGame
   ```

2. **配置环境变量**
   ```bash
   cp .env.example .env
   # 编辑 .env 文件，设置以下关键配置：
   # - SECRET_KEY（必须修改为随机字符串）
   ```

3. **启动服务**
   ```bash
   docker-compose up -d
   ```

4. **配置反向代理（Nginx示例）**
   ```nginx
   server {
       listen 80;
       server_name trade.easy2ai.com;

       # 重定向到HTTPS
       return 301 https://$server_name$request_uri;
   }

   server {
       listen 443 ssl http2;
       server_name trade.easy2ai.com;

       ssl_certificate /path/to/cert.pem;
       ssl_certificate_key /path/to/key.pem;

       location / {
           proxy_pass http://localhost:35008;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

5. **定期备份数据库**
   ```bash
   # 备份脚本
   docker exec ai-trade-simulator sqlite3 /app/data/trading_bot.db ".backup '/app/data/backup_$(date +%Y%m%d).db'"
   ```

#### 安全检查清单

- ✅ 设置强随机的 `SECRET_KEY`
- ✅ 使用 HTTPS（SSL/TLS证书）
- ✅ 配置防火墙（限制访问来源）
- ✅ 定期备份数据库
- ✅ 使用生产级WSGI服务器（Gunicorn）
- ✅ 设置合理的资源限制（CPU、内存）
- ✅ 配置日志轮转
- ✅ 监控系统资源和错误日志

---

## 📝 更新日志
- ✅ **完整的用户认证系统** - 注册/登录/权限控制
- ✅ **智能AI响应解析器** - 5层解析策略，支持多种格式
- ✅ **自定义交易策略Prompt** - 个性化AI交易风格
- ✅ **实时交易大屏** - Bloomberg级别的视觉效果
- ✅ **专业级绩效分析** - 夏普/Sortino/Calmar比率
- ✅ **回测系统** - 历史数据验证策略
- ✅ **暗黑模式** - 护眼舒适
- ✅ **Docker部署** - 一键启动
- ✅ **时区处理** - 精确的UTC ↔ 东八区转换

---

## 🤝 贡献

欢迎贡献代码、报告Bug或提出新功能建议！

### 贡献方式
1. Fork本项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启Pull Request

---

## 📄 许可证

本项目采用MIT许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

---

## ⚖️ 免责声明

**本平台仅用于教育和研究目的，不构成任何投资建议。**

- 本平台不涉及真实资金交易
- AI决策仅供学习和研究
- 加密货币投资存在高风险
- 请勿将本平台用于实盘交易
- 使用本平台造成的任何损失，开发者不承担责任

---

## 📧 联系方式

如有问题或建议，欢迎通过以下方式联系：
- 提交Issue：[GitHub Issues](https://github.com/yourusername/AITradeGame/issues)
- 在线体验：[https://trade.easy2ai.com](https://trade.easy2ai.com)

---

## 🙏 致谢

本项目基于 [chadyi/AITradeGame](https://github.com/chadyi/AITradeGame) 开发，感谢原作者的开源贡献！

在原项目基础上，我们增加了：
- 完整的用户认证系统
- 智能AI响应解析器（5层解析策略）
- 更完善的Docker部署方案
- 时区处理优化
- 更多的技术文档

---

**⭐ 如果这个项目对你有帮助，请给个Star支持一下！**
