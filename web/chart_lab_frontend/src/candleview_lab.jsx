import React from "react";
import { createRoot } from "react-dom/client";
import { CandleView } from "candleview";

const mountedRoots = new WeakMap();
const mountedCandleViews = new WeakMap();

function findRealDataRange(candleView) {
  const seriesData = candleView?.currentSeries?.series?.data;
  const rows = typeof seriesData === "function" ? seriesData.call(candleView.currentSeries.series) : (seriesData || []);
  if (!Array.isArray(rows)) {
    return null;
  }
  let first = -1;
  let last = -1;
  rows.forEach((row, index) => {
    if (
      row &&
      !row.isVirtual &&
      Number.isFinite(Number(row.open)) &&
      Number.isFinite(Number(row.high)) &&
      Number.isFinite(Number(row.low)) &&
      Number.isFinite(Number(row.close))
    ) {
      if (first === -1) first = index;
      last = index;
    }
  });
  return first === -1 || last === -1 ? null : { first, last };
}

function rowsForCandleView(candleView) {
  const seriesData = candleView?.currentSeries?.series?.data;
  const rows = typeof seriesData === "function" ? seriesData.call(candleView.currentSeries.series) : (seriesData || []);
  return Array.isArray(rows) ? rows : [];
}

function formatAxisDate(value) {
  if (!value) return "";
  const date = typeof value === "string"
    ? new Date(`${value.slice(0, 10)}T00:00:00Z`)
    : new Date(Number(value) * 1000);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getUTCMonth() + 1}/${date.getUTCDate()}`;
}

function mainPaneBounds(mount) {
  const mountRect = mount?.getBoundingClientRect?.();
  if (!mountRect) return null;
  const canvases = Array.from(mount.querySelectorAll("canvas"))
    .map((canvas) => ({ canvas, rect: canvas.getBoundingClientRect() }))
    .filter((item) => item.rect.width > 100 && item.rect.height > 80);
  if (!canvases.length) {
    return { left: mountRect.left, top: mountRect.top, width: mountRect.width, height: mountRect.height };
  }
  const top = Math.min(...canvases.map((item) => item.rect.top));
  const topPaneCanvases = canvases.filter((item) => Math.abs(item.rect.top - top) < 2);
  topPaneCanvases.sort((a, b) => {
    const heightDelta = a.rect.height - b.rect.height;
    if (Math.abs(heightDelta) > 2) return heightDelta;
    return (b.rect.width * b.rect.height) - (a.rect.width * a.rect.height);
  });
  const rect = topPaneCanvases[0]?.rect || canvases[0].rect;
  return { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
}

function updateMainPaneTimeAxis(mount) {
  const candleViewRef = mountedCandleViews.get(mount);
  const candleView = candleViewRef?.current;
  const rows = rowsForCandleView(candleView);
  const realRows = rows
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => (
      row &&
      !row.isVirtual &&
      Number.isFinite(Number(row.open)) &&
      Number.isFinite(Number(row.high)) &&
      Number.isFinite(Number(row.low)) &&
      Number.isFinite(Number(row.close))
    ));
  const bounds = mainPaneBounds(mount);
  if (!mount || !bounds || realRows.length < 2) return false;

  let axis = mount.querySelector(".ia-candleview-main-time-axis");
  if (!axis) {
    axis = document.createElement("div");
    axis.className = "ia-candleview-main-time-axis";
    mount.appendChild(axis);
  }

  const mountRect = mount.getBoundingClientRect();
  const timeScale = candleView?.chart?.timeScale?.();
  const tickCount = Math.min(6, Math.max(3, Math.floor(bounds.width / 180)));
  const ticks = [];
  for (let i = 0; i < tickCount; i += 1) {
    const ratio = tickCount === 1 ? 0 : i / (tickCount - 1);
    const rowIndex = Math.min(realRows.length - 1, Math.max(0, Math.round(ratio * (realRows.length - 1))));
    const real = realRows[rowIndex];
    let x = bounds.width * ratio;
    if (timeScale && typeof timeScale.logicalToCoordinate === "function") {
      const coordinate = Number(timeScale.logicalToCoordinate(real.index));
      if (Number.isFinite(coordinate)) x = coordinate;
    }
    ticks.push({
      left: Math.min(Math.max(x, 12), Math.max(12, bounds.width - 38)),
      label: formatAxisDate(real.row.time),
    });
  }

  axis.style.cssText = [
    "position:absolute",
    `left:${bounds.left - mountRect.left}px`,
    `top:${bounds.top - mountRect.top + bounds.height + 1}px`,
    `width:${bounds.width}px`,
    "height:24px",
    "z-index:40",
    "pointer-events:none",
    "border-top:1px solid rgba(226,232,240,0.95)",
    "background:rgba(255,255,255,0.92)",
    "font-family:Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    "font-size:11px",
    "font-weight:500",
    "color:#64748b",
  ].join(";");
  axis.innerHTML = ticks
    .map((tick) => `<span style="position:absolute;left:${tick.left}px;top:5px;transform:translateX(-50%);white-space:nowrap;">${tick.label}</span>`)
    .join("");
  return true;
}

function focusDataRange(mount, options = {}) {
  const candleViewRef = mountedCandleViews.get(mount);
  const candleView = candleViewRef?.current;
  const chart = candleView?.chart;
  const range = findRealDataRange(candleView);
  if (!chart || !range) {
    return false;
  }
  const leftPadding = Number.isFinite(Number(options.leftPadding)) ? Number(options.leftPadding) : 3;
  const rightPadding = Number.isFinite(Number(options.rightPadding)) ? Number(options.rightPadding) : 3;
  chart.timeScale().setVisibleLogicalRange({
    from: Math.max(0, range.first - leftPadding),
    to: range.last + rightPadding,
  });
  return true;
}

function measurePointFromMouse(mount, eventLike = {}, options = {}) {
  const candleViewRef = mountedCandleViews.get(mount);
  const candleView = candleViewRef?.current;
  const chart = candleView?.chart;
  const series = candleView?.currentSeries?.series;
  const rows = rowsForCandleView(candleView);
  const realRows = rows
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => (
      row &&
      !row.isVirtual &&
      Number.isFinite(Number(row.open)) &&
      Number.isFinite(Number(row.high)) &&
      Number.isFinite(Number(row.low)) &&
      Number.isFinite(Number(row.close))
    ));
  const bounds = mainPaneBounds(mount);
  if (!bounds || !realRows.length) return null;

  const relativeX = Math.min(Math.max(Number(eventLike.clientX || 0) - bounds.left, 0), bounds.width);
  const fallbackRaw = Math.round((relativeX / Math.max(bounds.width, 1)) * (realRows.length - 1));
  const fallbackIndex = realRows[Math.min(Math.max(fallbackRaw, 0), realRows.length - 1)].index;
  let index = fallbackIndex;
  const timeScale = chart?.timeScale?.();
  if (timeScale && typeof timeScale.coordinateToLogical === "function") {
    const logical = Number(timeScale.coordinateToLogical(relativeX));
    if (Number.isFinite(logical)) {
      const nearest = realRows.reduce((best, current) => (
        Math.abs(current.index - logical) < Math.abs(best.index - logical) ? current : best
      ), realRows[0]);
      index = nearest.index;
    }
  }

  const candle = rows[index];
  if (!candle) return null;
  const close = Number(candle.close);
  if (!Number.isFinite(close)) return null;
  let snappedX = relativeX;
  let snappedY = Math.min(Math.max(Number(eventLike.clientY || 0) - bounds.top, 0), bounds.height);
  if (timeScale && typeof timeScale.logicalToCoordinate === "function") {
    const x = Number(timeScale.logicalToCoordinate(index));
    if (Number.isFinite(x)) snappedX = x;
  }
  if (series && typeof series.priceToCoordinate === "function") {
    const y = Number(series.priceToCoordinate(close));
    if (Number.isFinite(y)) snappedY = y;
  }

  const date = typeof candle.time === "string"
    ? candle.time.slice(0, 10)
    : new Date(Number(candle.time) * 1000).toISOString().slice(0, 10);
  const mountRect = mount.getBoundingClientRect();
  return {
    index,
    date,
    close,
    open: Number(candle.open),
    high: Number(candle.high),
    low: Number(candle.low),
    volume: Number(candle.volume),
    x: (bounds.left - mountRect.left) + snappedX,
    y: (bounds.top - mountRect.top) + snappedY,
    paneTop: bounds.top - mountRect.top,
    paneHeight: bounds.height,
  };
}

function ensureSubCharts(mount, indicators = []) {
  const candleViewRef = mountedCandleViews.get(mount);
  const candleView = candleViewRef?.current;
  if (!candleView || typeof candleView.initializeSubChartIndicators !== "function") {
    return false;
  }
  candleView.initializeSubChartIndicators(indicators);
  return true;
}

function render(mount, options) {
  if (!mount) {
    throw new Error("CandleViewLab.render requires a mount element");
  }
  let root = mountedRoots.get(mount);
  if (!root) {
    root = createRoot(mount);
    mountedRoots.set(mount, root);
  }
  const candleViewRef = React.createRef();
  mountedCandleViews.set(mount, candleViewRef);
  root.render(
    React.createElement(CandleView, {
      ref: candleViewRef,
      theme: options.theme || "light",
      i18n: "zh-cn",
      height: options.height || 680,
      width: "100%",
      title: options.title || "CandleView",
      data: options.data || [],
      leftpanel: options.leftpanel !== false,
      toppanel: options.toppanel !== false,
      mainChartIndicators: options.mainChartIndicators || ["ma"],
      subChartIndicators: options.subChartIndicators || ["volume", "rsi"],
      terminal: false,
      ai: false,
      isThemeSelection: true,
      timeframe: "1d",
      timezone: "Asia/Shanghai",
    }),
  );
  if (options.fitDataRange !== false) {
    [250, 900, 2200, 5200].forEach((delay) => {
      window.setTimeout(() => {
        focusDataRange(mount, options.fitDataRangeOptions || {});
        updateMainPaneTimeAxis(mount);
      }, delay);
    });
  }
  [450, 1200, 2600, 5400].forEach((delay) => {
    window.setTimeout(() => updateMainPaneTimeAxis(mount), delay);
  });
  if (Array.isArray(options.subChartIndicators) && options.subChartIndicators.length > 0) {
    [1000, 2500, 5000].forEach((delay) => {
      window.setTimeout(() => ensureSubCharts(mount, options.subChartIndicators), delay);
    });
  }
}

function activateTool(mount, tool) {
  const candleViewRef = mountedCandleViews.get(mount);
  const candleView = candleViewRef?.current;
  if (!candleView || !tool) {
    return false;
  }
  const chartLayer = candleView.chartLayerRef?.current;
  if (tool === "time-price-range" && typeof chartLayer?.setTimePriceRangeMarkMode === "function") {
    chartLayer.setTimePriceRangeMarkMode();
    if (typeof candleView.handleToolSelect === "function") {
      candleView.handleToolSelect(tool);
    }
    return true;
  }
  if (typeof candleView.handleToolSelect === "function") {
    candleView.handleToolSelect(tool);
    return true;
  }
  return false;
}

function unmount(mount) {
  mount?.querySelector?.(".ia-candleview-main-time-axis")?.remove();
  const root = mountedRoots.get(mount);
  if (root) {
    root.unmount();
    mountedRoots.delete(mount);
  }
  mountedCandleViews.delete(mount);
}

export { activateTool, ensureSubCharts, focusDataRange, measurePointFromMouse, render, unmount, updateMainPaneTimeAxis };
