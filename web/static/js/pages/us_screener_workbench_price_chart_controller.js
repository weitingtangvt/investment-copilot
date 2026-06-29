(function () {
  const ERROR_MESSAGES = {
    layout_not_ready: "Text: Text, TextRefreshText",
    data_empty: "No dataText",
    library_missing: "KLineChart LoadFailed, Text",
    render_failed: "TextFailed, TextRefreshText",
    network_failed: "Text: Market DataTextFailed",
    request_aborted: "",
  };

  function errorText(code, detail = "") {
    const base = ERROR_MESSAGES[code] || ERROR_MESSAGES.render_failed;
    if (!detail) return base;
    return `${base}: ${detail}`;
  }

  function classifyError(error, fallbackCode = "render_failed") {
    const message = error?.message || String(error || "");
    if (error?.name === "AbortError") return "request_aborted";
    if (/Text|Text|layout|size/i.test(message)) return "layout_not_ready";
    if (/KLineChart|kline/i.test(message)) return "library_missing";
    if (/fetch|network|Failed to fetch|TextFailed/i.test(message)) return "network_failed";
    return fallbackCode;
  }

  function createAbortController() {
    if (typeof AbortController === "undefined") return null;
    return new AbortController();
  }

  function createUsScreenerWorkbenchPriceChartController() {
    return {
      setWorkbenchPriceChartError(code, detail = "") {
        this.workbenchPriceChartErrorCode = code || "";
        this.workbenchPriceChartError = code ? errorText(code, detail) : "";
      },

      clearWorkbenchPriceChartError() {
        this.workbenchPriceChartErrorCode = "";
        this.workbenchPriceChartError = "";
      },

      abortWorkbenchPriceChartRequest() {
        if (this.workbenchPriceChartAbortController) {
          try {
            this.workbenchPriceChartAbortController.abort();
          } catch (error) {
            // Abort is best-effort; stale responses are still guarded by request tokens.
          }
          this.workbenchPriceChartAbortController = null;
        }
      },

      workbenchPriceChartHasData() {
        return Array.isArray(this.workbenchPriceChartData?.series?.candles) && this.workbenchPriceChartData.series.candles.length > 0;
      },

      workbenchPriceChartNeedsHistoryHint() {
        return !!this.workbenchPriceChartData && this.workbenchPriceChartData?.meta?.has_enough_history_for_ma200 === false;
      },

      workbenchPriceChartMetaLine() {
        if (!this.workbenchPriceChartData) return "--";
        const parts = [
          this.workbenchPriceChartRange ? `Text ${String(this.workbenchPriceChartRange).toUpperCase()}` : "",
          this.workbenchPriceChartData.as_of_date ? `Text ${this.workbenchPriceChartData.as_of_date}` : "",
          this.workbenchPriceChartData?.meta?.provider ? `Text: ${String(this.workbenchPriceChartData.meta.provider).toUpperCase()}` : "",
        ].filter(Boolean);
        return parts.join(" · ") || "--";
      },

      destroyWorkbenchPriceChart(options = {}) {
        if (options.cancelRestore !== false) {
          this.cancelWorkbenchPriceChartRestore();
        }
        this.cancelWorkbenchPriceChartRender();
        this.cancelWorkbenchPriceChartResizeObserver();
        if (this.workbenchPriceChartChart) {
          this.destroyKlineChart(this.workbenchPriceChartChart);
          this.workbenchPriceChartChart = null;
        }
        this.workbenchPriceChartHoverSnapshot = null;
        this.resetWorkbenchPriceChartMeasure({ keepMode: true });
      },

      cancelWorkbenchPriceChartRender() {
        if (this.workbenchPriceChartRenderTimer) {
          window.clearTimeout(this.workbenchPriceChartRenderTimer);
          this.workbenchPriceChartRenderTimer = null;
        }
      },

      cancelWorkbenchPriceChartRestore() {
        if (this.workbenchPriceChartRestoreTimer) {
          window.clearTimeout(this.workbenchPriceChartRestoreTimer);
          this.workbenchPriceChartRestoreTimer = null;
        }
      },

      cancelWorkbenchPriceChartResizeObserver() {
        if (this.workbenchPriceChartResizeObserver) {
          try {
            this.workbenchPriceChartResizeObserver.disconnect();
          } catch (error) {
            // Ignore observer cleanup failures while switching candidates.
          }
          this.workbenchPriceChartResizeObserver = null;
        }
      },

      isWorkbenchPriceChartHostReady() {
        const host = document.getElementById("usScreenerWorkbenchPriceChartHost");
        if (!host || host.isConnected === false) return false;
        if (this.priceChartRendererApi && typeof this.priceChartRendererApi.chartHostReady === "function") {
          return this.priceChartRendererApi.chartHostReady(host);
        }
        const rect = typeof host.getBoundingClientRect === "function" ? host.getBoundingClientRect() : {};
        const width = Number(rect.width || host.clientWidth || 0);
        const height = Number(rect.height || host.clientHeight || 0);
        return width >= 24 && height >= 24;
      },

      isCurrentWorkbenchPriceChartTarget(requestToken, stockKey) {
        const token = requestToken || this.workbenchPriceChartRequestToken;
        const key = stockKey || this.itemKey(this.workbenchPriceChartStock);
        return (
          this.workbenchPriceChartRequestToken === token
          && this.itemKey(this.workbenchPriceChartStock) === key
          && this.workbenchPriceChartHasData()
        );
      },

      observeWorkbenchPriceChartHost(requestToken, stockKey) {
        const host = document.getElementById("usScreenerWorkbenchPriceChartHost");
        if (!host || !this.isCurrentWorkbenchPriceChartTarget(requestToken, stockKey)) return false;
        this.cancelWorkbenchPriceChartResizeObserver();
        if (typeof ResizeObserver !== "function") {
          this.workbenchPriceChartRenderTimer = window.setTimeout(() => this.scheduleWorkbenchPriceChartPreviewRender({
            requestToken,
            stockKey,
          }), 120);
          return true;
        }
        this.workbenchPriceChartResizeObserver = new ResizeObserver(() => {
          if (!this.isCurrentWorkbenchPriceChartTarget(requestToken, stockKey)) {
            this.cancelWorkbenchPriceChartResizeObserver();
            return;
          }
          if (!this.isWorkbenchPriceChartHostReady()) return;
          this.cancelWorkbenchPriceChartResizeObserver();
          window.requestAnimationFrame(() => {
            if (!this.isCurrentWorkbenchPriceChartTarget(requestToken, stockKey)) return;
            this.renderWorkbenchPriceChartPreview({ requestToken, stockKey });
          });
        });
        this.workbenchPriceChartResizeObserver.observe(host);
        return true;
      },

      scheduleWorkbenchPriceChartPreviewRender(options = {}) {
        if (!this.workbenchPriceChartHasData()) return;
        const requestToken = options.requestToken || this.workbenchPriceChartRequestToken;
        const stockKey = options.stockKey || this.itemKey(this.workbenchPriceChartStock);
        this.cancelWorkbenchPriceChartRender();
        this.$nextTick(() => {
          window.requestAnimationFrame(() => {
            if (!this.isCurrentWorkbenchPriceChartTarget(requestToken, stockKey)) return;
            if (!this.isWorkbenchPriceChartHostReady()) {
              this.setWorkbenchPriceChartError("layout_not_ready");
              this.observeWorkbenchPriceChartHost(requestToken, stockKey);
              return;
            }
            this.clearWorkbenchPriceChartError();
            this.renderWorkbenchPriceChartPreview({ requestToken, stockKey });
          });
        });
      },

      async loadWorkbenchPriceChartPreview(item = null) {
        this.cancelWorkbenchPriceChartRestore();
        this.abortWorkbenchPriceChartRequest();
        const stock = item || this.selectedItem;
        if (!stock) {
          this.workbenchPriceChartStock = null;
          this.workbenchPriceChartData = null;
          this.workbenchPriceChartHoverSnapshot = null;
          this.clearWorkbenchPriceChartError();
          this.workbenchPriceChartLoading = false;
          this.workbenchPriceChartRequestToken = "";
          this.workbenchPriceChartRenderAttempts = 0;
          this.destroyWorkbenchPriceChart();
          this.resetWorkbenchPriceChartMeasure({ keepMode: false });
          return;
        }
        const normalized = {
          stock_id: stock.stock_id || stock.ticker || "",
          stock_name: stock.stock_name || stock.ticker || "",
          ticker: stock.ticker || stock.stock_id || "",
          strategy: stock.strategy || this.activeStrategy,
          latest_close: stock.latest_close,
          max_daily_gain_5d_pct: stock.max_daily_gain_5d_pct,
          gain_30d_pct: stock.gain_30d_pct,
          rebound_from_new_low_pct: stock.rebound_from_new_low_pct,
          distance_above_200ma_pct: stock.distance_above_200ma_pct,
          avg_volume_5d_vs_3m: stock.avg_volume_5d_vs_3m,
        };
        const cacheKey = `${normalized.stock_id || normalized.ticker || "UNKNOWN"}|${this.workbenchPriceChartRange}|${this.priceChartCacheVersion}`;
        const requestToken = `${Date.now()}-${cacheKey}`;
        this.workbenchPriceChartStock = normalized;
        this.clearWorkbenchPriceChartError();
        this.workbenchPriceChartData = null;
        this.workbenchPriceChartHoverSnapshot = null;
        this.workbenchPriceChartRequestToken = requestToken;
        this.workbenchPriceChartRenderAttempts = 0;
        this.resetWorkbenchPriceChartMeasure({ keepMode: true });
        this.destroyWorkbenchPriceChart();

        if (this.priceChartCache[cacheKey]) {
          if (this.workbenchPriceChartRequestToken !== requestToken) return;
          this.workbenchPriceChartLoading = false;
          this.workbenchPriceChartData = this.priceChartCache[cacheKey];
          this.scheduleWorkbenchPriceChartPreviewRender({
            requestToken,
            stockKey: this.itemKey(normalized),
          });
          return;
        }

        this.workbenchPriceChartLoading = true;
        const controller = createAbortController();
        this.workbenchPriceChartAbortController = controller;
        try {
          const params = new URLSearchParams({
            stock_id: normalized.stock_id || "",
            stock_name: normalized.stock_name || "",
            ticker: normalized.ticker || "",
            range: this.workbenchPriceChartRange,
          });
          const fetchOptions = controller ? { signal: controller.signal } : {};
          const response = await fetch(`/api/price-chart?${params.toString()}`, fetchOptions);
          const data = await response.json();
          if (this.workbenchPriceChartRequestToken !== requestToken) {
            return;
          }
          if (this.workbenchPriceChartAbortController === controller) {
            this.workbenchPriceChartAbortController = null;
          }
          if (!response.ok || data.error) {
            throw new Error(data.error || `TextFailed (${response.status})`);
          }
          this.workbenchPriceChartData = data;
          if (data?.success) {
            this.priceChartCache[cacheKey] = data;
          }
          this.workbenchPriceChartLoading = false;
          this.scheduleWorkbenchPriceChartPreviewRender({
            requestToken,
            stockKey: this.itemKey(normalized),
          });
        } catch (error) {
          if (this.workbenchPriceChartRequestToken !== requestToken) {
            return;
          }
          if (this.workbenchPriceChartAbortController === controller) {
            this.workbenchPriceChartAbortController = null;
          }
          const code = classifyError(error, "network_failed");
          if (code === "request_aborted") return;
          console.error(error);
          this.setWorkbenchPriceChartError(code, error.message || String(error));
          this.workbenchPriceChartLoading = false;
        }
      },

      renderWorkbenchPriceChartPreview(options = {}) {
        const requestToken = options.requestToken || this.workbenchPriceChartRequestToken;
        const stockKey = options.stockKey || this.itemKey(this.workbenchPriceChartStock);
        if (!this.isCurrentWorkbenchPriceChartTarget(requestToken, stockKey)) return;
        if (!this.isWorkbenchPriceChartHostReady()) {
          this.scheduleWorkbenchPriceChartPreviewRender({ requestToken, stockKey });
          return;
        }
        const previousChart = this.workbenchPriceChartChart;
        this.workbenchPriceChartHoverSnapshot = null;
        this.resetWorkbenchPriceChartMeasure({ keepMode: true });
        const chart = this.renderPriceChartIntoHost("usScreenerWorkbenchPriceChartHost", this.workbenchPriceChartData, {
          showVolumePane: true,
          compactMode: true,
          enableMeasureTool: this.workbenchPriceChartMeasureMode,
          measureContext: "workbench",
          tradeExecutions: this.tradeExecutionsForPriceChart(this.workbenchPriceChartStock || this.selectedItem),
          onCrosshairChange: (snapshot) => {
            this.workbenchPriceChartHoverSnapshot = snapshot;
          },
          onError: (message) => {
            this.setWorkbenchPriceChartError(classifyError(new Error(message), "render_failed"), message);
          },
        });
        if (chart) {
          if (previousChart && previousChart !== chart) {
            this.destroyKlineChart(previousChart);
          }
          this.workbenchPriceChartChart = chart;
          this.workbenchPriceChartRenderAttempts = 0;
          this.clearWorkbenchPriceChartError();
          window.requestAnimationFrame(() => {
            if (!this.isCurrentWorkbenchPriceChartTarget(requestToken, stockKey) || this.workbenchPriceChartChart !== chart) return;
            if (!this.safePriceChartResize(this.workbenchPriceChartChart)) {
              this.setWorkbenchPriceChartError("render_failed", "TextFailed");
            }
          });
          return;
        }
        this.workbenchPriceChartChart = previousChart;
        if (!this.isCurrentWorkbenchPriceChartTarget(requestToken, stockKey)) return;
        this.workbenchPriceChartRenderAttempts += 1;
        if (this.workbenchPriceChartRenderAttempts <= 3) {
          this.clearWorkbenchPriceChartError();
          this.observeWorkbenchPriceChartHost(requestToken, stockKey);
        }
      },

      restoreWorkbenchPriceChartPreview() {
        if (!this.workbenchPriceChartHasData()) return;
        this.cancelWorkbenchPriceChartRestore();
        const restoreToken = this.workbenchPriceChartRequestToken;
        const restoreStockKey = this.itemKey(this.workbenchPriceChartStock);
        const isCurrentRestoreTarget = () => this.isCurrentWorkbenchPriceChartTarget(restoreToken, restoreStockKey);
        this.workbenchPriceChartRenderAttempts = 0;
        this.destroyWorkbenchPriceChart({ cancelRestore: false });
        this.$nextTick(() => {
          window.requestAnimationFrame(() => {
            if (!isCurrentRestoreTarget()) return;
            this.renderWorkbenchPriceChartPreview({ requestToken: restoreToken, stockKey: restoreStockKey });
            window.requestAnimationFrame(() => {
              if (!isCurrentRestoreTarget()) return;
              if (!this.safePriceChartResize(this.workbenchPriceChartChart)) {
                this.setWorkbenchPriceChartError("render_failed", "TextFailed");
              }
            });
            this.workbenchPriceChartRestoreTimer = window.setTimeout(() => {
              this.workbenchPriceChartRestoreTimer = null;
              if (!isCurrentRestoreTarget() || this.workbenchPriceChartLoading) return;
              const host = document.getElementById("usScreenerWorkbenchPriceChartHost");
              const canvasCount = host ? host.querySelectorAll("canvas").length : 0;
              if (!this.workbenchPriceChartChart || canvasCount === 0) {
                this.destroyWorkbenchPriceChart({ cancelRestore: false });
                this.renderWorkbenchPriceChartPreview({ requestToken: restoreToken, stockKey: restoreStockKey });
              } else if (!this.safePriceChartResize(this.workbenchPriceChartChart)) {
                this.setWorkbenchPriceChartError("render_failed", "TextFailed");
              }
            }, 350);
          });
        });
      },
    };
  }

  window.createUsScreenerWorkbenchPriceChartController = createUsScreenerWorkbenchPriceChartController;
})();
