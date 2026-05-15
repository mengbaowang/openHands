/* 仪表盘前端逻辑：模型管理、图表、持仓和交易展示。 */
class TradingApp {
    constructor() {
        const appConfig = window.APP_CONFIG || {};
        this.currentModelId = null;
        this.currentUser = null;
        this.models = [];
        this.chart = null;
        this.klineChart = null;
        this.currentKlineCoin = 'BTC';
        this.klineColorMode = 'red-up'; // 默认红涨绿跌
        this.currentModel = null;
        this.backtestChart = null;
        this.backtestResult = null;
        this.backtestHistory = [];
        this.currentBacktestJobId = null;
        this.backtestPollTimer = null;
        this.isRunningBacktest = false;
        this.marketRefreshInterval = Number(appConfig.market_refresh_interval) || 15000;
        this.portfolioRefreshInterval = Number(appConfig.portfolio_refresh_interval) || 30000;
        this.positionPnlRefreshInterval = Number(appConfig.position_pnl_refresh_interval) || 5000;
        this.trades = [];
        this.tradeFilters = {
            coin: '',
            action: '',
            search: '',
            sortBy: 'time_desc'
        };
        this.refreshIntervals = {
            market: null,
            portfolio: null,
            positionPnl: null,
            trades: null
        };
        this.isLoadingModels = false;
        this.isLoadingModelData = false;
        this.isLoadingMarketPrices = false;
        this.isLoadingKlineData = false;
        this.pendingModelDataRefresh = false;
        this.pendingMarketRefresh = false;
        this.pendingKlineRefresh = false;
        this.init();
    }

    async init() {
        // 检查登录状态
        await this.checkAuth();

        this.initEventListeners();
        this.restoreCachedMarketPrices();
        this.initKlineChart();
        const models = await this.loadModels();
        if (models.length > 0) {
            const savedModelId = this.getSavedModelId();
            const preferredModel = models.find(model => model.id === savedModelId) || models[0];
            await this.selectModel(preferredModel.id, { useCache: true, refreshModels: false });
        }
        await this.loadMarketPrices({ useCache: false });
        this.startRefreshCycles();
        this.initVisibilityRefresh();
    }

    async checkAuth() {
        try {
            const response = await fetch('/api/auth/me', {
                credentials: 'include'
            });

            if (response.ok) {
                this.currentUser = await response.json();
                this.updateUserInfo();
            } else {
                // 未登录，跳转到登录页
                window.location.href = '/login';
            }
        } catch (error) {
            console.error('Auth check failed:', error);
            window.location.href = '/login';
        }
    }

    updateUserInfo() {
        const userInfoEl = document.getElementById('userInfo');
        if (userInfoEl && this.currentUser) {
            userInfoEl.textContent = `欢迎, ${this.currentUser.username}`;
        }
    }

    escapeHtml(text) {
        if (!text) return '';
        const map = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        };
        return text.replace(/[&<>"']/g, m => map[m]);
    }

    getSavedModelId() {
        const rawValue = localStorage.getItem('dashboard_selected_model_id');
        const modelId = Number(rawValue);
        return Number.isFinite(modelId) && modelId > 0 ? modelId : null;
    }

    saveSelectedModelId(modelId) {
        if (Number.isFinite(Number(modelId))) {
            localStorage.setItem('dashboard_selected_model_id', String(modelId));
        }
    }

    getCacheKey(name, modelId = null) {
        return modelId ? `dashboard:${name}:${modelId}` : `dashboard:${name}`;
    }

    readCache(key) {
        try {
            const rawValue = sessionStorage.getItem(key);
            return rawValue ? JSON.parse(rawValue) : null;
        } catch (error) {
            console.warn('Failed to read cache:', key, error);
            return null;
        }
    }

    writeCache(key, value) {
        try {
            sessionStorage.setItem(key, JSON.stringify({
                savedAt: Date.now(),
                value
            }));
        } catch (error) {
            console.warn('Failed to write cache:', key, error);
        }
    }

    restoreCachedMarketPrices() {
        const cached = this.readCache(this.getCacheKey('market_prices'));
        if (cached && cached.value && Object.keys(cached.value).length > 0) {
            this.renderMarketPrices(cached.value);
        }
    }

    restoreCachedModelData(modelId) {
        const cached = this.readCache(this.getCacheKey('model_snapshot', modelId));
        if (!cached || !cached.value) return false;

        const { portfolio, trades, conversations } = cached.value;
        if (portfolio && portfolio.portfolio) {
            this.updateStats(portfolio.portfolio);
            this.updateChart(portfolio.account_value_history || [], portfolio.portfolio.total_value);
            this.updatePositions(portfolio.portfolio.positions || []);
        }
        if (Array.isArray(trades)) {
            this.updateTrades(trades);
        }
        if (Array.isArray(conversations)) {
            this.updateConversations(conversations);
        }
        return true;
    }

    initBacktestDefaults() {
        const startInput = document.getElementById('backtestStartDate');
        const endInput = document.getElementById('backtestEndDate');
        const today = new Date();
        const ninetyDaysAgo = new Date(today);
        ninetyDaysAgo.setDate(today.getDate() - 90);

        if (startInput && !startInput.value) {
            startInput.value = this.formatDateInput(ninetyDaysAgo);
        }
        if (endInput && !endInput.value) {
            endInput.value = this.formatDateInput(today);
        }
        this.applyBacktestPreset(90);
        this.toggleBacktestExportButtons(false);
    }

    applyModelDefaultsToBacktest() {
        const capitalInput = document.getElementById('backtestInitialCapital');
        if (capitalInput && this.currentModel && this.currentModel.initial_capital !== undefined) {
            capitalInput.value = Number(this.currentModel.initial_capital || 10000);
        }

        this.backtestHistory = this.readBacktestHistory();
        this.renderBacktestHistory();
        this.loadLatestBacktestJob();
        this.loadBacktestJobs();

        const statusEl = document.getElementById('backtestStatus');
        if (statusEl && this.currentModel) {
            statusEl.textContent = `当前模型：${this.currentModel.name}，你可以直接运行近90天回测，或调整参数后再跑。`;
        }
    }

    formatDateInput(date) {
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    formatCurrency(value, digits = 2) {
        const numeric = Number(value || 0);
        return `${numeric >= 0 ? '$' : '-$'}${Math.abs(numeric).toFixed(digits)}`;
    }

    formatPercent(value, digits = 2) {
        const numeric = Number(value || 0);
        return `${numeric >= 0 ? '' : '-'}${Math.abs(numeric).toFixed(digits)}%`;
    }

    formatInteger(value) {
        return Number(value || 0).toLocaleString('zh-CN');
    }

    async parseJsonResponse(response) {
        const contentType = response.headers.get('content-type') || '';
        const text = await response.text();
        if (!contentType.includes('application/json')) {
            const snippet = text.slice(0, 120).replace(/\s+/g, ' ').trim();
            throw new Error(`后端没有返回 JSON，当前返回的是 ${contentType || 'unknown'}。通常是服务未重启或接口不存在。${snippet ? ` 响应片段: ${snippet}` : ''}`);
        }
        try {
            return JSON.parse(text);
        } catch (error) {
            throw new Error(`后端 JSON 解析失败：${error.message}`);
        }
    }

    getBacktestHistoryKey() {
        return this.currentModelId ? `dashboard:backtest_history:${this.currentModelId}` : 'dashboard:backtest_history';
    }

    readBacktestHistory() {
        if (!this.currentModelId) return [];
        try {
            const rawValue = localStorage.getItem(this.getBacktestHistoryKey());
            const parsed = rawValue ? JSON.parse(rawValue) : [];
            return Array.isArray(parsed) ? parsed : [];
        } catch (error) {
            console.warn('Failed to read backtest history:', error);
            return [];
        }
    }

    writeBacktestHistory(history) {
        if (!this.currentModelId) return;
        try {
            localStorage.setItem(this.getBacktestHistoryKey(), JSON.stringify(history));
        } catch (error) {
            console.warn('Failed to write backtest history:', error);
        }
    }

    stopBacktestPolling() {
        if (this.backtestPollTimer) {
            clearInterval(this.backtestPollTimer);
            this.backtestPollTimer = null;
        }
    }

    startBacktestPolling(jobId) {
        this.stopBacktestPolling();
        this.currentBacktestJobId = jobId;
        this.backtestPollTimer = setInterval(() => {
            this.pollBacktestJob(jobId);
        }, 3000);
        this.pollBacktestJob(jobId);
    }

    async loadLatestBacktestJob() {
        if (!this.currentModelId) return;
        this.stopBacktestPolling();
        this.currentBacktestJobId = null;
        try {
            const response = await fetch(`/api/models/${this.currentModelId}/backtest-jobs/latest`, {
                credentials: 'include'
            });
            if (!response.ok) return;
            const job = await this.parseJsonResponse(response);
            if (!job || !job.id) {
                this.clearBacktestResults({ keepStatus: false });
                return;
            }
            this.currentBacktestJobId = job.id;
            if (job.status === 'completed' && job.result) {
                this.backtestResult = job.result;
                this.renderBacktestResult(job.result);
                const statusEl = document.getElementById('backtestStatus');
                if (statusEl) statusEl.textContent = job.message || '最近一次回测已完成。';
            } else if (['queued', 'running'].includes(job.status)) {
                this.setBacktestRunning(true);
                const statusEl = document.getElementById('backtestStatus');
                if (statusEl) {
                    statusEl.textContent = `${job.message || '回测进行中'} (${Math.round(Number(job.progress || 0))}%)`;
                }
                this.startBacktestPolling(job.id);
            } else if (job.status === 'failed') {
                const statusEl = document.getElementById('backtestStatus');
                if (statusEl) statusEl.textContent = `最近一次回测失败：${job.error || job.message || '未知错误'}`;
            }
        } catch (error) {
            console.warn('Failed to load latest backtest job:', error);
        }
    }

    async loadBacktestJobs() {
        if (!this.currentModelId) return;
        try {
            const response = await fetch(`/api/models/${this.currentModelId}/backtest-jobs?limit=20`, {
                credentials: 'include'
            });
            if (!response.ok) return;
            const jobs = await this.parseJsonResponse(response);
            this.renderBacktestJobs(Array.isArray(jobs) ? jobs : []);
        } catch (error) {
            console.warn('Failed to load backtest jobs:', error);
        }
    }

    renderBacktestJobs(jobs) {
        const tbody = document.getElementById('backtestJobsBody');
        if (!tbody) return;
        if (!jobs.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="empty-state">暂无任务</td></tr>';
            return;
        }

        const modeMap = {
            full_ai: '完整AI',
            candidate_ai: '候选AI',
            fast_rule: '快速规则'
        };
        const statusMap = {
            queued: '排队中',
            running: '运行中',
            completed: '已完成',
            failed: '失败'
        };

        tbody.innerHTML = jobs.map((job) => {
            const status = job.status || 'queued';
            const statusClass = status === 'completed'
                ? 'text-success'
                : status === 'failed'
                    ? 'text-danger'
                    : status === 'running'
                        ? 'text-info'
                        : 'text-warning';
            return `
                <tr>
                    <td>${new Date(job.created_at).toLocaleString('zh-CN')}</td>
                    <td>${modeMap[job.mode] || job.mode}</td>
                    <td>${job.start_date} ~ ${job.end_date}</td>
                    <td class="${statusClass}">${statusMap[status] || status}</td>
                    <td>${Math.round(Number(job.progress || 0))}%</td>
                    <td>${job.error || job.message || '-'}</td>
                </tr>
            `;
        }).join('');
    }

    applyBacktestPreset(days) {
        const endInput = document.getElementById('backtestEndDate');
        const startInput = document.getElementById('backtestStartDate');
        const endDate = endInput?.value ? new Date(`${endInput.value}T00:00:00`) : new Date();
        const startDate = new Date(endDate);
        startDate.setDate(endDate.getDate() - Number(days || 90));

        if (startInput) startInput.value = this.formatDateInput(startDate);
        if (endInput && !endInput.value) endInput.value = this.formatDateInput(endDate);

        document.querySelectorAll('.backtest-preset-btn').forEach((btn) => {
            btn.classList.toggle('active', Number(btn.dataset.days || 0) === Number(days));
        });
    }

    cacheModelData(modelId, snapshot) {
        this.writeCache(this.getCacheKey('model_snapshot', modelId), snapshot);
    }

    initEventListeners() {
        document.getElementById('addModelBtn').addEventListener('click', () => this.showModal());
        document.getElementById('closeModalBtn').addEventListener('click', () => this.hideModal());
        document.getElementById('cancelBtn').addEventListener('click', () => this.hideModal());
        document.getElementById('submitBtn').addEventListener('click', () => this.submitModel());
        document.getElementById('refreshBtn').addEventListener('click', () => this.refresh());
        document.getElementById('themeToggle').addEventListener('click', () => this.toggleTheme());
        document.getElementById('logoutBtn').addEventListener('click', () => this.logout());
        const runBacktestBtn = document.getElementById('runBacktestBtn');
        const backtestResetBtn = document.getElementById('backtestResetBtn');
        const exportBacktestJsonBtn = document.getElementById('exportBacktestJsonBtn');
        const exportBacktestCsvBtn = document.getElementById('exportBacktestCsvBtn');
        if (runBacktestBtn) runBacktestBtn.addEventListener('click', () => this.submitBacktestJob());
        if (backtestResetBtn) backtestResetBtn.addEventListener('click', () => this.resetBacktestPanel());
        if (exportBacktestJsonBtn) exportBacktestJsonBtn.addEventListener('click', () => this.exportBacktest('json'));
        if (exportBacktestCsvBtn) exportBacktestCsvBtn.addEventListener('click', () => this.exportBacktest('csv'));

        document.querySelectorAll('.backtest-preset-btn').forEach((btn) => {
            btn.addEventListener('click', () => this.applyBacktestPreset(Number(btn.dataset.days || 90)));
        });

        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', (e) => this.switchTab(e.target.dataset.tab));
        });

        document.getElementById('klineSelect').addEventListener('change', (e) => {
            this.currentKlineCoin = e.target.value;
            this.loadKlineData();
        });

        document.getElementById('klineColorToggle').addEventListener('click', () => {
            this.toggleKlineColor();
        });

        const tradeCoinFilter = document.getElementById('tradeCoinFilter');
        const tradeActionFilter = document.getElementById('tradeActionFilter');
        const tradeSearchInput = document.getElementById('tradeSearchInput');
        const tradeSortBy = document.getElementById('tradeSortBy');
        const tradeFilterReset = document.getElementById('tradeFilterReset');

        if (tradeCoinFilter) {
            tradeCoinFilter.addEventListener('change', (e) => {
                this.tradeFilters.coin = e.target.value;
                this.renderFilteredTrades();
            });
        }

        if (tradeActionFilter) {
            tradeActionFilter.addEventListener('change', (e) => {
                this.tradeFilters.action = e.target.value;
                this.renderFilteredTrades();
            });
        }

        if (tradeSearchInput) {
            tradeSearchInput.addEventListener('input', (e) => {
                this.tradeFilters.search = e.target.value.trim().toLowerCase();
                this.renderFilteredTrades();
            });
        }

        if (tradeSortBy) {
            tradeSortBy.addEventListener('change', (e) => {
                this.tradeFilters.sortBy = e.target.value;
                this.renderFilteredTrades();
            });
        }

        if (tradeFilterReset) {
            tradeFilterReset.addEventListener('click', () => this.resetTradeFilters());
        }

        // 加载保存的主题
        this.loadTheme();
    }

    async logout() {
        try {
            await fetch('/api/auth/logout', {
                method: 'POST',
                credentials: 'include'
            });
            window.location.href = '/login';
        } catch (error) {
            console.error('Logout failed:', error);
            window.location.href = '/login';
        }
    }

    async loadModels() {
        if (this.isLoadingModels) {
            return [];
        }
        this.isLoadingModels = true;
        try {
            const response = await fetch('/api/models', {
                credentials: 'include'
            });

            if (response.status === 401) {
                window.location.href = '/login';
                return [];
            }

            const models = await response.json();
            this.models = Array.isArray(models) ? models : [];
            this.renderModels(models);
            return this.models;
        } catch (error) {
            console.error('Failed to load models:', error);
            return [];
        } finally {
            this.isLoadingModels = false;
        }
    }

    renderModels(models) {
        const container = document.getElementById('modelList');
        
        if (models.length === 0) {
            container.innerHTML = '<div class="empty-state">暂无模型</div>';
            return;
        }

        container.innerHTML = models.map(model => `
            <div class="model-item ${model.id === this.currentModelId ? 'active' : ''}"
                 onclick="app.selectModel(${model.id})">
                <div class="model-name">${model.name}</div>
                <div class="model-info">
                    <span>${model.model_name}</span>
                    <div class="model-actions">
                        <span class="model-edit" onclick="event.stopPropagation(); app.editModel(${model.id})" title="编辑策略">
                            <i class="bi bi-pencil"></i>
                        </span>
                        <span class="model-delete" onclick="event.stopPropagation(); app.deleteModel(${model.id})" title="删除模型">
                            <i class="bi bi-trash"></i>
                        </span>
                    </div>
                </div>
            </div>
        `).join('');
    }

    async selectModel(modelId, options = {}) {
        const { useCache = true, refreshModels = true } = options;
        if (!modelId) return;

        if (this.currentModelId !== modelId) {
            this.clearBacktestResults({ keepStatus: false });
        }
        this.currentModelId = modelId;
        this.saveSelectedModelId(modelId);

        if (refreshModels) {
            const models = await this.loadModels();
            if (models.length > 0) {
                this.renderModels(models);
            }
        }

        this.currentModel = (this.models || []).find(model => model.id === modelId) || null;
        this.applyModelDefaultsToBacktest();

        if (useCache) {
            this.restoreCachedModelData(modelId);
        }

        await this.loadModelData({ silentIfBusy: false });
    }

    async loadModelData(options = {}) {
        if (!this.currentModelId) return;

        if (this.isLoadingModelData) {
            this.pendingModelDataRefresh = true;
            if (!options.silentIfBusy) {
                console.debug('Model data request skipped because another request is in flight.');
            }
            return;
        }

        this.isLoadingModelData = true;

        try {
            const timeout = (ms) => new Promise((_, reject) =>
                setTimeout(() => reject(new Error('Request timeout')), ms)
            );

            const fetchWithTimeout = (url, ms = 10000) =>
                Promise.race([
                    fetch(url, { credentials: 'include' }),
                    timeout(ms)
                ]);

            const [portfolioRes, tradesRes, conversationsRes] = await Promise.all([
                fetchWithTimeout(`/api/models/${this.currentModelId}/portfolio`),
                fetchWithTimeout(`/api/models/${this.currentModelId}/trades?limit=50`),
                fetchWithTimeout(`/api/models/${this.currentModelId}/conversations?limit=20`)
            ]);

            if (portfolioRes.status === 401 || tradesRes.status === 401 || conversationsRes.status === 401) {
                console.warn('Session expired, redirecting to login...');
                window.location.href = '/login';
                return;
            }

            const [portfolio, trades, conversations] = await Promise.all([
                portfolioRes.json(),
                tradesRes.json(),
                conversationsRes.json()
            ]);

            this.updateStats(portfolio.portfolio);
            this.updateChart(portfolio.account_value_history || [], portfolio.portfolio.total_value);
            this.updatePositions(portfolio.portfolio.positions || []);
            this.updateTrades(trades);
            this.updateConversations(conversations);
            this.cacheModelData(this.currentModelId, { portfolio, trades, conversations });
        } catch (error) {
            console.error('Failed to load model data:', error);

            if (error.message === 'Request timeout') {
                console.warn('Request timeout, retrying...');
                setTimeout(() => this.loadModelData({ silentIfBusy: true }), 3000);
            } else if (error.message.includes('Failed to fetch')) {
                console.error('Network error, please check your connection');
            }
        } finally {
            this.isLoadingModelData = false;
            if (this.pendingModelDataRefresh) {
                this.pendingModelDataRefresh = false;
                queueMicrotask(() => this.loadModelData({ silentIfBusy: true }));
            }
        }
    }

    async loadPortfolioSnapshot(options = {}) {
        if (!this.currentModelId) return;

        if (this.isLoadingModelData) {
            this.pendingModelDataRefresh = true;
            return;
        }

        this.isLoadingModelData = true;

        try {
            const response = await fetch(`/api/models/${this.currentModelId}/portfolio`, {
                credentials: 'include'
            });

            if (response.status === 401) {
                window.location.href = '/login';
                return;
            }

            const portfolio = await response.json();
            this.updateStats(portfolio.portfolio);
            this.updateChart(portfolio.account_value_history || [], portfolio.portfolio.total_value);
            this.updatePositions(portfolio.portfolio.positions || []);

            if (!options.skipCacheWrite) {
                const cached = this.readCache(this.getCacheKey('model_snapshot', this.currentModelId));
                const trades = cached?.value?.trades || this.trades || [];
                const conversations = cached?.value?.conversations || [];
                this.cacheModelData(this.currentModelId, { portfolio, trades, conversations });
            }
        } catch (error) {
            console.error('Failed to load portfolio snapshot:', error);
        } finally {
            this.isLoadingModelData = false;
            if (this.pendingModelDataRefresh) {
                this.pendingModelDataRefresh = false;
                queueMicrotask(() => this.loadPortfolioSnapshot({ skipCacheWrite: true }));
            }
        }
    }

    updateStats(portfolio) {
        const stats = [
            { value: portfolio.total_value || 0, class: portfolio.total_value > portfolio.initial_capital ? 'positive' : portfolio.total_value < portfolio.initial_capital ? 'negative' : '' },
            { value: portfolio.cash || 0, class: '' },
            { value: portfolio.realized_pnl || 0, class: portfolio.realized_pnl > 0 ? 'positive' : portfolio.realized_pnl < 0 ? 'negative' : '' },
            { value: portfolio.unrealized_pnl || 0, class: portfolio.unrealized_pnl > 0 ? 'positive' : portfolio.unrealized_pnl < 0 ? 'negative' : '' }
        ];

        document.querySelectorAll('.stat-value').forEach((el, index) => {
            if (stats[index]) {
                el.textContent = `$${Math.abs(stats[index].value).toFixed(2)}`;
                el.className = `stat-value ${stats[index].class}`;
            }
        });
        this.updateAccountTooltip(portfolio.wallet_balances || {});
    }

    updateAccountTooltip(walletBalances) {
        const tooltipBody = document.getElementById('accountValueTooltipBody');
        if (!tooltipBody) return;

        const entries = Object.entries(walletBalances || {})
            .filter(([, balance]) => {
                const total = Number(balance?.total || 0);
                const available = Number(balance?.available || 0);
                const frozen = Number(balance?.frozen || 0);
                return total !== 0 || available !== 0 || frozen !== 0;
            })
            .sort((a, b) => Number(b[1].total || 0) - Number(a[1].total || 0));

        if (entries.length === 0) {
            tooltipBody.innerHTML = '<div class="account-tooltip-empty">暂无余额数据</div>';
            return;
        }

        tooltipBody.innerHTML = entries.map(([coin, balance]) => `
            <div class="account-tooltip-row">
                <div class="account-tooltip-coin">${coin}</div>
                <div class="account-tooltip-values">
                    <div>总额: ${Number(balance.total || 0).toFixed(6)}</div>
                    <div>可用: ${Number(balance.available || 0).toFixed(6)}</div>
                    <div>冻结: ${Number(balance.frozen || 0).toFixed(6)}</div>
                </div>
            </div>
        `).join('');
    }

    updateChart(history, currentValue) {
        const chartDom = document.getElementById('accountChart');
        
        if (!this.chart) {
            this.chart = echarts.init(chartDom);
            window.addEventListener('resize', () => {
                if (this.chart) {
                    this.chart.resize();
                }
            });
        }

        const data = history.reverse().map(h => ({
            // 后端返回ISO 8601格式（带时区），JavaScript会自动转换成本地时区
            time: new Date(h.timestamp).toLocaleTimeString('zh-CN', {
                hour: '2-digit',
                minute: '2-digit'
            }),
            value: h.total_value
        }));

        if (currentValue !== undefined && currentValue !== null) {
            const now = new Date();
            const currentTime = now.toLocaleTimeString('zh-CN', { 
                timeZone: 'Asia/Shanghai',
                hour: '2-digit', 
                minute: '2-digit' 
            });
            data.push({
                time: currentTime,
                value: currentValue
            });
        }

        const option = {
            grid: {
                left: '60',
                right: '20',
                bottom: '30',
                top: '20',
                containLabel: false
            },
            xAxis: {
                type: 'category',
                boundaryGap: false,
                data: data.map(d => d.time),
                axisLine: { lineStyle: { color: '#e5e6eb' } },
                axisLabel: { color: '#86909c', fontSize: 11 }
            },
            yAxis: {
                type: 'value',
                scale: true,
                axisLine: { lineStyle: { color: '#e5e6eb' } },
                axisLabel: { 
                    color: '#86909c', 
                    fontSize: 11,
                    formatter: (value) => `$${value.toLocaleString()}`
                },
                splitLine: { lineStyle: { color: '#f2f3f5' } }
            },
            series: [{
                type: 'line',
                data: data.map(d => d.value),
                smooth: true,
                symbol: 'none',
                lineStyle: { color: '#3370ff', width: 2 },
                areaStyle: {
                    color: {
                        type: 'linear',
                        x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [
                            { offset: 0, color: 'rgba(51, 112, 255, 0.2)' },
                            { offset: 1, color: 'rgba(51, 112, 255, 0)' }
                        ]
                    }
                }
            }],
            tooltip: {
                trigger: 'axis',
                backgroundColor: 'rgba(255, 255, 255, 0.95)',
                borderColor: '#e5e6eb',
                borderWidth: 1,
                textStyle: { color: '#1d2129' },
                formatter: (params) => {
                    const value = params[0].value;
                    return `${params[0].axisValue}<br/>$${value.toFixed(2)}`;
                }
            }
        };

        this.chart.setOption(option);
        
        setTimeout(() => {
            if (this.chart) {
                this.chart.resize();
            }
        }, 100);
    }

    updatePositions(positions) {
        const tbody = document.getElementById('positionsBody');
        
        if (positions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty-state">暂无持仓</td></tr>';
            return;
        }

        tbody.innerHTML = positions.map(pos => {
            const sideClass = pos.side === 'long' ? 'badge-long' : 'badge-short';
            const sideText = pos.side === 'long' ? '做多' : '做空';
            
            const currentPrice = pos.current_price !== null && pos.current_price !== undefined 
                ? `$${pos.current_price.toFixed(2)}` 
                : '-';
            
            let pnlDisplay = '-';
            let pnlClass = '';
            if (pos.pnl !== undefined && pos.pnl !== 0) {
                pnlClass = pos.pnl > 0 ? 'text-success' : 'text-danger';
                pnlDisplay = `${pos.pnl > 0 ? '+' : ''}$${pos.pnl.toFixed(2)}`;
            }
            
            return `
                <tr>
                    <td><strong>${pos.coin}</strong></td>
                    <td><span class="badge ${sideClass}">${sideText}</span></td>
                    <td>${pos.quantity.toFixed(4)}</td>
                    <td>$${pos.avg_price.toFixed(2)}</td>
                    <td>${currentPrice}</td>
                    <td>${pos.leverage}x</td>
                    <td class="${pnlClass}"><strong>${pnlDisplay}</strong></td>
                </tr>
            `;
        }).join('');
    }

    updateTrades(trades) {
        this.trades = Array.isArray(trades) ? trades : [];
        this.renderFilteredTrades();
    }

    resetTradeFilters() {
        this.tradeFilters = {
            coin: '',
            action: '',
            search: '',
            sortBy: 'time_desc'
        };

        const tradeCoinFilter = document.getElementById('tradeCoinFilter');
        const tradeActionFilter = document.getElementById('tradeActionFilter');
        const tradeSearchInput = document.getElementById('tradeSearchInput');
        const tradeSortBy = document.getElementById('tradeSortBy');

        if (tradeCoinFilter) tradeCoinFilter.value = '';
        if (tradeActionFilter) tradeActionFilter.value = '';
        if (tradeSearchInput) tradeSearchInput.value = '';
        if (tradeSortBy) tradeSortBy.value = 'time_desc';

        this.renderFilteredTrades();
    }

    getFilteredTrades() {
        const signalMap = {
            'buy_to_enter': '开多',
            'sell_to_enter': '开空',
            'reduce_position': '减仓',
            'increase_position': '加仓',
            'fixed_stop': '固定止损',
            'close_position': '平仓',
            'sell_to_close': '平多',
            'buy_to_close': '平空',
            'take_profit': '止盈',
            'trailing_stop': '移动止损',
            'move_stop_loss': '上移止损',
            'auto_close': '自动平仓',
            'hold': '持有'
        };

        let trades = [...this.trades];

        if (this.tradeFilters.coin) {
            trades = trades.filter(trade => trade.coin === this.tradeFilters.coin);
        }

        if (this.tradeFilters.action) {
            trades = trades.filter(trade => trade.signal === this.tradeFilters.action);
        }

        if (this.tradeFilters.search) {
            trades = trades.filter(trade => {
                const coin = (trade.coin || '').toLowerCase();
                const signal = (trade.signal || '').toLowerCase();
                const signalText = (trade.action_text || signalMap[trade.signal] || '').toLowerCase();
                return coin.includes(this.tradeFilters.search)
                    || signal.includes(this.tradeFilters.search)
                    || signalText.includes(this.tradeFilters.search);
            });
        }

        trades.sort((a, b) => {
            switch (this.tradeFilters.sortBy) {
                case 'time_asc':
                    return new Date(a.timestamp) - new Date(b.timestamp);
                case 'pnl_desc':
                    return (b.pnl || 0) - (a.pnl || 0);
                case 'pnl_asc':
                    return (a.pnl || 0) - (b.pnl || 0);
                case 'time_desc':
                default:
                    return new Date(b.timestamp) - new Date(a.timestamp);
            }
        });

        return trades;
    }

    renderFilteredTrades() {
        const tbody = document.getElementById('tradesBody');
        const trades = this.getFilteredTrades();
        
        if (trades.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="empty-state">暂无交易记录</td></tr>';
            return;
        }

        tbody.innerHTML = trades.map(trade => {
            const signalMap = {
                'buy_to_enter': { badge: 'badge-buy', text: '开多' },
                'sell_to_enter': { badge: 'badge-sell', text: '开空' },
                'reduce_position': { badge: 'badge-reduce', text: '减仓' },
                'increase_position': { badge: 'badge-increase', text: '加仓' },
                'fixed_stop': { badge: 'badge-fixed', text: '固定止损' },
                'close_position': { badge: 'badge-close', text: '平仓' },
                'sell_to_close': { badge: 'badge-close', text: '平多' },
                'buy_to_close': { badge: 'badge-close', text: '平空' },
                'move_stop_loss': { badge: 'badge-fixed', text: '上移止损' },
                'take_profit': { badge: 'badge-fixed', text: '止盈' },
                'trailing_stop': { badge: 'badge-fixed', text: '移动止损' },
                'auto_close': { badge: 'badge-close', text: '自动平仓' },
                'hold': { badge: '', text: '持有' }
            };
            const signal = signalMap[trade.signal] || { badge: '', text: trade.action_text || trade.signal };
            const netPnl = trade.net_pnl ?? trade.pnl ?? 0;
            const grossPnl = trade.gross_pnl ?? trade.pnl ?? 0;
            const fee = trade.fee ?? 0;
            const pnlClass = netPnl > 0 ? 'text-success' : netPnl < 0 ? 'text-danger' : '';
            const pnlTitle = `毛盈亏: $${grossPnl.toFixed(2)}\n手续费: $${fee.toFixed(2)}\n净盈亏: $${netPnl.toFixed(2)}`;

            return `
                <tr>
                    <td>${new Date(trade.timestamp).toLocaleString('zh-CN')}</td>
                    <td><strong>${trade.coin}</strong></td>
                    <td><span class="badge ${signal.badge}">${signal.text}</span></td>
                    <td>${trade.quantity.toFixed(4)}</td>
                    <td>$${trade.price.toFixed(2)}</td>
                    <td class="${pnlClass}" title="${pnlTitle}">$${netPnl.toFixed(2)}</td>
                </tr>
            `;
        }).join('');
    }

    updateConversations(conversations) {
        const container = document.getElementById('conversationsBody');

        if (conversations.length === 0) {
            container.innerHTML = '<div class="empty-state">暂无对话记录</div>';
            return;
        }

        container.innerHTML = conversations.map(conv => {
            // 解析AI响应，提取决策信息
            const response = conv.ai_response;
            let decision = '观望';
            let marketAnalysis = '';
            let reasoning = '';
            let confidence = '';
            let parsedData = null;
            let coinData = null;

            // 尝试解析JSON格式的响应（支持多种格式）
            try {
                // 方法1: 直接解析整个响应
                try {
                    parsedData = JSON.parse(response);
                } catch (e1) {
                    // 方法2: 提取JSON对象
                    const jsonMatch = response.match(/\{[\s\S]*\}/);
                    if (jsonMatch) {
                        parsedData = JSON.parse(jsonMatch[0]);
                    }
                }

                if (parsedData) {
                    // 检查是否是嵌套格式：{"BTC": {"signal": "hold", "reasoning": {...}}}
                    const coinKeys = ['BTC', 'ETH', 'SOL', 'BNB', 'DOGE', 'XRP', 'ZEC'];
                    for (const coin of coinKeys) {
                        if (parsedData[coin]) {
                            coinData = parsedData[coin];
                            break;
                        }
                    }

                    // 如果是嵌套格式，使用coinData
                    const data = coinData || parsedData;

                    // 提取决策（支持多种字段名）
                    decision = data.decision || data.action || data.signal || '观望';

                    // 映射signal到中文
                    if (decision === 'buy') decision = '买入';
                    else if (decision === 'sell') decision = '卖出';
                    else if (decision === 'hold') decision = '持有';

                    // 提取市场分析
                    if (data.reasoning && typeof data.reasoning === 'object') {
                        // reasoning是对象格式
                        marketAnalysis = data.reasoning.market_analysis || '';
                        reasoning = data.reasoning.decision_rationale || data.reasoning.reasoning || '';
                    } else {
                        // reasoning是字符串格式
                        marketAnalysis = data.market_analysis || data.analysis ||
                                       data.market_condition || data.market || '';
                        reasoning = data.reasoning || data.reason ||
                                  data.rationale || data.explanation || '';
                    }

                    // 提取信心指数
                    confidence = data.confidence || data.confidence_level || '';
                    if (typeof confidence === 'number') {
                        confidence = `${(confidence * 100).toFixed(0)}%`;
                    }

                    // 如果有price_target，添加到reasoning
                    if (data.profit_target && data.profit_target > 0) {
                        reasoning += `\n目标价格: $${data.profit_target.toFixed(2)}`;
                    }

                    // 如果有stop_loss，添加到reasoning
                    if (data.stop_loss && data.stop_loss > 0) {
                        reasoning += `\n止损价格: $${data.stop_loss.toFixed(2)}`;
                    }

                    // 如果有leverage，添加到reasoning
                    if (data.leverage && data.leverage > 1) {
                        reasoning += `\n杠杆倍数: ${data.leverage}x`;
                    }
                }
            } catch (e) {
                console.warn('Failed to parse AI response as JSON:', e);
            }

            // 智能文本解析 - 如果JSON解析失败
            if (!parsedData || (parsedData && Object.keys(parsedData).length === 0)) {
                // 尝试从文本中提取信息
                const textAnalysis = this.extractFromText(response);

                if (textAnalysis.signal) {
                    decision = textAnalysis.signal;
                    reasoning = textAnalysis.reasoning;
                    marketAnalysis = textAnalysis.marketAnalysis;
                    confidence = textAnalysis.confidence;
                } else {
                    // 如果完全无法解析，显示原始文本
                    decision = '观望';
                    reasoning = response.length > 500 ? response.substring(0, 500) + '...' : response;
                }
            }

            // 决策类型样式
            let decisionClass = 'decision-hold';
            let decisionIcon = '⏸';
            if (decision.includes('买') || decision.includes('BUY') || decision.includes('buy')) {
                decisionClass = 'decision-buy';
                decisionIcon = '📈';
            } else if (decision.includes('卖') || decision.includes('SELL') || decision.includes('sell')) {
                decisionClass = 'decision-sell';
                decisionIcon = '📉';
            } else if (decision.includes('平') || decision.includes('close')) {
                decisionClass = 'decision-close';
                decisionIcon = '🔄';
            }

            // 构建友好的展示内容
            let displayContent = '';
            if (parsedData && (marketAnalysis || reasoning || confidence)) {
                // 结构化展示
                displayContent = `
                    <div class="ai-analysis-label">
                        <i class="bi bi-robot"></i> AI分析
                    </div>
                    ${marketAnalysis ? `<div class="analysis-section">
                        <div class="section-title"><i class="bi bi-graph-up"></i> 市场分析</div>
                        <div class="section-content">${this.escapeHtml(marketAnalysis)}</div>
                    </div>` : ''}
                    ${reasoning ? `<div class="analysis-section">
                        <div class="section-title"><i class="bi bi-lightbulb"></i> 决策理由</div>
                        <div class="section-content">${this.escapeHtml(reasoning).replace(/\n/g, '<br>')}</div>
                    </div>` : ''}
                    ${confidence ? `<div class="analysis-section">
                        <div class="section-title"><i class="bi bi-speedometer2"></i> 信心指数</div>
                        <div class="section-content"><strong>${confidence}</strong></div>
                    </div>` : ''}
                `;
            } else {
                // 简单文本展示
                displayContent = `
                    <div class="ai-analysis-label">
                        <i class="bi bi-robot"></i> AI分析
                    </div>
                    <div class="analysis-section">
                        <div class="section-content">${this.escapeHtml(reasoning).replace(/\n/g, '<br>')}</div>
                    </div>
                `;
            }

            return `
                <div class="conversation-item">
                    <div class="conversation-header">
                        <div class="conversation-time">
                            <i class="bi bi-clock"></i>
                            ${new Date(conv.timestamp).toLocaleString('zh-CN', {
                                year: 'numeric',
                                month: '2-digit',
                                day: '2-digit',
                                hour: '2-digit',
                                minute: '2-digit'
                            })}
                        </div>
                        <div class="conversation-decision ${decisionClass}">
                            ${decisionIcon} ${decision}
                        </div>
                    </div>
                    <div class="conversation-content">
                        ${displayContent}
                    </div>
                </div>
            `;
        }).join('');
    }

    async loadMarketPrices(options = {}) {
        const { useCache = true } = options;

        if (this.isLoadingMarketPrices) {
            this.pendingMarketRefresh = true;
            return;
        }

        if (useCache) {
            this.restoreCachedMarketPrices();
        }

        this.isLoadingMarketPrices = true;

        try {
            const response = await fetch('/api/market/prices');
            const prices = await response.json();
            this.renderMarketPrices(prices);
            this.writeCache(this.getCacheKey('market_prices'), prices);
        } catch (error) {
            console.error('Failed to load market prices:', error);
        } finally {
            this.isLoadingMarketPrices = false;
            if (this.pendingMarketRefresh) {
                this.pendingMarketRefresh = false;
                queueMicrotask(() => this.loadMarketPrices({ useCache: false }));
            }
        }
    }

    renderMarketPrices(prices) {
        const container = document.getElementById('marketPrices');
        
        container.innerHTML = Object.entries(prices).map(([coin, data]) => {
            const changeClass = data.change_24h >= 0 ? 'positive' : 'negative';
            const changeIcon = data.change_24h >= 0 ? '▲' : '▼';
            
            return `
                <div class="price-item">
                    <div>
                        <div class="price-symbol">${coin}</div>
                        <div class="price-change ${changeClass}">${changeIcon} ${Math.abs(data.change_24h).toFixed(2)}%</div>
                    </div>
                    <div class="price-value">$${data.price.toFixed(2)}</div>
                </div>
            `;
        }).join('');
    }

    extractFromText(text) {
        /**
         * 智能从文本中提取交易决策信息
         */
        const result = {
            signal: '',
            reasoning: '',
            marketAnalysis: '',
            confidence: ''
        };

        if (!text || text.trim() === '') {
            return result;
        }

        const textLower = text.toLowerCase();

        // 提取信号
        if (textLower.includes('buy') || textLower.includes('买入') || textLower.includes('做多')) {
            result.signal = '买入';
        } else if (textLower.includes('sell') || textLower.includes('卖出') || textLower.includes('做空')) {
            result.signal = '卖出';
        } else if (textLower.includes('close') || textLower.includes('平仓')) {
            result.signal = '平仓';
        } else if (textLower.includes('hold') || textLower.includes('持有') || textLower.includes('观望')) {
            result.signal = '持有';
        }

        // 提取市场分析
        const marketMatch = text.match(/(?:market|市场|分析)[:\s]+([^\n.]{20,200})/i);
        if (marketMatch) {
            result.marketAnalysis = marketMatch[1].trim();
        }

        // 提取推理
        const reasoningMatch = text.match(/(?:reason|reasoning|理由|原因)[:\s]+([^\n.]{20,300})/i);
        if (reasoningMatch) {
            result.reasoning = reasoningMatch[1].trim();
        } else {
            // 如果没有找到明确的推理，使用前200字符
            result.reasoning = text.substring(0, 200).trim();
        }

        // 提取信心指数
        const confidenceMatch = text.match(/confidence[:\s]+([0-9.]+)/i);
        if (confidenceMatch) {
            let conf = parseFloat(confidenceMatch[1]);
            if (conf > 1) conf = conf / 100;
            result.confidence = `${(conf * 100).toFixed(0)}%`;
        }

        return result;
    }

    resetBacktestPanel() {
        this.stopBacktestPolling();
        this.currentBacktestJobId = null;
        this.initBacktestDefaults();
        this.applyModelDefaultsToBacktest();
        const decisionInterval = document.getElementById('backtestDecisionInterval');
        const riskInterval = document.getElementById('backtestRiskInterval');
        const maxAiCalls = document.getElementById('backtestMaxAiCalls');
        const mode = document.getElementById('backtestMode');
        if (decisionInterval) decisionInterval.value = '3600';
        if (riskInterval) riskInterval.value = '300';
        if (maxAiCalls) maxAiCalls.value = '2000';
        if (mode) mode.value = 'candidate_ai';
        this.clearBacktestResults({ keepStatus: false });
    }

    clearBacktestResults(options = {}) {
        const { keepStatus = false } = options;
        this.backtestResult = null;
        const emptyState = document.getElementById('backtestEmptyState');
        const results = document.getElementById('backtestResults');
        const meta = document.getElementById('backtestRunMeta');
        const coinStatsBody = document.getElementById('backtestCoinStatsBody');
        const tradesBody = document.getElementById('backtestTradesBody');

        if (emptyState) emptyState.classList.remove('hidden');
        if (results) results.classList.add('hidden');
        if (meta) meta.textContent = '';
        if (coinStatsBody) coinStatsBody.innerHTML = '<tr><td colspan="5" class="empty-state">暂无数据</td></tr>';
        if (tradesBody) tradesBody.innerHTML = '<tr><td colspan="5" class="empty-state">暂无数据</td></tr>';
        this.toggleBacktestExportButtons(false);

        document.querySelectorAll('[data-metric]').forEach((el) => {
            el.textContent = '--';
            el.classList.remove('positive', 'negative');
        });

        if (this.backtestChart) {
            this.backtestChart.clear();
        }

        if (!keepStatus) {
            const statusEl = document.getElementById('backtestStatus');
            if (statusEl) {
                statusEl.textContent = this.currentModel
                    ? `当前模型：${this.currentModel.name}，你可以直接运行近90天回测，或调整参数后再跑。`
                    : '请选择模型后再运行回测。';
            }
        }
    }

    setBacktestRunning(isRunning) {
        this.isRunningBacktest = isRunning;
        const runBtn = document.getElementById('runBacktestBtn');
        const resetBtn = document.getElementById('backtestResetBtn');
        if (runBtn) {
            runBtn.disabled = isRunning;
            runBtn.innerHTML = isRunning
                ? '<i class="bi bi-hourglass-split"></i> 回测中...'
                : '<i class="bi bi-play-fill"></i> 运行回测';
        }
        if (resetBtn) {
            resetBtn.disabled = isRunning;
        }
        this.toggleBacktestExportButtons(!isRunning && !!this.backtestResult);
    }

    toggleBacktestExportButtons(enabled) {
        ['exportBacktestJsonBtn', 'exportBacktestCsvBtn'].forEach((id) => {
            const btn = document.getElementById(id);
            if (btn) btn.disabled = !enabled;
        });
    }

    async runBacktest() {
        if (!this.currentModelId) {
            alert('请先选择一个模型再运行回测');
            return;
        }
        if (this.isRunningBacktest) {
            return;
        }

        const startDate = document.getElementById('backtestStartDate')?.value;
        const endDate = document.getElementById('backtestEndDate')?.value;
        const initialCapital = Number(document.getElementById('backtestInitialCapital')?.value || 0);
        const decisionInterval = Number(document.getElementById('backtestDecisionInterval')?.value || 3600);
        const riskInterval = Number(document.getElementById('backtestRiskInterval')?.value || 300);
        const maxAiCalls = Number(document.getElementById('backtestMaxAiCalls')?.value || 2000);

        if (!startDate || !endDate) {
            alert('请选择回测开始和结束日期');
            return;
        }
        if (startDate > endDate) {
            alert('开始日期不能晚于结束日期');
            return;
        }
        if (!Number.isFinite(initialCapital) || initialCapital <= 0) {
            alert('初始资金必须大于 0');
            return;
        }
        if (!Number.isFinite(maxAiCalls) || maxAiCalls <= 0) {
            alert('最大AI调用数必须大于 0');
            return;
        }

        const statusEl = document.getElementById('backtestStatus');
        if (statusEl) {
            statusEl.textContent = '正在拉取历史数据并回放AI决策，这一步可能需要一些时间，请稍候...';
        }

        this.setBacktestRunning(true);

        try {
            const response = await fetch('/api/backtest', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    model_id: this.currentModelId,
                    start_date: startDate,
                    end_date: endDate,
                    initial_capital: initialCapital,
                    decision_interval_seconds: decisionInterval,
                    risk_interval_seconds: riskInterval,
                    max_ai_calls: maxAiCalls
                })
            });

            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.error || '回测失败');
            }

            this.backtestResult = result;
            this.renderBacktestResult(result);
            this.saveBacktestHistoryEntry(result);
            if (statusEl) {
                statusEl.textContent = `回测完成：${result.start_date} 至 ${result.end_date}，共回放 ${this.formatInteger(result.summary?.decision_cycles || 0)} 次AI决策周期。`;
            }
        } catch (error) {
            console.error('Backtest failed:', error);
            if (statusEl) {
                statusEl.textContent = `回测失败：${error.message}`;
            }
            alert(error.message || '回测失败');
        } finally {
            this.setBacktestRunning(false);
        }
    }

    renderBacktestResult(result) {
        const emptyState = document.getElementById('backtestEmptyState');
        const results = document.getElementById('backtestResults');
        if (emptyState) emptyState.classList.add('hidden');
        if (results) results.classList.remove('hidden');
        this.toggleBacktestExportButtons(true);

        const metrics = result.metrics || {};
        const summaryMetrics = {
            total_return: { value: this.formatPercent(metrics.total_return), numeric: Number(metrics.total_return || 0) },
            total_net_pnl: { value: this.formatCurrency(metrics.total_net_pnl), numeric: Number(metrics.total_net_pnl || 0) },
            win_rate: { value: this.formatPercent(metrics.win_rate), numeric: Number(metrics.win_rate || 0) },
            total_fees: { value: this.formatCurrency(metrics.total_fees), numeric: -Math.abs(Number(metrics.total_fees || 0)) },
            entry_count: { value: this.formatInteger(metrics.entry_count), numeric: 0 },
            max_drawdown: { value: this.formatPercent((metrics.max_drawdown || 0) * 100), numeric: -Math.abs(Number(metrics.max_drawdown || 0)) }
        };

        document.querySelectorAll('[data-metric]').forEach((el) => {
            const key = el.dataset.metric;
            const item = summaryMetrics[key];
            if (!item) return;
            el.textContent = item.value;
            el.classList.remove('positive', 'negative');
            if (item.numeric > 0 && key !== 'entry_count') el.classList.add('positive');
            if (item.numeric < 0) el.classList.add('negative');
        });

        const meta = document.getElementById('backtestRunMeta');
        if (meta) {
            const settings = result.settings || {};
            meta.textContent = `AI周期 ${this.formatInteger(settings.effective_decision_interval_seconds || 0)} 秒 · 风控周期 ${this.formatInteger(settings.effective_risk_interval_seconds || 0)} 秒 · 结束资金 ${this.formatCurrency(result.final_value || 0)}`;
        }

        this.renderBacktestChart(result.daily_values || []);
        this.renderBacktestCoinStats(metrics.coin_stats || []);
        this.renderBacktestTrades(result.trades || []);
        this.renderBacktestHistory();
    }

    renderBacktestChart(dailyValues) {
        const chartDom = document.getElementById('backtestChart');
        if (!chartDom) return;

        if (!this.backtestChart) {
            this.backtestChart = echarts.init(chartDom);
            window.addEventListener('resize', () => {
                if (this.backtestChart) this.backtestChart.resize();
            });
        }

        const data = (dailyValues || []).map((item) => ({
            date: item.date,
            value: Number(item.total_value || 0)
        }));

        this.backtestChart.setOption({
            grid: { left: 56, right: 20, top: 20, bottom: 35 },
            tooltip: {
                trigger: 'axis',
                formatter: (params) => {
                    const point = params?.[0];
                    if (!point) return '';
                    return `${point.axisValue}<br>${this.formatCurrency(point.value)}`;
                }
            },
            xAxis: {
                type: 'category',
                data: data.map((item) => item.date),
                axisLabel: { color: '#86909c' },
                axisLine: { lineStyle: { color: '#c9cdd4' } }
            },
            yAxis: {
                type: 'value',
                scale: true,
                axisLabel: {
                    color: '#86909c',
                    formatter: (value) => `$${Number(value).toLocaleString('zh-CN')}`
                },
                splitLine: { lineStyle: { color: '#f2f3f5' } }
            },
            series: [{
                type: 'line',
                data: data.map((item) => item.value),
                smooth: true,
                symbol: 'none',
                lineStyle: { color: '#165dff', width: 2.5 },
                areaStyle: {
                    color: {
                        type: 'linear',
                        x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [
                            { offset: 0, color: 'rgba(22, 93, 255, 0.22)' },
                            { offset: 1, color: 'rgba(22, 93, 255, 0.02)' }
                        ]
                    }
                }
            }]
        });
    }

    renderBacktestCoinStats(coinStats) {
        const tbody = document.getElementById('backtestCoinStatsBody');
        if (!tbody) return;
        if (!coinStats.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="empty-state">暂无数据</td></tr>';
            return;
        }

        tbody.innerHTML = coinStats.map((item) => {
            const netPnl = Number(item.net_pnl || 0);
            const pnlClass = netPnl > 0 ? 'text-success' : netPnl < 0 ? 'text-danger' : '';
            return `
                <tr>
                    <td><strong>${item.coin}</strong></td>
                    <td>${this.formatInteger(item.trades)}</td>
                    <td>${this.formatPercent(item.win_rate)}</td>
                    <td class="${pnlClass}">${this.formatCurrency(netPnl)}</td>
                    <td>${this.formatCurrency(item.fees)}</td>
                </tr>
            `;
        }).join('');
    }

    renderBacktestTrades(trades) {
        const tbody = document.getElementById('backtestTradesBody');
        if (!tbody) return;

        const displayTrades = (trades || [])
            .filter((trade) => ['buy_to_enter', 'sell_to_enter', 'increase_position', 'reduce_position', 'close_position', 'fixed_stop', 'take_profit'].includes(trade.signal))
            .slice(-12)
            .reverse();

        if (!displayTrades.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="empty-state">暂无数据</td></tr>';
            return;
        }

        const signalMap = {
            buy_to_enter: '开多',
            sell_to_enter: '开空',
            increase_position: '加仓',
            reduce_position: '减仓',
            close_position: '平仓',
            fixed_stop: '止损',
            take_profit: '止盈'
        };

        tbody.innerHTML = displayTrades.map((trade) => {
            const pnl = Number(trade.pnl || 0);
            const pnlClass = pnl > 0 ? 'text-success' : pnl < 0 ? 'text-danger' : '';
            return `
                <tr>
                    <td>${trade.timestamp}</td>
                    <td><strong>${trade.coin || '-'}</strong></td>
                    <td>${signalMap[trade.signal] || trade.signal}</td>
                    <td>${this.formatCurrency(trade.price || 0)}</td>
                    <td class="${pnlClass}">${this.formatCurrency(pnl)}</td>
                </tr>
            `;
        }).join('');
    }

    async submitBacktestJob() {
        if (!this.currentModelId) {
            alert('请先选择一个模型再运行回测');
            return;
        }
        if (this.isRunningBacktest) {
            return;
        }

        const startDate = document.getElementById('backtestStartDate')?.value;
        const endDate = document.getElementById('backtestEndDate')?.value;
        const initialCapital = Number(document.getElementById('backtestInitialCapital')?.value || 0);
        const decisionInterval = Number(document.getElementById('backtestDecisionInterval')?.value || 3600);
        const riskInterval = Number(document.getElementById('backtestRiskInterval')?.value || 300);
        const maxAiCalls = Number(document.getElementById('backtestMaxAiCalls')?.value || 2000);
        const mode = document.getElementById('backtestMode')?.value || 'candidate_ai';

        if (!startDate || !endDate) {
            alert('请选择回测开始和结束日期');
            return;
        }
        if (startDate > endDate) {
            alert('开始日期不能晚于结束日期');
            return;
        }
        if (!Number.isFinite(initialCapital) || initialCapital <= 0) {
            alert('初始资金必须大于 0');
            return;
        }
        if (!Number.isFinite(maxAiCalls) || maxAiCalls <= 0) {
            alert('最大AI调用数必须大于 0');
            return;
        }

        const statusEl = document.getElementById('backtestStatus');
        if (statusEl) {
            statusEl.textContent = '正在创建回测任务并提交到后台，请稍候...';
        }

        this.stopBacktestPolling();
        this.setBacktestRunning(true);

        try {
            const response = await fetch('/api/backtest/jobs', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    model_id: this.currentModelId,
                    start_date: startDate,
                    end_date: endDate,
                    initial_capital: initialCapital,
                    decision_interval_seconds: decisionInterval,
                    risk_interval_seconds: riskInterval,
                    max_ai_calls: maxAiCalls,
                    mode
                })
            });
            const job = await this.parseJsonResponse(response);
            if (!response.ok) {
                throw new Error(job.error || '回测失败');
            }

            this.currentBacktestJobId = job.id;
            this.clearBacktestResults({ keepStatus: true });
            this.loadBacktestJobs();
            if (statusEl) {
                statusEl.textContent = job.message || '回测任务已提交，正在后台执行...';
            }
            this.startBacktestPolling(job.id);
        } catch (error) {
            console.error('Backtest job creation failed:', error);
            if (statusEl) {
                statusEl.textContent = `回测失败：${error.message}`;
            }
            alert(error.message || '回测失败');
            this.setBacktestRunning(false);
        }
    }

    async pollBacktestJob(jobId) {
        if (!jobId) return;
        try {
            const response = await fetch(`/api/backtest/jobs/${jobId}`, {
                credentials: 'include'
            });
            if (!response.ok) {
                throw new Error('无法获取回测任务状态');
            }
            const job = await this.parseJsonResponse(response);
            const statusEl = document.getElementById('backtestStatus');
            if (statusEl) {
                statusEl.textContent = `${job.message || '回测进行中'} (${Math.round(Number(job.progress || 0))}%)`;
            }

            if (job.status === 'completed') {
                this.stopBacktestPolling();
                this.setBacktestRunning(false);
                this.currentBacktestJobId = job.id;
                if (job.result) {
                    this.backtestResult = job.result;
                    this.renderBacktestResult(job.result);
                    this.saveBacktestHistoryEntry(job.result);
                }
                this.loadBacktestJobs();
                return;
            }

            if (job.status === 'failed') {
                this.stopBacktestPolling();
                this.setBacktestRunning(false);
                if (statusEl) {
                    statusEl.textContent = `回测失败：${job.error || job.message || '未知错误'}`;
                }
                this.loadBacktestJobs();
                return;
            }

            this.setBacktestRunning(true);
            this.loadBacktestJobs();
        } catch (error) {
            console.error('Failed to poll backtest job:', error);
        }
    }

    saveBacktestHistoryEntry(result) {
        const metrics = result.metrics || {};
        const settings = result.settings || {};
        const entry = {
            created_at: new Date().toISOString(),
            start_date: result.start_date,
            end_date: result.end_date,
            total_return: Number(metrics.total_return || 0),
            total_net_pnl: Number(metrics.total_net_pnl || 0),
            win_rate: Number(metrics.win_rate || 0),
            total_fees: Number(metrics.total_fees || 0),
            decision_interval_seconds: Number(settings.effective_decision_interval_seconds || 0),
            risk_interval_seconds: Number(settings.effective_risk_interval_seconds || 0),
            entry_count: Number(metrics.entry_count || 0),
            max_drawdown: Number(metrics.max_drawdown || 0),
        };

        this.backtestHistory = [entry, ...(this.backtestHistory || [])]
            .slice(0, 10);
        this.writeBacktestHistory(this.backtestHistory);
    }

    renderBacktestHistory() {
        const tbody = document.getElementById('backtestHistoryBody');
        if (!tbody) return;

        const history = this.backtestHistory || [];
        if (!history.length) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty-state">暂无对比记录</td></tr>';
            return;
        }

        tbody.innerHTML = history.map((item) => {
            const returnClass = item.total_return > 0 ? 'text-success' : item.total_return < 0 ? 'text-danger' : '';
            const pnlClass = item.total_net_pnl > 0 ? 'text-success' : item.total_net_pnl < 0 ? 'text-danger' : '';
            return `
                <tr>
                    <td>${new Date(item.created_at).toLocaleString('zh-CN')}</td>
                    <td>${item.start_date} ~ ${item.end_date}</td>
                    <td class="${returnClass}">${this.formatPercent(item.total_return)}</td>
                    <td class="${pnlClass}">${this.formatCurrency(item.total_net_pnl)}</td>
                    <td>${this.formatPercent(item.win_rate)}</td>
                    <td>${this.formatCurrency(item.total_fees)}</td>
                    <td>${this.formatInteger(item.decision_interval_seconds)}秒</td>
                </tr>
            `;
        }).join('');
    }

    exportBacktest(format) {
        if (!this.backtestResult) {
            alert('请先运行一次回测再导出');
            return;
        }

        const modelName = (this.currentModel?.name || `model_${this.currentModelId || 'unknown'}`)
            .replace(/[^\w\u4e00-\u9fa5-]+/g, '_');
        const suffix = `${this.backtestResult.start_date}_${this.backtestResult.end_date}`;

        if (format === 'json') {
            const content = JSON.stringify(this.backtestResult, null, 2);
            this.downloadTextFile(`${modelName}_backtest_${suffix}.json`, content, 'application/json;charset=utf-8');
            return;
        }

        const rows = [];
        rows.push(['section', 'timestamp', 'coin', 'signal', 'price', 'quantity', 'pnl', 'fee']);
        (this.backtestResult.trades || []).forEach((trade) => {
            rows.push([
                'trades',
                trade.timestamp || '',
                trade.coin || '',
                trade.signal || '',
                trade.price ?? '',
                trade.quantity ?? '',
                trade.pnl ?? '',
                trade.fee ?? ''
            ]);
        });

        rows.push([]);
        rows.push(['metric', 'value']);
        const metrics = this.backtestResult.metrics || {};
        Object.entries(metrics).forEach(([key, value]) => {
            if (Array.isArray(value) || (value && typeof value === 'object')) return;
            rows.push([key, value]);
        });

        rows.push([]);
        rows.push(['coin', 'trades', 'win_rate', 'net_pnl', 'fees']);
        (metrics.coin_stats || []).forEach((item) => {
            rows.push([item.coin, item.trades, item.win_rate, item.net_pnl, item.fees]);
        });

        const csv = rows
            .map((row) => row.map((value) => {
                const stringValue = String(value ?? '');
                return /[",\n]/.test(stringValue) ? `"${stringValue.replace(/"/g, '""')}"` : stringValue;
            }).join(','))
            .join('\n');
        this.downloadTextFile(`${modelName}_backtest_${suffix}.csv`, csv, 'text/csv;charset=utf-8');
    }

    downloadTextFile(filename, content, mimeType) {
        const blob = new Blob([content], { type: mimeType });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
    }

    switchTab(tabName) {
        document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));

        document.querySelector(`[data-tab="${tabName}"]`).classList.add('active');
        document.getElementById(`${tabName}Tab`).classList.add('active');
    }

    showModal() {
        document.getElementById('addModelModal').classList.add('show');
    }

    hideModal() {
        document.getElementById('addModelModal').classList.remove('show');
    }

    async submitModel() {
        const systemPrompt = document.getElementById('systemPrompt').value.trim();

        const data = {
            name: document.getElementById('modelName').value,
            api_key: document.getElementById('apiKey').value,
            api_url: document.getElementById('apiUrl').value,
            model_name: document.getElementById('modelIdentifier').value,
            initial_capital: parseFloat(document.getElementById('initialCapital').value),
            system_prompt: systemPrompt || null  // 如果为空则传null，使用默认prompt
        };

        if (!data.name || !data.api_key || !data.api_url || !data.model_name) {
            alert('请填写所有必填字段');
            return;
        }

        try {
            const response = await fetch('/api/models', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });

            if (response.ok) {
                this.hideModal();
                this.loadModels();
                this.clearForm();
            }
        } catch (error) {
            console.error('Failed to add model:', error);
            alert('添加模型失败');
        }
    }

    async editModel(modelId) {
        try {
            // 获取模型详情
            const response = await fetch(`/api/models/${modelId}`);
            if (!response.ok) {
                alert('获取模型信息失败');
                return;
            }

            const model = await response.json();

            // 填充编辑表单
            document.getElementById('editModelId').value = model.id;
            document.getElementById('editModelName').value = model.name;
            document.getElementById('editApiKey').value = '••••••••';  // 不显示真实API Key
            document.getElementById('editApiUrl').value = model.api_url;
            document.getElementById('editModelIdentifier').value = model.model_name;
            document.getElementById('editInitialCapital').value = model.initial_capital;
            document.getElementById('editSystemPrompt').value = model.system_prompt || '';

            // 显示编辑modal
            document.getElementById('editModelModal').classList.add('show');
        } catch (error) {
            console.error('Failed to load model for editing:', error);
            alert('获取模型信息失败');
        }
    }

    async deleteModel(modelId) {
        if (!confirm('确定要删除这个模型吗？')) return;

        try {
            const response = await fetch(`/api/models/${modelId}`, {
                method: 'DELETE'
            });

            if (response.ok) {
                if (this.currentModelId === modelId) {
                    this.currentModelId = null;
                }
                this.loadModels();
            }
        } catch (error) {
            console.error('Failed to delete model:', error);
        }
    }

    clearForm() {
        document.getElementById('modelName').value = '';
        document.getElementById('apiKey').value = '';
        document.getElementById('apiUrl').value = '';
        document.getElementById('modelIdentifier').value = '';
        document.getElementById('initialCapital').value = '10000';
        document.getElementById('systemPrompt').value = '';
    }

    async refresh() {
        const models = await this.loadModels();
        if (models.length > 0 && this.currentModelId && !models.some(model => model.id === this.currentModelId)) {
            this.currentModelId = models[0].id;
            this.saveSelectedModelId(this.currentModelId);
        }
        await Promise.all([
            this.loadMarketPrices({ useCache: false }),
            this.loadModelData({ silentIfBusy: false }),
            this.loadKlineData(),
            this.loadLatestBacktestJob(),
            this.loadBacktestJobs()
        ]);
    }

    initVisibilityRefresh() {
        const refreshWhenVisible = () => {
            if (document.visibilityState === 'visible') {
                this.loadMarketPrices({ useCache: true });
                if (this.currentModelId) {
                    this.loadModelData({ silentIfBusy: true });
                    this.loadLatestBacktestJob();
                    this.loadBacktestJobs();
                }
                this.loadKlineData();
            }
        };

        document.addEventListener('visibilitychange', refreshWhenVisible);
        window.addEventListener('focus', refreshWhenVisible);
    }

    startRefreshCycles() {
        this.refreshIntervals.market = setInterval(() => {
            this.loadMarketPrices();
        }, this.marketRefreshInterval);

        this.refreshIntervals.positionPnl = setInterval(() => {
            if (this.currentModelId) {
                this.loadPortfolioSnapshot({ skipCacheWrite: false });
            }
        }, this.positionPnlRefreshInterval);

        this.refreshIntervals.portfolio = setInterval(() => {
            if (this.currentModelId) {
                this.loadModelData({ silentIfBusy: true });
            }
        }, this.portfolioRefreshInterval);
    }

    stopRefreshCycles() {
        Object.values(this.refreshIntervals).forEach(interval => {
            if (interval) clearInterval(interval);
        });
    }

    toggleTheme() {
        const currentTheme = document.documentElement.getAttribute('data-theme');
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

        document.documentElement.setAttribute('data-theme', newTheme);
        localStorage.setItem('theme', newTheme);

        // 更新图标
        const icon = document.querySelector('#themeToggle i');
        if (newTheme === 'dark') {
            icon.className = 'bi bi-sun-fill';
        } else {
            icon.className = 'bi bi-moon-fill';
        }

        // 重新渲染图表
        if (this.chart) {
            this.chart.dispose();
            this.chart = echarts.init(document.getElementById('accountChart'));
            this.loadPortfolioSnapshot({ skipCacheWrite: false });
        }
        if (this.klineChart) {
            this.klineChart.dispose();
            this.klineChart = echarts.init(document.getElementById('klineChart'));
            this.loadKlineData();
        }
        if (this.backtestChart) {
            this.backtestChart.dispose();
            this.backtestChart = echarts.init(document.getElementById('backtestChart'));
            if (this.backtestResult) {
                this.renderBacktestChart(this.backtestResult.daily_values || []);
            }
        }
    }

    loadTheme() {
        const savedTheme = localStorage.getItem('theme') || 'light';
        document.documentElement.setAttribute('data-theme', savedTheme);

        const icon = document.querySelector('#themeToggle i');
        if (savedTheme === 'dark') {
            icon.className = 'bi bi-sun-fill';
        } else {
            icon.className = 'bi bi-moon-fill';
        }
    }

    getKlineColors() {
        if (this.klineColorMode === 'red-up') {
            // 红涨绿跌（中国大陆）
            return {
                color: '#ef4444',        // 涨：红色（实体）
                color0: '#22c55e',       // 跌：绿色（实体）
                borderColor: '#ef4444',  // 涨：红色（边框和影线）
                borderColor0: '#22c55e', // 跌：绿色（边框和影线）
                borderWidth: 3           // 增加边框宽度，影线更明显
            };
        } else {
            // 绿涨红跌（国际）
            return {
                color: '#22c55e',        // 涨：绿色（实体）
                color0: '#ef4444',       // 跌：红色（实体）
                borderColor: '#22c55e',  // 涨：绿色（边框和影线）
                borderColor0: '#ef4444', // 跌：红色（边框和影线）
                borderWidth: 3           // 增加边框宽度，影线更明显
            };
        }
    }

    toggleKlineColor() {
        this.klineColorMode = this.klineColorMode === 'red-up' ? 'green-up' : 'red-up';
        const btn = document.getElementById('klineColorToggle');
        btn.innerHTML = this.klineColorMode === 'red-up'
            ? '<i class="bi bi-palette"></i> 绿涨红跌'
            : '<i class="bi bi-palette"></i> 红涨绿跌';
        this.loadKlineData(); // 重新加载K线图
    }

    initKlineChart() {
        const chartDom = document.getElementById('klineChart');
        this.klineChart = echarts.init(chartDom);
        this.loadKlineData();
    }

    async loadKlineData() {
        try {
            const response = await fetch(`/api/market/historical/${this.currentKlineCoin}?days=30`);
            const data = await response.json();

            if (!data || data.length === 0) {
                console.warn('No kline data available');
                return;
            }

            // 转换数据格式为ECharts K线图格式
            const klineData = data.map(item => [
                item.timestamp,
                item.open || item.price,
                item.close || item.price,
                item.low || item.price,
                item.high || item.price,
                item.volume || 0
            ]);

            const option = {
                title: {
                    text: `${this.currentKlineCoin}/USDT`,
                    left: 0
                },
                tooltip: {
                    trigger: 'axis',
                    axisPointer: {
                        type: 'cross'
                    }
                },
                legend: {
                    data: ['K线', '成交量'],
                    top: 30
                },
                grid: [
                    {
                        left: '10%',
                        right: '10%',
                        top: '15%',
                        height: '50%'
                    },
                    {
                        left: '10%',
                        right: '10%',
                        top: '70%',
                        height: '15%'
                    }
                ],
                xAxis: [
                    {
                        type: 'category',
                        data: klineData.map(item => new Date(item[0]).toLocaleDateString()),
                        boundaryGap: false,
                        axisLine: { onZero: false },
                        splitLine: { show: false },
                        min: 'dataMin',
                        max: 'dataMax'
                    },
                    {
                        type: 'category',
                        gridIndex: 1,
                        data: klineData.map(item => new Date(item[0]).toLocaleDateString()),
                        boundaryGap: false,
                        axisLine: { onZero: false },
                        axisTick: { show: false },
                        splitLine: { show: false },
                        axisLabel: { show: false },
                        min: 'dataMin',
                        max: 'dataMax'
                    }
                ],
                yAxis: [
                    {
                        scale: true,
                        splitArea: {
                            show: false
                        },
                        splitLine: {
                            show: true,
                            lineStyle: {
                                color: '#e5e6eb',
                                type: 'dashed'
                            }
                        },
                        axisLabel: {
                            fontSize: 12,
                            color: '#86909c'
                        }
                    },
                    {
                        scale: true,
                        gridIndex: 1,
                        splitNumber: 2,
                        axisLabel: { show: false },
                        axisLine: { show: false },
                        axisTick: { show: false },
                        splitLine: { show: false }
                    }
                ],
                dataZoom: [
                    {
                        type: 'inside',
                        xAxisIndex: [0, 1],
                        start: 50,
                        end: 100
                    },
                    {
                        show: true,
                        xAxisIndex: [0, 1],
                        type: 'slider',
                        top: '90%',
                        start: 50,
                        end: 100
                    }
                ],
                series: [
                    {
                        name: 'K线',
                        type: 'candlestick',
                        data: klineData.map(item => [item[1], item[2], item[3], item[4]]),
                        itemStyle: this.getKlineColors(),
                        barWidth: '90%',           // 更粗的蜡烛
                        barMaxWidth: 30,           // 增加最大宽度
                        barMinWidth: 8             // 增加最小宽度
                    },
                    {
                        name: '成交量',
                        type: 'bar',
                        xAxisIndex: 1,
                        yAxisIndex: 1,
                        data: klineData.map((item, idx) => {
                            // 根据涨跌设置成交量颜色
                            const isUp = idx === 0 ? true : item[4] >= klineData[idx-1][4];
                            return {
                                value: item[5],
                                itemStyle: {
                                    color: isUp ? 'rgba(34, 197, 94, 0.4)' : 'rgba(239, 68, 68, 0.4)'
                                }
                            };
                        })
                    }
                ]
            };

            this.klineChart.setOption(option);
        } catch (error) {
            console.error('Failed to load kline data:', error);
        }
    }
}

const app = new TradingApp();

// Edit Model Modal事件监听
document.getElementById('closeEditModalBtn').addEventListener('click', () => {
    document.getElementById('editModelModal').classList.remove('show');
});

document.getElementById('cancelEditBtn').addEventListener('click', () => {
    document.getElementById('editModelModal').classList.remove('show');
});

document.getElementById('submitEditBtn').addEventListener('click', async () => {
    const modelId = document.getElementById('editModelId').value;
    const systemPrompt = document.getElementById('editSystemPrompt').value;

    try {
        const response = await fetch(`/api/models/${modelId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                system_prompt: systemPrompt
            })
        });

        if (response.ok) {
            document.getElementById('editModelModal').classList.remove('show');
            alert('交易策略更新成功！');
            app.loadModels();
        } else {
            const error = await response.json();
            alert(error.error || '更新失败');
        }
    } catch (error) {
        console.error('Failed to update model:', error);
        alert('更新失败');
    }
});
