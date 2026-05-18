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
        document.getElementById('strategyCode')?.addEventListener('change', () => this.updateStrategyFields());
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
        document.getElementById('strategyCode').value = 'ai_replay';
        this.updateStrategyFields();
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
                <div class="model-name">
                    <span>${item.start_date} ~ ${item.end_date}</span>
                    <button class="btn-icon backtest-delete-btn" data-delete-result-id="${item.id}" title="删除回测结果">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
                <div class="model-info">
                    <span>${this.renderStrategyText(item.strategy_code)} · ${this.renderModeText(item.mode)} · ${this.renderIntervalText(item)} · ${this.formatPercent(item.total_return)}</span>
                </div>
            </div>
        `).join('');
        container.querySelectorAll('[data-result-id]').forEach((item) => {
            item.addEventListener('click', () => this.loadBacktestResultDetail(Number(item.dataset.resultId)));
        });
        container.querySelectorAll('[data-delete-result-id]').forEach((button) => {
            button.addEventListener('click', (event) => {
                event.stopPropagation();
                this.deleteBacktestResult(Number(button.dataset.deleteResultId));
            });
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
            `${this.renderStrategyText(row.strategy_code)} · ${this.renderModeText(row.mode)} · ${row.start_date} ~ ${row.end_date} · 结束资金 ${this.formatCurrency(result.final_value || 0)}`;
        this.renderBacktestChart(result.daily_values || []);
        this.renderCoinStats(metrics.coin_stats || []);
        this.renderTrades(result.trades || []);
        this.renderExitReasonStats(metrics.exit_reason_stats || {}, metrics, row.strategy_code);
        this.renderLadderRounds(metrics.ladder_rounds || [], row.strategy_code);
        this.renderExecutionStats(metrics.execution_simulation || {}, row.strategy_code);
        this.renderHighRiskRounds(metrics.ladder_rounds || [], metrics, row.strategy_code);
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
                <td>${this.renderStrategyText(job.strategy_code)} / ${this.renderModeText(job.mode)}</td>
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
            this.setRunning(true);
            this.startBacktestPolling(job.id);
        } else if (job.status === 'completed') {
            document.getElementById('backtestStatus').textContent = job.message || '最近任务已完成';
            this.setRunning(false);
        } else if (job.status === 'failed') {
            document.getElementById('backtestStatus').textContent = `最近任务失败：${job.error || job.message || '未知错误'}`;
            this.setRunning(false);
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
            this.setRunning(false);
            await this.loadBacktestResults();
            if (job.result) {
                this.currentResult = { id: job.id, strategy_code: job.strategy_code, mode: job.mode, start_date: job.start_date, end_date: job.end_date, result: job.result };
                this.renderBacktestDetail(this.currentResult);
            }
        }
        if (job.status === 'failed') {
            this.stopBacktestPolling();
            this.setRunning(false);
        }
    }

    async submitBacktestJob() {
        if (!this.currentModelId) {
            alert('请先选择一个模型再运行回测');
            return;
        }
        if (this.isRunningBacktest) return;

        const strategyCode = document.getElementById('strategyCode').value || 'ai_replay';
        const payload = {
            model_id: this.currentModelId,
            strategy_code: strategyCode,
            start_date: document.getElementById('backtestStartDate').value,
            end_date: document.getElementById('backtestEndDate').value,
            initial_capital: Number(document.getElementById('backtestInitialCapital').value || 0),
            decision_interval_seconds: Number(document.getElementById('backtestDecisionInterval').value || 3600),
            risk_interval_seconds: Number(document.getElementById('backtestRiskInterval').value || 300),
            max_ai_calls: Number(document.getElementById('backtestMaxAiCalls').value || 2000),
            mode: document.getElementById('backtestMode').value || 'candidate_ai',
            strategy_params: strategyCode === 'event_reversal' ? {
                pair: document.getElementById('eventPair').value || 'BTCUSDT',
                interval: document.getElementById('eventInterval').value || '1m',
                payout_ratio: Number(document.getElementById('eventPayoutRatio').value || 0.92),
                cooldown_signals: Number(document.getElementById('eventCooldownSignals').value || 3),
                stakes: String(document.getElementById('eventStakeLadder').value || '7,13,30,66,142')
                    .split(',')
                    .map((item) => Number(item.trim()))
                    .filter((item) => Number.isFinite(item) && item > 0),
            } : strategyCode === 'event_reversal_futures' ? {
                coin: document.getElementById('futuresCoin').value || 'BTC',
                timeframe: document.getElementById('futuresTimeframe').value || '15m',
                leverage: Number(document.getElementById('futuresLeverage').value || 2),
                risk_pct: Number(document.getElementById('futuresRiskPct').value || 0.005),
                stop_loss_pct: Number(document.getElementById('futuresStopLossPct').value || 0.003),
                take_profit_pct: Number(document.getElementById('futuresTakeProfitPct').value || 0.006),
                max_hold_bars: Number(document.getElementById('futuresMaxHoldBars').value || 3),
                fee_rate: Number(document.getElementById('futuresFeeRate').value || 0.0005),
                slippage_rate: Number(document.getElementById('futuresSlippageRate').value || 0.0002),
                trend_filter_enabled: (document.getElementById('futuresTrendFilterEnabled').value || 'true') === 'true',
                trend_rsi_threshold: Number(document.getElementById('futuresTrendRsiThreshold').value || 60),
                trend_lookback: Number(document.getElementById('futuresTrendLookback').value || 30),
            } : {},
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

    renderLadderRounds(rounds, strategyCode) {
        const panel = document.getElementById('ladderRoundsPanel');
        const list = document.getElementById('ladderRoundsList');
        if (!panel || !list) return;

        if (strategyCode !== 'event_reversal' || !Array.isArray(rounds) || !rounds.length) {
            panel.classList.add('hidden');
            list.innerHTML = '<div class="empty-state">暂无分层推进数据</div>';
            return;
        }

        panel.classList.remove('hidden');
        list.innerHTML = rounds.map((round) => {
            const isWin = round.final_status === 'win';
            return `
                <div class="ladder-round-card">
                    <div class="ladder-round-header">
                        <div class="ladder-round-title">第 ${round.round_id} 轮</div>
                        <div class="ladder-round-badge ${isWin ? 'win' : 'loss'}">${isWin ? '本轮获胜' : '第五层失败'}</div>
                    </div>
                    <div class="ladder-round-summary">
                        <div class="ladder-round-summary-item">
                            <span class="ladder-round-summary-label">推进层数</span>
                            <span class="ladder-round-summary-value">${this.formatInteger(round.levels_used)}</span>
                        </div>
                        <div class="ladder-round-summary-item">
                            <span class="ladder-round-summary-label">总下注</span>
                            <span class="ladder-round-summary-value">${this.formatCurrency(round.total_stake)}</span>
                        </div>
                        <div class="ladder-round-summary-item">
                            <span class="ladder-round-summary-label">本轮净盈亏</span>
                            <span class="ladder-round-summary-value ${round.total_pnl > 0 ? 'text-success' : round.total_pnl < 0 ? 'text-danger' : ''}">${this.formatCurrency(round.total_pnl)}</span>
                        </div>
                    </div>
                    <div class="ladder-steps">
                        ${(round.steps || []).map((step) => `
                            <div class="ladder-step">
                                <div class="ladder-step-index">第${step.level}层</div>
                                <div class="ladder-step-body">
                                    <div class="ladder-step-main">${step.timestamp} · ${step.direction} · 下注 ${this.formatCurrency(step.stake)}</div>
                                    <div class="ladder-step-sub">触发形态: ${step.trigger_color === 'bull' ? '三连阳反转做空' : '三连阴反转做多'} · 手续费 ${this.formatCurrency(step.fee, 4)}</div>
                                </div>
                                <div class="ladder-step-result ${step.won ? 'text-success' : 'text-danger'}">${step.won ? '胜' : '负'} ${this.formatCurrency(step.pnl)}</div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        }).join('');
    }

    renderExitReasonStats(exitReasonStats, metrics, strategyCode) {
        const panel = document.getElementById('exitReasonStatsPanel');
        const tbody = document.getElementById('exitReasonStatsBody');
        if (!panel || !tbody) return;

        if (strategyCode !== 'event_reversal_futures') {
            panel.classList.add('hidden');
            tbody.innerHTML = '<tr><td colspan="3" class="empty-state">暂无数据</td></tr>';
            return;
        }

        panel.classList.remove('hidden');
        const entries = Object.entries(exitReasonStats || {});
        const labelMap = {
            stop_loss: '止损出场',
            take_profit: '止盈出场',
            time_exit: '时间到期平仓',
        };
        if (!entries.length) {
            tbody.innerHTML = '<tr><td colspan="3" class="empty-state">暂无数据</td></tr>';
        } else {
            tbody.innerHTML = entries.map(([reason, item]) => `
                <tr>
                    <td>${labelMap[reason] || reason}</td>
                    <td>${this.formatInteger(item.count || 0)}</td>
                    <td class="${Number(item.net_pnl || 0) > 0 ? 'text-success' : Number(item.net_pnl || 0) < 0 ? 'text-danger' : ''}">${this.formatCurrency(item.net_pnl || 0)}</td>
                </tr>
            `).join('');
        }

        document.getElementById('futuresPatternCandidates').textContent = this.formatInteger(metrics.pattern_candidates || 0);
        document.getElementById('futuresTrendFiltered').textContent = this.formatInteger(metrics.trend_filtered || 0);
        document.getElementById('futuresExecutedEntries').textContent = this.formatInteger(metrics.executed_entries || 0);
    }

    renderExecutionStats(stats, strategyCode) {
        const panel = document.getElementById('executionStatsPanel');
        if (!panel) return;
        if (strategyCode !== 'event_reversal') {
            panel.classList.add('hidden');
            return;
        }
        panel.classList.remove('hidden');
        document.getElementById('execPatternCandidates').textContent = this.formatInteger(stats.pattern_candidates || 0);
        document.getElementById('execPreorderArmed').textContent = this.formatInteger(stats.preorder_armed || 0);
        document.getElementById('execFinalConfirmed').textContent = this.formatInteger(stats.final_confirmed_entries || 0);
        document.getElementById('execRecheckRejected').textContent = this.formatInteger(stats.recheck_rejected || 0);
        document.getElementById('execCooldownRejected').textContent = this.formatInteger(stats.cooldown_rejected || 0);
        document.getElementById('execBalanceRejected').textContent = this.formatInteger(stats.balance_rejected || 0);
    }

    renderHighRiskRounds(rounds, metrics, strategyCode) {
        const panel = document.getElementById('highRiskRoundsPanel');
        const list = document.getElementById('highRiskRoundsList');
        if (!panel || !list) return;
        if (strategyCode !== 'event_reversal') {
            panel.classList.add('hidden');
            list.innerHTML = '<div class="empty-state">暂无高风险轮次</div>';
            return;
        }
        const filtered = (rounds || []).filter((round) => Number(round.levels_used || 0) >= 4);
        panel.classList.remove('hidden');
        document.getElementById('highRiskCount4').textContent = this.formatInteger(metrics.high_risk_rounds_4plus || 0);
        document.getElementById('highRiskCount5').textContent = this.formatInteger(metrics.high_risk_rounds_5plus || 0);

        if (!filtered.length) {
            list.innerHTML = '<div class="empty-state">暂无高风险轮次</div>';
            return;
        }

        list.innerHTML = filtered.map((round) => {
            const reachedFive = Number(round.levels_used || 0) >= 5;
            return `
                <div class="ladder-round-card">
                    <div class="ladder-round-header">
                        <div class="ladder-round-title">第 ${round.round_id} 轮 · 打到第 ${round.levels_used} 层</div>
                        <div class="ladder-round-badge ${reachedFive ? 'loss' : 'win'}">${reachedFive ? '五层风险' : '四层风险'}</div>
                    </div>
                    <div class="ladder-round-summary">
                        <div class="ladder-round-summary-item">
                            <span class="ladder-round-summary-label">总下注</span>
                            <span class="ladder-round-summary-value">${this.formatCurrency(round.total_stake)}</span>
                        </div>
                        <div class="ladder-round-summary-item">
                            <span class="ladder-round-summary-label">总手续费</span>
                            <span class="ladder-round-summary-value">${this.formatCurrency(round.total_fee, 4)}</span>
                        </div>
                        <div class="ladder-round-summary-item">
                            <span class="ladder-round-summary-label">本轮净盈亏</span>
                            <span class="ladder-round-summary-value ${round.total_pnl > 0 ? 'text-success' : 'text-danger'}">${this.formatCurrency(round.total_pnl)}</span>
                        </div>
                    </div>
                    <div class="ladder-steps">
                        ${(round.steps || []).map((step) => `
                            <div class="ladder-step">
                                <div class="ladder-step-index">第${step.level}层</div>
                                <div class="ladder-step-body">
                                    <div class="ladder-step-main">${step.timestamp} · ${step.direction} · 下注 ${this.formatCurrency(step.stake)}</div>
                                    <div class="ladder-step-sub">净盈亏 ${this.formatCurrency(step.pnl)} · ${step.won ? '该层胜出' : '继续推进'}</div>
                                </div>
                                <div class="ladder-step-result ${step.won ? 'text-success' : 'text-danger'}">${step.won ? '胜' : '负'}</div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        }).join('');
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

    async deleteBacktestResult(resultId) {
        if (!confirm('确定删除这条回测结果吗？')) {
            return;
        }
        try {
            const response = await fetch(`/api/backtest-results/${resultId}`, {
                method: 'DELETE',
                credentials: 'include'
            });
            const payload = response.ok ? await response.json() : await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(payload.error || '删除失败');
            }
            if (this.currentResult && this.currentResult.id === resultId) {
                this.currentResult = null;
                document.getElementById('backtestResults').classList.add('hidden');
                document.getElementById('backtestEmptyState').classList.remove('hidden');
                this.toggleExportButtons(false);
            }
            await this.loadBacktestResults();
            await this.loadBacktestJobs();
        } catch (error) {
            alert(error.message || '删除失败');
        }
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

    renderIntervalText(item) {
        const params = item.params || {};
        const result = item.result || {};
        const settings = result.settings || {};
        return params.interval || params.timeframe || settings.interval || settings.timeframe || settings.base_timeframe || '-';
    }

    renderStrategyText(strategyCode) {
        return {
            ai_replay: 'AI回放',
            event_reversal: '事件反转',
            event_reversal_futures: '合约版反转'
        }[strategyCode] || strategyCode || '-';
    }

    updateStrategyFields() {
        const strategyCode = document.getElementById('strategyCode')?.value || 'ai_replay';
        const eventFields = document.getElementById('eventStrategyFields');
        const futuresFields = document.getElementById('futuresStrategyFields');
        const aiMode = document.getElementById('backtestMode');
        const decisionInterval = document.getElementById('backtestDecisionInterval');
        const riskInterval = document.getElementById('backtestRiskInterval');
        const maxAiCalls = document.getElementById('backtestMaxAiCalls');

        if (eventFields) {
            eventFields.classList.toggle('hidden', strategyCode !== 'event_reversal');
        }
        if (futuresFields) {
            futuresFields.classList.toggle('hidden', strategyCode !== 'event_reversal_futures');
        }
        if (aiMode) aiMode.disabled = strategyCode !== 'ai_replay';
        const disableAiFields = strategyCode !== 'ai_replay';
        if (decisionInterval) decisionInterval.disabled = disableAiFields;
        if (riskInterval) riskInterval.disabled = disableAiFields;
        if (maxAiCalls) maxAiCalls.disabled = disableAiFields;
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
