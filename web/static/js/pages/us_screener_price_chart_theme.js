(function () {
  const appFontFamily = 'IBM Plex Sans, Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
  const monoFontFamily = 'IBM Plex Mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
  const marketGreen = "#16a34a";
  const marketRed = "#dc2626";
  const neutral = "#94a3b8";
  const indicatorLines = [
    { color: "rgba(245, 158, 11, 0.82)", size: 1.35 },
    { color: "rgba(37, 99, 235, 0.78)", size: 1.35 },
    { color: "rgba(100, 116, 139, 0.72)", size: 1.2 },
  ];

  function indicatorLineStyles() {
    return indicatorLines.map((line) => ({ ...line }));
  }

  function markerPalette() {
    return {
      buy: "#2563eb",
      sell: marketRed,
      signal: "#0284c7",
      range: "#475569",
      volume: "#64748b",
      gain: "#b45309",
    };
  }

  function chartStyles(compactMode) {
    const candleSlot = compactMode ? 8 : 7.5;
    const candleBody = Number((candleSlot * 0.8).toFixed(2));
    const extremumTag = {
      show: true,
      line: { show: true, style: "solid", size: 1, color: "#94a3b8", length: 18 },
      text: {
        show: true,
        color: "#64748b",
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
      backgroundColor: "rgba(15, 23, 42, 0.92)",
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
          upColor: marketGreen,
          downColor: marketRed,
          noChangeColor: neutral,
          upBorderColor: marketGreen,
          downBorderColor: marketRed,
          noChangeBorderColor: neutral,
          upWickColor: marketGreen,
          downWickColor: marketRed,
          noChangeWickColor: neutral,
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
            upColor: marketGreen,
            downColor: marketRed,
            noChangeColor: neutral,
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
        tickText: { color: compactMode ? "#64748b" : "#475569", size: 10, weight: "normal", family: monoFontFamily },
      },
      yAxis: {
        show: true,
        position: "right",
        size: compactMode ? 54 : 58,
        axisLine: { show: false },
        tickLine: { show: false },
        tickText: { color: compactMode ? "#64748b" : "#475569", size: 10, weight: "normal", family: monoFontFamily },
      },
      indicator: {
        ohlc: { upColor: marketGreen, downColor: marketRed, noChangeColor: neutral },
        bars: [{ upColor: "rgba(22, 163, 74, 0.20)", downColor: "rgba(220, 38, 38, 0.18)", noChangeColor: "rgba(148, 163, 184, 0.18)" }],
        lines: indicatorLineStyles(),
        lastValueMark: {
          show: false,
          text: { show: false, color: "#64748b", size: compactMode ? 10 : 11, weight: "normal", family: monoFontFamily },
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

  window.usScreenerPriceChartTheme = {
    appFontFamily,
    monoFontFamily,
    chartStyles,
    indicatorLineStyles,
    markerPalette,
  };
})();
