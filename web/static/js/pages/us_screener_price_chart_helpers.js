(function () {
  function numberOrNull(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function signedPercentText(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return "--";
    return `${parsed >= 0 ? "+" : ""}${parsed.toFixed(2)}%`;
  }

  function metricText(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return "--";
    if (Math.abs(parsed) >= 1000) return parsed.toLocaleString(undefined, { maximumFractionDigits: 0 });
    if (Math.abs(parsed) >= 100) return parsed.toLocaleString(undefined, { maximumFractionDigits: 1 });
    return parsed.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }

  function snapshotSource(chartData, hoverSnapshot) {
    if (hoverSnapshot?.date) return hoverSnapshot;
    const candles = normalizeRows(chartData);
    if (!candles.length) return null;
    const last = candles[candles.length - 1] || {};
    const prev = candles.length > 1 ? candles[candles.length - 2] || {} : {};
    const close = Number(last.close);
    const previousClose = Number(prev.close);
    const dayChangePct = Number.isFinite(close) && Number.isFinite(previousClose) && previousClose > 0
      ? ((close / previousClose) - 1) * 100
      : null;
    return {
      date: String(last.date || "").slice(0, 10),
      open: Number(last.open),
      high: Number(last.high),
      low: Number(last.low),
      close,
      volume: Number(last.volume),
      dayChangePct,
    };
  }

  function latestMaValue(chartData, key) {
    const series = Array.isArray(chartData?.series?.[key]) ? chartData.series[key] : [];
    for (let index = series.length - 1; index >= 0; index -= 1) {
      const value = Number(series[index]?.value);
      if (Number.isFinite(value)) return value;
    }
    return null;
  }

  function trendState(chartData, snapshot) {
    const source = snapshot || snapshotSource(chartData);
    const close = Number(source?.close);
    const ma50 = latestMaValue(chartData, "ma50");
    const ma100 = latestMaValue(chartData, "ma100");
    const ma200 = latestMaValue(chartData, "ma200");
    if (![close, ma50, ma100, ma200].every(Number.isFinite)) {
      return { text: "--", valueClass: "text-slate-500" };
    }
    if (close > ma50 && ma50 > ma100 && ma100 > ma200) {
      return { text: "Price > MA50 > MA100 > MA200", valueClass: "text-emerald-700" };
    }
    if (close < ma50 && ma50 < ma100 && ma100 < ma200) {
      return { text: "Price < MA50 < MA100 < MA200", valueClass: "text-rose-700" };
    }
    if (close >= ma200) {
      return { text: "Above MA200", valueClass: "text-slate-900" };
    }
    return { text: "Below MA200", valueClass: "text-amber-700" };
  }

  function volumeContext(chartData, snapshot) {
    const source = snapshot || snapshotSource(chartData);
    const currentVolume = Number(source?.volume);
    const candles = normalizeRows(chartData);
    const recent = candles
      .slice(-20)
      .map((item) => Number(item?.volume))
      .filter(Number.isFinite);
    if (!Number.isFinite(currentVolume) || !recent.length) return "--";
    const average20 = recent.reduce((sum, value) => sum + value, 0) / recent.length;
    if (!Number.isFinite(average20) || average20 <= 0) return "--";
    return `${(currentVolume / average20).toFixed(2)}x`;
  }

  function measurement(start, end) {
    const startClose = Number(start?.close);
    const endClose = Number(end?.close);
    const startDate = Date.parse(String(start?.date || ""));
    const endDate = Date.parse(String(end?.date || ""));
    const startIndex = Number(start?.index);
    const endIndex = Number(end?.index);
    const absolute = Number.isFinite(startClose) && Number.isFinite(endClose)
      ? endClose - startClose
      : null;
    const percent = Number.isFinite(startClose) && Number.isFinite(endClose) && startClose > 0
      ? ((endClose / startClose) - 1) * 100
      : null;
    const days = Number.isFinite(startDate) && Number.isFinite(endDate) && endDate >= startDate
      ? Math.max(0, Math.round((endDate - startDate) / 86400000))
      : null;
    const bars = Number.isInteger(startIndex) && Number.isInteger(endIndex) && endIndex >= startIndex
      ? (endIndex - startIndex) + 1
      : null;
    let annualized = null;
    if (Number.isFinite(percent) && Number.isFinite(days) && days > 0) {
      const totalReturn = percent / 100;
      if (1 + totalReturn > 0) {
        annualized = (Math.pow(1 + totalReturn, 365 / days) - 1) * 100;
      }
    }
    return { absolute, percent, annualized, days, bars };
  }

  function latestPriceTag(chartData, formatters) {
    const snapshot = snapshotSource(chartData);
    if (!snapshot) return "--";
    const price = typeof formatters?.priceChartMetric === "function"
      ? formatters.priceChartMetric(snapshot.close)
      : metricText(snapshot.close);
    const change = typeof formatters?.priceChangeText === "function"
      ? formatters.priceChangeText(snapshot.dayChangePct)
      : signedPercentText(snapshot.dayChangePct);
    return `${price} / ${change}`;
  }

  function normalizeRows(chartData) {
    const candles = Array.isArray(chartData?.series?.candles) ? chartData.series.candles : [];
    return candles
      .map((item, sourceIndex) => {
        const timestamp = Date.parse(`${item?.date || ""}T00:00:00`);
        const open = Number(item?.open);
        const high = Number(item?.high);
        const low = Number(item?.low);
        const close = Number(item?.close);
        const volume = Number(item?.volume);
        if (![timestamp, open, high, low, close].every(Number.isFinite)) return null;
        return {
          timestamp,
          open,
          high,
          low,
          close,
          volume: Number.isFinite(volume) ? volume : 0,
          date: String(item?.date || "").slice(0, 10),
          sourceIndex,
        };
      })
      .filter(Boolean);
  }

  function klineSymbolInfo(rows) {
    const sample = Array.isArray(rows) && rows.length ? rows[rows.length - 1] : null;
    const close = Number(sample?.close);
    const pricePrecision = Number.isFinite(close) && !Number.isInteger(close) ? 2 : 0;
    return {
      ticker: "LOCAL",
      pricePrecision,
      volumePrecision: 0,
    };
  }

  function candleAt(chartData, index) {
    const candles = normalizeRows(chartData);
    if (!Number.isInteger(index) || index < 0 || index >= candles.length) return null;
    const candle = candles[index] || {};
    const close = Number(candle.close);
    const open = Number(candle.open);
    const high = Number(candle.high);
    const low = Number(candle.low);
    if (![open, high, low, close].every(Number.isFinite)) return null;
    const previousClose = index > 0 ? Number(candles[index - 1]?.close) : NaN;
    const dayChangePct = Number.isFinite(previousClose) && previousClose > 0
      ? ((close / previousClose) - 1) * 100
      : null;
    return {
      date: String(candle.date || "").slice(0, 10),
      close,
      open,
      high,
      low,
      volume: Number(candle.volume),
      dayChangePct,
      index,
      sourceIndex: Number.isInteger(candle.sourceIndex) ? candle.sourceIndex : index,
    };
  }

  function measurePointFromCrosshair(crosshair, chartData, host) {
    if (!host) return null;
    const dataIndex = Number.isInteger(crosshair?.dataIndex) ? crosshair.dataIndex : -1;
    const candle = candleAt(chartData, dataIndex);
    if (!candle?.date) return null;
    const rect = host.getBoundingClientRect();
    const x = Number(crosshair?.x);
    const y = Number(crosshair?.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return {
      ...candle,
      x: Math.min(Math.max(x, 0), rect.width),
      y: Math.min(Math.max(y, 0), rect.height),
    };
  }

  function measurePointFromMouse(event, host, chartData, chart) {
    if (!event || !host || !chart) return null;
    const candles = normalizeRows(chartData);
    if (!candles.length) return null;
    const rect = host.getBoundingClientRect();
    const width = rect.width || host.clientWidth || 0;
    const height = rect.height || host.clientHeight || 0;
    if (width <= 0 || height <= 0) return null;
    const relativeX = Math.min(Math.max(event.clientX - rect.left, 0), width);
    const relativeY = Math.min(Math.max(event.clientY - rect.top, 0), height);
    if (typeof chart.convertFromPixel !== "function") return null;
    const converted = chart.convertFromPixel({ x: relativeX, y: relativeY }, { id: "candle_pane" }) || {};
    const rawIndex = Number(converted.dataIndex);
    if (!Number.isInteger(rawIndex)) return null;
    const index = Math.min(candles.length - 1, Math.max(0, rawIndex));
    const candle = candleAt(chartData, index);
    if (!candle) return null;
    let snappedX = relativeX;
    let snappedY = relativeY;
    if (typeof chart.convertToPixel === "function") {
      const pixel = chart.convertToPixel({ dataIndex: index, value: candle.close }, { id: "candle_pane" }) || {};
      if (Number.isFinite(Number(pixel.x))) snappedX = Number(pixel.x);
      if (Number.isFinite(Number(pixel.y))) snappedY = Number(pixel.y);
    }
    return {
      ...candle,
      x: snappedX,
      y: snappedY,
    };
  }

  function avoidMeasureLabelCollisions(labelX, labelY, start, end, host) {
    if (!host) return { x: labelX, y: labelY };
    const width = host.clientWidth || host.width || 0;
    const height = host.clientHeight || host.height || 0;
    const labelWidth = 112;
    const labelHeight = 28;
    const markerRadius = 22;
    const clampX = (value) => Math.min(Math.max(value, 8), Math.max(8, width - labelWidth - 8));
    const clampY = (value) => Math.min(Math.max(value, 8), Math.max(8, height - labelHeight - 8));
    let x = clampX(labelX);
    let y = clampY(labelY);
    const overlapsMarker = (point) => {
      if (!point) return false;
      const px = Number(point.x);
      const py = Number(point.y);
      if (!Number.isFinite(px) || !Number.isFinite(py)) return false;
      const nearestX = Math.min(Math.max(px, x), x + labelWidth);
      const nearestY = Math.min(Math.max(py, y), y + labelHeight);
      return Math.sqrt(((px - nearestX) ** 2) + ((py - nearestY) ** 2)) < markerRadius;
    };
    if (overlapsMarker(start) || overlapsMarker(end)) {
      const averageY = (Number(start?.y) + Number(end?.y)) / 2;
      const moveBelow = Number.isFinite(averageY) && averageY < height * 0.42;
      y = clampY(y + (moveBelow ? 34 : -34));
    }
    return { x, y };
  }

  function buildChartMarkers(chartData, stock) {
    const candles = Array.isArray(chartData?.series?.candles) ? chartData.series.candles : [];
    const normalized = candles
      .map((item, index) => ({
        index,
        date: String(item?.date || "").slice(0, 10),
        open: Number(item?.open),
        high: Number(item?.high),
        low: Number(item?.low),
        close: Number(item?.close),
        volume: Number(item?.volume),
      }))
      .filter((item) => item.date && [item.open, item.high, item.low, item.close].every(Number.isFinite));
    if (!normalized.length) return [];
    const markers = [];
    const seen = new Set();
    const pushMarker = (marker) => {
      if (!marker?.date || !Number.isFinite(Number(marker.value)) || !Number.isInteger(marker.index)) return;
      const key = `${marker.type}|${marker.index}|${Number(marker.value).toFixed(6)}`;
      if (seen.has(key)) return;
      seen.add(key);
      markers.push(marker);
    };
    const triggerDate = String(stock?.trigger_trade_date || stock?.new_low_trade_date || "").slice(0, 10);
    if (triggerDate) {
      const trigger = normalized.find((item) => item.date === triggerDate);
      if (trigger) {
        pushMarker({
          type: "trigger",
          category: "signal",
          label: stock?.new_low_trade_date && !stock?.trigger_trade_date ? "Text" : "Text",
          date: trigger.date,
          index: trigger.index,
          value: trigger.high,
          tone: "blue",
        });
      }
    }
    const high = normalized.reduce((best, item) => (item.high > best.high ? item : best), normalized[0]);
    const low = normalized.reduce((best, item) => (item.low < best.low ? item : best), normalized[0]);
    const maxVolume = normalized
      .filter((item) => Number.isFinite(item.volume))
      .reduce((best, item) => (item.volume > best.volume ? item : best), normalized.find((item) => Number.isFinite(item.volume)) || normalized[0]);
    let maxGain = null;
    for (let index = 1; index < normalized.length; index += 1) {
      const prev = normalized[index - 1];
      const item = normalized[index];
      if (!Number.isFinite(prev.close) || prev.close <= 0) continue;
      const gainPct = ((item.close / prev.close) - 1) * 100;
      if (!maxGain || gainPct > maxGain.gainPct) {
        maxGain = { ...item, gainPct };
      }
    }
    pushMarker({ type: "high_52w", category: "range", label: "52Text", date: high.date, index: high.index, value: high.high, tone: "red" });
    pushMarker({ type: "low_52w", category: "range", label: "52Text", date: low.date, index: low.index, value: low.low, tone: "green" });
    if (maxVolume) {
      pushMarker({ type: "max_volume", category: "volume", label: "Text", date: maxVolume.date, index: maxVolume.index, value: maxVolume.high, tone: "slate" });
    }
    if (maxGain && Number.isFinite(maxGain.gainPct)) {
      pushMarker({ type: "max_gain", category: "gain", label: "Text", date: maxGain.date, index: maxGain.index, value: maxGain.high, tone: "amber", detail: signedPercentText(maxGain.gainPct) });
    }
    return markers;
  }

  function normalizeTradeExecutions(trades) {
    if (!Array.isArray(trades)) return [];
    return trades
      .map((item) => {
        const action = String(item?.action || item?.Action || "").trim().toUpperCase();
        const date = String(item?.date || item?.Date || "").slice(0, 10);
        const price = Number(item?.price ?? item?.Price);
        if (!date || !["BUY", "SELL"].includes(action) || !Number.isFinite(price)) return null;
        return { date, action, price };
      })
      .filter(Boolean);
  }

  function buildTradeExecutionMarkers(chartData, trades) {
    const normalizedRows = normalizeRows(chartData);
    if (!normalizedRows.length) return [];
    const byDate = new Map(normalizedRows.map((item) => [String(item.date || "").slice(0, 10), item]));
    return normalizeTradeExecutions(trades)
      .map((trade, index) => {
        const candle = byDate.get(trade.date);
        if (!candle) return null;
        const isBuy = trade.action === "BUY";
        return {
          type: isBuy ? "trade_buy" : "trade_sell",
          category: "trade",
          label: trade.action,
          date: trade.date,
          index: candle.index,
          value: Number((isBuy ? Number(candle.low) * 0.995 : Number(candle.high) * 1.005).toFixed(6)),
          price: trade.price,
          tone: isBuy ? "cyan" : "magenta",
          symbol: isBuy ? "triangle-up" : "triangle-down",
          color: isBuy ? "#00E5FF" : "#FF1744",
          borderColor: "#111827",
          detail: `${trade.action} @ ${metricText(trade.price)}`,
          sortIndex: index,
        };
      })
      .filter(Boolean);
  }

  function tradeTooltipRowsForDate(markers, date, formatters) {
    const target = String(date || "").slice(0, 10);
    if (!target || !Array.isArray(markers)) return [];
    const priceFormatter = typeof formatters?.priceChartMetric === "function"
      ? formatters.priceChartMetric
      : metricText;
    return markers
      .filter((marker) => String(marker?.date || "").slice(0, 10) === target && String(marker?.category || "") === "trade")
      .sort((a, b) => Number(a.sortIndex || 0) - Number(b.sortIndex || 0))
      .map((marker) => ({
        label: "Trade",
        value: `${marker.label === "BUY" ? "Buy" : "Sell"} @ ${priceFormatter(marker.price)}`,
        valueClass: marker.type === "trade_buy" ? "numeric text-sky-700" : "numeric text-rose-700",
      }));
  }

  function filterChartMarkers(markers, layerState) {
    if (!Array.isArray(markers)) return [];
    const state = layerState || {};
    return markers.filter((marker) => {
      const category = String(marker?.category || "signal");
      return state[category] !== false;
    });
  }

  function chartMarkerDetail(marker, formatters) {
    if (!marker) return null;
    const type = String(marker.type || "");
    const categoryLabels = {
      gain: "Text",
      range: "52Text",
      signal: "Text",
      trade: "Trade",
      volume: "Text",
    };
    const descriptions = {
      trigger: "TextCurrentText K Text. ",
      high_52w: "CurrentText. ",
      low_52w: "CurrentText. ",
      max_volume: "CurrentTextTradeText. ",
      max_gain: "CurrentTextTradeText. ",
      trade_buy: "TextBuyText. ",
      trade_sell: "TextSellText. ",
    };
    const price = typeof formatters?.priceChartMetric === "function"
      ? formatters.priceChartMetric(marker.value)
      : metricText(marker.value);
    return {
      title: String(marker.label || marker.type || "Text"),
      date: String(marker.date || "").slice(0, 10) || "--",
      price,
      category: categoryLabels[String(marker.category || "signal")] || "Text",
      detail: String(marker.detail || "").trim(),
      description: descriptions[type] || "Current K Text. ",
      tone: String(marker.tone || "slate"),
      type,
    };
  }

  function projectChartMarkers(markers, chart, host) {
    if (!Array.isArray(markers) || !chart || typeof chart.convertToPixel !== "function") return [];
    const width = host?.clientWidth || chart.clientWidth || 0;
    const height = host?.clientHeight || chart.clientHeight || 0;
    const slotsByColumn = new Map();
    const toneClasses = {
      amber: "border-amber-300 bg-amber-50 text-amber-700",
      blue: "border-sky-300 bg-sky-50 text-sky-700",
      cyan: "border-slate-900 bg-cyan-50 text-slate-900",
      green: "border-emerald-300 bg-emerald-50 text-emerald-700",
      magenta: "border-slate-900 bg-rose-50 text-slate-900",
      red: "border-rose-300 bg-rose-50 text-rose-700",
      slate: "border-slate-300 bg-white text-slate-600",
    };
    return markers
      .map((marker) => {
        const pixel = chart.convertToPixel({ dataIndex: marker.index, value: marker.value }, { id: "candle_pane" }) || {};
        const x = Number(pixel.x);
        const y = Number(pixel.y);
        if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
        const markerWidth = 132;
        const markerHeight = 32;
        const clampLeft = (value) => Math.min(Math.max(value, 8), Math.max(8, width - markerWidth));
        const clampTop = (value) => Math.min(Math.max(value, 8), Math.max(8, height - markerHeight));
        const left = clampLeft(x);
        const columnKey = String(Math.round(left / 24));
        const slot = slotsByColumn.get(columnKey) || 0;
        slotsByColumn.set(columnKey, slot + 1);
        const baseTop = clampTop(y - 18);
        const rowStep = markerHeight + 6;
        const maxTop = Math.max(8, height - markerHeight);
        const nearTop = baseTop <= 8;
        const nearBottom = baseTop >= maxTop - 1;
        let top = baseTop;
        if (slot > 0) {
          if (nearTop) {
            top = clampTop(baseTop + (slot * rowStep));
          } else if (nearBottom) {
            top = clampTop(baseTop - (slot * rowStep));
          } else {
            const direction = slot % 2 === 1 ? -1 : 1;
            const row = Math.ceil(slot / 2);
            top = clampTop(baseTop + (direction * row * rowStep));
          }
        }
        return {
          ...marker,
          style: `left:${left}px;top:${top}px;`,
          className: toneClasses[marker.tone] || toneClasses.slate,
          shapeClassName: marker.symbol === "triangle-down" ? "price-chart-trade-triangle-down" : marker.symbol === "triangle-up" ? "price-chart-trade-triangle-up" : "",
          shapeStyle: marker.symbol === "triangle-down" ? `border-top-color:${marker.color || "#FF1744"};` : marker.symbol === "triangle-up" ? `border-bottom-color:${marker.color || "#00E5FF"};` : "",
        };
      })
      .filter(Boolean);
  }

  function chartStyles(compactMode) {
    if (window.usScreenerPriceChartTheme && typeof window.usScreenerPriceChartTheme.chartStyles === "function") {
      return window.usScreenerPriceChartTheme.chartStyles(compactMode);
    }
    const appFontFamily = 'IBM Plex Sans, Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    const monoFontFamily = 'IBM Plex Mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
    const candleSlot = compactMode ? 8 : 7.5;
    const candleBody = Number((candleSlot * 0.8).toFixed(2));
    const extremumTag = {
      show: true,
      line: { show: true, style: "solid", size: 1, color: "#9CA3AF", length: 18 },
      text: {
        show: true,
        color: "#9CA3AF",
        size: 10,
        weight: "normal",
        family: monoFontFamily,
        marginLeft: 4,
        marginRight: 4,
        paddingLeft: 0,
        paddingRight: 0,
      },
    };
    const axisBubbleText = {
      show: !compactMode,
      color: "#ffffff",
      backgroundColor: "rgba(31, 41, 55, 0.9)",
      borderRadius: 4,
      paddingLeft: 7,
      paddingRight: 7,
      paddingTop: 3,
      paddingBottom: 3,
      size: 11,
      weight: "bold",
      family: monoFontFamily,
    };
    return {
      layout: {
        backgroundColor: "#ffffff",
        textColor: "#0f172a",
      },
      grid: {
        show: true,
        horizontal: { show: true, size: 1, color: "rgba(148, 163, 184, 0.18)", style: "dash", dashValue: [2, 4] },
        vertical: { show: true, size: 1, color: "rgba(148, 163, 184, 0.14)", style: "dash", dashValue: [2, 4] },
      },
      candle: {
        bar: {
          widthRatio: 0.8,
          gapRatio: 0.2,
          bodyWidth: candleBody,
          wickSize: 1,
          upColor: "#16a34a",
          downColor: "#dc2626",
          noChangeColor: "#9CA3AF",
          upBorderColor: "#16a34a",
          downBorderColor: "#dc2626",
          noChangeBorderColor: "#9CA3AF",
          upWickColor: "#16a34a",
          downWickColor: "#dc2626",
          noChangeWickColor: "#9CA3AF",
        },
        margin: { top: compactMode ? 0.08 : 0.11, bottom: compactMode ? 0.05 : 0.075 },
        type: "candle_solid",
        tooltip: {
          showRule: "always",
          showType: "standard",
          labels: ["Text", "Text", "Text", "Text", "Text", "Text"],
        },
        priceMark: {
          show: true,
          high: extremumTag,
          low: extremumTag,
          last: {
            show: true,
            upColor: "#16a34a",
            downColor: "#dc2626",
            noChangeColor: "#9CA3AF",
            line: { show: true, style: "dash", dashValue: [4, 4], size: 1 },
            text: {
              show: true,
              paddingLeft: 6,
              paddingTop: 3,
              paddingRight: 6,
              paddingBottom: 3,
              borderRadius: 4,
              color: "#ffffff",
              size: 11,
              weight: "bold",
              family: monoFontFamily,
            },
          },
        },
      },
      xAxis: {
        show: true,
        size: compactMode ? 24 : 26,
        axisLine: { show: false },
        tickLine: { show: false },
        tickText: { color: compactMode ? "#9CA3AF" : "#A7B0BE", size: 10, weight: "normal", family: monoFontFamily },
      },
      yAxis: {
        show: true,
        position: "right",
        size: compactMode ? 54 : 58,
        axisLine: { show: false },
        tickLine: { show: false },
        tickText: { color: compactMode ? "#9CA3AF" : "#A7B0BE", size: 10, weight: "normal", family: monoFontFamily },
      },
      indicator: {
        ohlc: { upColor: "#16a34a", downColor: "#dc2626", noChangeColor: "#9CA3AF" },
        bars: [{ upColor: "rgba(22, 163, 74, 0.20)", downColor: "rgba(220, 38, 38, 0.18)", noChangeColor: "rgba(156,163,175,0.18)" }],
        lines: [
          { color: "rgba(245, 158, 11, 0.82)", size: 1.35 },
          { color: "rgba(37, 99, 235, 0.78)", size: 1.35 },
          { color: "rgba(100, 116, 139, 0.72)", size: 1.2 },
        ],
        lastValueMark: {
          show: false,
          text: { show: false, color: "#A7B0BE", size: compactMode ? 10 : 11, weight: "normal", family: monoFontFamily },
        },
      },
      separator: {
        size: 1,
        color: compactMode ? "rgba(203, 213, 225, 0.78)" : "rgba(203, 213, 225, 0.9)",
        fill: true,
        activeBackgroundColor: "rgba(248, 250, 252, 0.98)",
      },
      crosshair: {
        show: true,
        horizontal: {
          show: true,
          line: { show: true, style: "dash", dashValue: [4, 4], size: 1, color: "#64748b" },
          text: axisBubbleText,
        },
        vertical: {
          show: true,
          line: { show: true, style: "dash", dashValue: [4, 4], size: 1, color: "#64748b" },
          text: axisBubbleText,
        },
      },
      technical: {
        antiAlias: true,
        candleSlot,
        candleBody,
        candleGap: Number((candleSlot * 0.2).toFixed(2)),
        candleWidthRatio: 0.8,
      },
    };
  }

  function resolveIndicatorPreset(value) {
    const preset = String(value || "trend").trim().toLowerCase();
    if (preset === "clean") {
      return { id: "clean", maParams: [50, 100, 200], showVolumePane: false, showRsiPane: false };
    }
    if (preset === "momentum") {
      return { id: "momentum", maParams: [50, 100, 200], showVolumePane: true, showRsiPane: true };
    }
    return { id: "trend", maParams: [50, 100, 200], showVolumePane: true, showRsiPane: false };
  }

  function applyMovingAverageIndicator(chart, maParams) {
    if (!chart || typeof chart.createIndicator !== "function") return;
    const params = Array.isArray(maParams) && maParams.length ? maParams : [50, 100, 200];
    const lineStyles = window.usScreenerPriceChartTheme && typeof window.usScreenerPriceChartTheme.indicatorLineStyles === "function"
      ? window.usScreenerPriceChartTheme.indicatorLineStyles()
      : [
          { color: "rgba(245, 158, 11, 0.82)", size: 1.35 },
          { color: "rgba(37, 99, 235, 0.78)", size: 1.35 },
          { color: "rgba(100, 116, 139, 0.72)", size: 1.2 },
        ];
    chart.createIndicator("MA", true, { id: "candle_pane" });
    if (typeof chart.overrideIndicator === "function") {
      chart.overrideIndicator({
        name: "MA",
        shortName: "MA",
        series: "normal",
        calcParams: params,
        styles: {
          lines: lineStyles,
        },
      });
    }
  }

  function clearNode(node) {
    if (!node) return;
    if (typeof node.replaceChildren === "function") {
      node.replaceChildren();
      return;
    }
    if (typeof node.removeChild === "function") {
      while (node.firstChild) node.removeChild(node.firstChild);
    }
    if (Array.isArray(node.children)) node.children.length = 0;
    if ("textContent" in node) node.textContent = "";
  }

  function appendNode(parent, node) {
    if (!parent || !node) return false;
    if (typeof parent.appendChild === "function") {
      parent.appendChild(node);
      return true;
    }
    if (Array.isArray(parent.children)) {
      parent.children.push(node);
      return true;
    }
    return false;
  }

  function createChartMount(host) {
    if (!host || typeof document === "undefined" || typeof document.createElement !== "function") return null;
    const mount = document.createElement("div");
    mount.className = "kline-chart-mount";
    if (mount.style) {
      mount.style.position = "relative";
      mount.style.width = "100%";
      mount.style.height = "100%";
      mount.style.minHeight = "inherit";
      mount.style.overflow = "hidden";
    }
    return appendNode(host, mount) ? mount : null;
  }

  function discardChartMount(host, mount) {
    if (!host || !mount || mount === host) return;
    if (typeof mount.remove === "function") {
      mount.remove();
      return;
    }
    if (mount.parentNode && typeof mount.parentNode.removeChild === "function") {
      mount.parentNode.removeChild(mount);
      return;
    }
    if (typeof host.removeChild === "function") {
      try {
        host.removeChild(mount);
        return;
      } catch (error) {
        // Fall through for test doubles and partially detached nodes.
      }
    }
    if (Array.isArray(host.children)) {
      const index = host.children.indexOf(mount);
      if (index >= 0) host.children.splice(index, 1);
    }
  }

  function commitChartMount(host, mount) {
    if (!host || !mount || mount === host) return;
    if (typeof host.replaceChildren === "function") {
      host.replaceChildren(mount);
      return;
    }
    const children = Array.from(host.childNodes || host.children || []);
    for (const child of children) {
      if (child === mount) continue;
      if (typeof host.removeChild === "function") {
        try {
          host.removeChild(child);
          continue;
        } catch (error) {
          // Continue to fallback handling below.
        }
      }
      if (typeof child.remove === "function") child.remove();
    }
    if (Array.isArray(host.children)) {
      const existing = host.children.filter((child) => child === mount);
      host.children.length = 0;
      host.children.push(...existing);
    }
  }

  function chartHostReady(host) {
    if (!host) return false;
    if (host.isConnected === false) return false;
    const rect = typeof host.getBoundingClientRect === "function"
      ? host.getBoundingClientRect()
      : {};
    const width = Number(rect.width || host.clientWidth || 0);
    const height = Number(rect.height || host.clientHeight || 0);
    return width >= 24 && height >= 24;
  }

  function errorMessage(error) {
    return error?.message || String(error || "unknown error");
  }

  function reportError(options, message) {
    try {
      if (typeof options?.onError === "function") options.onError(message);
    } catch (error) {
      // Error reporting must never become the reason the chart crashes.
    }
  }

  function safeCall(fn) {
    try {
      return fn();
    } catch (error) {
      return { __chartError: error };
    }
  }

  function isChartError(result) {
    return !!result && Object.prototype.hasOwnProperty.call(result, "__chartError");
  }

  function callChart(chart, methodName, args = [], options = {}) {
    if (!chart || typeof chart[methodName] !== "function") {
      if (options.required) {
        return { ok: false, error: new Error(`${methodName} unavailable`) };
      }
      return { ok: true, value: undefined };
    }
    try {
      return { ok: true, value: chart[methodName](...args) };
    } catch (error) {
      return { ok: false, error };
    }
  }

  function renderTooltipRows(container, rows) {
    if (!container) return false;
    clearNode(container);
    const items = Array.isArray(rows) ? rows.filter(Boolean) : [];
    if (!items.length) return false;

    let grid = null;
    const ensureGrid = () => {
      if (grid) return grid;
      grid = document.createElement("div");
      grid.className = "chart-hover-tooltip-grid";
      container.appendChild(grid);
      return grid;
    };

    items.forEach((row) => {
      if (row.type === "date") {
        const date = document.createElement("div");
        date.className = "mb-1 text-xs font-semibold text-slate-900";
        date.textContent = row.value ?? "--";
        container.appendChild(date);
        return;
      }

      const line = document.createElement("div");
      line.className = "chart-hover-tooltip-row";

      const label = document.createElement("span");
      label.textContent = row.label ?? "";

      const value = document.createElement("strong");
      value.className = row.valueClass || "numeric";
      value.textContent = row.value ?? "--";

      line.appendChild(label);
      line.appendChild(value);
      ensureGrid().appendChild(line);
    });
    return true;
  }

  function hideNode(node) {
    if (node?.style) node.style.display = "none";
  }

  function renderKlineChart(options) {
    const klinecharts = window.klinecharts || {};
    if (typeof klinecharts.init !== "function") {
      reportError(options, "KLineChart LoadFailed, Text");
      return null;
    }
    const host = options?.host;
    if (!host) return null;
    const rows = Array.isArray(options?.rows) ? options.rows : [];
    if (!rows.length) {
      reportError(options, "No dataText");
      return null;
    }
    if (!chartHostReady(host)) {
      reportError(options, "Text");
      return null;
    }
    const chartHost = createChartMount(host);
    if (!chartHost) {
      reportError(options, "KLineChart TextFailed");
      return null;
    }

    const compactMode = options.compactMode !== false;
    const showFloatingTooltip = !compactMode;
    const failRender = (message, chartToDestroy = null) => {
      if (chartToDestroy) destroyKlineChart(chartToDestroy);
      discardChartMount(host, chartHost);
      reportError(options, message);
      return null;
    };

    const initialized = safeCall(() => klinecharts.init(chartHost, {
      locale: "zh-CN",
      timezone: "Asia/Shanghai",
      styles: chartStyles(compactMode),
    }));
    if (isChartError(initialized)) {
      return failRender(`KLineChart TextFailed: ${errorMessage(initialized.__chartError)}`);
    }
    const chart = initialized;

    if (!chart) {
      return failRender("KLineChart TextFailed");
    }

    if (host.parentElement) {
      host.parentElement.classList.toggle("chart-surface-preview", compactMode);
      host.parentElement.classList.toggle("chart-surface-modal", !compactMode);
    }

    let floatingTooltip = null;
    if (showFloatingTooltip) {
      floatingTooltip = document.createElement("div");
      floatingTooltip.className = "chart-hover-tooltip";
      floatingTooltip.style.display = "none";
      appendNode(chartHost, floatingTooltip);
    }

    const symbolInfo = safeCall(() => (
      typeof options.klineSymbolInfo === "function" ? options.klineSymbolInfo(rows) : klineSymbolInfo(rows)
    ));
    if (isChartError(symbolInfo)) {
      return failRender(`KLineChart TextFailed: ${errorMessage(symbolInfo.__chartError)}`, chart);
    }

    const dataLoader = {
      getBars: ({ callback } = {}) => {
        try {
          if (typeof callback === "function") callback(rows, false);
        } catch (error) {
          reportError(options, `KLineChart TextLoadFailed: ${errorMessage(error)}`);
        }
      },
    };

    const requiredCalls = [
      callChart(chart, "setDataLoader", [dataLoader], { required: true }),
      callChart(chart, "setSymbol", [symbolInfo], { required: true }),
      callChart(chart, "setPeriod", [{ type: "day", span: 1 }], { required: true }),
      callChart(chart, "resetData", [], { required: true }),
    ];
    const failedRequiredCall = requiredCalls.find((result) => !result.ok);
    if (failedRequiredCall) {
      return failRender(`KLineChart TextFailed: ${errorMessage(failedRequiredCall.error)}`, chart);
    }

    const indicatorPreset = resolveIndicatorPreset(options.indicatorPreset);
    const showVolumePane = options.showVolumePane ?? indicatorPreset.showVolumePane;

    if (typeof chart.createIndicator === "function" && showVolumePane) {
      const volumeResult = callChart(chart, "createIndicator", ["VOL", false]);
      if (!volumeResult.ok) {
        return failRender(`KLineChart TextFailed: ${errorMessage(volumeResult.error)}`, chart);
      }
      const paneResult = callChart(chart, "setPaneOptions", [{
          id: "volume_pane",
          height: compactMode ? 64 : 88,
          minHeight: compactMode ? 52 : 72,
        }]);
      if (!paneResult.ok) {
        return failRender(`KLineChart TextFailed: ${errorMessage(paneResult.error)}`, chart);
      }
    }

    const maResult = safeCall(() => applyMovingAverageIndicator(chart, indicatorPreset.maParams));
    if (isChartError(maResult)) {
      return failRender(`KLineChart TextFailed: ${errorMessage(maResult.__chartError)}`, chart);
    }

    if (typeof chart.createIndicator === "function" && indicatorPreset.showRsiPane) {
      const rsiResult = callChart(chart, "createIndicator", ["RSI", false]);
      if (!rsiResult.ok) {
        return failRender(`KLineChart TextFailed: ${errorMessage(rsiResult.error)}`, chart);
      }
      const rsiPaneResult = callChart(chart, "setPaneOptions", [{
          id: "rsi_pane",
          height: compactMode ? 58 : 72,
          minHeight: compactMode ? 48 : 60,
        }]);
      if (!rsiPaneResult.ok) {
        return failRender(`KLineChart TextFailed: ${errorMessage(rsiPaneResult.error)}`, chart);
      }
    }

    if (typeof chart.subscribeAction === "function" && typeof options.onCrosshairChange === "function") {
      const subscribeResult = callChart(chart, "subscribeAction", ["onCrosshairChange", (crosshair) => {
        let snapshot = null;
        try {
          snapshot = options.resolveHoverSnapshot(crosshair, options.chartData);
          options.onCrosshairChange(snapshot);
        } catch (error) {
          hideNode(floatingTooltip);
          reportError(options, `KLineChart TextFailed: ${errorMessage(error)}`);
          return;
        }

        try {
          if (options.enableMeasureTool && typeof options.handleMeasureCrosshair === "function") {
            options.handleMeasureCrosshair(crosshair, options.chartData, host);
          }
        } catch (error) {
          reportError(options, `KLineChart TextFailed: ${errorMessage(error)}`);
        }

        try {
          if (!floatingTooltip) return;
          if (!snapshot?.date || !Number.isFinite(crosshair?.x) || !Number.isFinite(crosshair?.y)) {
            hideNode(floatingTooltip);
            return;
          }
          const rendered = renderTooltipRows(
            floatingTooltip,
            typeof options.priceChartTooltipRows === "function"
              ? options.priceChartTooltipRows(snapshot, { includeVolume: showVolumePane !== false })
              : []
          );
          if (!rendered) {
            hideNode(floatingTooltip);
            return;
          }
          const tooltipWidth = 198;
          const tooltipHeight = showVolumePane !== false ? 176 : 148;
          const safePadding = 10;
          const shouldDockLeft = Number(crosshair.x) > (Number(chartHost.clientWidth || host.clientWidth || 0) * 0.58);
          const preferredX = shouldDockLeft
            ? Number(crosshair.x) - tooltipWidth - 16
            : Number(crosshair.x) + 16;
          const hostWidth = Number(chartHost.clientWidth || host.clientWidth || 0);
          const hostHeight = Number(chartHost.clientHeight || host.clientHeight || 0);
          const x = Math.min(Math.max(preferredX, safePadding), Math.max(safePadding, hostWidth - tooltipWidth - safePadding));
          const y = Math.min(Math.max(Number(crosshair.y) + 14, 8), Math.max(8, hostHeight - tooltipHeight - 8));
          floatingTooltip.style.left = `${x}px`;
          floatingTooltip.style.top = `${y}px`;
          floatingTooltip.style.display = "block";
        } catch (error) {
          hideNode(floatingTooltip);
          reportError(options, `KLineChart TextFailed: ${errorMessage(error)}`);
          return;
        }
      }]);
      if (!subscribeResult.ok) {
        return failRender(`KLineChart TextFailed: ${errorMessage(subscribeResult.error)}`, chart);
      }
    }

    if (options.enableMeasureTool) {
      if (typeof chart.subscribeAction === "function" && typeof options.handleMeasureAction === "function") {
        const measureSubscribeResult = callChart(chart, "subscribeAction", ["onCandleBarClick", (action) => {
          try {
            options.handleMeasureAction(action, options.chartData, host);
          } catch (error) {
            reportError(options, `KLineChart TextFailed: ${errorMessage(error)}`);
          }
        }]);
        if (!measureSubscribeResult.ok) {
          return failRender(`KLineChart TextFailed: ${errorMessage(measureSubscribeResult.error)}`, chart);
        }
      }
      const handleKeyDown = (event) => {
        if (event.key === "Escape" && typeof options.onEscapeMeasure === "function") {
          try {
            options.onEscapeMeasure();
          } catch (error) {
            reportError(options, `KLineChart TextFailed: ${errorMessage(error)}`);
          }
        }
      };
      try {
        window.addEventListener("keydown", handleKeyDown);
      } catch (error) {
        reportError(options, `KLineChart TextFailed: ${errorMessage(error)}`);
      }
      chart.__measureCleanup = () => {
        try {
          window.removeEventListener("keydown", handleKeyDown);
        } catch (error) {
          // Ignore cleanup failures while replacing or closing chart instances.
        }
      };
    }

    for (const result of [
      callChart(chart, "setPriceVolumePrecision", [2, 0]),
      callChart(chart, "setBarSpace", [compactMode ? 8 : 7.5]),
      callChart(chart, "setOffsetRightDistance", [compactMode ? 10 : 18]),
      callChart(chart, "resize", []),
    ]) {
      if (!result.ok) {
        return failRender(`KLineChart TextFailed: ${errorMessage(result.error)}`, chart);
      }
    }
    commitChartMount(host, chartHost);
    return chart;
  }

  function destroyKlineChart(chart) {
    if (!chart) return;
    if (typeof chart.__measureCleanup === "function") {
      try {
        chart.__measureCleanup();
      } catch (error) {
        // Best-effort cleanup; chart libraries can throw while tearing down detached nodes.
      }
      chart.__measureCleanup = null;
    }
    if (typeof chart.destroy === "function") {
      try {
        chart.destroy();
        return;
      } catch (error) {
        // Fall through to dispose if the library exposes it.
      }
    }
    if (typeof chart.dispose === "function") {
      try {
        chart.dispose();
      } catch (error) {
        // Ignore teardown failures to avoid crashing the page.
      }
    }
  }

  window.usScreenerPriceChartHelpers = {
    chartStyles,
    chartHostReady,
    destroyKlineChart,
    avoidMeasureLabelCollisions,
    buildChartMarkers,
    buildTradeExecutionMarkers,
    chartMarkerDetail,
    filterChartMarkers,
    klineSymbolInfo,
    latestMaValue,
    latestPriceTag,
    measurement,
    measurePointFromCrosshair,
    measurePointFromMouse,
    normalizeRows,
    numberOrNull,
    renderKlineChart,
    renderTooltipRows,
    projectChartMarkers,
    resolveIndicatorPreset,
    snapshotSource,
    trendState,
    tradeTooltipRowsForDate,
    volumeContext,
  };
})();
