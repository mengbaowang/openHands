class BacktestCenterApp {
    constructor() {
        this.currentUser = null;
        this.models = [];
        this.currentModelId = null;
        this.currentModel = null;
        this.currentResult = null;
        this.currentBacktestJobId = null;
        this.backtestChart = null;
        this.backtestPollTimer = null;
        this.isRunningBacktest = false;
        this.init();
    }

    async init() {
        await this.checkAuth();
        this.initEventListeners();
        this.initBacktestDefaults();
        await this.loadModels();
        if (this.models.length > 0) {
            const savedModelId = this.getSavedModelId();
            const preferredModel = this.models.find((model) => model.id === savedModelId) || this.models[0];
            await this.selectModel(preferredModel.id);
        }
        this.loadTheme();
    }

    async checkAuth() {
        const response = await fetch('/api/auth/me', { credentials: 'include' });
        if (!response.ok) {
            window.location.href = '/login';
            return;
        }
        this.currentUser = await response.json();
        const userInfoEl = document.getElementById('userInfo');
        if (userInfoEl && this.currentUser) {
            userInfoEl.textContent = `欢迎, ${this.currentUser.username}`;
        }
    }

    initEventListeners() {
        document.getElementById('runBacktestBtn')?.addEventListener('click', () => this.submitBacktestJob());
        document.getElementById('backtestResetBtn')?.addEventListener('click', () => this.resetBacktestForm());
        document.getElementById('exportBacktestJsonBtn')?.addEventListener('click', () => this.exportBacktest('json'));
        document.getElementById('exportBacktestCsvBtn')?.addEventListener('click', () => this.exportBacktest('csv'));
        document.getElementById('themeToggle')?.addEventListener('click', () => this.toggleTheme());
        document.getElementById('logoutBtn')?.addEventListener('click', () => this.logout());
        document.querySelectorAll('.backtest-preset-btn').forEach((btn) => {
            btn.addEventListener('click', () => this.applyBacktestPreset(Number(btn.dataset.days || 90)));
        });
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

    initBacktestDefaults() {
        const endDate = new Date();
        const startDate = new Date(endDate);
        startDate.setDate(endDate.getDate() - 90);
        document.getElementById('backtestStartDate').value = this.formatDateInput(startDate);
        document.getElementById('backtestEndDate').value = this.formatDateInput(endDate);
        this.applyBacktestPreset(90);
        this.toggleExportButtons(false);
    }

    resetBacktestForm() {
        this.initBacktestDefaults();
        document.getElementById('backtestDecisionInterval').value = '3600';
        document.getElementById('backtestRiskInterval').value = '300';
        document.getElementById('backtestMaxAiCalls').value = '2000';
        document.getElementById('backtestMode').value = 'candidate_ai';
        if (this.currentModel?.initial_capital !== undefined) {
            document.getElementById('backtestInitialCapital').value = Number(this.currentModel.initial_capital || 10000);
        }
    }

    async loadModels() {
        const response = await fetch('/api/models', { credentials: 'include' });
        this.models = response.ok ? await response.json() : [];
        this.renderModels();
    }

    renderModels() {
        const container = document.getElementById('modelList');
        if (!this.models.length) {
            container.innerHTML = '<div class="empty-state">暂无模型</div>';
            return;
        }
        container.innerHTML = this.models.map((model) => `
            <div class="model-item ${model.id === this.currentModelId ? 'active' : ''}" data-model-id="${model.id}">
                <div class="model-name">${model.name}</div>
                <div class="model-info"><span>${model.model_name}</span></div>
            </div>
        `).join('');
        container.querySelectorAll('[data-model-id]').forEach((item) => {
            item.addEventListener('click', () => this.selectModel(Number(item.dataset.modelId)));
        });
    }

    async selectModel(modelId) {
        this.currentModelId = modelId;
        this.currentModel = this.models.find((item) => item.id === modelId) || null;
        this.saveSelectedModelId(modelId);
        this.renderModels();
        if (this.currentModel?.initial_capital !== undefined) {
            document.getElementById('backtestInitialCapital').value = Number(this.currentModel.initial_capital || 10000);
        }
        await Promise.all([
            this.loadBacktestResults(),
            this.loadBacktestJobs(),
            this.loadLatestBacktestJob(),
        ]);
    }

    async loadBacktestResults() {
        if (!this.currentModelId) return;
        const response = await fetch(`/api/models/${this.currentModelId}/backtest-results?limit=50`, { credentials: 'include' });
        const results = response.ok ? await response.json() : [];
        this.renderBacktestResultsList(Array.isArray(results) ? results : []);
    }

    renderBacktestResultsList(results) {
        const container = document.getElementById('backtestResultList');
        if (!results.length) {
            container.innerHTML = '<div class="empty-state">暂无回测结果</div>';
            return;
        }
        container.innerHTML = results.map((item) => `
            <div class="model-item ${this.currentResult && this.currentResult.id === item.id ? 'active' : ''}" data-result-id="${item.id}">
                <div class="model-name">${item.start_date} ~ ${item.end_date}</div>
                <div class="model-info">
                    <span>${this.renderModeText(item.mode)} · ${this.formatPercent(item.total_return)}</span>
                </div>
            </div>
        `).join('');
        container.querySelectorAll('[data-result-id]').forEach((item) => {
            item.addEventListener('click', () => this.loadBacktestResultDetail(Number(item.dataset.resultId)));
        });
    }

    async loadBacktestResultDetail(resultId) {
        const response = await fetch(`/api/backtest-results/${resultId}`, { credentials: 'include' });
        if (!response.ok) return;
        const result = await response.json();
        this.currentResult = result;
        this.renderBacktestDetail(result);
        await this.loadBacktestResults();
    }

    renderBacktestDetail(row) {
        const result = row.result || {};
        if (!result || !Object.keys(result).length) return;
        document.getElementById('backtestEmptyState').classList.add('hidden');
        document.getElementById('backtestResults').classList.remove('hidden');
        this.toggleExportButtons(true);

        const metrics = result.metrics || {};
        const summaryMetrics = {
            total_return: { value: this.formatPercent(metrics.total_return), numeric: Number(metrics.total_return || 0) },
            total_net_pnl: { value: this.formatCurrency(metrics.total_net_pnl), numeric: Number(metrics.total_net_pnl || 0) },
            win_rate: { value: this.formatPercent(metrics.win_rate), numeric: Number(metrics.win_rate || 0) },
            total_fees: { value: this.formatCurrency(metrics.total_fees), numeric: -Math.abs(Number(metrics.total_fees || 0)) },
            entry_count: { value: this.formatInteger(metrics.entry_count), numeric: 0 },
            max_drawdown: { value: this.formatPercent(metrics.max_drawdown || 0), numeric: -Math.abs(Number(metrics.max_drawdown || 0)) }
        };
        document.querySelectorAll('[data-metric]').forEach((el) => {
            const item = summaryMetrics[el.dataset.metric];
            if (!item) return;
            el.textContent = item.value;
            el.classList.remove('positive', 'negative');
            if (item.numeric > 0 && el.dataset.metric !== 'entry_count') el.classList.add('positive');
            if (item.numeric < 0) el.classList.add('negative');
        });

        document.getElementById('backtestRunMeta').textContent =
            `${this.renderModeText(row.mode)} · ${row.start_date} ~ ${row.end_date} · 结束资金 ${this.formatCurrency(result.final_value || 0)}`;
        this.renderBacktestChart(result.daily_values || []);
        this.renderCoinStats(metrics.coin_stats || []);
        this.renderTrades(result.trades || []);
    }

    async loadBacktestJobs() {
        if (!this.currentModelId) return;
        const response = await fetch(`/api/models/${this.currentModelId}/backtest-jobs?limit=20`, { credentials: 'include' });
        const jobs = response.ok ? await response.json() : [];
        this.renderBacktestJobs(Array.isArray(jobs) ? jobs : []);
    }

    renderBacktestJobs(jobs) {
        const tbody = document.getElementById('backtestJobsBody');
        if (!jobs.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="empty-state">暂无任务</td></tr>';
            return;
        }
        const statusMap = { queued: '排队中', running: '运行中', completed: '已完成', failed: '失败' };
        tbody.innerHTML = jobs.map((job) => `
            <tr>
                <td>${new Date(job.created_at).toLocaleString('zh-CN')}</td>
                <td>${this.renderModeText(job.mode)}</td>
                <td>${job.start_date} ~ ${job.end_date}</td>
                <td>${statusMap[job.status] || job.status}</td>
                <td>${Math.round(Number(job.progress || 0))}%</td>
                <td>${job.error || job.message || '-'}</td>
            </tr>
        `).join('');
    }

    async loadLatestBacktestJob() {
        if (!this.currentModelId) return;
        const response = await fetch(`/api/models/${this.currentModelId}/backtest-jobs/latest`, { credentials: 'include' });
        if (!response.ok) return;
        const job = await response.json();
        if (!job || !job.id) return;
        this.currentBacktestJobId = job.id;
        if (['queued', 'running'].includes(job.status)) {
            document.getElementById('backtestStatus').textContent = `${job.message || '回测进行中'} (${Math.round(Number(job.progress || 0))}%)`;
            this.startBacktestPolling(job.id);
        } else if (job.status === 'completed') {
            document.getElementById('backtestStatus').textContent = job.message || '最近任务已完成';
        } else if (job.status === 'failed') {
            document.getElementById('backtestStatus').textContent = `最近任务失败：${job.error || job.message || '未知错误'}`;
        }
    }

    startBacktestPolling(jobId) {
        this.stopBacktestPolling();
        this.currentBacktestJobId = jobId;
        this.backtestPollTimer = setInterval(() => this.pollBacktestJob(jobId), 3000);
    }

    stopBacktestPolling() {
        if (this.backtestPollTimer) {
            clearInterval(this.backtestPollTimer);
            this.backtestPollTimer = null;
        }
    }

    async pollBacktestJob(jobId) {
        const response = await fetch(`/api/backtest/jobs/${jobId}`, { credentials: 'include' });
        if (!response.ok) return;
        const job = await response.json();
        document.getElementById('backtestStatus').textContent = `${job.message || '回测进行中'} (${Math.round(Number(job.progress || 0))}%)`;
        await this.loadBacktestJobs();
        if (job.status === 'completed') {
            this.stopBacktestPolling();
            await this.loadBacktestResults();
            if (job.result) {
                this.currentResult = { id: job.id, mode: job.mode, start_date: job.start_date, end_date: job.end_date, result: job.result };
                this.renderBacktestDetail(this.currentResult);
            }
        }
        if (job.status === 'failed') {
            this.stopBacktestPolling();
        }
    }

    async submitBacktestJob() {
        if (!this.currentModelId) {
            alert('请先选择一个模型再运行回测');
            return;
        }
        if (this.isRunningBacktest) return;

        const payload = {
            model_id: this.currentModelId,
            start_date: document.getElementById('backtestStartDate').value,
            end_date: document.getElementById('backtestEndDate').value,
            initial_capital: Number(document.getElementById('backtestInitialCapital').value || 0),
            decision_interval_seconds: Number(document.getElementById('backtestDecisionInterval').value || 3600),
            risk_interval_seconds: Number(document.getElementById('backtestRiskInterval').value || 300),
            max_ai_calls: Number(document.getElementById('backtestMaxAiCalls').value || 2000),
            mode: document.getElementById('backtestMode').value || 'candidate_ai',
        };
        document.getElementById('backtestStatus').textContent = '正在创建回测任务并提交到后台，请稍候...';
        this.setRunning(true);
        try {
            const response = await fetch('/api/backtest/jobs', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const job = await response.json();
            if (!response.ok) throw new Error(job.error || '回测失败');
            this.currentBacktestJobId = job.id;
            document.getElementById('backtestStatus').textContent = job.message || '回测任务已提交';
            await this.loadBacktestJobs();
            this.startBacktestPolling(job.id);
        } catch (error) {
            document.getElementById('backtestStatus').textContent = `回测失败：${error.message}`;
            alert(error.message || '回测失败');
            this.setRunning(false);
        }
    }

    renderBacktestChart(dailyValues) {
        const chartDom = document.getElementById('backtestChart');
        if (!this.backtestChart) {
            this.backtestChart = echarts.init(chartDom);
            window.addEventListener('resize', () => this.backtestChart && this.backtestChart.resize());
        }
        const data = (dailyValues || []).map((item) => ({ date: item.date, value: Number(item.total_value || 0) }));
        this.backtestChart.setOption({
            grid: { left: 56, right: 20, top: 20, bottom: 35 },
            tooltip: {
                trigger: 'axis',
                formatter: (params) => {
                    const point = params?.[0];
                    return point ? `${point.axisValue}<br>${this.formatCurrency(point.value)}` : '';
                }
            },
            xAxis: { type: 'category', data: data.map((item) => item.date) },
            yAxis: {
                type: 'value',
                scale: true,
                axisLabel: { formatter: (value) => `$${Number(value).toLocaleString('zh-CN')}` }
            },
            series: [{
                type: 'line',
                data: data.map((item) => item.value),
                smooth: true,
                symbol: 'none',
                lineStyle: { color: '#165dff', width: 2.5 }
            }]
        });
    }

    renderCoinStats(coinStats) {
        const tbody = document.getElementById('backtestCoinStatsBody');
        if (!coinStats.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="empty-state">暂无数据</td></tr>';
            return;
        }
        tbody.innerHTML = coinStats.map((item) => `
            <tr>
                <td><strong>${item.coin}</strong></td>
                <td>${this.formatInteger(item.trades)}</td>
                <td>${this.formatPercent(item.win_rate)}</td>
                <td class="${item.net_pnl > 0 ? 'text-success' : item.net_pnl < 0 ? 'text-danger' : ''}">${this.formatCurrency(item.net_pnl)}</td>
                <td>${this.formatCurrency(item.fees)}</td>
            </tr>
        `).join('');
    }

    renderTrades(trades) {
        const tbody = document.getElementById('backtestTradesBody');
        const displayTrades = (trades || []).slice(-12).reverse();
        if (!displayTrades.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="empty-state">暂无数据</td></tr>';
            return;
        }
        tbody.innerHTML = displayTrades.map((trade) => `
            <tr>
                <td>${trade.timestamp || '-'}</td>
                <td><strong>${trade.coin || '-'}</strong></td>
                <td>${trade.signal || '-'}</td>
                <td>${this.formatCurrency(trade.price || 0)}</td>
                <td class="${trade.pnl > 0 ? 'text-success' : trade.pnl < 0 ? 'text-danger' : ''}">${this.formatCurrency(trade.pnl || 0)}</td>
            </tr>
        `).join('');
    }

    exportBacktest(format) {
        const result = this.currentResult?.result;
        if (!result) {
            alert('请先选择一条回测结果');
            return;
        }
        const modelName = (this.currentModel?.name || `model_${this.currentModelId || 'unknown'}`).replace(/[^\w\u4e00-\u9fa5-]+/g, '_');
        const suffix = `${result.start_date || this.currentResult.start_date}_${result.end_date || this.currentResult.end_date}`;
        if (format === 'json') {
            return this.downloadTextFile(`${modelName}_backtest_${suffix}.json`, JSON.stringify(result, null, 2), 'application/json;charset=utf-8');
        }
        const rows = [['section', 'timestamp', 'coin', 'signal', 'price', 'quantity', 'pnl', 'fee']];
        (result.trades || []).forEach((trade) => rows.push(['trades', trade.timestamp || '', trade.coin || '', trade.signal || '', trade.price ?? '', trade.quantity ?? '', trade.pnl ?? '', trade.fee ?? '']));
        const csv = rows.map((row) => row.map((value) => {
            const stringValue = String(value ?? '');
            return /[",\n]/.test(stringValue) ? `"${stringValue.replace(/"/g, '""')}"` : stringValue;
        }).join(',')).join('\n');
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

    toggleExportButtons(enabled) {
        ['exportBacktestJsonBtn', 'exportBacktestCsvBtn'].forEach((id) => {
            const button = document.getElementById(id);
            if (button) button.disabled = !enabled;
        });
    }

    setRunning(isRunning) {
        this.isRunningBacktest = isRunning;
        const runBtn = document.getElementById('runBacktestBtn');
        if (runBtn) {
            runBtn.disabled = isRunning;
            runBtn.innerHTML = isRunning ? '<i class="bi bi-hourglass-split"></i> 回测中...' : '<i class="bi bi-play-fill"></i> 运行回测';
        }
    }

    applyBacktestPreset(days) {
        const endDate = new Date(document.getElementById('backtestEndDate').value || new Date());
        const startDate = new Date(endDate);
        startDate.setDate(endDate.getDate() - Number(days || 90));
        document.getElementById('backtestStartDate').value = this.formatDateInput(startDate);
        document.querySelectorAll('.backtest-preset-btn').forEach((btn) => {
            btn.classList.toggle('active', Number(btn.dataset.days || 0) === Number(days));
        });
    }

    async logout() {
        await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' });
        window.location.href = '/login';
    }

    toggleTheme() {
        const currentTheme = document.documentElement.getAttribute('data-theme');
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', newTheme);
        localStorage.setItem('theme', newTheme);
        const icon = document.querySelector('#themeToggle i');
        if (icon) icon.className = newTheme === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-fill';
    }

    loadTheme() {
        const savedTheme = localStorage.getItem('theme') || 'light';
        document.documentElement.setAttribute('data-theme', savedTheme);
        const icon = document.querySelector('#themeToggle i');
        if (icon) icon.className = savedTheme === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-fill';
    }

    renderModeText(mode) {
        return {
            candidate_ai: '候选AI',
            fast_rule: '快速规则',
            full_ai: '完整AI'
        }[mode] || mode || '-';
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
}

window.backtestCenterApp = new BacktestCenterApp();
