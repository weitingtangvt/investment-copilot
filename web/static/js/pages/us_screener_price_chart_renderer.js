(function () {
  function helpers() {
    return window.usScreenerPriceChartHelpers || {};
  }

  function chartHostReady(host) {
    const helper = helpers();
    if (typeof helper.chartHostReady === "function") return helper.chartHostReady(host);
    if (!host) return false;
    if (host.isConnected === false) return false;
    const rect = typeof host.getBoundingClientRect === "function"
      ? host.getBoundingClientRect()
      : {};
    const width = Number(rect.width || host.clientWidth || 0);
    const height = Number(rect.height || host.clientHeight || 0);
    return width >= 24 && height >= 24;
  }

  function renderKlineChart(options) {
    const helper = helpers();
    if (typeof helper.renderKlineChart !== "function") {
      if (typeof options?.onError === "function") options.onError("KLineChart TextLoad");
      return null;
    }
    return helper.renderKlineChart(options);
  }

  function destroyKlineChart(chart) {
    const helper = helpers();
    if (typeof helper.destroyKlineChart === "function") {
      helper.destroyKlineChart(chart);
      return;
    }
    if (typeof chart?.destroy === "function") chart.destroy();
  }

  window.usScreenerPriceChartRenderer = {
    chartHostReady,
    renderKlineChart,
    destroyKlineChart,
  };
})();
