function portfolioAnalysis() {
    return {
        loading: false,
        dataLoaded: false,
        factorLoading: false,
        factorLoaded: false,
        factorError: '',
        factorDays: 252,
        factorModelPack: 'us_equity_lab_v1',
        benchmarkMode: 'equal_weight_active_holdings',
        benchmarkRows: [],
        exposureFilter: 'all',
        holdings: [],
        currentReviewWeekId: '',
        totalValue: 0,
        totalCost: 0,
        totalPnl: 0,
        totalPnlPct: 0,
        factorResult: {},
        showAllCorr: false,
        _charts: {},

        get usHoldings() {
            return this.holdings.filter(item => item.market === 'us');
        },
        get nonUsHoldings() {
            return this.holdings.filter(item => item.market !== 'us').map(item => ({
                ...item,
                market_label: item.market === 'hk' ? 'Text' : item.market === 'a' ? 'A Text' : item.market
            }));
        },

        async init() {
            await this.refreshData();
        },

        showLoadingState() {
            return this.loading && !this.dataLoaded;
        },

        showErrorState() {
            return !this.showLoadingState() && !!this.factorError;
        },

        showEmptyState() {
            return !this.showLoadingState() && !this.showErrorState() && this.dataLoaded && this.holdings.length === 0;
        },

        showSuccessState() {
            return !this.showLoadingState() && !this.showErrorState() && this.dataLoaded && this.holdings.length > 0;
        },

        canRunFactorAnalysis() {
            return this.dataLoaded && this.holdings.length > 0 && !this.loading && !this.factorLoading;
        },

        async refreshData() {
            this.loading = true;
            this.factorError = '';
            try {
                let data = await fetch('/api/weekly-review').then(r => r.json());
                const stocks = data.stocks || {};
                const hasData = Object.values(stocks).some(s => s && s.avg_cost != null);
                if (!hasData && data.week_id) {
                    const [y, w] = data.week_id.split('-W').map(Number);
                    const prevWeek = w > 1 ? `${y}-W${String(w - 1).padStart(2, '0')}` : `${y - 1}-W52`;
                    data = await fetch(`/api/weekly-review?week_id=${prevWeek}`).then(r => r.json());
                }
                this.processData(data);
                this.loadSavedFactorAnalysis(data.factor_analysis || {});
                this.dataLoaded = true;
            } catch (e) {
                this.factorError = 'LoadTextFailed: ' + (e.message || e);
            }
            this.loading = false;
        },

        processData(review) {
            const stocksData = review.stocks || {};
            const usdToHkd = Number(review.usd_to_hkd || 7.8);
            const cnyToHkd = Number(review.cny_to_hkd || 1.07);
            const eurToHkd = Number(review.eur_to_hkd || 8.4);
            const jpyToHkd = Number(review.jpy_to_hkd || 0.052);
            const krwToHkd = Number(review.krw_to_hkd || 0.0056);
            const serverTotalValue = Number(review.total_portfolio_value || 0);
            this.currentReviewWeekId = review.week_id || '';
            this.holdings = [];
            this.totalCost = 0;
            this.totalValue = 0;
            this.totalPnl = 0;

            for (const [stockId, sdata] of Object.entries(stocksData)) {
                if (!sdata) continue;
                const shares = Number(sdata.shares_held || 0);
                if (shares <= 0) continue;
                const avgCost = Number(sdata.avg_cost || 0);
                const perf = sdata.performance_data || {};
                const endPrice = Number(perf.end_price || 0);
                const ticker = sdata.position_metrics?.ticker || sdata.ticker || stockId;
                const isHk = String(ticker).toUpperCase().endsWith('.HK');
                const isA = /\.(SH|SZ|SS)$/i.test(String(ticker));
                const isEu = /\.(AS|DE)$/i.test(String(ticker));
                const isJp = /\.T$/i.test(String(ticker));
                const isKr = /\.(KS|KQ)$/i.test(String(ticker));
                const market = isHk ? 'hk' : (isA ? 'a' : (isEu ? 'eu' : (isJp ? 'jp' : (isKr ? 'kr' : 'us'))));
                const fx = market === 'us' ? usdToHkd : (market === 'a' ? cnyToHkd : (market === 'eu' ? eurToHkd : (market === 'jp' ? jpyToHkd : (market === 'kr' ? krwToHkd : 1))));
                const costPerShare = avgCost * fx;
                const pricePerShare = endPrice * fx;
                const cost = shares * costPerShare;
                const metricValue = Number(sdata.position_metrics?.holding_value_hkd || 0);
                const metricPnl = Number(sdata.position_metrics?.unrealized_pnl_hkd || 0);
                const value = metricValue > 0 ? metricValue : (pricePerShare > 0 ? shares * pricePerShare : cost);
                const pnl = metricValue > 0 ? metricPnl : value - cost;
                this.totalCost += cost;
                this.totalValue += value;
                this.totalPnl += pnl;
                this.holdings.push({
                    stock_id: stockId,
                    stock_name: sdata.stock_name || stockId,
                    ticker,
                    market,
                    shares,
                    cost,
                    value,
                    pnl,
                    weight: 0,
                });
            }

            if (serverTotalValue > 0) {
                this.totalValue = serverTotalValue;
            }
            this.totalPnlPct = this.totalCost > 0 ? this.totalPnl / this.totalCost * 100 : 0;
            this.holdings = this.holdings.map(item => ({
                ...item,
                weight: this.totalValue > 0 ? item.value / this.totalValue : 0,
            })).sort((a, b) => b.value - a.value);
            this.resetBenchmarkRows();
        },

        loadSavedFactorAnalysis(saved) {
            if (!saved || !Object.keys(saved).length) {
                this.factorResult = {};
                this.factorLoaded = false;
                this.factorError = '';
                return;
            }
            this.factorResult = saved;
            this.factorLoaded = !saved.error;
            this.factorError = saved.error || '';
            if (this.factorLoaded) this.initCharts();
        },

        async runFactorAnalysis() {
            this.factorLoading = true;
            this.factorError = '';
            try {
                const response = await fetch('/api/factor-analysis', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        week_id: this.currentReviewWeekId,
                        days: Number(this.factorDays) || 252,
                        windows: [63, 126, 252],
                        us_only: true,
                        model_pack: this.factorModelPack,
                        include_q_factors: true,
                        include_style_overlays: true,
                        include_sector_macro: true,
                        compare_prev_week: true,
                        benchmark_holdings: this.benchmarkPayload(),
                    })
                });
                const data = await response.json();
                if (!response.ok || data.error) throw new Error(data.error || 'TextAnalysisFailed');
                this.factorResult = data;
                this.factorLoaded = true;
                this.initCharts();
            } catch (e) {
                this.factorError = e.message || String(e);
                this.factorLoaded = false;
            }
            this.factorLoading = false;
        },

        benchmarkPayload() {
            if (this.benchmarkMode !== 'custom_benchmark_holdings') return null;
            return this.benchmarkRows
                .map(row => ({
                    stock_id: row.stock_id,
                    ticker: row.ticker,
                    weight: Number(row.weight_pct || 0) / 100,
                }))
                .filter(row => row.weight > 0);
        },

        resetBenchmarkRows() {
            const rows = this.usHoldings;
            const equalWeight = rows.length > 0 ? 100 / rows.length : 0;
            this.benchmarkRows = rows.map(item => ({
                stock_id: item.stock_id,
                ticker: item.ticker || item.stock_id,
                stock_name: item.stock_name || item.stock_id,
                portfolio_weight_pct: Number(item.weight || 0) * 100,
                weight_pct: Number(equalWeight.toFixed(2)),
            }));
        },

        benchmarkWeightTotalPct() {
            return this.benchmarkRows.reduce((sum, row) => sum + Number(row.weight_pct || 0), 0);
        },

        exposureFilterOptions() {
            return [
                { key: 'all', label: 'All' },
                { key: 'style', label: 'Style' },
                { key: 'sector', label: 'Sector' },
                { key: 'theme', label: 'Theme' },
            ];
        },

        exposureTypeForKey(key) {
            if (String(key || '').startsWith('sector_')) return 'sector';
            if (String(key || '').startsWith('theme_')) return 'theme';
            return 'style';
        },

        exposureRowsAll() {
            const rows = [];
            const factorLabels = this.factorResult.factor_labels || {};
            for (const [factor, value] of Object.entries(this.factorResult.portfolio_exposure || {})) {
                const type = this.exposureTypeForKey(factor);
                rows.push({
                    key: factor,
                    type,
                    typeLabel: type.charAt(0).toUpperCase() + type.slice(1),
                    label: (factorLabels[factor] || {}).zh || factor,
                    desc: (factorLabels[factor] || {}).desc || '',
                    value: Number(value || 0),
                    bias: this.factorBias(factor, Number(value || 0)),
                    source: 'portfolio_exposure',
                });
            }
            for (const [key, item] of Object.entries(this.factorResult.style_overlays || {})) {
                if (!item || item.error) continue;
                rows.push({
                    key,
                    type: 'style',
                    typeLabel: 'Style',
                    label: item.label || key,
                    desc: item.desc || '',
                    value: Number(item.headline_value || 0),
                    bias: this.factorBias(item.headline_factor || key, Number(item.headline_value || 0)),
                    source: 'style_overlay',
                });
            }
            return rows.sort((a, b) => Math.abs(Number(b.value || 0)) - Math.abs(Number(a.value || 0)));
        },

        exposureRows() {
            return this.exposureRowsAll()
                .filter(row => this.exposureFilter === 'all' || row.type === this.exposureFilter)
                .slice(0, 8);
        },

        exposureSummaryCards() {
            const rows = this.exposureRowsAll();
            const top = this.primaryRiskRow();
            const styleCount = rows.filter(row => row.type === 'style').length;
            const active = this.factorResult.risk_decomposition?.active_risk || {};
            const activeTop = (active.top_active_exposures || [])[0] || {};
            return [
                {
                    key: 'top',
                    label: 'Top exposure',
                    value: top.label || '--',
                    note: top.key ? `${this.signed(top.value, 3)} · ${top.typeLabel}` : 'No factor exposure yet',
                },
                {
                    key: 'active',
                    label: 'Top active tilt',
                    value: activeTop.label || '--',
                    note: activeTop.factor ? `${this.signed(activeTop.active_exposure, 3)} vs benchmark` : 'Run Barra-lite for active tilt',
                },
                {
                    key: 'coverage',
                    label: 'Coverage',
                    value: `${rows.length} factors`,
                    note: `${styleCount} style · ${rows.length - styleCount} sector/theme`,
                },
            ];
        },

        exposureRiskHighlights() {
            const rows = this.exposureRowsAll();
            const top = this.primaryRiskRow();
            const betaRow = rows.find(row => this.isBetaLikeExposure(row)) || top;
            const active = this.factorResult.risk_decomposition?.active_risk || {};
            const activeTop = (active.top_active_exposures || [])[0] || {};
            const topWatch = this.exposureWatchItems()[0];
            return [
                {
                    key: 'primaryRisk',
                    label: 'Primary risk',
                    value: top.label || '--',
                    note: top.key ? `${this.signed(top.value, 3)} / ${top.typeLabel} / ${top.bias}` : 'Run factor analysis to identify the main risk',
                },
                {
                    key: 'sensitiveBeta',
                    label: 'Most sensitive beta',
                    value: betaRow.label || '--',
                    note: betaRow.key ? `${this.signed(betaRow.value, 3)} / ${this.exposureRiskPhrase(betaRow)}` : 'No beta-like exposure yet',
                },
                {
                    key: 'watch',
                    label: 'What to watch',
                    value: activeTop.label || topWatch?.title || '--',
                    note: activeTop.factor
                        ? `${this.signed(activeTop.active_exposure, 3)} vs benchmark`
                        : (topWatch?.text || 'No watch item yet'),
                },
            ];
        },

        primaryRiskRow() {
            const riskRow = this.factorRiskRows()[0];
            if (riskRow && riskRow.factor) {
                return {
                    key: riskRow.factor,
                    label: riskRow.label || riskRow.factor,
                    value: Number(riskRow.variance_share || 0) * 100,
                    typeLabel: 'Risk',
                    bias: riskRow.exposure != null ? `${this.signed(riskRow.exposure, 3)} exposure` : 'top variance contributor',
                };
            }
            return this.exposureRowsAll()[0] || {};
        },

        isBetaLikeExposure(row) {
            if (!row || !row.key) return false;
            const key = String(row.key).toLowerCase();
            const label = String(row.label || '').toLowerCase();
            return row.type !== 'sector' && (
                key.includes('mkt') ||
                key.includes('beta') ||
                key.includes('bab') ||
                key.includes('momentum') ||
                key.includes('mom') ||
                key.includes('growth') ||
                key.includes('value') ||
                label.includes('beta') ||
                label.includes('market') ||
                label.includes('momentum') ||
                label.includes('growth') ||
                label.includes('value')
            );
        },

        exposureRiskPhrase(row) {
            if (!row || !row.key) return 'watch broad factor moves';
            const key = String(row.key).toLowerCase();
            const label = String(row.label || '').toLowerCase();
            if (key.includes('mkt') || label.includes('market')) return 'broad market drawdown matters';
            if (key.includes('bab') || key.includes('beta') || label.includes('beta')) return Number(row.value || 0) >= 0 ? 'defensive beta tilt matters' : 'high beta selloff matters';
            if (key.includes('mom') || label.includes('momentum')) return 'momentum reversal matters';
            if (key.includes('growth') || label.includes('growth')) return 'duration and rates matter';
            if (key.includes('value') || label.includes('value')) return 'value/growth rotation matters';
            if (row.type === 'sector' || row.type === 'theme') return 'sector/theme shock matters';
            return 'factor regime shift matters';
        },

        exposureWatchItems() {
            const rows = this.exposureRowsAll().slice(0, 4);
            const items = [];
            rows.forEach((row, index) => {
                const key = String(row.key || '').toLowerCase();
                const label = row.label || row.key || 'Factor';
                let text;
                if (key.includes('mkt') || String(row.label || '').toLowerCase().includes('market')) {
                    text = `${label}: broad market selloffs are the cleanest risk read-through.`;
                } else if (key.includes('bab') || key.includes('beta') || String(row.label || '').toLowerCase().includes('beta')) {
                    text = `${label}: watch whether the tape rewards high-beta risk or defensive balance sheets.`;
                } else if (key.includes('mom') || String(row.label || '').toLowerCase().includes('momentum')) {
                    text = `${label}: momentum reversal can hurt quickly when leadership rotates.`;
                } else if (key.includes('growth') || key.includes('value') || String(row.label || '').toLowerCase().match(/growth|value/)) {
                    text = `${label}: rates and value/growth rotation are the main macro tells.`;
                } else if (row.type === 'sector' || row.type === 'theme') {
                    text = `${label}: follow sector news, earnings revisions, and valuation compression.`;
                } else if (row.type === 'style') {
                    text = `${label}: watch whether this style factor is being rewarded or unwound.`;
                } else {
                    text = `${label}: monitor factor moves and the top holding drivers.`;
                }
                items.push({ key: `${row.key || 'row'}-${index}`, title: label, text });
            });
            return items.slice(0, 4);
        },

        exposureRowDrivers(row) {
            if (!row || !row.key) return '--';
            const active = this.factorResult.risk_decomposition?.active_risk || {};
            const activeDrivers = active.holding_contributors?.[row.key] || [];
            if (activeDrivers.length) {
                return activeDrivers.slice(0, 2).map(item => `${item.ticker || item.stock_id} ${this.signed(item.contribution, 2)}`).join(' / ');
            }
            const barraLite = this.factorResult.holding_factor_contributors?.barra_lite_factors || {};
            const academic = this.factorResult.holding_factor_contributors?.academic_factors || {};
            const rows = barraLite[row.key] || academic[row.key] || [];
            if (!rows.length) return '--';
            return rows.slice(0, 2).map(item => `${item.ticker || item.stock_id} ${this.signed(item.contribution, 2)}`).join(' / ');
        },

        rollingExposureRows() {
            const rolling = this.factorResult.rolling_exposures || [];
            if (rolling.length < 2) return [];
            const first = rolling[0]?.exposures || {};
            const last = rolling[rolling.length - 1]?.exposures || {};
            const labels = this.factorResult.factor_labels || {};
            const factors = Array.from(new Set([...Object.keys(first), ...Object.keys(last)]));
            return factors.map(factor => {
                const start = Number(first[factor] || 0);
                const current = Number(last[factor] || 0);
                const change = current - start;
                const direction = Math.abs(change) < 0.05 ? 'Stable' : change > 0 ? 'Rising tilt' : 'Falling tilt';
                return {
                    factor,
                    label: (labels[factor] || {}).zh || factor,
                    start,
                    current,
                    change,
                    direction,
                };
            }).sort((a, b) => Math.abs(b.change) - Math.abs(a.change)).slice(0, 6);
        },

        rollingExposureSummary() {
            const rows = this.rollingExposureRows();
            const top = rows[0] || {};
            const rising = rows.filter(row => row.change > 0.05).length;
            const falling = rows.filter(row => row.change < -0.05).length;
            return [
                {
                    key: 'changed',
                    label: 'Largest drift',
                    value: top.label || '--',
                    note: top.factor ? `${this.signed(top.change, 3)} over the window` : 'Not enough history yet',
                },
                {
                    key: 'direction',
                    label: 'Current direction',
                    value: top.direction || '--',
                    note: top.factor ? `${top.label} is moving most` : 'No visible drift',
                },
                {
                    key: 'breadth',
                    label: 'Breadth',
                    value: `${rising} up / ${falling} down`,
                    note: 'Count of meaningful factor moves',
                },
            ];
        },

        styleRows() {
            return this.exposureRowsAll();
        },

        sectorRows() {
            const details = this.factorResult.sector_macro_details || {};
            return Object.values(details).sort((a, b) => Math.abs(Number(b.beta || 0)) - Math.abs(Number(a.beta || 0)));
        },

        contributorGroups() {
            const groups = [];
            const academic = this.factorResult.holding_factor_contributors?.academic_factors || {};
            const proxy = this.factorResult.holding_factor_contributors?.proxy_factors || {};
            const barraLite = this.factorResult.holding_factor_contributors?.barra_lite_factors || {};
            for (const [factor, rows] of Object.entries(barraLite)) {
                if (!rows || !rows.length) continue;
                groups.push({
                    title: `Barra-lite · ${factor}`,
                    subtitle: 'TextCurrent Barra-lite TextHoldings',
                    rows: rows.slice(0, 3),
                });
            }
            for (const [factor, rows] of Object.entries(academic)) {
                if (!rows || !rows.length) continue;
                groups.push({
                    title: `Text · ${factor}`,
                    subtitle: 'TextCurrentTextHoldings',
                    rows: rows.slice(0, 3),
                });
            }
            for (const [factor, rows] of Object.entries(proxy)) {
                if (!rows || !rows.length) continue;
                groups.push({
                    title: `Text · ${factor}`,
                    subtitle: 'Text / TextHoldings',
                    rows: rows.slice(0, 3),
                });
            }
            return groups.slice(0, 4);
        },

        sourceStatusRows() {
            const status = this.factorResult.factor_source_status || {};
            if (status.barra_lite_us_v1) {
                const barra = status.barra_lite_us_v1 || {};
                const basis = barra.exposure_basis === 'external_universe' ? 'Text universe' : 'HoldingsText';
                const count = Number(barra.universe_count || 0);
                const estimator = barra.covariance_estimator || 'ewma_shrinkage';
                const specific = barra.specific_risk_estimator || 'factor_residuals';
                const benchmark = barra.benchmark_model || 'equal_weight_active_holdings';
                return [
                    {
                        key: 'barra_lite_us_v1',
                        label: 'Barra-lite MVP',
                        success: !!barra.success,
                        note: `${basis} · universe ${count} · benchmark ${benchmark} · covariance ${estimator} · specific ${specific} · ${barra.provider || 'price returns and optional characteristics'}`,
                    }
                ];
            }
            const overlayStatus = status.style_overlay_pack || {};
            const overlaySuccess = Object.values(overlayStatus).some(item => item && item.success);
            return [
                { key: 'ff_core_pack', label: 'FF Core Text', success: !!status.ff_core_pack?.success, note: status.ff_core_pack?.provider || 'Text' },
                { key: 'q_factor_pack', label: 'Q-Factor Text', success: !!status.q_factor_pack?.success, note: status.q_factor_pack?.provider || 'Text' },
                { key: 'style_overlay_pack', label: 'Style Overlay Text', success: overlaySuccess, note: 'QMJ / BAB / Liquidity / Mispricing' },
                { key: 'sector_macro_pack', label: 'Text / Text', success: !!status.sector_macro_pack?.success, note: status.sector_macro_pack?.provider || 'Text' },
            ];
        },

        factorBias(factor, value) {
            if (factor === 'Mkt-RF') return value >= 0 ? 'Text Beta' : 'Text Beta';
            if (factor === 'SMB' || factor === 'ME') return value >= 0 ? 'Text' : 'Text';
            if (factor === 'HML') return value >= 0 ? 'Text' : 'Text';
            if (factor === 'RMW' || factor === 'ROE' || factor === 'QMJ') return value >= 0 ? 'Text' : 'Text';
            if (factor === 'CMA' || factor === 'IA') return value >= 0 ? 'Text' : 'Text';
            if (factor === 'EG') return value >= 0 ? 'Text' : 'Text';
            if (factor === 'Mom') return value >= 0 ? 'Text' : 'Text';
            if (factor === 'BAB') return value >= 0 ? 'Text Beta / Text' : 'Text Beta / Text';
            if (factor === 'AGG_LIQ') return value >= 0 ? 'Text' : 'Text';
            return value >= 0 ? 'Text' : 'Text';
        },

        barWidth(value) {
            return Math.min(Math.abs(Number(value || 0)) / 1.2 * 100, 100);
        },

        formatMoney(value) {
            const n = Number(value || 0);
            const sign = n >= 0 ? '' : '-';
            const abs = Math.abs(n);
            return `${sign}HK$${Math.round(abs).toLocaleString('zh-CN')}`;
        },

        formatPct(value, digits = 2) {
            return `${Number(value || 0).toFixed(digits)}%`;
        },

        riskCards() {
            const m = this.factorResult.risk_metrics || {};
            const sev = (key) => {
                const s = m[key + '_severity'] || 'green';
                if (s === 'red') return { borderClass: 'border-l-red-600', textClass: 'text-red-600', label: 'Text' };
                if (s === 'yellow') return { borderClass: 'border-l-amber-500', textClass: 'text-amber-500', label: 'Text' };
                return { borderClass: 'border-l-green-600', textClass: 'text-green-600', label: 'Text' };
            };
            return [
                { key: 'var', label: 'VaR (95%)', display: this.formatPct(m.var_95, 2), note: `${sev('var_95').label}・95%Text`, ...sev('var_95') },
                { key: 'cvar', label: 'CVaR (ES)', display: this.formatPct(m.cvar_95, 2), note: `${sev('cvar_95').label}・Text`, ...sev('cvar_95') },
                { key: 'mdd', label: 'Text', display: this.formatPct(m.max_drawdown, 2), note: `${sev('max_drawdown').label}・Text`, ...sev('max_drawdown') },
                { key: 'vol', label: 'Text', display: this.formatPct(m.volatility, 2), note: `${sev('volatility').label}・TextReturnText×√252`, ...sev('volatility') },
                { key: 'sharpe', label: 'Sharpe', display: Number(m.sharpe || 0).toFixed(2), note: `${sev('sharpe').label}・RiskTextReturn`, ...sev('sharpe') },
            ];
        },

        riskDecompositionCards() {
            const d = this.factorResult.risk_decomposition || {};
            const top = this.factorRiskRows()[0] || {};
            return [
                {
                    key: 'total',
                    label: 'TextRisk',
                    value: this.formatPct(d.total_risk_pct, 2),
                    note: 'Text',
                },
                {
                    key: 'factor',
                    label: 'TextRisk',
                    value: this.formatPct(d.factor_risk_pct, 2),
                    note: `TextRisk ${this.formatPct(Number(d.factor_risk_share || 0) * 100, 1)}`,
                },
                {
                    key: 'specific',
                    label: 'TextRisk',
                    value: this.formatPct(d.specific_risk_pct, 2),
                    note: `TextRisk ${this.formatPct(Number(d.specific_risk_share || 0) * 100, 1)}`,
                },
                {
                    key: 'top',
                    label: 'Text',
                    value: top.label || top.factor || '--',
                    note: top.factor ? `Contribution ${this.formatPct(Number(top.variance_share || 0) * 100, 1)}` : 'No dataTextContribution',
                },
            ];
        },

        riskContributionSummaryCards() {
            const summary = this.factorResult.risk_contribution_summary || {};
            const topFactor = summary.top_factor || this.factorRiskRows()[0] || {};
            const topHolding = (summary.top_holding_contributors || [])[0] || this.specificRiskRows()[0] || {};
            return [
                {
                    key: 'topFactor',
                    label: 'Top factor risk',
                    value: topFactor.label || topFactor.factor || '--',
                    note: topFactor.factor ? `Variance share ${this.formatPct(Number(topFactor.variance_share || 0) * 100, 1)}` : 'No factor risk contribution yet',
                },
                {
                    key: 'topHolding',
                    label: 'Top holding risk',
                    value: topHolding.ticker || topHolding.stock_name || topHolding.stock_id || '--',
                    note: topHolding.stock_id ? `Contribution ${this.formatPct(Number(topHolding.pct_contribution || 0), 1)}` : 'No holding contribution yet',
                },
                {
                    key: 'factorCount',
                    label: 'Risk factors',
                    value: Number(summary.factor_count || this.factorRiskRows().length || 0).toFixed(0),
                    note: 'Factors with non-zero model contribution',
                },
            ];
        },

        factorRiskRows() {
            const direct = this.factorResult.factor_risk_contributions || [];
            const nested = this.factorResult.risk_decomposition?.factor_risk_contributions || [];
            return (direct.length ? direct : nested)
                .filter(row => row && row.factor)
                .slice(0, 8);
        },

        factorRiskAlertRows() {
            return (this.factorResult.factor_risk_alerts || [])
                .filter(row => row && row.title)
                .slice(0, 5);
        },

        factorRiskAlertClass(alert) {
            if (alert?.severity === 'high') return 'border-red-200 bg-red-50 text-red-800';
            if (alert?.severity === 'medium') return 'border-amber-200 bg-amber-50 text-amber-800';
            return 'border-slate-200 bg-slate-50 text-slate-700';
        },

        analysisMetadataCards() {
            const metadata = this.factorResult.analysis_metadata || {};
            return [
                {
                    key: 'model',
                    label: 'Model pack',
                    value: metadata.model_pack || this.factorResult.model_pack || '--',
                    note: `${Number(metadata.lookback_days || this.factorResult.lookback_days || 0).toFixed(0)} lookback days`,
                },
                {
                    key: 'benchmark',
                    label: 'Benchmark',
                    value: metadata.benchmark_model || '--',
                    note: 'Used for active risk attribution',
                },
                {
                    key: 'covariance',
                    label: 'Covariance estimator',
                    value: metadata.covariance_estimator || '--',
                    note: metadata.covariance_quality || 'Quality unavailable',
                },
                {
                    key: 'generated',
                    label: 'Generated at',
                    value: metadata.generated_at || this.factorResult.analysis_date || '--',
                    note: metadata.specific_risk_estimator || 'Specific risk model',
                },
            ];
        },

        factorRiskDeltaRows() {
            const delta = this.factorResult.factor_risk_delta || {};
            return (delta.top_changes || [])
                .filter(row => row && row.factor)
                .slice(0, 8);
        },

        factorRiskDeltaAlerts() {
            return (this.factorResult.factor_risk_delta?.alerts || [])
                .filter(row => row && row.title)
                .slice(0, 4);
        },

        factorLineageCards() {
            const lineage = this.factorResult.factor_input_lineage || {};
            const comparison = this.factorResult.factor_risk_delta?.lineage_comparison || {};
            return [
                {
                    key: 'changeType',
                    label: 'Change type',
                    value: comparison.input_change_type || '--',
                    note: 'Explains whether delta came from portfolio or model inputs',
                },
                {
                    key: 'signature',
                    label: 'Input signature',
                    value: lineage.input_signature || '--',
                    note: `${Number(lineage.holding_count || 0).toFixed(0)} holdings`,
                },
                {
                    key: 'model',
                    label: 'Model inputs',
                    value: lineage.model_pack || '--',
                    note: `${Number(lineage.lookback_days || 0).toFixed(0)} days · ${lineage.benchmark_mode || '--'}`,
                },
                {
                    key: 'universe',
                    label: 'Universe snapshot',
                    value: Number(lineage.factor_universe_count || 0).toFixed(0),
                    note: lineage.factor_universe_updated_at || 'No universe timestamp',
                },
            ];
        },

        factorDataQualitySummaryCards() {
            const summary = this.factorResult.factor_data_quality?.summary || {};
            return [
                {
                    key: 'coverage',
                    label: 'Coverage',
                    value: this.formatPct(Number(summary.avg_coverage_ratio || 0) * 100, 0),
                    note: `${Number(summary.factor_count || 0).toFixed(0)} factors checked`,
                },
                {
                    key: 'high',
                    label: 'High confidence',
                    value: Number(summary.high_confidence_count || 0).toFixed(0),
                    note: 'Coverage at or above 80%',
                },
                {
                    key: 'low',
                    label: 'Low confidence',
                    value: Number(summary.low_confidence_count || 0).toFixed(0),
                    note: 'Needs better source coverage',
                },
            ];
        },

        factorDataQualityRows() {
            const factors = this.factorResult.factor_data_quality?.factors || {};
            return Object.values(factors)
                .filter(row => row && row.factor)
                .sort((a, b) => Number(a.coverage_ratio || 0) - Number(b.coverage_ratio || 0))
                .slice(0, 10);
        },

        activeRiskCards() {
            const active = this.factorResult.risk_decomposition?.active_risk;
            if (!active || !active.benchmark_model) return [];
            return [
                {
                    key: 'tracking',
                    label: 'Tracking error',
                    value: this.formatPct(active.tracking_error_pct, 2),
                    note: active.benchmark_model,
                },
                {
                    key: 'activeFactor',
                    label: 'Active factor risk',
                    value: this.formatPct(active.active_factor_risk_pct, 2),
                    note: `Text TE ${this.formatPct(Number(active.active_factor_risk_share || 0) * 100, 1)}`,
                },
                {
                    key: 'benchmark',
                    label: 'Benchmark holdings',
                    value: Number(active.benchmark_weight_count || 0).toFixed(0),
                    note: 'equal-weight active holdings',
                },
            ];
        },

        activeRiskDrilldownGroups() {
            const active = this.factorResult.risk_decomposition?.active_risk || {};
            const rows = active.top_active_exposures || [];
            return rows
                .filter(row => row && row.factor)
                .map(row => ({
                    ...row,
                    contributors: row.contributors || active.holding_contributors?.[row.factor] || [],
                }))
                .slice(0, 6);
        },

        activeRiskContributorText(group) {
            const contributors = group?.contributors || [];
            if (!contributors.length) return '--';
            return contributors.slice(0, 3).map(item => {
                const name = item.ticker || item.stock_name || item.stock_id;
                return `${name} ${this.signed(item.contribution, 2)}`;
            }).join(' / ');
        },

        specificRiskRows() {
            const rows = this.factorResult.risk_decomposition?.specific_risk_contributions || [];
            return rows
                .filter(row => row && row.stock_id)
                .slice(0, 8);
        },

        specificRiskModelNote() {
            const model = this.factorResult.risk_decomposition?.specific_risk_model;
            if (!model || !model.estimator) return '';
            const count = Number(model.fitted_factor_count || 0);
            return `${model.estimator} · ${count} fitted factors`;
        },

        covarianceQualityCard() {
            const model = this.factorResult.risk_decomposition?.covariance_model;
            if (!model || !model.estimator) return null;
            const quality = model.quality || 'Low';
            const score = Number(model.quality_score || 0);
            const obs = Number(model.n_observations || 0);
            const factors = Number(model.n_factors || 0);
            const shrink = Number(model.shrinkage_intensity || 0);
            return {
                title: `${model.estimator} · ${quality} (${Math.round(score * 100)}%)`,
                note: `${obs} observations · ${factors} factors · shrink ${this.formatPct(shrink * 100, 0)}`,
            };
        },

        stressRowClass(loss) {
            if (loss == null) return 'text-slate-400';
            const v = Math.abs(Number(loss));
            if (v < 5) return 'bg-green-50 text-green-700';
            if (v < 10) return 'bg-amber-50 text-amber-700';
            if (v < 20) return 'bg-red-50 text-red-700';
            return 'bg-red-100 text-red-700 font-bold';
        },

        heatmapLabels() {
            return this.factorResult.correlation_matrix?.labels || [];
        },

        heatmapRows() {
            return this.factorResult.correlation_matrix?.matrix || [];
        },

        heatmapColor(val) {
            const v = Number(val || 0);
            if (v >= 0) {
                const t = Math.min(v, 1);
                const h = 0;
                const s = Math.round(70 * t);
                const l = Math.round(100 - 35 * t);
                return `hsl(${h}, ${s}%, ${l}%)`;
            } else {
                const t = Math.min(Math.abs(v), 1);
                const h = 140;
                const s = Math.round(70 * t);
                const l = Math.round(100 - 35 * t);
                return `hsl(${h}, ${s}%, ${l}%)`;
            }
        },

        radarAriaLabel() {
            const exp = this.factorResult.portfolio_exposure || {};
            const labels = this.factorResult.factor_labels || {};
            const parts = Object.entries(exp).map(([k, v]) => `${(labels[k] || {}).zh || k} ${Number(v).toFixed(2)}`);
            return 'Text: ' + (parts.join(', ') || 'No dataText');
        },

        bubbleAriaLabel() {
            const mr = this.factorResult.marginal_risk || [];
            const parts = mr.slice(0, 5).map(item => `${item.stock_name || item.stock_id} RiskContribution${Number(item.pct_contribution || 0).toFixed(1)}%`);
            return 'TextRiskContributionText: ' + (parts.join(', ') || 'No dataText');
        },

        initCharts() {
            if (typeof Chart === 'undefined') return;
            this.$nextTick(() => {
                this._initRadarChart();
                this._initBubbleChart();
                this._initRollingChart();
            });
        },

        _destroyChart(id) {
            if (this._charts[id]) {
                this._charts[id].destroy();
                delete this._charts[id];
            }
        },

        _initRadarChart() {
            this._destroyChart('radar');
            const canvas = document.getElementById('radarChart');
            if (!canvas) return;
            const exp = this.factorResult.portfolio_exposure || {};
            const labels = this.factorResult.factor_labels || {};
            const keys = Object.keys(exp);
            if (keys.length < 3) return;

            const datasets = [{
                label: this.factorResult.primary_model?.label || 'Text',
                data: keys.map(k => Number(exp[k] || 0)),
                backgroundColor: 'rgba(14, 165, 233, 0.2)',
                borderColor: 'rgba(14, 165, 233, 0.8)',
                pointBackgroundColor: 'rgba(14, 165, 233, 1)',
                borderWidth: 2,
            }];

            const qExp = this.factorResult.model_comparison?.find(m => m.key === 'q_factor')?.exposures;
            if (qExp && Object.keys(qExp).length >= 3) {
                const qKeys = Object.keys(qExp);
                datasets.push({
                    label: 'Q-Factor',
                    data: qKeys.map(k => Number(qExp[k] || 0)),
                    backgroundColor: 'rgba(100, 116, 139, 0.15)',
                    borderColor: 'rgba(100, 116, 139, 0.6)',
                    pointBackgroundColor: 'rgba(100, 116, 139, 1)',
                    borderWidth: 1.5,
                });
            }

            this._charts.radar = new Chart(canvas, {
                type: 'radar',
                data: {
                    labels: keys.map(k => (labels[k] || {}).zh || k),
                    datasets,
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'top' },
                        tooltip: {
                            callbacks: {
                                label: (ctx) => {
                                    const k = keys[ctx.dataIndex];
                                    const v = ctx.raw.toFixed(3);
                                    const sig = Math.abs(ctx.raw) > 0.1 ? ' *' : '';
                                    return `${ctx.dataset.label}: ${v}${sig}`;
                                }
                            }
                        }
                    },
                    scales: {
                        r: {
                            beginAtZero: false,
                            ticks: { font: { size: 10 } },
                            pointLabels: { font: { size: 11 } },
                        }
                    }
                }
            });
        },

        _initBubbleChart() {
            this._destroyChart('bubble');
            const canvas = document.getElementById('bubbleChart');
            if (!canvas) return;
            const mr = this.factorResult.marginal_risk || [];
            if (mr.length < 2) return;

            const bucketColors = {
                semis_hardware: 'rgba(14, 165, 233, 0.7)',
                software_ai: 'rgba(139, 92, 246, 0.7)',
                industrials: 'rgba(245, 158, 11, 0.7)',
                materials_resources: 'rgba(16, 185, 129, 0.7)',
                transport_logistics: 'rgba(100, 116, 139, 0.7)',
                rates_sensitive_growth: 'rgba(244, 63, 94, 0.7)',
                defensive_other: 'rgba(156, 163, 175, 0.7)',
            };
            const bucketLabelsZh = {
                semis_hardware: 'Text/Text',
                software_ai: 'Text/AI',
                industrials: 'Text',
                materials_resources: 'Text/Text',
                transport_logistics: 'Text/Text',
                rates_sensitive_growth: 'Text',
                defensive_other: 'Text/Text',
            };

            const buckets = {};
            mr.forEach(item => {
                const b = item.theme_bucket || 'defensive_other';
                if (!buckets[b]) buckets[b] = [];
                buckets[b].push(item);
            });

            const datasets = Object.entries(buckets).map(([bucket, items]) => ({
                label: bucketLabelsZh[bucket] || bucket,
                backgroundColor: bucketColors[bucket] || 'rgba(156,163,175,0.7)',
                data: items.map(item => ({
                    x: Number(item.weight || 0) * 100,
                    y: Number(item.pct_contribution || 0),
                    r: Math.max(8, Math.min(48, Number(item.pct_contribution || 0) * 2)),
                    _name: item.stock_name || item.stock_id,
                    _bucket: bucketLabelsZh[bucket] || bucket,
                })),
            }));

            this._charts.bubble = new Chart(canvas, {
                type: 'bubble',
                data: { datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'top' },
                        tooltip: {
                            callbacks: {
                                label: (ctx) => {
                                    const d = ctx.raw;
                                    return `${d._name}: Text${d.x.toFixed(1)}%, RiskContribution${d.y.toFixed(1)}%, ${d._bucket}`;
                                }
                            }
                        }
                    },
                    scales: {
                        x: { title: { display: true, text: 'Text (%)' } },
                        y: { title: { display: true, text: 'TextRiskContribution (%)' } },
                    }
                }
            });
        },

        _initRollingChart() {
            this._destroyChart('rolling');
            const canvas = document.getElementById('rollingChart');
            if (!canvas) return;
            const rolling = this.factorResult.rolling_exposures || [];
            if (rolling.length === 0) return;

            const palette = [
                'rgba(14, 165, 233, 1)', 'rgba(245, 158, 11, 1)', 'rgba(16, 185, 129, 1)',
                'rgba(244, 63, 94, 1)', 'rgba(139, 92, 246, 1)', 'rgba(100, 116, 139, 1)',
            ];
            const allFactors = new Set();
            rolling.forEach(pt => Object.keys(pt.exposures || {}).forEach(f => allFactors.add(f)));
            const factors = [...allFactors];
            const dates = rolling.map(pt => pt.date?.slice(0, 7) || '');

            const datasets = factors.map((f, i) => ({
                label: f,
                data: rolling.map(pt => {
                    const v = pt.exposures?.[f];
                    return v != null ? Number(v) : null;
                }),
                borderColor: palette[i % palette.length],
                backgroundColor: 'transparent',
                borderWidth: 2,
                pointRadius: 1.5,
                spanGaps: false,
                tension: 0.2,
            }));

            this._charts.rolling = new Chart(canvas, {
                type: 'line',
                data: { labels: dates, datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'top' },
                        tooltip: { mode: 'index', intersect: false },
                    },
                    scales: {
                        x: { ticks: { maxTicksLimit: 12 } },
                        y: { title: { display: true, text: 'Beta Text' } },
                    }
                }
            });
        },

        signed(value, digits = 2) {
            const n = Number(value || 0);
            return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}`;
        }
    };
}
