(function () {
  function normalizedPreset(context) {
    return String(context?.priceChartIndicatorPreset || "trend").trim().toLowerCase();
  }

  function priceChartRangeLabel() {
    const options = Array.isArray(this.priceChartRangeOptions) ? this.priceChartRangeOptions : [];
    const option = options.find((item) => item.value === this.priceChartRange);
    return option?.label || "1Text";
  }

  function priceChartStateIndicatorLabel() {
    const preset = normalizedPreset(this);
    if (preset === "momentum") return "RSI";
    if (preset === "clean") return "MA50/100/200";
    return this.priceChartShowVolumePane ? "MA50/100/200 + VOL" : "MA50/100/200";
  }

  function priceChartIndicatorPresetDetail() {
    const preset = normalizedPreset(this);
    if (preset === "momentum") {
      return {
        title: "Text",
        description: "TextConfirmText RSI Text, Text. ",
      };
    }
    if (preset === "clean") {
      return {
        title: "Text",
        description: "Text K Text MA50 / MA100 / MA200, Text. ",
      };
    }
    return {
      title: "Text",
      description: "Text MA50 / MA100 / MA200 Text. ",
    };
  }

  function priceChartStateBadgeText() {
    return `${priceChartRangeLabel.call(this)} · ${priceChartIndicatorPresetDetail.call(this).title} · ${priceChartStateIndicatorLabel.call(this)}`;
  }

  function priceChartModeLabel() {
    if (!this.priceChartMeasureMode) return "Text";
    if (this.priceChartMeasureLocked) return "Text";
    return "Text";
  }

  function priceChartModeHint() {
    if (!this.priceChartMeasureMode) return "Text: Text OHLC, Text. ";
    if (!this.priceChartMeasureStart) return "Text: Text, Text K Text. ";
    if (!this.priceChartMeasureLocked) return "Text: TextText, Text. ";
    return "Text: TextResultText, Text. ";
  }

  function priceChartIndicatorLayerItems() {
    const preset = normalizedPreset(this);
    if (preset === "momentum") return ["KText", "MA50 / MA100 / MA200", "VOL", "RSI"];
    if (preset === "clean") return ["KText", "MA50 / MA100 / MA200"];
    return ["KText", "MA50 / MA100 / MA200", "VOL"];
  }

  function priceChartToolbarLayerItems() {
    const preset = normalizedPreset(this);
    const base = [
      { key: "candles", label: "KText", shortLabel: "K", tone: "neutral", active: true, toggleable: false },
    ];
    if (preset === "clean") {
      return [
        ...base,
        { key: "ma50", label: "MA50", tone: "amber", active: true, toggleable: false },
        { key: "ma100", label: "MA100", tone: "blue", active: true, toggleable: false },
        { key: "ma200", label: "MA200", tone: "neutral", active: true, toggleable: false },
      ];
    }
    const trendLayers = [
      ...base,
      { key: "ma50", label: "MA50", tone: "amber", active: true, toggleable: false },
      { key: "ma100", label: "MA100", tone: "blue", active: true, toggleable: false },
      { key: "ma200", label: "MA200", tone: "neutral", active: true, toggleable: false },
      { key: "vol", label: "Text", tone: "neutral", active: this.priceChartShowVolumePane, toggleable: true },
    ];
    if (preset === "momentum") {
      trendLayers.push({ key: "rsi", label: "RSI", tone: "sky", active: true, toggleable: false });
    }
    return trendLayers;
  }

  function priceChartToolbarLayerClass(item) {
    const active = item?.active !== false;
    if (!active) return "border-slate-200 bg-slate-100 text-slate-400";
    const tone = String(item?.tone || "neutral");
    if (tone === "blue") return "border-blue-200 bg-blue-50/96 text-blue-700";
    if (tone === "amber") return "border-amber-200 bg-amber-50/96 text-amber-700";
    if (tone === "emerald") return "border-emerald-200 bg-emerald-50/96 text-emerald-700";
    if (tone === "sky") return "border-sky-200 bg-sky-50/96 text-sky-700";
    return "border-slate-300 bg-white/96 text-slate-600";
  }

  function priceChartRailButtonClass(item) {
    const classes = priceChartToolbarLayerClass(item);
    if (item?.toggleable) return classes;
    return `${classes} disabled:cursor-default disabled:opacity-100`;
  }

  function activePriceChartMeasurePoint() {
    if (this.priceChartMeasureLocked && this.priceChartMeasureEnd) {
      return this.priceChartMeasureEnd;
    }
    return this.priceChartMeasureHoverPoint || this.priceChartMeasureEnd || null;
  }

  function priceChartMeasureStats() {
    if (!this.priceChartHelpers || typeof this.priceChartHelpers.measurement !== "function") {
      return { absolute: null, percent: null, annualized: null, days: null, bars: null };
    }
    return this.priceChartHelpers.measurement(this.priceChartMeasureStart, this.activePriceChartMeasurePoint());
  }

  function priceChartMeasurePercentValue() {
    return this.priceChartMeasureStats().percent;
  }

  function priceChartMeasurePercentText() {
    const value = this.priceChartMeasurePercentValue();
    return Number.isFinite(value) ? `${value >= 0 ? "+" : ""}${value.toFixed(2)}%` : "";
  }

  function priceChartMeasureAbsoluteValue() {
    return this.priceChartMeasureStats().absolute;
  }

  function priceChartMeasureAbsoluteText(value = null) {
    const resolved = value === null ? this.priceChartMeasureAbsoluteValue() : Number(value);
    if (!Number.isFinite(resolved)) return "--";
    return `${resolved >= 0 ? "+" : ""}${resolved.toFixed(2)}`;
  }

  function priceChartMeasureAnnualizedValue() {
    return this.priceChartMeasureStats().annualized;
  }

  function priceChartMeasureAnnualizedText() {
    const value = this.priceChartMeasureAnnualizedValue();
    if (!Number.isFinite(value)) return "--";
    return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
  }

  function priceChartMeasureDaysValue() {
    return this.priceChartMeasureStats().days;
  }

  function priceChartMeasureDaysText() {
    const value = this.priceChartMeasureDaysValue();
    return Number.isFinite(value) ? `${value}D` : "--";
  }

  function priceChartMeasureBarsValue() {
    return this.priceChartMeasureStats().bars;
  }

  function priceChartMeasureBarsText() {
    const value = this.priceChartMeasureBarsValue();
    return Number.isFinite(value) ? `${value} bars` : "--";
  }

  function priceChartMeasureSummaryItems() {
    const start = this.priceChartMeasureStart;
    const end = this.activePriceChartMeasurePoint();
    const absoluteValue = this.priceChartMeasureAbsoluteValue();
    const percentValue = this.priceChartMeasurePercentValue();
    const annualizedValue = this.priceChartMeasureAnnualizedValue();
    const toneClass = Number.isFinite(percentValue)
      ? (percentValue >= 0 ? "numeric text-sm font-semibold text-green-600" : "numeric text-sm font-semibold text-red-600")
      : "numeric text-sm font-semibold text-slate-900";
    return [
      {
        label: "Text",
        value: start?.date && Number.isFinite(Number(start?.close))
          ? `${start.date} · ${this.priceChartMetric(start.close)}`
          : "--",
        valueClass: "numeric text-sm font-semibold text-slate-900",
      },
      {
        label: "Text",
        value: end?.date && Number.isFinite(Number(end?.close))
          ? `${end.date} · ${this.priceChartMetric(end.close)}`
          : "--",
        valueClass: "numeric text-sm font-semibold text-slate-900",
      },
      {
        label: "Text",
        value: this.priceChartMeasureAbsoluteText(absoluteValue),
        valueClass: toneClass,
      },
      {
        label: "Text",
        value: this.priceChartMeasurePercentText(),
        valueClass: toneClass,
      },
      {
        label: "Text",
        value: this.priceChartMeasureAnnualizedText(),
        valueClass: Number.isFinite(annualizedValue) ? toneClass : "numeric text-sm font-semibold text-slate-500",
      },
      {
        label: "Text",
        value: this.priceChartMeasureDaysText(),
        valueClass: "numeric text-sm font-semibold text-slate-900",
      },
      {
        label: "KText",
        value: this.priceChartMeasureBarsText(),
        valueClass: "numeric text-sm font-semibold text-slate-900",
      },
    ];
  }

  function priceChartMeasureSummaryCardTitle() {
    const percentText = this.priceChartMeasurePercentText();
    return percentText ? `TextResult ${percentText}` : "TextResult";
  }

  function priceChartMeasureSummaryCardItems() {
    return this.priceChartMeasureSummaryItems().filter((item) => item.label !== "KText");
  }

  function activeWorkbenchPriceChartMeasurePoint() {
    if (this.workbenchPriceChartMeasureLocked && this.workbenchPriceChartMeasureEnd) {
      return this.workbenchPriceChartMeasureEnd;
    }
    return this.workbenchPriceChartMeasureHoverPoint || this.workbenchPriceChartMeasureEnd || null;
  }

  function workbenchPriceChartMeasureStats() {
    if (!this.priceChartHelpers || typeof this.priceChartHelpers.measurement !== "function") {
      return { percent: null, annualized: null, days: null };
    }
    return this.priceChartHelpers.measurement(this.workbenchPriceChartMeasureStart, this.activeWorkbenchPriceChartMeasurePoint());
  }

  function workbenchPriceChartMeasurePercentValue() {
    return this.workbenchPriceChartMeasureStats().percent;
  }

  function workbenchPriceChartMeasurePercentText() {
    const value = this.workbenchPriceChartMeasurePercentValue();
    return Number.isFinite(value) ? `${value >= 0 ? "+" : ""}${value.toFixed(2)}%` : "--";
  }

  function workbenchPriceChartMeasureAnnualizedText() {
    const value = this.workbenchPriceChartMeasureStats().annualized;
    return Number.isFinite(value) ? `${value >= 0 ? "+" : ""}${value.toFixed(2)}%` : "--";
  }

  function workbenchPriceChartMeasureDaysText() {
    const value = this.workbenchPriceChartMeasureStats().days;
    return Number.isFinite(value) ? `${value}Text` : "--";
  }

  function workbenchPriceChartMeasureModeLabel() {
    if (this.workbenchPriceChartMeasureLocked) return "Text";
    if (this.workbenchPriceChartMeasureStart) return "Text: TextResult";
    return "Text: Text K Text";
  }

  function workbenchPriceChartMeasureHint() {
    if (!this.workbenchPriceChartMeasureMode) return "";
    if (!this.workbenchPriceChartMeasureStart) return "Text: Text, Text K Text. ";
    if (!this.workbenchPriceChartMeasureLocked) return "Text, TextResult. ";
    return "Text: Text. ";
  }

  function createUsScreenerPriceChartViewModel() {
    return {
      priceChartRangeLabel,
      priceChartStateIndicatorLabel,
      priceChartStateBadgeText,
      priceChartModeLabel,
      priceChartModeHint,
      priceChartIndicatorPresetDetail,
      priceChartIndicatorLayerItems,
      priceChartToolbarLayerItems,
      priceChartToolbarLayerClass,
      priceChartRailButtonClass,
      activePriceChartMeasurePoint,
      priceChartMeasureStats,
      priceChartMeasurePercentValue,
      priceChartMeasurePercentText,
      priceChartMeasureAbsoluteValue,
      priceChartMeasureAbsoluteText,
      priceChartMeasureAnnualizedValue,
      priceChartMeasureAnnualizedText,
      priceChartMeasureDaysValue,
      priceChartMeasureDaysText,
      priceChartMeasureBarsValue,
      priceChartMeasureBarsText,
      priceChartMeasureSummaryItems,
      priceChartMeasureSummaryCardTitle,
      priceChartMeasureSummaryCardItems,
      activeWorkbenchPriceChartMeasurePoint,
      workbenchPriceChartMeasureStats,
      workbenchPriceChartMeasurePercentValue,
      workbenchPriceChartMeasurePercentText,
      workbenchPriceChartMeasureAnnualizedText,
      workbenchPriceChartMeasureDaysText,
      workbenchPriceChartMeasureModeLabel,
      workbenchPriceChartMeasureHint,
    };
  }

  window.createUsScreenerPriceChartViewModel = createUsScreenerPriceChartViewModel;
})();
