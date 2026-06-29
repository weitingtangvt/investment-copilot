(function () {
  function toFiniteNumber(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? number : (fallback ?? null);
  }

  function parseRowDate(row) {
    const raw = row.date || row.time || row.timestamp;
    if (!raw) {
      return null;
    }
    const date = raw instanceof Date ? raw : new Date(raw);
    if (Number.isNaN(date.getTime())) {
      return null;
    }
    return date;
  }

  function mapPriceChartRowsToCandleViewData(rows) {
    if (!Array.isArray(rows)) {
      return [];
    }
    return rows
      .map((row) => {
        const date = parseRowDate(row || {});
        const open = toFiniteNumber(row.open);
        const high = toFiniteNumber(row.high);
        const low = toFiniteNumber(row.low);
        const close = toFiniteNumber(row.close);
        if (!date || open === null || high === null || low === null || close === null) {
          return null;
        }
        return {
          time: Math.floor(date.getTime() / 1000),
          open: toFiniteNumber(row.open),
          high: toFiniteNumber(row.high),
          low: toFiniteNumber(row.low),
          close: toFiniteNumber(row.close),
          volume: toFiniteNumber(row.volume, 0),
        };
      })
      .filter(Boolean)
      .sort((left, right) => left.time - right.time);
  }

  function extractRows(payload) {
    if (Array.isArray(payload)) {
      return payload;
    }
    if (Array.isArray(payload?.candles)) {
      return payload.candles;
    }
    if (Array.isArray(payload?.series?.candles)) {
      return payload.series.candles;
    }
    if (Array.isArray(payload?.data)) {
      return payload.data;
    }
    if (Array.isArray(payload?.items)) {
      return payload.items;
    }
    return [];
  }

  function buildPriceChartUrl(root, ticker, rangeName) {
    const endpoint = root.dataset.priceChartEndpoint || "/api/price-chart";
    const url = new URL(endpoint, window.location.origin);
    url.searchParams.set("ticker", ticker);
    url.searchParams.set("range", rangeName);
    return url.toString();
  }

  async function loadChart(root) {
    const mount = document.getElementById("chart-lab-candleview");
    const status = document.getElementById("chart-lab-status");
    const tickerInput = document.getElementById("chart-lab-ticker");
    const rangeInput = document.getElementById("chart-lab-range");
    const ticker = (tickerInput?.value || root.dataset.defaultTicker || "NVDA").trim().toUpperCase();
    const rangeName = rangeInput?.value || root.dataset.defaultRange || "1y";
    if (!mount || !ticker) {
      return;
    }
    status.textContent = `Load ${ticker} ${rangeName}...`;
    const response = await fetch(buildPriceChartUrl(root, ticker, rangeName));
    if (!response.ok) {
      throw new Error(`price-chart ${response.status}`);
    }
    const payload = await response.json();
    const data = mapPriceChartRowsToCandleViewData(extractRows(payload));
    if (!data.length) {
      throw new Error("Text OHLCV Text");
    }
    window.CandleViewLab.render(mount, {
      title: `${ticker} · ${rangeName.toUpperCase()}`,
      data,
      leftpanel: true,
      toppanel: true,
      mainChartIndicators: ["ma"],
      subChartIndicators: ["volume", "rsi"],
    });
    status.textContent = `TextLoad ${data.length} Text K Text · ${ticker}`;
  }

  function initChartLab() {
    const root = document.getElementById("chart-lab-root");
    if (!root) {
      return;
    }
    const button = document.getElementById("chart-lab-load");
    button?.addEventListener("click", () => {
      loadChart(root).catch((error) => {
        const status = document.getElementById("chart-lab-status");
        status.textContent = `LoadFailed: ${error.message}`;
      });
    });
    loadChart(root).catch((error) => {
      const status = document.getElementById("chart-lab-status");
      status.textContent = `LoadFailed: ${error.message}`;
    });
  }

  window.ChartLab = {
    extractRows,
    mapPriceChartRowsToCandleViewData,
    loadChart,
  };

  document.addEventListener("DOMContentLoaded", initChartLab);
})();
