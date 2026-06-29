from __future__ import annotations

import contextlib
import html
import io
import math
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from core.weekly_review_rebalancing import is_buy_like_op, is_sell_like_op


TRADING_DAYS_PER_YEAR = 252
DEFAULT_FX = {
    "HKD": 1.0,
    "USD": 7.8,
    "CNY": 1.07,
    "EUR": 8.4,
    "JPY": 0.052,
    "KRW": 0.0056,
}
MANUAL_TICKER_ALIASES = {
    "NEBIUS": "SAMPLE",
}


def _week_end_date_text(week_id: Any) -> str:
    text = str(week_id or "").strip().upper()
    if "-W" not in text:
        return ""
    try:
        year_text, week_text = text.split("-W", 1)
        return pd.Timestamp.fromisocalendar(int(year_text), int(week_text), 5).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def _date_text_or_week_end(date_value: Any, week_id: Any) -> str:
    parsed = pd.to_datetime(date_value, errors="coerce")
    if not pd.isna(parsed):
        return pd.Timestamp(parsed).normalize().strftime("%Y-%m-%d")
    return _week_end_date_text(week_id)


def _canonical_key(value: Any) -> str:
    return str(value or "").strip().upper()


def _canonical_ticker(value: Any, aliases: Optional[Dict[str, str]] = None) -> str:
    raw = _canonical_key(value)
    if not raw:
        return ""
    lookup = {**MANUAL_TICKER_ALIASES, **(aliases or {})}
    seen = set()
    ticker = raw
    while ticker in lookup and ticker not in seen:
        seen.add(ticker)
        ticker = _canonical_key(lookup[ticker])
    return ticker


def _register_ticker_alias(aliases: Dict[str, str], source: Any, target: Any) -> None:
    source_key = _canonical_key(source)
    target_key = _canonical_ticker(target, aliases)
    if source_key and target_key and source_key != target_key:
        aliases[source_key] = target_key


def _ticker_aliases_from_reviews(reviews: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    aliases = dict(MANUAL_TICKER_ALIASES)
    for review in reviews or []:
        if not isinstance(review, dict):
            continue
        for stock_id, payload in (review.get("stocks") or {}).items():
            if not isinstance(payload, dict):
                continue
            _register_ticker_alias(aliases, stock_id, payload.get("ticker"))
            _register_ticker_alias(aliases, payload.get("stock_id"), payload.get("ticker"))
        for item in review.get("closed_positions") or []:
            if not isinstance(item, dict):
                continue
            _register_ticker_alias(aliases, item.get("stock_id"), item.get("ticker"))
        for op in review.get("rebalancing_ops") or []:
            if not isinstance(op, dict):
                continue
            _register_ticker_alias(aliases, op.get("stock_id"), op.get("ticker"))
    return aliases


def _detect_currency(ticker: str) -> str:
    code = _canonical_key(ticker)
    if code.endswith(".HK"):
        return "HKD"
    if code.endswith((".SH", ".SZ", ".SS")):
        return "CNY"
    if code.endswith((".DE", ".AS", ".VI")):
        return "EUR"
    if code.endswith(".T"):
        return "JPY"
    if code.endswith((".KS", ".KQ")):
        return "KRW"
    return "USD"


def _fx_rate_for(review: Dict[str, Any], currency: str) -> float:
    ccy = _canonical_key(currency) or "USD"
    if ccy == "HKD":
        return 1.0
    key = {
        "USD": "usd_to_hkd",
        "CNY": "cny_to_hkd",
        "EUR": "eur_to_hkd",
        "JPY": "jpy_to_hkd",
        "KRW": "krw_to_hkd",
    }.get(ccy)
    if key:
        value = _safe_float((review or {}).get(key))
        if value is not None and value > 0:
            return value
    return DEFAULT_FX.get(ccy, DEFAULT_FX["USD"])


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _round(value: Optional[float], digits: int = 6) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def _series_from_rows(rows: Iterable[Dict[str, Any]], field: str) -> pd.Series:
    values: Dict[pd.Timestamp, float] = {}
    for row in rows or []:
        date_text = str((row or {}).get("date") or "").strip()
        if not date_text:
            continue
        value = _safe_float((row or {}).get(field))
        if value is None:
            continue
        try:
            values[pd.Timestamp(date_text)] = value
        except (TypeError, ValueError):
            continue
    if not values:
        return pd.Series(dtype=float)
    return pd.Series(values).sort_index().astype(float)


def _data_quality_from_rows(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    seen: Dict[pd.Timestamp, int] = {}
    invalid_date_count = 0
    missing_date_count = 0
    row_count = 0
    for row in rows or []:
        row_count += 1
        date_text = str((row or {}).get("date") or "").strip()
        if not date_text:
            missing_date_count += 1
            continue
        try:
            timestamp = pd.Timestamp(date_text)
        except (TypeError, ValueError):
            invalid_date_count += 1
            continue
        seen[timestamp] = seen.get(timestamp, 0) + 1
    duplicate_dates = [index.strftime("%Y-%m-%d") for index, count in sorted(seen.items()) if count > 1]
    return {
        "row_count": row_count,
        "invalid_date_row_count": invalid_date_count,
        "missing_date_row_count": missing_date_count,
        "duplicate_date_count": len(duplicate_dates),
        "duplicate_dates": duplicate_dates[:20],
    }


def _data_quality_notes(data_quality: Dict[str, Any]) -> List[str]:
    notes: List[str] = []
    invalid = int(data_quality.get("invalid_date_row_count") or 0)
    missing = int(data_quality.get("missing_date_row_count") or 0)
    duplicate_count = int(data_quality.get("duplicate_date_count") or 0)
    if invalid or missing:
        notes.append(f"Skipped {invalid + missing} daily rows with missing or invalid dates before calculating metrics.")
    if duplicate_count:
        sample = ", ".join(str(value) for value in (data_quality.get("duplicate_dates") or [])[:5])
        suffix = f": {sample}" if sample else ""
        notes.append(f"Detected {duplicate_count} duplicate daily dates{suffix}; later rows are used for deterministic analytics.")
    return notes


def _benchmark_period_returns(rows: List[Dict[str, Any]]) -> pd.Series:
    cumulative = _series_from_rows(rows, "benchmark_return")
    if cumulative.empty:
        return pd.Series(dtype=float)
    wealth = 1.0 + cumulative
    returns = wealth.pct_change()
    if len(returns):
        returns.iloc[0] = 0.0
    return returns.replace([np.inf, -np.inf], np.nan).dropna()


def quantstats_return_series_from_rows(rows: Iterable[Dict[str, Any]]) -> tuple[pd.Series, pd.Series]:
    source_rows = list(rows or [])
    returns = _series_from_rows(source_rows, "period_return").dropna()
    benchmark_returns = _benchmark_period_returns(source_rows)
    if len(returns) > 1 and abs(float(returns.iloc[0])) <= 1e-12:
        returns = returns.iloc[1:]
    benchmark_returns = benchmark_returns.reindex(returns.index).dropna()
    returns, benchmark_returns = returns.align(benchmark_returns, join="inner")
    return returns.astype(float), benchmark_returns.astype(float)


def _format_report_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.4f}"
    if isinstance(value, int):
        return f"{value:,}"
    if value is None:
        return "-"
    return str(value)


def _fallback_quantstats_full_report(analytics: Dict[str, Any], benchmark: str) -> str:
    lines = [
        "QuantStats-style Full Report",
        f"Benchmark: {benchmark}",
        "Generated without optional quantstats dependency.",
        "",
        "== Metrics ==",
    ]
    for key, value in (analytics.get("metrics") or {}).items():
        lines.append(f"{key}: {_format_report_value(value)}")
    lines.extend(["", "== Relative =="])
    for key, value in (analytics.get("relative") or {}).items():
        lines.append(f"{key}: {_format_report_value(value)}")
    review = analytics.get("review") or {}
    lines.extend(["", "== Review =="])
    for key in ("headline", "risk_label", "quality_label"):
        lines.append(f"{key}: {_format_report_value(review.get(key))}")
    for note in review.get("notes") or []:
        lines.append(f"- {note}")
    if analytics.get("metric_notes"):
        lines.extend(["", "== Metric Notes =="])
        for note in analytics.get("metric_notes") or []:
            lines.append(f"- {note}")
    data_quality = analytics.get("data_quality") or {}
    if data_quality:
        lines.extend(["", "== Data Quality =="])
        for key, value in data_quality.items():
            lines.append(f"{key}: {_format_report_value(value)}")
    dashboard = analytics.get("dashboard") or {}
    trade_summary = ((dashboard.get("trade_journal") or {}).get("summary") or {})
    if trade_summary:
        lines.extend(["", "== Trade Journal =="])
        for key, value in trade_summary.items():
            lines.append(f"{key}: {_format_report_value(value)}")
    security_summary = ((dashboard.get("security_performance") or {}).get("summary") or {})
    if security_summary:
        lines.extend(["", "== Security Performance =="])
        for key, value in security_summary.items():
            lines.append(f"{key}: {_format_report_value(value)}")
    lines.extend(["", "== Monthly Returns =="])
    for row in analytics.get("monthly_returns") or []:
        lines.append(f"{row.get('month')}: {_format_report_value(row.get('return_pct'))}%")
    drawdowns = (analytics.get("drawdown_series") or [])[-20:]
    lines.extend(["", "== Recent Drawdowns =="])
    for row in drawdowns:
        lines.append(f"{row.get('date')}: {_format_report_value(row.get('drawdown_pct'))}%")
    lines.append("")
    return "\n".join(lines)


def _fallback_quantstats_html_report(analytics: Dict[str, Any], benchmark: str) -> str:
    metrics = analytics.get("metrics") or {}
    relative = analytics.get("relative") or {}
    review = analytics.get("review") or {}
    tear_sheet = analytics.get("tear_sheet") or {}
    data_quality = analytics.get("data_quality") or {}
    dashboard = analytics.get("dashboard") or {}

    def metric_card(label: str, value: Any, note: str = "") -> str:
        return (
            "<div class=\"metric-card\">"
            f"<div class=\"metric-label\">{html.escape(label)}</div>"
            f"<div class=\"metric-value\">{html.escape(_format_report_value(value))}</div>"
            f"<div class=\"metric-note\">{html.escape(note)}</div>"
            "</div>"
        )

    section_html = []
    for section in tear_sheet.get("sections") or []:
        cards = [
            metric_card(str(row.get("label") or ""), row.get("value"), str(row.get("note") or ""))
            for row in section.get("metrics") or []
        ]
        section_html.append(
            "<section>"
            f"<h2>{html.escape(str(section.get('title') or 'Section'))}</h2>"
            f"<p>{html.escape(str(section.get('description') or ''))}</p>"
            f"<div class=\"metrics-grid\">{''.join(cards)}</div>"
            "</section>"
        )

    monthly_rows = "".join(
        f"<tr><td>{html.escape(str(row.get('month') or ''))}</td><td>{html.escape(_format_report_value(row.get('return_pct')))}%</td></tr>"
        for row in analytics.get("monthly_returns") or []
    )
    drawdown_rows = "".join(
        f"<tr><td>{html.escape(str(row.get('date') or ''))}</td><td>{html.escape(_format_report_value(row.get('drawdown_pct')))}%</td></tr>"
        for row in (analytics.get("drawdown_series") or [])[-25:]
    )
    notes = "".join(f"<li>{html.escape(str(note))}</li>" for note in review.get("notes") or [])
    metric_notes = "".join(f"<li>{html.escape(str(note))}</li>" for note in analytics.get("metric_notes") or [])
    data_quality_rows = "".join(
        f"<tr><td>{html.escape(str(key))}</td><td>{html.escape(_format_report_value(value))}</td></tr>"
        for key, value in data_quality.items()
    )

    def summary_rows(module_key: str) -> str:
        summary = ((dashboard.get(module_key) or {}).get("summary") or {})
        return "".join(
            f"<tr><td>{html.escape(str(key))}</td><td>{html.escape(_format_report_value(value))}</td></tr>"
            for key, value in summary.items()
        )

    trade_rows = summary_rows("trade_journal")
    security_rows = summary_rows("security_performance")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>QuantStats-style Portfolio Tear Sheet</title>
  <style>
    body {{ margin: 0; background: #f8fafc; color: #1f2937; font-family: Arial, sans-serif; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 32px 24px 48px; }}
    header {{ border-bottom: 1px solid #e5e7eb; margin-bottom: 24px; padding-bottom: 18px; }}
    .eyebrow {{ color: #64748b; font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
    h1 {{ margin: 8px 0 8px; font-size: 30px; line-height: 1.15; }}
    h2 {{ margin: 0 0 8px; font-size: 18px; }}
    p {{ color: #64748b; line-height: 1.55; }}
    section {{ margin-top: 18px; border: 1px solid #e5e7eb; border-radius: 10px; background: white; padding: 18px; }}
    .metrics-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .metric-card {{ border: 1px solid #edf2f7; border-radius: 8px; padding: 12px; background: #fbfdff; }}
    .metric-label {{ color: #64748b; font-size: 12px; }}
    .metric-value {{ margin-top: 6px; font-size: 22px; font-weight: 700; color: #0f172a; }}
    .metric-note {{ margin-top: 4px; color: #94a3b8; font-size: 11px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 8px 10px; border-bottom: 1px solid #edf2f7; text-align: left; }}
    th {{ color: #64748b; font-size: 11px; text-transform: uppercase; }}
    .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
    @media (max-width: 800px) {{ .metrics-grid, .two-col {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <div class="eyebrow">Generated without optional quantstats dependency</div>
    <h1>QuantStats-style Portfolio Tear Sheet</h1>
    <p>Benchmark: {html.escape(str(benchmark))}. This report is generated from the local daily TWR ledger and benchmark series.</p>
  </header>
  <section>
    <h2>{html.escape(str(review.get('headline') or 'Portfolio Review'))}</h2>
    <ul>{notes}</ul>
    <ul>{metric_notes}</ul>
  </section>
  <section>
    <h2>Headline Metrics</h2>
    <div class="metrics-grid">
      {metric_card('Cumulative Return %', metrics.get('cumulative_return_pct'))}
      {metric_card('Sharpe', metrics.get('sharpe'))}
      {metric_card('Volatility %', metrics.get('volatility_annualized_pct'), 'annualized')}
      {metric_card('Active Return ppt', relative.get('active_return_ppt'), str(relative.get('benchmark') or benchmark))}
    </div>
  </section>
  {''.join(section_html)}
  <div class="two-col">
    <section><h2>Data Quality</h2><table><tbody>{data_quality_rows}</tbody></table></section>
    <section><h2>Trade Journal</h2><table><tbody>{trade_rows}</tbody></table></section>
  </div>
  <section><h2>Security Performance</h2><table><tbody>{security_rows}</tbody></table></section>
  <div class="two-col">
    <section><h2>Monthly Returns</h2><table><thead><tr><th>Month</th><th>Return</th></tr></thead><tbody>{monthly_rows}</tbody></table></section>
    <section><h2>Recent Drawdowns</h2><table><thead><tr><th>Date</th><th>Drawdown</th></tr></thead><tbody>{drawdown_rows}</tbody></table></section>
  </div>
</main>
</body>
</html>"""


def _fallback_quantstats_report(
    series_rows: Iterable[Dict[str, Any]],
    benchmark: str,
    report_type: str,
    analytics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    analytics = analytics or build_portfolio_quant_analytics(series_rows, benchmark=benchmark)
    if not analytics.get("success"):
        return {
            "success": False,
            "error": analytics.get("error") or "insufficient_return_series",
            "message": "Need enough daily TWR returns to generate a fallback QuantStats-style report.",
        }
    if report_type == "html":
        return {
            "success": True,
            "content": _fallback_quantstats_html_report(analytics, benchmark),
            "mimetype": "text/html; charset=utf-8",
            "extension": "html",
            "source": "fallback",
        }
    return {
        "success": True,
        "content": _fallback_quantstats_full_report(analytics, benchmark),
        "mimetype": "text/plain; charset=utf-8",
        "extension": "txt",
        "source": "fallback",
    }


def generate_quantstats_report(
    series_rows: Iterable[Dict[str, Any]],
    *,
    benchmark: str = "QQQ",
    report_type: str = "html",
    analytics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    selected_type = str(report_type or "html").strip().lower()
    if selected_type not in {"html", "full"}:
        return {"success": False, "error": "unsupported_report_type", "message": f"Unsupported QuantStats report type: {report_type}"}
    try:
        import quantstats as qs  # type: ignore
    except Exception:
        return _fallback_quantstats_report(series_rows, str(benchmark or "QQQ").strip().upper() or "QQQ", selected_type, analytics)

    returns, benchmark_returns = quantstats_return_series_from_rows(series_rows)
    if len(returns) < 2:
        return {"success": False, "error": "insufficient_return_series", "message": "Need at least two daily TWR returns to generate a QuantStats report."}

    title = f"Portfolio vs {str(benchmark or 'benchmark').upper()} QuantStats Report"
    try:
        if selected_type == "html":
            with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
                output_path = Path(tmp.name)
            try:
                qs.reports.html(returns, benchmark=benchmark_returns, title=title, output=str(output_path))
                content = output_path.read_text(encoding="utf-8", errors="replace")
            finally:
                with contextlib.suppress(FileNotFoundError):
                    output_path.unlink()
            return {"success": True, "content": content, "mimetype": "text/html; charset=utf-8", "extension": "html"}

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            qs.reports.full(returns, benchmark=benchmark_returns)
        content = buffer.getvalue()
        if not content.strip():
            content = "QuantStats full report generated no textual stdout. Use the HTML tear sheet download for plots.\n"
        return {"success": True, "content": content, "mimetype": "text/plain; charset=utf-8", "extension": "txt"}
    except Exception:
        return _fallback_quantstats_report(series_rows, str(benchmark or "QQQ").strip().upper() or "QQQ", selected_type, analytics)


def _max_drawdown(returns: pd.Series) -> tuple[Optional[float], List[Dict[str, Any]]]:
    if returns.empty:
        return None, []
    wealth = (1.0 + returns).cumprod()
    peaks = wealth.cummax()
    drawdowns = wealth / peaks - 1.0
    rows = [
        {"date": index.strftime("%Y-%m-%d"), "drawdown_pct": round(float(value) * 100.0, 4)}
        for index, value in drawdowns.items()
    ]
    return float(drawdowns.min()), rows


def _best_worst_day(returns: pd.Series, *, best: bool) -> Optional[Dict[str, Any]]:
    if returns.empty:
        return None
    index = returns.idxmax() if best else returns.idxmin()
    value = float(returns.loc[index])
    return {"date": index.strftime("%Y-%m-%d"), "return_pct": round(value * 100.0, 4)}


def _monthly_returns(returns: pd.Series) -> List[Dict[str, Any]]:
    if returns.empty:
        return []
    monthly = (1.0 + returns).resample("ME").prod() - 1.0
    return [
        {
            "month": index.strftime("%Y-%m"),
            "return_pct": round(float(value) * 100.0, 4),
        }
        for index, value in monthly.dropna().items()
    ]


def _monthly_heatmap_rows(monthly_returns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in monthly_returns:
        month_text = str(row.get("month") or "")
        try:
            timestamp = pd.Timestamp(f"{month_text}-01")
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "year": int(timestamp.year),
                "month": int(timestamp.month),
                "month_label": timestamp.strftime("%b"),
                "return_pct": _round(_safe_float(row.get("return_pct")), 4),
            }
        )
    return rows


def _yearly_returns(monthly_returns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_year: Dict[int, List[float]] = {}
    for row in monthly_returns:
        month_text = str(row.get("month") or "")
        value = _safe_float(row.get("return_pct"))
        if value is None:
            continue
        try:
            year = int(pd.Timestamp(f"{month_text}-01").year)
        except (TypeError, ValueError):
            continue
        by_year.setdefault(year, []).append(value / 100.0)
    return [
        {
            "year": year,
            "return_pct": _round(((float(np.prod([1.0 + value for value in values])) - 1.0) * 100.0), 4),
        }
        for year, values in sorted(by_year.items())
    ]


def _annualized_return(returns: pd.Series) -> Optional[float]:
    if returns.empty:
        return None
    wealth = float((1.0 + returns).prod())
    if wealth <= 0:
        return None
    years = len(returns) / TRADING_DAYS_PER_YEAR
    if years <= 0:
        return None
    return wealth ** (1.0 / years) - 1.0


def _information_ratio(active_returns: pd.Series) -> Optional[float]:
    active = active_returns.dropna()
    if len(active) < 2:
        return None
    tracking_error = float(active.std(ddof=1))
    if tracking_error <= 0:
        return None
    return float(active.mean() / tracking_error * math.sqrt(TRADING_DAYS_PER_YEAR))


def _tail_risk_metrics(returns: pd.Series) -> Dict[str, Optional[float]]:
    cleaned = returns.dropna()
    if cleaned.empty:
        return {
            "var_95_pct": None,
            "cvar_95_pct": None,
            "tail_ratio": None,
            "skew": None,
            "kurtosis": None,
            "gain_to_pain": None,
            "profit_factor": None,
            "payoff_ratio": None,
        }
    var_95 = float(cleaned.quantile(0.05))
    cvar_values = cleaned[cleaned <= var_95]
    cvar_95 = float(cvar_values.mean()) if not cvar_values.empty else var_95
    left_tail = abs(float(cleaned.quantile(0.05)))
    right_tail = abs(float(cleaned.quantile(0.95)))
    tail_ratio = right_tail / left_tail if left_tail > 0 else None
    positive = cleaned[cleaned > 0]
    negative = cleaned[cleaned < 0]
    pain = abs(float(negative.sum()))
    gains = float(positive.sum())
    avg_gain = float(positive.mean()) if not positive.empty else None
    avg_loss = abs(float(negative.mean())) if not negative.empty else None
    return {
        "var_95_pct": _round(var_95 * 100.0, 4),
        "cvar_95_pct": _round(cvar_95 * 100.0, 4),
        "tail_ratio": _round(tail_ratio, 4),
        "skew": _round(float(cleaned.skew()) if len(cleaned) >= 3 else None, 4),
        "kurtosis": _round(float(cleaned.kurt()) if len(cleaned) >= 4 else None, 4),
        "gain_to_pain": _round(gains / pain if pain > 0 else None, 4),
        "profit_factor": _round(gains / pain if pain > 0 else None, 4),
        "payoff_ratio": _round(avg_gain / avg_loss if avg_gain is not None and avg_loss and avg_loss > 0 else None, 4),
    }


def _drawdown_periods(drawdown_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    episodes: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for row in drawdown_rows:
        date = str(row.get("date") or "")
        drawdown = _safe_float(row.get("drawdown_pct"))
        if not date or drawdown is None:
            continue
        if drawdown < 0 and current is None:
            current = {
                "start_date": date,
                "trough_date": date,
                "end_date": None,
                "max_drawdown_pct": drawdown,
                "duration_days": 1,
            }
            continue
        if current is not None:
            current["duration_days"] = int(current.get("duration_days") or 0) + 1
            if drawdown < float(current.get("max_drawdown_pct") or 0):
                current["max_drawdown_pct"] = drawdown
                current["trough_date"] = date
            if drawdown >= 0:
                current["end_date"] = date
                episodes.append(current)
                current = None
    if current is not None:
        current["end_date"] = None
        episodes.append(current)
    episodes.sort(key=lambda item: float(item.get("max_drawdown_pct") or 0))
    return episodes[:10]


def _weekly_heatmap_rows(returns: pd.Series) -> List[Dict[str, Any]]:
    cleaned = returns.dropna()
    if cleaned.empty:
        return []
    rows: List[Dict[str, Any]] = []
    frame = pd.DataFrame({"return": cleaned})
    frame["week_start"] = frame.index.to_period("W-MON").start_time
    for week_start, group in frame.groupby("week_start", sort=True):
        if group.empty:
            continue
        linked_return = float((1.0 + group["return"]).prod() - 1.0)
        week_start_ts = pd.Timestamp(week_start).normalize()
        week_end_ts = pd.Timestamp(group.index.max()).normalize()
        iso = week_end_ts.isocalendar()
        rows.append(
            {
                "week_id": f"{int(iso.year)}-W{int(iso.week):02d}",
                "start_date": week_start_ts.strftime("%Y-%m-%d"),
                "end_date": week_end_ts.strftime("%Y-%m-%d"),
                "return_pct": _round(linked_return * 100.0, 4),
                "trading_days": int(len(group)),
            }
        )
    return rows


def _drawdown_episode_markers(drawdown_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index, episode in enumerate(_drawdown_periods(drawdown_rows)):
        trough_date = str(episode.get("trough_date") or "")
        if not trough_date:
            continue
        rows.append(
            {
                "episode_index": index,
                "date": trough_date,
                "drawdown_pct": _round(_safe_float(episode.get("max_drawdown_pct")), 4),
                "start_date": episode.get("start_date"),
                "end_date": episode.get("end_date"),
                "duration_days": episode.get("duration_days"),
            }
        )
    return rows


def _visualization_payload(returns: pd.Series, drawdown_rows: List[Dict[str, Any]], analytics: Dict[str, Any]) -> Dict[str, Any]:
    dashboard = analytics.get("dashboard") or {}
    security_rows = ((dashboard.get("holding_trade_stats") or {}).get("security_performance") or {}).get("rows") or []
    contribution_bars = []
    for row in security_rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or row.get("stock_id") or "").strip().upper()
        pnl = _safe_float(row.get("total_pnl_hkd"))
        if not ticker or pnl is None:
            continue
        contribution_bars.append(
            {
                "ticker": ticker,
                "value_hkd": _round(pnl, 2),
                "return_pct": _round(_safe_float(row.get("total_return_pct")), 4),
                "status": row.get("status") or "",
            }
        )
    contribution_bars.sort(key=lambda item: abs(float(item.get("value_hkd") or 0.0)), reverse=True)
    return {
        "weekly_heatmap": _weekly_heatmap_rows(returns),
        "drawdown_episode_markers": _drawdown_episode_markers(drawdown_rows),
        "contribution_bars": contribution_bars[:12],
    }


def _drawdown_episode_context(
    episode: Dict[str, Any],
    source_rows: List[Dict[str, Any]],
    drawdown_rows: List[Dict[str, Any]],
    trade_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    start = pd.to_datetime(episode.get("start_date"), errors="coerce")
    end = pd.to_datetime(episode.get("end_date") or episode.get("trough_date"), errors="coerce")
    if pd.isna(start) or pd.isna(end):
        return {
            "rows": [],
            "flow_rows": [],
            "trade_rows": [],
            "path_length": 0,
            "peak_to_trough_days": None,
        }

    start_day = pd.Timestamp(start).normalize()
    end_day = pd.Timestamp(end).normalize()
    source_lookup = {str(row.get("date") or ""): row for row in source_rows or [] if str(row.get("date") or "")}
    drawdown_lookup = {str(row.get("date") or ""): row for row in drawdown_rows or [] if str(row.get("date") or "")}
    path_rows: List[Dict[str, Any]] = []
    flow_rows: List[Dict[str, Any]] = []
    trade_context: List[Dict[str, Any]] = []
    for row in source_rows or []:
        date_text = str(row.get("date") or "")
        if not date_text:
            continue
        day = pd.to_datetime(date_text, errors="coerce")
        if pd.isna(day):
            continue
        day = pd.Timestamp(day).normalize()
        if day < start_day or day > end_day:
            continue
        drawdown_row = drawdown_lookup.get(date_text, {})
        path_rows.append(
            {
                "date": date_text,
                "portfolio_value_hkd": _round(_safe_float(row.get("portfolio_value_hkd")), 2),
                "market_value_hkd": _round(_safe_float(row.get("market_value_hkd")), 2),
                "cash_balance_hkd": _round(_safe_float(row.get("cash_balance_hkd")), 2),
                "portfolio_twr_pct": _round((_safe_float(row.get("portfolio_twr")) or 0.0) * 100.0, 4),
                "period_return_pct": _round((_safe_float(row.get("period_return")) or 0.0) * 100.0, 4),
                "benchmark_return_pct": _round((_safe_float(row.get("benchmark_return")) or 0.0) * 100.0, 4),
                "drawdown_pct": _round(_safe_float(drawdown_row.get("drawdown_pct")), 4),
                "explicit_cash_flow_hkd": _round(_safe_float(row.get("explicit_cash_flow_hkd")), 2),
                "implied_cash_flow_hkd": _round(_safe_float(row.get("implied_cash_flow_hkd")), 2),
                "internal_rebalancing_cash_hkd": _round(_safe_float(row.get("internal_rebalancing_cash_hkd")), 2),
            }
        )
        flow_value = (_safe_float(row.get("explicit_cash_flow_hkd")) or 0.0) + (_safe_float(row.get("implied_cash_flow_hkd")) or 0.0)
        if abs(flow_value) > 0.01 or abs(_safe_float(row.get("internal_rebalancing_cash_hkd")) or 0.0) > 0.01:
            flow_rows.append(
                {
                    "date": date_text,
                    "explicit_cash_flow_hkd": _round(_safe_float(row.get("explicit_cash_flow_hkd")), 2),
                    "implied_cash_flow_hkd": _round(_safe_float(row.get("implied_cash_flow_hkd")), 2),
                    "internal_rebalancing_cash_hkd": _round(_safe_float(row.get("internal_rebalancing_cash_hkd")), 2),
                    "twr_external_cash_flow_hkd": _round(_safe_float(row.get("twr_external_cash_flow_hkd")), 2),
                    "portfolio_value_hkd": _round(_safe_float(row.get("portfolio_value_hkd")), 2),
                    "cash_balance_hkd": _round(_safe_float(row.get("cash_balance_hkd")), 2),
                }
            )
    for trade in trade_rows or []:
        entry_date = pd.to_datetime(trade.get("entry_date") or trade.get("date"), errors="coerce")
        exit_date = pd.to_datetime(trade.get("exit_date") or trade.get("date"), errors="coerce")
        if pd.isna(entry_date) and pd.isna(exit_date):
            continue
        trade_day = pd.Timestamp((exit_date if not pd.isna(exit_date) else entry_date)).normalize()
        if trade_day < start_day or trade_day > end_day:
            continue
        trade_context.append(
            {
                "ticker": trade.get("ticker"),
                "status": trade.get("status"),
                "entry_date": trade.get("entry_date"),
                "exit_date": trade.get("exit_date"),
                "quantity": _round(_safe_float(trade.get("quantity")), 4),
                "entry_value_hkd": _round(_safe_float(trade.get("entry_value_hkd")), 2),
                "exit_value_hkd": _round(_safe_float(trade.get("exit_value_hkd")), 2),
                "pnl_hkd": _round(_safe_float(trade.get("pnl_hkd")), 2),
                "return_pct": _round(_safe_float(trade.get("return_pct")), 4),
            }
        )
    return {
        "rows": path_rows,
        "flow_rows": flow_rows,
        "trade_rows": trade_context,
        "path_length": len(path_rows),
        "peak_to_trough_days": int(episode.get("duration_days") or 0),
    }


def _risk_regime_state_machine(
    source_rows: List[Dict[str, Any]],
    returns: pd.Series,
    benchmark_returns: pd.Series,
    drawdown_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if returns.empty or len(source_rows) < 2:
        return {
            "title": "Risk Regime",
            "summary": {
                "current_regime": "neutral",
                "current_regime_label": "Neutral",
                "transition_count": 0,
                "point_count": 0,
            },
            "rows": [],
            "notes": ["Need at least two daily rows to classify risk regime."],
        }

    rolling_window = min(20, max(5, len(returns)))
    rolling_sharpe, _, rolling_beta = _rolling_ratio_rows(returns, benchmark_returns, window=rolling_window)
    rolling_active_risk = _rolling_active_risk_rows(returns, benchmark_returns, window=rolling_window)
    drawdown_map = {str(row.get("date") or ""): _safe_float(row.get("drawdown_pct")) for row in drawdown_rows or []}
    sharpe_map = {str(row.get("date") or ""): _safe_float(row.get("value")) for row in rolling_sharpe or []}
    beta_map = {str(row.get("date") or ""): _safe_float(row.get("value")) for row in rolling_beta or []}
    te_map = {str(row.get("date") or ""): _safe_float(row.get("value")) for row in rolling_active_risk.get("rolling_tracking_error") or []}
    corr_map = {str(row.get("date") or ""): _safe_float(row.get("value")) for row in rolling_active_risk.get("rolling_correlation") or []}

    def classify(drawdown_pct: Optional[float], sharpe: Optional[float], beta: Optional[float], tracking_error: Optional[float], corr: Optional[float]) -> tuple[str, int, str]:
        score = 50
        reasons: List[str] = []
        if drawdown_pct is not None:
            if drawdown_pct <= -12:
                score -= 35
                reasons.append("deep drawdown")
            elif drawdown_pct <= -6:
                score -= 18
                reasons.append("material drawdown")
            elif drawdown_pct >= -2:
                score += 8
        if sharpe is not None:
            if sharpe >= 1.0:
                score += 18
                reasons.append("strong rolling Sharpe")
            elif sharpe >= 0.4:
                score += 8
            elif sharpe <= 0.0:
                score -= 18
                reasons.append("weak rolling Sharpe")
        if beta is not None:
            if beta >= 1.2:
                score -= 8
                reasons.append("high beta")
            elif beta <= 0.8:
                score += 5
        if tracking_error is not None:
            if tracking_error >= 3.5:
                score -= 12
                reasons.append("high tracking error")
            elif tracking_error <= 1.5:
                score += 6
        if corr is not None:
            if corr <= 0.35:
                score -= 10
                reasons.append("low correlation")
            elif corr >= 0.7:
                score += 5

        if (drawdown_pct is not None and drawdown_pct <= -12) or (sharpe is not None and sharpe <= -0.5) or (tracking_error is not None and tracking_error >= 4.5):
            return "stress", max(0, min(100, score)), ", ".join(reasons) or "stress conditions"
        if (drawdown_pct is not None and drawdown_pct <= -6) or (sharpe is not None and sharpe <= 0.2) or (beta is not None and beta >= 1.15):
            return "defensive", max(0, min(100, score)), ", ".join(reasons) or "defensive posture"
        if (sharpe is not None and sharpe >= 1.0) and (drawdown_pct is None or drawdown_pct >= -4) and (tracking_error is None or tracking_error <= 2.5):
            return "risk_on", max(0, min(100, score)), ", ".join(reasons) or "risk-on posture"
        return "neutral", max(0, min(100, score)), ", ".join(reasons) or "balanced posture"

    rows: List[Dict[str, Any]] = []
    previous_regime = ""
    transition_count = 0
    for row in source_rows:
        date_text = str(row.get("date") or "")
        if not date_text:
            continue
        regime, score, reason = classify(
            drawdown_map.get(date_text),
            sharpe_map.get(date_text),
            beta_map.get(date_text),
            te_map.get(date_text),
            corr_map.get(date_text),
        )
        if previous_regime and regime != previous_regime:
            transition_count += 1
        previous_regime = regime
        rows.append(
            {
                "date": date_text,
                "regime": regime,
                "regime_label": regime.replace("_", " ").title(),
                "score": score,
                "drawdown_pct": _round(drawdown_map.get(date_text), 4),
                "rolling_sharpe": _round(sharpe_map.get(date_text), 4),
                "rolling_beta": _round(beta_map.get(date_text), 4),
                "tracking_error_pct": _round(te_map.get(date_text), 4),
                "correlation": _round(corr_map.get(date_text), 4),
                "reason": reason,
            }
        )

    current = rows[-1] if rows else {}
    regime_counts: Dict[str, int] = {}
    for row in rows:
        regime_counts[row["regime"]] = regime_counts.get(row["regime"], 0) + 1

    return {
        "title": "Risk Regime",
        "summary": {
            "current_regime": current.get("regime") or "neutral",
            "current_regime_label": current.get("regime_label") or "Neutral",
            "current_score": current.get("score"),
            "transition_count": transition_count,
            "point_count": len(rows),
            "regime_counts": regime_counts,
            "latest_reason": current.get("reason") or "",
        },
        "rows": rows[-120:],
        "notes": [
            "Regime labels are derived from rolling Sharpe, beta, tracking error, correlation, and drawdown.",
            "Stress is only assigned when multiple adverse signals align; otherwise the panel stays neutral or defensive.",
        ],
    }


def _return_distribution(returns: pd.Series, buckets: int = 12) -> List[Dict[str, Any]]:
    cleaned = returns.dropna()
    if cleaned.empty:
        return []
    values = cleaned.to_numpy(dtype=float) * 100.0
    if np.allclose(values.min(), values.max()):
        label = f"{values.min():+.2f}%"
        return [{"bucket": label, "count": int(len(values))}]
    counts, edges = np.histogram(values, bins=buckets)
    rows: List[Dict[str, Any]] = []
    for idx, count in enumerate(counts):
        rows.append(
            {
                "bucket": f"{edges[idx]:+.2f}% to {edges[idx + 1]:+.2f}%",
                "bucket_mid_pct": round(float((edges[idx] + edges[idx + 1]) / 2.0), 4),
                "count": int(count),
            }
        )
    return rows


def _top_period_rows(returns: pd.Series, benchmark_returns: pd.Series, *, best: bool, limit: int = 10) -> List[Dict[str, Any]]:
    cleaned = returns.dropna()
    if cleaned.empty:
        return []
    ordered = cleaned.sort_values(ascending=not best).head(limit)
    benchmark_aligned = benchmark_returns.reindex(cleaned.index)
    rows: List[Dict[str, Any]] = []
    for index, value in ordered.items():
        benchmark_value = _safe_float(benchmark_aligned.get(index))
        portfolio_pct = float(value) * 100.0
        benchmark_pct = None if benchmark_value is None else benchmark_value * 100.0
        rows.append(
            {
                "date": index.strftime("%Y-%m-%d"),
                "portfolio_return_pct": round(portfolio_pct, 4),
                "benchmark_return_pct": _round(benchmark_pct, 4),
                "active_return_ppt": _round(None if benchmark_pct is None else portfolio_pct - benchmark_pct, 4),
            }
        )
    return rows


def _series_exposure_rows(rows: List[Dict[str, Any]], returns: pd.Series) -> tuple[Dict[str, Optional[float]], List[Dict[str, Any]]]:
    by_date: Dict[pd.Timestamp, Dict[str, Any]] = {}
    for row in rows or []:
        date_text = str((row or {}).get("date") or "").strip()
        if not date_text:
            continue
        try:
            by_date[pd.Timestamp(date_text)] = row or {}
        except (TypeError, ValueError):
            continue

    exposure_rows: List[Dict[str, Any]] = []
    cash_pcts: List[float] = []
    invested_pcts: List[float] = []
    for index in returns.index:
        row = by_date.get(index, {})
        end_value = _safe_float(row.get("end_value"))
        if end_value is None:
            end_value = _safe_float(row.get("portfolio_value_hkd"))
        cash = _safe_float(row.get("cash_balance"))
        if cash is None:
            cash = _safe_float(row.get("cash_balance_hkd"))
        if end_value is None or end_value <= 0 or cash is None:
            exposure_rows.append({"date": index.strftime("%Y-%m-%d"), "cash_pct": None, "invested_pct": None})
            continue
        cash_pct = cash / end_value * 100.0
        invested_pct = 100.0 - cash_pct
        cash_pcts.append(cash_pct)
        invested_pcts.append(invested_pct)
        exposure_rows.append(
            {
                "date": index.strftime("%Y-%m-%d"),
                "cash_pct": round(cash_pct, 4),
                "invested_pct": round(invested_pct, 4),
            }
        )

    average_cash = float(np.mean(cash_pcts)) if cash_pcts else None
    average_exposure = float(np.mean(invested_pcts)) if invested_pcts else None
    cumulative_return = float((1.0 + returns.dropna()).prod() - 1.0) if not returns.dropna().empty else None
    invested_return = None
    if cumulative_return is not None and average_exposure is not None and average_exposure > 0:
        invested_return = cumulative_return / (average_exposure / 100.0)
    exposure = {
        "average_exposure_pct": _round(average_exposure, 4),
        "average_cash_pct": _round(average_cash, 4),
        "invested_capital_return_pct": _round(None if invested_return is None else invested_return * 100.0, 4),
        "cash_drag_ppt": _round(None if invested_return is None or cumulative_return is None else (invested_return - cumulative_return) * 100.0, 4),
    }
    return exposure, exposure_rows


def _rolling_ratio_rows(
    returns: pd.Series,
    benchmark_returns: pd.Series,
    *,
    window: int,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    sharpe_rows: List[Dict[str, Any]] = []
    sortino_rows: List[Dict[str, Any]] = []
    beta_rows: List[Dict[str, Any]] = []
    benchmark_aligned = benchmark_returns.reindex(returns.index)
    for end_index in range(len(returns)):
        start_index = max(0, end_index - window + 1)
        sample = returns.iloc[start_index : end_index + 1].dropna()
        if len(sample) < 2:
            continue
        date = returns.index[end_index].strftime("%Y-%m-%d")
        std = float(sample.std(ddof=1))
        sharpe = float(sample.mean() / std * math.sqrt(TRADING_DAYS_PER_YEAR)) if std > 0 else None
        if sharpe is not None and math.isfinite(sharpe):
            sharpe_rows.append({"date": date, "value": round(sharpe, 4)})
        downside = sample[sample < 0]
        if len(downside) >= 2:
            downside_std = float(downside.std(ddof=1))
            sortino = float(sample.mean() / downside_std * math.sqrt(TRADING_DAYS_PER_YEAR)) if downside_std > 0 else None
            if sortino is not None and math.isfinite(sortino):
                sortino_rows.append({"date": date, "value": round(sortino, 4)})
        benchmark_sample = benchmark_aligned.iloc[start_index : end_index + 1].dropna()
        aligned_returns, aligned_benchmark = sample.align(benchmark_sample, join="inner")
        if len(aligned_returns) >= 2:
            benchmark_var = float(aligned_benchmark.var(ddof=1))
            if benchmark_var > 0:
                covariance = float(aligned_returns.cov(aligned_benchmark))
                beta = covariance / benchmark_var
                if math.isfinite(beta):
                    beta_rows.append({"date": date, "value": round(beta, 4)})
    return sharpe_rows, sortino_rows, beta_rows


def _rolling_active_risk_rows(
    returns: pd.Series,
    benchmark_returns: pd.Series,
    *,
    window: int,
) -> Dict[str, List[Dict[str, Any]]]:
    portfolio, benchmark = returns.align(benchmark_returns, join="inner")
    portfolio = portfolio.dropna()
    benchmark = benchmark.dropna()
    portfolio, benchmark = portfolio.align(benchmark, join="inner")
    active = portfolio - benchmark
    rows = {
        "rolling_active_return": [],
        "rolling_tracking_error": [],
        "rolling_correlation": [],
        "rolling_information_ratio": [],
    }
    for end_index in range(len(active)):
        start_index = max(0, end_index - window + 1)
        sample = active.iloc[start_index : end_index + 1].dropna()
        portfolio_sample = portfolio.iloc[start_index : end_index + 1].dropna()
        benchmark_sample = benchmark.iloc[start_index : end_index + 1].dropna()
        if len(sample) < 2:
            continue
        date = active.index[end_index].strftime("%Y-%m-%d")
        active_linked = float((1.0 + sample).prod() - 1.0) * 100.0
        tracking_error = float(sample.std(ddof=1)) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0
        information_ratio = (float(sample.mean()) / float(sample.std(ddof=1)) * math.sqrt(TRADING_DAYS_PER_YEAR)) if float(sample.std(ddof=1)) > 0 else None
        correlation = None
        aligned_portfolio, aligned_benchmark = portfolio_sample.align(benchmark_sample, join="inner")
        if len(aligned_portfolio) >= 2:
            portfolio_std = float(aligned_portfolio.std(ddof=1))
            benchmark_std = float(aligned_benchmark.std(ddof=1))
            if portfolio_std > 0 and benchmark_std > 0:
                corr_value = float(aligned_portfolio.corr(aligned_benchmark))
                correlation = corr_value if math.isfinite(corr_value) else None
        rows["rolling_active_return"].append({"date": date, "value": round(active_linked, 4)})
        rows["rolling_tracking_error"].append({"date": date, "value": round(tracking_error, 4)})
        rows["rolling_correlation"].append({"date": date, "value": _round(correlation, 4)})
        rows["rolling_information_ratio"].append({"date": date, "value": _round(information_ratio, 4)})
    return rows


def _vol_matched_benchmark(
    returns: pd.Series,
    benchmark_returns: pd.Series,
) -> tuple[Dict[str, Optional[float]], List[Dict[str, Any]]]:
    portfolio, benchmark = returns.align(benchmark_returns, join="inner")
    portfolio = portfolio.dropna()
    benchmark = benchmark.dropna()
    portfolio, benchmark = portfolio.align(benchmark, join="inner")
    if len(portfolio) < 2 or len(benchmark) < 2:
        return {
            "vol_matched_benchmark_return_pct": None,
            "vol_matched_active_return_ppt": None,
            "vol_match_scale": None,
        }, []
    benchmark_std = float(benchmark.std(ddof=1))
    portfolio_std = float(portfolio.std(ddof=1))
    if benchmark_std <= 0:
        return {
            "vol_matched_benchmark_return_pct": None,
            "vol_matched_active_return_ppt": None,
            "vol_match_scale": None,
        }, []
    scale = portfolio_std / benchmark_std
    matched = benchmark * scale
    portfolio_cumulative = (1.0 + portfolio).cumprod() - 1.0
    matched_cumulative = (1.0 + matched).cumprod() - 1.0
    rows = []
    for index in portfolio.index:
        portfolio_pct = float(portfolio_cumulative.loc[index]) * 100.0
        matched_pct = float(matched_cumulative.loc[index]) * 100.0
        rows.append(
            {
                "date": index.strftime("%Y-%m-%d"),
                "portfolio_pct": round(portfolio_pct, 4),
                "vol_matched_benchmark_pct": round(matched_pct, 4),
                "active_ppt": round(portfolio_pct - matched_pct, 4),
            }
        )
    return {
        "vol_matched_benchmark_return_pct": _round(float(matched_cumulative.iloc[-1]) * 100.0, 4),
        "vol_matched_active_return_ppt": _round(float(portfolio_cumulative.iloc[-1] - matched_cumulative.iloc[-1]) * 100.0, 4),
        "vol_match_scale": _round(scale, 4),
    }, rows


def _strategy_tear_sheet_charts(
    source_rows: List[Dict[str, Any]],
    returns: pd.Series,
    benchmark_returns: pd.Series,
    monthly_returns: List[Dict[str, Any]],
    drawdown_rows: List[Dict[str, Any]],
    vol_matched_rows: List[Dict[str, Any]],
    exposure_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if returns.empty:
        return []

    benchmark_aligned = benchmark_returns.reindex(returns.index).fillna(0.0)
    portfolio_cumulative = (1.0 + returns).cumprod() - 1.0
    benchmark_cumulative = (1.0 + benchmark_aligned).cumprod() - 1.0
    cumulative_rows = []
    for index in returns.index:
        portfolio_pct = float(portfolio_cumulative.loc[index]) * 100.0
        benchmark_pct = float(benchmark_cumulative.loc[index]) * 100.0 if index in benchmark_cumulative.index else None
        cumulative_rows.append(
            {
                "date": index.strftime("%Y-%m-%d"),
                "portfolio_pct": round(portfolio_pct, 4),
                "benchmark_pct": _round(None if benchmark_pct is None else benchmark_pct, 4),
                "active_ppt": _round(None if benchmark_pct is None else portfolio_pct - benchmark_pct, 4),
            }
        )

    rolling_window = min(20, max(2, len(returns)))
    rolling_volatility = returns.rolling(rolling_window, min_periods=2).std(ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0
    rolling_rows = [
        {"date": index.strftime("%Y-%m-%d"), "volatility_pct": round(float(value), 4)}
        for index, value in rolling_volatility.dropna().items()
        if math.isfinite(float(value))
    ]
    rolling_sharpe, rolling_sortino, rolling_beta = _rolling_ratio_rows(
        returns,
        benchmark_returns,
        window=rolling_window,
    )
    rolling_active_risk = _rolling_active_risk_rows(
        returns,
        benchmark_returns,
        window=rolling_window,
    )

    return [
        {
            "key": "cumulative_returns",
            "title": "Cumulative Returns",
            "kind": "line",
            "rows": cumulative_rows,
        },
        {
            "key": "drawdown",
            "title": "Drawdown",
            "kind": "area",
            "rows": drawdown_rows,
        },
        {
            "key": "monthly_returns",
            "title": "Monthly Returns",
            "kind": "bar",
            "rows": monthly_returns,
        },
        {
            "key": "monthly_heatmap",
            "title": "Monthly Return Heatmap",
            "kind": "heatmap",
            "rows": _monthly_heatmap_rows(monthly_returns),
        },
        {
            "key": "yearly_returns",
            "title": "Yearly / YTD Returns",
            "kind": "bar",
            "rows": _yearly_returns(monthly_returns),
        },
        {
            "key": "rolling_volatility",
            "title": f"Rolling Volatility ({rolling_window}D)",
            "kind": "line",
            "rows": rolling_rows,
        },
        {
            "key": "rolling_sharpe",
            "title": f"Rolling Sharpe ({rolling_window}D)",
            "kind": "line",
            "rows": rolling_sharpe,
        },
        {
            "key": "rolling_sortino",
            "title": f"Rolling Sortino ({rolling_window}D)",
            "kind": "line",
            "rows": rolling_sortino,
        },
        {
            "key": "rolling_beta",
            "title": f"Rolling Beta vs Benchmark ({rolling_window}D)",
            "kind": "line",
            "rows": rolling_beta,
        },
        {
            "key": "rolling_active_return",
            "title": f"Rolling Active Return ({rolling_window}D)",
            "kind": "line",
            "rows": rolling_active_risk["rolling_active_return"],
        },
        {
            "key": "rolling_tracking_error",
            "title": f"Rolling Tracking Error ({rolling_window}D)",
            "kind": "line",
            "rows": rolling_active_risk["rolling_tracking_error"],
        },
        {
            "key": "rolling_correlation",
            "title": f"Rolling Correlation vs Benchmark ({rolling_window}D)",
            "kind": "line",
            "rows": rolling_active_risk["rolling_correlation"],
        },
        {
            "key": "rolling_information_ratio",
            "title": f"Rolling Information Ratio ({rolling_window}D)",
            "kind": "line",
            "rows": rolling_active_risk["rolling_information_ratio"],
        },
        {
            "key": "return_distribution",
            "title": "Return Distribution",
            "kind": "bar",
            "rows": _return_distribution(returns),
        },
        {
            "key": "vol_matched_benchmark",
            "title": "Volatility-Matched Benchmark",
            "kind": "line",
            "rows": vol_matched_rows,
        },
        {
            "key": "cash_exposure",
            "title": "Cash / Invested Exposure",
            "kind": "area",
            "rows": exposure_rows,
        },
    ]


def _fallback_metrics(
    source_rows: List[Dict[str, Any]],
    returns: pd.Series,
    benchmark_returns: pd.Series,
    benchmark: str,
) -> Dict[str, Any]:
    cleaned = returns.dropna()
    investable = cleaned.iloc[1:] if len(cleaned) > 1 and abs(float(cleaned.iloc[0])) <= 1e-12 else cleaned
    if len(investable) < 2:
        return {
            "success": False,
            "error": "insufficient_return_series",
            "source": "fallback",
            "metrics": {},
            "relative": {"benchmark": benchmark},
            "exposure": {},
            "review": {},
            "monthly_returns": [],
            "drawdown_series": [],
            "metric_notes": ["Need at least two non-anchor daily returns for QuantStats-style analytics."],
            "tear_sheet": {"available": False, "reason": "insufficient_return_series"},
        }

    mean = float(investable.mean())
    std = float(investable.std(ddof=1))
    downside = investable[investable < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) >= 2 else None
    volatility = std * math.sqrt(TRADING_DAYS_PER_YEAR) if std > 0 else 0.0
    sharpe = mean / std * math.sqrt(TRADING_DAYS_PER_YEAR) if std > 0 else None
    sortino = mean / downside_std * math.sqrt(TRADING_DAYS_PER_YEAR) if downside_std and downside_std > 0 else None
    max_dd, drawdown_rows = _max_drawdown(investable)
    cagr = _annualized_return(investable)
    calmar = cagr / abs(max_dd) if cagr is not None and max_dd is not None and max_dd < 0 else None
    win_rate = float((investable > 0).sum() / len(investable))
    aligned_portfolio, aligned_benchmark = investable.align(benchmark_returns, join="inner")
    active = aligned_portfolio - aligned_benchmark
    information_ratio = _information_ratio(active)
    cumulative_return = float((1.0 + investable).prod() - 1.0)
    benchmark_aligned = benchmark_returns.reindex(investable.index).dropna()
    benchmark_cumulative = float((1.0 + benchmark_aligned).prod() - 1.0) if not benchmark_aligned.empty else None
    active_return = None if benchmark_cumulative is None else cumulative_return - benchmark_cumulative

    risk_label = "Low"
    if volatility >= 0.45:
        risk_label = "High"
    elif volatility >= 0.25:
        risk_label = "Medium"

    quality_label = "Constructive" if sharpe is not None and sharpe >= 1.0 else "Mixed"
    if sharpe is not None and sharpe < 0:
        quality_label = "Weak"

    notes = [
        f"Daily win rate is {win_rate * 100.0:.1f}%; this is not trade-level hit rate.",
        f"Annualized volatility is {volatility * 100.0:.1f}%, placing realized risk in the {risk_label.lower()} bucket.",
    ]
    if active_return is not None:
        notes.append(f"Active return versus {benchmark} is {active_return * 100.0:+.1f} percentage points over the measured window.")

    monthly_returns = _monthly_returns(investable)
    vol_match_metrics, vol_matched_rows = _vol_matched_benchmark(investable, benchmark_returns)
    exposure_metrics, exposure_rows = _series_exposure_rows(source_rows, investable)
    best_periods = _top_period_rows(investable, benchmark_returns, best=True)
    worst_periods = _top_period_rows(investable, benchmark_returns, best=False)
    tear_sheet_charts = _strategy_tear_sheet_charts(
        source_rows,
        investable,
        benchmark_returns,
        monthly_returns,
        drawdown_rows,
        vol_matched_rows,
        exposure_rows,
    )
    tail_metrics = _tail_risk_metrics(investable)
    metric_values = {
        "period_count": int(len(investable)),
        "cumulative_return_pct": _round(cumulative_return * 100.0, 4),
        "cagr_pct": _round(None if cagr is None else cagr * 100.0, 4),
        "sharpe": _round(sharpe, 4),
        "sortino": _round(sortino, 4),
        "volatility_annualized_pct": _round(volatility * 100.0, 4),
        "max_drawdown_pct": _round(None if max_dd is None else max_dd * 100.0, 4),
        "calmar": _round(calmar, 4),
        "win_rate_pct": _round(win_rate * 100.0, 4),
        "best_day": _best_worst_day(investable, best=True),
        "worst_day": _best_worst_day(investable, best=False),
        **tail_metrics,
    }
    tear_sheet = _build_strategy_tear_sheet(
        metrics=metric_values,
        relative={
            "benchmark": benchmark,
            "benchmark_cumulative_return_pct": _round(None if benchmark_cumulative is None else benchmark_cumulative * 100.0, 4),
            "active_return_ppt": _round(None if active_return is None else active_return * 100.0, 4),
            "information_ratio": _round(information_ratio, 4),
            **vol_match_metrics,
        },
        review={
            "headline": f"{quality_label} risk-adjusted profile vs {benchmark}",
            "risk_label": risk_label,
            "quality_label": quality_label,
            "notes": notes,
        },
        exposure=exposure_metrics,
        monthly_returns=monthly_returns,
        drawdown_rows=drawdown_rows,
        drawdown_periods=_drawdown_periods(drawdown_rows),
        best_periods=best_periods,
        worst_periods=worst_periods,
        charts=tear_sheet_charts,
        source="fallback",
    )

    return {
        "success": True,
        "source": "fallback",
        "metrics": tear_sheet["metrics"],
        "relative": tear_sheet["relative"],
        "exposure": tear_sheet["exposure"],
        "review": tear_sheet["review"],
        "monthly_returns": monthly_returns,
        "drawdown_series": drawdown_rows,
        "metric_notes": [
            "Win rate is daily period win rate, not trade-level win rate.",
            "Metrics consume the daily TWR series produced by the local ledger simulator.",
        ],
        "tear_sheet": tear_sheet,
    }


def _metric_row(label: str, value: Any, note: str = "") -> Dict[str, Any]:
    return {"label": label, "value": value, "note": note}


def _tear_sheet_table_rows(tear_sheet: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    for table in tear_sheet.get("tables") or []:
        if (table or {}).get("key") == key:
            rows = (table or {}).get("rows") or []
            return rows if isinstance(rows, list) else []
    return []


def _build_strategy_tear_sheet(
    *,
    metrics: Dict[str, Any],
    relative: Dict[str, Any],
    review: Dict[str, Any],
    exposure: Dict[str, Any],
    monthly_returns: List[Dict[str, Any]],
    drawdown_rows: List[Dict[str, Any]],
    drawdown_periods: List[Dict[str, Any]],
    best_periods: List[Dict[str, Any]],
    worst_periods: List[Dict[str, Any]],
    charts: List[Dict[str, Any]],
    source: str,
) -> Dict[str, Any]:
    benchmark = str(relative.get("benchmark") or "benchmark")
    sections = [
        {
            "key": "strategy_summary",
            "title": "Strategy Summary",
            "description": "Core return profile based on the daily TWR ledger.",
            "metrics": [
                _metric_row("Cumulative Return", metrics.get("cumulative_return_pct"), "pct"),
                _metric_row("CAGR", metrics.get("cagr_pct"), "annualized pct"),
                _metric_row("Sharpe", metrics.get("sharpe"), "risk-adjusted"),
                _metric_row("Win Rate", metrics.get("win_rate_pct"), "daily periods"),
            ],
        },
        {
            "key": "risk_profile",
            "title": "Risk Profile",
            "description": "Realized volatility, downside risk, and peak-to-trough loss.",
            "metrics": [
                _metric_row("Volatility", metrics.get("volatility_annualized_pct"), "annualized pct"),
                _metric_row("Sortino", metrics.get("sortino"), "downside-adjusted"),
                _metric_row("Max Drawdown", metrics.get("max_drawdown_pct"), "pct"),
                _metric_row("Calmar", metrics.get("calmar"), "CAGR / max drawdown"),
            ],
        },
        {
            "key": "benchmark_comparison",
            "title": "Benchmark Comparison",
            "description": f"Relative performance against {benchmark}.",
            "metrics": [
                _metric_row(f"{benchmark} Return", relative.get("benchmark_cumulative_return_pct"), "pct"),
                _metric_row("Active Return", relative.get("active_return_ppt"), "percentage points"),
                _metric_row("Information Ratio", relative.get("information_ratio"), benchmark),
                _metric_row("Quality", review.get("quality_label"), review.get("headline") or ""),
            ],
        },
        {
            "key": "drawdown_review",
            "title": "Drawdown Review",
            "description": "Worst/best daily periods and current path through drawdown.",
            "metrics": [
                _metric_row("Worst Day", (metrics.get("worst_day") or {}).get("return_pct"), (metrics.get("worst_day") or {}).get("date") or ""),
                _metric_row("Best Day", (metrics.get("best_day") or {}).get("return_pct"), (metrics.get("best_day") or {}).get("date") or ""),
                _metric_row("Risk Label", review.get("risk_label"), ""),
                _metric_row("Periods", metrics.get("period_count"), "daily returns"),
            ],
        },
        {
            "key": "tail_risk",
            "title": "Tail Risk",
            "description": "Left-tail loss metrics for daily TWR periods.",
            "metrics": [
                _metric_row("VaR 95", metrics.get("var_95_pct"), "daily pct"),
                _metric_row("CVaR 95", metrics.get("cvar_95_pct"), "daily pct"),
                _metric_row("Tail Ratio", metrics.get("tail_ratio"), "p95 / abs(p5)"),
                _metric_row("Skew", metrics.get("skew"), "return asymmetry"),
            ],
        },
        {
            "key": "exposure_profile",
            "title": "Exposure Profile",
            "description": "Cash and invested exposure inferred from daily NAV rows.",
            "metrics": [
                _metric_row("Average Exposure", exposure.get("average_exposure_pct"), "pct invested"),
                _metric_row("Average Cash", exposure.get("average_cash_pct"), "pct cash"),
                _metric_row("Invested Return", exposure.get("invested_capital_return_pct"), "cash-adjusted pct"),
                _metric_row("Cash Drag", exposure.get("cash_drag_ppt"), "percentage points"),
            ],
        },
        {
            "key": "extreme_periods",
            "title": "Best / Worst Periods",
            "description": "Largest daily TWR moves compared with the benchmark on the same dates.",
            "metrics": [
                _metric_row("Best Day", (metrics.get("best_day") or {}).get("return_pct"), (metrics.get("best_day") or {}).get("date") or ""),
                _metric_row("Worst Day", (metrics.get("worst_day") or {}).get("return_pct"), (metrics.get("worst_day") or {}).get("date") or ""),
                _metric_row("Payoff Ratio", metrics.get("payoff_ratio"), "avg win / avg loss"),
                _metric_row("Profit Factor", metrics.get("profit_factor"), "gross gain / gross loss"),
            ],
        },
        {
            "key": "return_shape",
            "title": "Return Shape",
            "description": "Distribution quality beyond volatility: asymmetry, fat tails, and gain-to-pain profile.",
            "metrics": [
                _metric_row("Skew", metrics.get("skew"), "higher favors right tail"),
                _metric_row("Kurtosis", metrics.get("kurtosis"), "tail thickness"),
                _metric_row("Gain to Pain", metrics.get("gain_to_pain"), "gross gain / abs loss"),
                _metric_row("Payoff Ratio", metrics.get("payoff_ratio"), "avg win / avg loss"),
            ],
        },
    ]
    tables = [
        {
            "key": "monthly_returns",
            "title": "Monthly Returns",
            "columns": ["month", "return_pct"],
            "rows": monthly_returns,
        },
        {
            "key": "recent_drawdowns",
            "title": "Recent Drawdowns",
            "columns": ["date", "drawdown_pct"],
            "rows": drawdown_rows[-20:],
        },
        {
            "key": "drawdown_periods",
            "title": "Top Drawdown Periods",
            "columns": ["start_date", "trough_date", "end_date", "max_drawdown_pct", "duration_days"],
            "rows": drawdown_periods,
        },
        {
            "key": "best_periods",
            "title": "Best Periods vs Benchmark",
            "columns": ["date", "portfolio_return_pct", "benchmark_return_pct", "active_return_ppt"],
            "rows": best_periods,
        },
        {
            "key": "worst_periods",
            "title": "Worst Periods vs Benchmark",
            "columns": ["date", "portfolio_return_pct", "benchmark_return_pct", "active_return_ppt"],
            "rows": worst_periods,
        },
        {
            "key": "rolling_active_risk_latest",
            "title": "Latest Rolling Active Risk",
            "columns": ["metric", "value"],
            "rows": [
                {"metric": chart.get("title"), "value": ((chart.get("rows") or [{}])[-1] or {}).get("value")}
                for chart in charts
                if chart.get("key") in {"rolling_active_return", "rolling_tracking_error", "rolling_correlation", "rolling_information_ratio"}
            ],
        },
    ]
    return {
        "available": True,
        "label": "Strategy Tear Sheet",
        "source": source,
        "metrics": metrics,
        "relative": relative,
        "exposure": exposure,
        "review": review,
        "sections": sections,
        "tables": tables,
        "charts": charts,
        "notes": [
            "This inline tear sheet follows the QuantStats report structure but remains available without optional plotting dependencies.",
            "All figures are derived from the local daily TWR ledger and benchmark series.",
        ],
    }


def _row_value(row: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = _safe_float((row or {}).get(key))
        if value is not None:
            return value
    return None


def _dashboard_card(key: str, label: str, value: Any, note: str = "", unit: str = "") -> Dict[str, Any]:
    return {"key": key, "label": label, "value": value, "note": note, "unit": unit}


def _clamp(value: Optional[float], low: float = 0.0, high: float = 100.0) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    return max(low, min(high, value))


def _downside_volatility_pct(returns: pd.Series) -> Optional[float]:
    downside = returns.dropna()
    downside = downside[downside < 0]
    if len(downside) < 2:
        return None
    return _round(float(downside.std(ddof=1)) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0, 4)


def _capture_ratio_pct(returns: pd.Series, benchmark_returns: pd.Series, *, upside: bool) -> Optional[float]:
    portfolio, benchmark = returns.align(benchmark_returns, join="inner")
    if portfolio.empty or benchmark.empty:
        return None
    mask = benchmark > 0 if upside else benchmark < 0
    portfolio_sample = portfolio[mask].dropna()
    benchmark_sample = benchmark[mask].dropna()
    portfolio_sample, benchmark_sample = portfolio_sample.align(benchmark_sample, join="inner")
    if portfolio_sample.empty or benchmark_sample.empty:
        return None
    benchmark_average = float(benchmark_sample.mean())
    if abs(benchmark_average) <= 1e-12:
        return None
    return _round(float(portfolio_sample.mean()) / benchmark_average * 100.0, 4)


def _benchmark_attribution_cards(
    returns: pd.Series,
    benchmark_returns: pd.Series,
    relative: Dict[str, Any],
    benchmark: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    portfolio, benchmark_series = returns.align(benchmark_returns, join="inner")
    portfolio = portfolio.dropna()
    benchmark_series = benchmark_series.dropna()
    portfolio, benchmark_series = portfolio.align(benchmark_series, join="inner")
    beta = None
    correlation = None
    alpha = None
    tracking_error = None
    if len(portfolio) >= 2 and len(benchmark_series) >= 2:
        benchmark_var = float(benchmark_series.var(ddof=1))
        if benchmark_var > 0:
            beta_value = float(portfolio.cov(benchmark_series)) / benchmark_var
            beta = beta_value if math.isfinite(beta_value) else None
        portfolio_std = float(portfolio.std(ddof=1))
        benchmark_std = float(benchmark_series.std(ddof=1))
        if portfolio_std > 0 and benchmark_std > 0:
            corr_value = float(portfolio.corr(benchmark_series))
            correlation = corr_value if math.isfinite(corr_value) else None
        active = portfolio - benchmark_series
        if len(active.dropna()) >= 2:
            tracking_error = _round(float(active.std(ddof=1)) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0, 4)
        portfolio_cagr = _annualized_return(portfolio)
        benchmark_cagr = _annualized_return(benchmark_series)
        if portfolio_cagr is not None and benchmark_cagr is not None:
            alpha = portfolio_cagr - ((beta if beta is not None else 1.0) * benchmark_cagr)
    cards = [
        _dashboard_card("beta", "Beta", _round(beta, 4), f"vs {benchmark}", "number"),
        _dashboard_card("alpha_annualized", "Annualized Alpha", _round(None if alpha is None else alpha * 100.0, 4), "CAPM-style, rf=0", "ppt"),
        _dashboard_card("correlation", "Correlation", _round(correlation, 4), f"daily vs {benchmark}", "number"),
        _dashboard_card("tracking_error", "Tracking Error", tracking_error, "annualized active risk", "pct"),
        _dashboard_card("up_capture", "Up Capture", _capture_ratio_pct(portfolio, benchmark_series, upside=True), "benchmark up days", "pct"),
        _dashboard_card("down_capture", "Down Capture", _capture_ratio_pct(portfolio, benchmark_series, upside=False), "benchmark down days", "pct"),
    ]
    summary = {
        "benchmark": benchmark,
        "active_return_ppt": relative.get("active_return_ppt"),
        "information_ratio": relative.get("information_ratio"),
        "vol_matched_active_return_ppt": relative.get("vol_matched_active_return_ppt"),
    }
    return cards, summary


def _benchmark_attribution_waterfall(
    metrics: Dict[str, Any],
    relative: Dict[str, Any],
    exposure: Dict[str, Any],
    source_rows: List[Dict[str, Any]],
    benchmark_cards: List[Dict[str, Any]],
    benchmark: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    portfolio_return = _safe_float(metrics.get("cumulative_return_pct"))
    benchmark_return = _safe_float(relative.get("benchmark_cumulative_return_pct"))
    active_return = _safe_float(relative.get("active_return_ppt"))
    beta = _module_card_value(benchmark_cards, "beta")
    beta = 1.0 if beta is None else beta
    beta_return = benchmark_return * beta if benchmark_return is not None else None
    cash_drag = _safe_float(exposure.get("cash_drag_ppt"))
    cash_effect = -cash_drag if cash_drag is not None else None
    trading_turnover_hkd = sum(abs(_safe_float(row.get("internal_rebalancing_cash_hkd")) or 0.0) for row in source_rows or [])
    nav_values = [_safe_float(row.get("portfolio_value_hkd")) for row in source_rows or []]
    nav_values = [value for value in nav_values if value is not None and value > 0]
    average_nav = float(np.mean(nav_values)) if nav_values else None
    trading_turnover_pct = trading_turnover_hkd / average_nav * 100.0 if average_nav and average_nav > 0 else None
    trading_impact = None
    selection_effect = None
    if portfolio_return is not None and beta_return is not None:
        selection_effect = portfolio_return - beta_return
        if cash_effect is not None:
            selection_effect -= cash_effect
        if trading_impact is not None:
            selection_effect -= trading_impact
    rows = [
        {"key": "benchmark_return", "label": f"{benchmark} Return", "value": _round(benchmark_return, 4), "unit": "pct", "note": "same date range"},
        {"key": "beta_return", "label": "Beta Return", "value": _round(beta_return, 4), "unit": "ppt", "note": f"{benchmark} return x beta"},
        {"key": "selection_effect", "label": "Selection / Timing", "value": _round(selection_effect, 4), "unit": "ppt", "note": "residual after beta and cash"},
        {"key": "cash_drag", "label": "Cash Effect", "value": _round(cash_effect, 4), "unit": "ppt", "note": "negative means cash diluted invested return"},
        {"key": "trading_impact", "label": "Trading Impact", "value": _round(trading_impact, 4), "unit": "ppt", "note": "not estimated without intraday execution marks"},
        {"key": "portfolio_return", "label": "Portfolio TWR", "value": _round(portfolio_return, 4), "unit": "pct", "note": "daily linked TWR"},
    ]
    summary = {
        "benchmark": benchmark,
        "portfolio_return_pct": _round(portfolio_return, 4),
        "benchmark_return_pct": _round(benchmark_return, 4),
        "active_return_ppt": _round(active_return, 4),
        "beta": _round(beta, 4),
        "beta_return_ppt": _round(beta_return, 4),
        "selection_effect_ppt": _round(selection_effect, 4),
        "cash_drag_ppt": _round(cash_drag, 4),
        "cash_effect_ppt": _round(cash_effect, 4),
        "trading_impact_ppt": _round(trading_impact, 4),
        "trading_turnover_pct": _round(trading_turnover_pct, 4),
        "method": "beta_cash_residual",
    }
    return rows, summary


def _latest_cash_exposure_rows(tear_sheet: Dict[str, Any]) -> List[Dict[str, Any]]:
    for chart in tear_sheet.get("charts") or []:
        if (chart or {}).get("key") == "cash_exposure":
            rows = (chart or {}).get("rows") or []
            return rows if isinstance(rows, list) else []
    return []


def _holding_quality_snapshot(reviews: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    review_list = list(reviews or [])
    aliases = _ticker_aliases_from_reviews(review_list)
    latest = _latest_review(review_list)
    latest_week_id = str((latest or {}).get("week_id") or "")
    rows: List[Dict[str, Any]] = []
    total_value = 0.0
    for stock_id, payload in ((latest.get("stocks") or {}) if isinstance(latest, dict) else {}).items():
        if not isinstance(payload, dict):
            continue
        shares = _safe_float(payload.get("shares_held")) or 0.0
        if shares <= 0:
            continue
        metrics = payload.get("position_metrics") or {}
        ticker = _stock_label(str(stock_id), payload, aliases)
        currency = _detect_currency(ticker)
        fx_rate = _fx_rate_for(latest, currency)
        holding_value = _safe_float(metrics.get("holding_value_hkd"))
        unrealized_pnl = _safe_float(metrics.get("unrealized_pnl_hkd"))
        avg_cost = _safe_float(payload.get("avg_cost"))
        valuation_basis = "position_metrics" if holding_value is not None else "avg_cost" if avg_cost is not None else "missing"
        if holding_value is None and avg_cost is not None:
            holding_value = avg_cost * shares * fx_rate
        if unrealized_pnl is None:
            unrealized_pnl = _safe_float(metrics.get("realized_pnl_hkd"))
        return_pct = _safe_float(metrics.get("return_since_buy"))
        if return_pct is None and holding_value is not None and unrealized_pnl is not None:
            entry_value = holding_value - unrealized_pnl
            if entry_value > 0:
                return_pct = unrealized_pnl / entry_value * 100.0
        quality_score = None
        if return_pct is not None:
            quality_score = max(0.0, min(100.0, 50.0 + float(return_pct)))
        momentum_bucket = "unavailable"
        if return_pct is not None:
            if return_pct >= 20:
                momentum_bucket = "strong"
            elif return_pct >= 5:
                momentum_bucket = "constructive"
            elif return_pct > -5:
                momentum_bucket = "neutral"
            elif return_pct > -15:
                momentum_bucket = "weak"
            else:
                momentum_bucket = "stressed"
        row = {
            "ticker": ticker,
            "week_id": latest_week_id,
            "shares": _round(shares, 4),
            "holding_value_hkd": _round(holding_value, 2),
            "unrealized_pnl_hkd": _round(unrealized_pnl, 2),
            "return_pct": _round(return_pct, 4),
            "quality_score": _round(quality_score, 4),
            "momentum_bucket": momentum_bucket,
            "valuation_basis": valuation_basis,
        }
        if holding_value is not None:
            total_value += holding_value
        rows.append(row)
    for row in rows:
        value = _safe_float(row.get("holding_value_hkd"))
        row["weight_pct"] = _round((value / total_value * 100.0) if value is not None and total_value > 0 else None, 4)
    rows.sort(key=lambda row: _safe_float(row.get("quality_score")) if _safe_float(row.get("quality_score")) is not None else -1.0, reverse=True)
    covered = sum(1 for row in rows if _safe_float(row.get("holding_value_hkd")) is not None)
    positive = sum(1 for row in rows if (_safe_float(row.get("return_pct")) or 0.0) > 0)
    negative = sum(1 for row in rows if (_safe_float(row.get("return_pct")) or 0.0) < 0)
    return {
        "title": "Holdings Quality / Momentum",
        "summary": {
            "holding_count": len(rows),
            "covered_holding_count": covered,
            "coverage_pct": _round((covered / len(rows) * 100.0) if rows else None, 4),
            "positive_momentum_count": positive,
            "negative_momentum_count": negative,
            "average_quality_score": _round(float(np.mean([float(row["quality_score"]) for row in rows if _safe_float(row.get("quality_score")) is not None])) if any(_safe_float(row.get("quality_score")) is not None for row in rows) else None, 4),
        },
        "rows": rows[:50],
        "notes": [
            "Quality score is a local proxy based on position-level return where available.",
            "This avoids making fundamentals an input to core NAV/TWR calculations.",
        ],
    }


def _module_card_value(cards: Iterable[Dict[str, Any]], key: str) -> Optional[float]:
    for card in cards or []:
        if (card or {}).get("key") == key:
            return _safe_float((card or {}).get("value"))
    return None


def _holding_return_attribution(
    security_performance: Dict[str, Any],
    holding_quality: Dict[str, Any],
) -> Dict[str, Any]:
    quality_by_ticker = {
        str((row or {}).get("ticker") or ""): row
        for row in (holding_quality.get("rows") or [])
        if (row or {}).get("ticker")
    }
    rows: List[Dict[str, Any]] = []
    for item in security_performance.get("rows") or []:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "")
        if not ticker:
            continue
        quality = quality_by_ticker.get(ticker, {})
        total_pnl = _safe_float(item.get("total_pnl_hkd"))
        realized = _safe_float(item.get("realized_pnl_hkd")) or 0.0
        unrealized = _safe_float(item.get("unrealized_pnl_hkd")) or 0.0
        rows.append(
            {
                "ticker": ticker,
                "status": item.get("status") or "closed",
                "weight_pct": _round(_safe_float(quality.get("weight_pct")), 4),
                "total_pnl_hkd": _round(total_pnl, 2),
                "realized_pnl_hkd": _round(realized, 2),
                "unrealized_pnl_hkd": _round(unrealized, 2),
                "return_pct": _round(_safe_float(item.get("total_return_pct") or quality.get("return_pct")), 4),
                "purchase_value_hkd": _round(_safe_float(item.get("purchase_value_hkd")), 2),
                "market_value_hkd": _round(_safe_float(item.get("market_value_hkd")), 2),
            }
        )
    total_abs_pnl = sum(abs(float(row.get("total_pnl_hkd") or 0.0)) for row in rows)
    for row in rows:
        pnl = _safe_float(row.get("total_pnl_hkd")) or 0.0
        row["contribution_pct"] = _round((pnl / total_abs_pnl * 100.0) if total_abs_pnl > 0 else None, 4)
    rows.sort(key=lambda row: _safe_float(row.get("total_pnl_hkd")) or 0.0, reverse=True)
    contributors = [row for row in rows if (_safe_float(row.get("total_pnl_hkd")) or 0.0) > 0]
    detractors = [row for row in rows if (_safe_float(row.get("total_pnl_hkd")) or 0.0) < 0]
    return {
        "title": "Holding-Level Return Attribution",
        "summary": {
            "covered_security_count": len(rows),
            "top_contributor": (contributors[0] if contributors else {}).get("ticker"),
            "top_detractor": (sorted(detractors, key=lambda row: _safe_float(row.get("total_pnl_hkd")) or 0.0)[0] if detractors else {}).get("ticker"),
            "net_pnl_hkd": _round(sum(float(row.get("total_pnl_hkd") or 0.0) for row in rows), 2),
            "gross_positive_pnl_hkd": _round(sum(float(row.get("total_pnl_hkd") or 0.0) for row in contributors), 2),
            "gross_negative_pnl_hkd": _round(sum(float(row.get("total_pnl_hkd") or 0.0) for row in detractors), 2),
        },
        "rows": rows,
        "notes": ["Security-level realized and unrealized P/L is used as the holding attribution base."],
    }


def _security_performance_ledger(
    security_performance: Dict[str, Any],
    relative: Dict[str, Any],
) -> Dict[str, Any]:
    benchmark_return = _safe_float(relative.get("benchmark_cumulative_return_pct"))
    rows: List[Dict[str, Any]] = []
    for item in security_performance.get("rows") or []:
        if not isinstance(item, dict):
            continue
        total_return = _safe_float(item.get("total_return_pct"))
        row = dict(item)
        row["alpha_vs_benchmark_ppt"] = _round(
            None if total_return is None or benchmark_return is None else total_return - benchmark_return,
            4,
        )
        row["pnl_source"] = "fifo_trade_journal"
        rows.append(row)
    summary = security_performance.get("summary") or {}
    return {
        "title": "Security Performance Ledger",
        "summary": {
            **summary,
            "benchmark_return_pct": _round(benchmark_return, 4),
            "method": "fifo_realized_plus_latest_unrealized",
        },
        "cards": [
            _dashboard_card("security_count", "Securities", summary.get("security_count"), "current and closed", "number"),
            _dashboard_card("total_pnl", "Total P/L", summary.get("total_pnl_hkd"), "realized + unrealized", "money"),
            _dashboard_card("realized_pnl", "Realized P/L", summary.get("total_realized_pnl_hkd"), "closed lots", "money"),
            _dashboard_card("unrealized_pnl", "Unrealized P/L", summary.get("total_unrealized_pnl_hkd"), "open lots", "money"),
        ],
        "rows": rows,
        "notes": [
            "Rows aggregate FIFO trade lots by ticker.",
            "Alpha uses each security's total return minus the selected benchmark return over the dashboard period when both are available.",
        ],
    }


def _top_holding_proxy_rows(
    holding_quality: Dict[str, Any],
    *,
    worst: bool,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    rows = []
    for row in holding_quality.get("rows") or []:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "")
        if not ticker:
            continue
        proxy = _safe_float(row.get("return_pct"))
        pnl = _safe_float(row.get("unrealized_pnl_hkd"))
        if proxy is None and pnl is None:
            continue
        rows.append(
            {
                "ticker": ticker,
                "return_pct": _round(proxy, 4),
                "pnl_hkd": _round(pnl, 2),
                "weight_pct": _round(_safe_float(row.get("weight_pct")), 4),
            }
        )
    rows.sort(key=lambda item: (_safe_float(item.get("return_pct")) if _safe_float(item.get("return_pct")) is not None else 0.0), reverse=not worst)
    return rows[:limit]


def _linked_return_pct(series: pd.Series) -> Optional[float]:
    cleaned = series.dropna()
    if cleaned.empty:
        return None
    return _round((float((1.0 + cleaned).prod()) - 1.0) * 100.0, 4)


def _drawdown_attribution(
    analytics: Dict[str, Any],
    returns: pd.Series,
    benchmark_returns: pd.Series,
    holding_quality: Dict[str, Any],
    exposure_dashboard: Dict[str, Any],
) -> Dict[str, Any]:
    drawdown_rows = analytics.get("drawdown_series") or []
    episodes = _drawdown_periods(drawdown_rows)
    cash_buffer = _safe_float((exposure_dashboard.get("summary") or {}).get("average_cash_pct"))
    rows: List[Dict[str, Any]] = []
    for episode in episodes[:5]:
        start = pd.to_datetime(episode.get("start_date"), errors="coerce")
        trough = pd.to_datetime(episode.get("trough_date"), errors="coerce")
        portfolio_return = None
        benchmark_return = None
        if not pd.isna(start) and not pd.isna(trough):
            window = returns[(returns.index >= pd.Timestamp(start)) & (returns.index <= pd.Timestamp(trough))]
            benchmark_window = benchmark_returns[(benchmark_returns.index >= pd.Timestamp(start)) & (benchmark_returns.index <= pd.Timestamp(trough))]
            portfolio_return = _linked_return_pct(window)
            benchmark_return = _linked_return_pct(benchmark_window)
        rows.append(
            {
                "start_date": episode.get("start_date"),
                "trough_date": episode.get("trough_date"),
                "end_date": episode.get("end_date"),
                "max_drawdown_pct": episode.get("max_drawdown_pct"),
                "duration_days": episode.get("duration_days"),
                "portfolio_return_pct": portfolio_return,
                "benchmark_return_pct": benchmark_return,
                "active_return_ppt": _round(None if portfolio_return is None or benchmark_return is None else portfolio_return - benchmark_return, 4),
                "portfolio_vs_benchmark_ppt": _round(None if portfolio_return is None or benchmark_return is None else portfolio_return - benchmark_return, 4),
                "cash_buffer_pct": _round(cash_buffer, 4),
                "cash_context_pct": _round(cash_buffer, 4),
                "contribution_method": "holding_pnl_proxy",
                "top_detractors": _top_holding_proxy_rows(holding_quality, worst=True),
                "top_stabilizers": _top_holding_proxy_rows(holding_quality, worst=False),
            }
        )
    return {
        "title": "Drawdown Attribution",
        "summary": {
            "episode_count": len(rows),
            "worst_drawdown_pct": (rows[0] if rows else {}).get("max_drawdown_pct"),
            "average_cash_buffer_pct": _round(cash_buffer, 4),
        },
        "episodes": rows,
        "notes": [
            "Drawdown episodes come from the daily TWR curve.",
            "Top detractors/stabilizers use latest holding P/L proxies until ticker-level drawdown attribution is available.",
        ],
    }


def _security_returns_frame(source_rows: Iterable[Dict[str, Any]]) -> pd.DataFrame:
    records: Dict[pd.Timestamp, Dict[str, float]] = {}
    for row in source_rows or []:
        date_text = str((row or {}).get("date") or "").strip()
        try:
            day = pd.Timestamp(date_text).normalize()
        except (TypeError, ValueError):
            continue
        values: Dict[str, float] = {}
        for field in ("security_returns", "holding_returns", "ticker_returns", "position_returns"):
            raw = (row or {}).get(field)
            if not isinstance(raw, dict):
                continue
            for ticker, value in raw.items():
                canonical = _canonical_ticker(ticker)
                parsed = _safe_float(value)
                if canonical and parsed is not None:
                    values[canonical] = parsed
        if values:
            records[day] = values
    if not records:
        return pd.DataFrame()
    frame = pd.DataFrame.from_dict(records, orient="index").sort_index()
    return frame.apply(pd.to_numeric, errors="coerce").dropna(axis=1, how="all")


def _security_weight_map_from_rows(source_rows: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    latest: Dict[str, float] = {}
    totals: Dict[str, List[float]] = {}
    for row in source_rows or []:
        raw = (row or {}).get("security_weights")
        if not isinstance(raw, dict):
            continue
        for ticker, value in raw.items():
            canonical = _canonical_ticker(ticker)
            parsed = _safe_float(value)
            if not canonical or parsed is None or parsed <= 0:
                continue
            latest[canonical] = parsed
            totals.setdefault(canonical, []).append(parsed)
    if latest:
        return latest
    return {
        ticker: float(np.mean(values))
        for ticker, values in totals.items()
        if values
    }


def _covariance_risk_budget(
    holding_quality: Dict[str, Any],
    source_rows: Iterable[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    source_row_list = list(source_rows or [])
    returns_frame = _security_returns_frame(source_rows)
    if returns_frame.empty or len(returns_frame.dropna(how="all")) < 2:
        return None
    weight_by_ticker: Dict[str, float] = {}
    for row in holding_quality.get("rows") or []:
        if not isinstance(row, dict):
            continue
        ticker = _canonical_ticker(row.get("ticker"))
        weight = _safe_float(row.get("weight_pct"))
        if ticker and weight is not None and weight > 0:
            weight_by_ticker[ticker] = weight / 100.0
    if not weight_by_ticker:
        weight_by_ticker = _security_weight_map_from_rows(source_row_list)
    tickers = [ticker for ticker in returns_frame.columns if ticker in weight_by_ticker]
    if not tickers:
        return None
    sample = returns_frame[tickers].dropna(how="all").fillna(0.0)
    if len(sample) < 2:
        return None
    weights = np.array([weight_by_ticker[ticker] for ticker in tickers], dtype=float)
    if float(np.sum(np.abs(weights))) <= 1e-12:
        return None
    covariance = sample.cov().to_numpy(dtype=float)
    if covariance.size == 0:
        return None
    portfolio_variance = float(weights.T @ covariance @ weights)
    if not math.isfinite(portfolio_variance) or portfolio_variance <= 1e-16:
        return None
    portfolio_vol_daily = math.sqrt(portfolio_variance)
    marginal_daily = covariance @ weights / portfolio_vol_daily
    component_daily = weights * marginal_daily
    component_total = float(component_daily.sum())
    if abs(component_total) <= 1e-12:
        return None
    rows: List[Dict[str, Any]] = []
    for index, ticker in enumerate(tickers):
        security_vol = float(sample[ticker].std(ddof=1)) if len(sample[ticker].dropna()) >= 2 else None
        contribution = float(component_daily[index] / component_total * 100.0)
        rows.append(
            {
                "ticker": ticker,
                "weight_pct": _round(weight_by_ticker[ticker] * 100.0, 4),
                "realized_volatility_pct": _round(None if security_vol is None else security_vol * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0, 4),
                "marginal_vol_annualized_pct": _round(float(marginal_daily[index]) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0, 4),
                "component_vol_annualized_pct": _round(float(component_daily[index]) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0, 4),
                "risk_contribution_pct": _round(contribution, 4),
                "covariance_contribution_pct": _round(contribution, 4),
            }
        )
    rows.sort(key=lambda row: abs(_safe_float(row.get("risk_contribution_pct")) or 0.0), reverse=True)
    largest = rows[0] if rows else {}
    concentration = abs(_safe_float(largest.get("risk_contribution_pct")) or 0.0) if largest else None
    return {
        "title": "Risk Budget",
        "summary": {
            "method": "covariance",
            "holding_count": len(rows),
            "sample_count": int(len(sample)),
            "largest_risk_ticker": largest.get("ticker"),
            "largest_risk_contribution_pct": _round(concentration, 4),
            "portfolio_volatility_annualized_pct": _round(portfolio_vol_daily * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0, 4),
            "concentration_warning": bool(concentration is not None and concentration >= 35.0),
        },
        "rows": rows,
        "notes": ["Risk contribution uses the covariance matrix of available ticker-level daily returns and latest holding weights."],
    }


def _risk_budget(holding_quality: Dict[str, Any], source_rows: Optional[Iterable[Dict[str, Any]]] = None) -> Dict[str, Any]:
    covariance_budget = _covariance_risk_budget(holding_quality, source_rows or [])
    if covariance_budget is not None:
        return covariance_budget
    rows: List[Dict[str, Any]] = []
    for row in holding_quality.get("rows") or []:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "")
        weight = _safe_float(row.get("weight_pct"))
        if not ticker or weight is None:
            continue
        return_proxy = _safe_float(row.get("return_pct"))
        volatility_proxy = max(5.0, abs(return_proxy or 0.0))
        marginal_proxy = volatility_proxy / 100.0
        risk_score = abs(weight) * volatility_proxy
        rows.append(
            {
                "ticker": ticker,
                "weight_pct": _round(weight, 4),
                "return_proxy_pct": _round(return_proxy, 4),
                "volatility_proxy_pct": _round(volatility_proxy, 4),
                "marginal_risk_proxy": _round(marginal_proxy, 6),
                "_risk_score": risk_score,
            }
        )
    total_score = sum(float(row.get("_risk_score") or 0.0) for row in rows)
    for row in rows:
        score = float(row.pop("_risk_score", 0.0) or 0.0)
        row["risk_contribution_pct"] = _round((score / total_score * 100.0) if total_score > 0 else None, 4)
        row["covariance_contribution_pct"] = None
        row["component_vol_annualized_pct"] = None
        row["marginal_vol_annualized_pct"] = None
    rows.sort(key=lambda row: _safe_float(row.get("risk_contribution_pct")) or 0.0, reverse=True)
    largest = rows[0] if rows else {}
    concentration = _safe_float(largest.get("risk_contribution_pct"))
    return {
        "title": "Risk Budget",
        "summary": {
            "method": "proxy",
            "holding_count": len(rows),
            "largest_risk_ticker": largest.get("ticker"),
            "largest_risk_contribution_pct": _round(concentration, 4),
            "portfolio_volatility_annualized_pct": None,
            "concentration_warning": bool(concentration is not None and concentration >= 35.0),
        },
        "rows": rows,
        "notes": ["Risk contribution is a local proxy: position weight multiplied by absolute return/volatility proxy."],
    }


def _ticker_category_weights(holding_quality: Dict[str, Any]) -> Dict[str, float]:
    semiconductor = {
        "NVDA", "AMD", "AVGO", "TSM", "ASML", "AMAT", "LRCX", "KLAC", "MU",
        "ON", "SAMPLE", "SAMPLE", "SOXX", "SMH", "BESI.AS", "SAMPLE",
    }
    weights = {
        "semiconductor_weight": 0.0,
        "hk_china_weight": 0.0,
        "non_hkd_weight": 0.0,
        "largest_holding_weight": 0.0,
        "largest_holding_ticker": "",
    }
    for row in holding_quality.get("rows") or []:
        ticker = str((row or {}).get("ticker") or "").upper()
        weight = _safe_float((row or {}).get("weight_pct")) or 0.0
        if not ticker or weight <= 0:
            continue
        if ticker in semiconductor or ticker.split(".")[0] in semiconductor:
            weights["semiconductor_weight"] += weight
        if ticker.endswith(".HK") or ticker.endswith((".SS", ".SZ", ".SH")):
            weights["hk_china_weight"] += weight
        if _detect_currency(ticker) != "HKD":
            weights["non_hkd_weight"] += weight
        if weight > float(weights["largest_holding_weight"] or 0.0):
            weights["largest_holding_weight"] = weight
            weights["largest_holding_ticker"] = ticker
    return weights


def _benchmark_exposure_decomposition(
    benchmark: str,
    benchmark_cards: List[Dict[str, Any]],
    holding_quality: Dict[str, Any],
    exposure_dashboard: Dict[str, Any],
) -> Dict[str, Any]:
    category_weights = _ticker_category_weights(holding_quality)
    exposure_summary = exposure_dashboard.get("summary") or {}
    beta = _module_card_value(benchmark_cards, "beta")
    rows = [
        {"key": "selected_benchmark_beta", "label": f"{benchmark} Beta", "value": _round(beta, 4), "unit": "number", "note": "daily return regression proxy"},
        {"key": "semiconductor_weight", "label": "Semiconductor Weight", "value": _round(category_weights["semiconductor_weight"], 4), "unit": "pct", "note": "SOXX/SMH-like exposure"},
        {"key": "hk_china_weight", "label": "HK / China Weight", "value": _round(category_weights["hk_china_weight"], 4), "unit": "pct", "note": "HK/CN listed exposure"},
        {"key": "cash_weight", "label": "Cash Weight", "value": exposure_summary.get("latest_cash_pct") or exposure_summary.get("average_cash_pct"), "unit": "pct", "note": "latest or average cash"},
        {"key": "non_hkd_weight", "label": "Non-HKD Weight", "value": _round(category_weights["non_hkd_weight"], 4), "unit": "pct", "note": "FX-sensitive exposure"},
    ]
    return {
        "title": "Benchmark Exposure Decomposition",
        "summary": {
            "benchmark": benchmark,
            "selected_benchmark_beta": _round(beta, 4),
            **{key: _round(value, 4) if isinstance(value, float) else value for key, value in category_weights.items()},
        },
        "rows": rows,
        "notes": ["Exposure decomposition combines benchmark beta, cash exposure, and ticker-category weights."],
    }


def _scenario_stress(
    source_rows: List[Dict[str, Any]],
    benchmark_cards: List[Dict[str, Any]],
    holding_quality: Dict[str, Any],
    benchmark_exposure: Dict[str, Any],
) -> Dict[str, Any]:
    latest_nav = _safe_float((source_rows[-1] if source_rows else {}).get("portfolio_value_hkd"))
    summary = benchmark_exposure.get("summary") or {}
    beta = _module_card_value(benchmark_cards, "beta")
    beta = beta if beta is not None else 1.0
    semiconductor_weight = _safe_float(summary.get("semiconductor_weight")) or 0.0
    hk_china_weight = _safe_float(summary.get("hk_china_weight")) or 0.0
    non_hkd_weight = _safe_float(summary.get("non_hkd_weight")) or 0.0
    largest_weight = _safe_float(summary.get("largest_holding_weight")) or 0.0
    largest_ticker = str(summary.get("largest_holding_ticker") or "largest holding")

    scenarios = [
        ("qqq_down_5", "QQQ -5%", -5.0 * beta, "selected benchmark beta"),
        ("soxx_down_7", "SOXX -7%", -7.0 * (semiconductor_weight / 100.0), "semiconductor sleeve shock"),
        ("hk_china_down_5", "HK / China -5%", -5.0 * (hk_china_weight / 100.0), "HK/CN listed sleeve shock"),
        ("largest_holding_down_10", f"{largest_ticker} -10%", -10.0 * (largest_weight / 100.0), "single-name concentration shock"),
        ("non_hkd_fx_down_3", "Non-HKD FX -3%", -3.0 * (non_hkd_weight / 100.0), "currency translation shock"),
    ]
    rows = []
    for key, label, hit_pct, note in scenarios:
        rows.append(
            {
                "key": key,
                "label": label,
                "estimated_portfolio_hit_pct": _round(hit_pct, 4),
                "estimated_hit_hkd": _round(None if latest_nav is None else latest_nav * hit_pct / 100.0, 2),
                "note": note,
            }
        )
    return {
        "title": "Scenario Stress Table",
        "summary": {
            "latest_nav_hkd": _round(latest_nav, 2),
            "scenario_count": len(rows),
            "worst_estimated_hit_pct": min((float(row.get("estimated_portfolio_hit_pct") or 0.0) for row in rows), default=0.0),
        },
        "rows": rows,
        "notes": ["Stress rows are transparent first-order estimates, not broker risk-system forecasts."],
    }


def _latest_nav_value(source_rows: List[Dict[str, Any]]) -> Optional[float]:
    latest = source_rows[-1] if source_rows else {}
    return _row_value(latest, "portfolio_value_hkd", "end_value", "nav_hkd")


def _portfolio_overview(
    source_rows: List[Dict[str, Any]],
    holding_quality: Dict[str, Any],
    exposure_dashboard: Dict[str, Any],
    holding_return_attribution: Dict[str, Any],
    benchmark_exposure: Dict[str, Any],
) -> Dict[str, Any]:
    latest_nav = _latest_nav_value(source_rows)
    exposure_summary = exposure_dashboard.get("summary") or {}
    holding_summary = holding_quality.get("summary") or {}
    benchmark_summary = benchmark_exposure.get("summary") or {}
    latest_cash_pct = _safe_float(exposure_summary.get("latest_cash_pct"))
    if latest_cash_pct is None:
        latest_cash_pct = _safe_float(exposure_summary.get("average_cash_pct"))
    latest_invested_pct = _safe_float(exposure_summary.get("latest_invested_pct"))
    if latest_invested_pct is None and latest_cash_pct is not None:
        latest_invested_pct = 100.0 - latest_cash_pct
    latest_cash_hkd = latest_nav * latest_cash_pct / 100.0 if latest_nav is not None and latest_cash_pct is not None else None
    latest_invested_hkd = latest_nav * latest_invested_pct / 100.0 if latest_nav is not None and latest_invested_pct is not None else None

    largest_ticker = str(benchmark_summary.get("largest_holding_ticker") or "")
    largest_weight = _safe_float(benchmark_summary.get("largest_holding_weight"))
    top_contributor = (holding_return_attribution.get("summary") or {}).get("top_contributor")
    top_detractor = (holding_return_attribution.get("summary") or {}).get("top_detractor")

    return {
        "title": "Portfolio Overview",
        "summary": {
            "latest_nav_hkd": _round(latest_nav, 2),
            "latest_invested_hkd": _round(latest_invested_hkd, 2),
            "latest_cash_hkd": _round(latest_cash_hkd, 2),
            "latest_invested_pct": _round(latest_invested_pct, 4),
            "latest_cash_pct": _round(latest_cash_pct, 4),
            "holding_count": holding_summary.get("holding_count"),
            "coverage_pct": holding_summary.get("coverage_pct"),
            "largest_holding_ticker": largest_ticker,
            "largest_holding_weight_pct": _round(largest_weight, 4),
            "top_contributor": top_contributor,
            "top_detractor": top_detractor,
        },
        "cards": [
            _dashboard_card("latest_nav", "Latest NAV", _round(latest_nav, 2), "total portfolio value", "hkd"),
            _dashboard_card("latest_invested", "Latest Invested", _round(latest_invested_pct, 4), "market exposure", "pct"),
            _dashboard_card("latest_cash", "Latest Cash", _round(latest_cash_pct, 4), "cash buffer", "pct"),
            _dashboard_card("holding_count", "Holdings", holding_summary.get("holding_count"), "current positions", "number"),
            _dashboard_card("largest_holding", "Largest Holding", largest_weight, largest_ticker or "single-name weight", "pct"),
            _dashboard_card("top_contributor", "Top Contributor", top_contributor or "", "security P/L", "text"),
        ],
        "notes": [
            "Overview combines latest ledger NAV, daily cash exposure, and latest weekly holdings.",
            "Use this as the first-pass PM control view before reading detailed diagnostics.",
        ],
    }


def _score_from_signed_metric(value: Optional[float], neutral: float, scale: float, *, higher_is_better: bool = True) -> Optional[float]:
    number = _safe_float(value)
    if number is None or scale <= 0:
        return None
    direction = 1.0 if higher_is_better else -1.0
    return _round(_clamp(50.0 + direction * (number - neutral) / scale * 50.0), 4)


def _average_optional(values: Iterable[Optional[float]]) -> Optional[float]:
    numbers = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not numbers:
        return None
    return _round(float(np.mean(numbers)), 4)


def _performance_risk_scorecard(
    metrics: Dict[str, Any],
    relative: Dict[str, Any],
    exposure_dashboard: Dict[str, Any],
    benchmark_cards: List[Dict[str, Any]],
) -> Dict[str, Any]:
    cumulative_return = _safe_float(metrics.get("cumulative_return_pct"))
    active_return = _safe_float(relative.get("active_return_ppt"))
    sharpe = _safe_float(metrics.get("sharpe"))
    sortino = _safe_float(metrics.get("sortino"))
    win_rate = _safe_float(metrics.get("win_rate_pct"))
    max_drawdown = _safe_float(metrics.get("max_drawdown_pct"))
    volatility = _safe_float(metrics.get("volatility_annualized_pct"))
    tail_ratio = _safe_float(metrics.get("tail_ratio"))
    information_ratio = _safe_float(relative.get("information_ratio"))
    cash_drag = _safe_float((exposure_dashboard.get("summary") or {}).get("cash_drag_ppt"))
    beta = _module_card_value(benchmark_cards, "beta")
    down_capture = _module_card_value(benchmark_cards, "down_capture")

    return_score = _average_optional(
        [
            _score_from_signed_metric(cumulative_return, 0.0, 30.0),
            _score_from_signed_metric(active_return, 0.0, 20.0),
            _score_from_signed_metric(win_rate, 50.0, 25.0),
        ]
    )
    risk_score = _average_optional(
        [
            _score_from_signed_metric(max_drawdown, -20.0, 20.0),
            _score_from_signed_metric(volatility, 35.0, 35.0, higher_is_better=False),
            _score_from_signed_metric(down_capture, 100.0, 100.0, higher_is_better=False),
        ]
    )
    efficiency_score = _average_optional(
        [
            _score_from_signed_metric(sharpe, 0.0, 2.0),
            _score_from_signed_metric(sortino, 0.0, 3.0),
            _score_from_signed_metric(tail_ratio, 1.0, 1.0),
        ]
    )
    benchmark_score = _average_optional(
        [
            _score_from_signed_metric(active_return, 0.0, 20.0),
            _score_from_signed_metric(information_ratio, 0.0, 2.0),
            _score_from_signed_metric(beta, 1.0, 1.0, higher_is_better=False),
        ]
    )
    cash_drag_score = _score_from_signed_metric(cash_drag, 0.0, 15.0, higher_is_better=False)
    overall_score = _average_optional([return_score, risk_score, efficiency_score, benchmark_score, cash_drag_score])

    notes = []
    if active_return is not None:
        notes.append(f"Action: active return is {active_return:+.1f} ppt versus the selected benchmark.")
    if max_drawdown is not None and max_drawdown <= -15:
        notes.append(f"Action: drawdown is {max_drawdown:+.1f}%, review risk budget and single-name exposure.")
    if cash_drag is not None and cash_drag >= 5:
        notes.append(f"Action: cash drag is {cash_drag:+.1f} ppt; separate deliberate cash buffer from idle cash.")
    if information_ratio is not None and information_ratio < 0:
        notes.append("Action: information ratio is negative; check whether active risk is being paid.")
    if not notes:
        notes.append("Action: no single scorecard dimension is flashing red; review factor rows for concentration.")

    return {
        "title": "Performance / Risk Scorecard",
        "summary": {
            "return_score": return_score,
            "risk_score": risk_score,
            "efficiency_score": efficiency_score,
            "benchmark_score": benchmark_score,
            "cash_drag_score": cash_drag_score,
            "overall_score": overall_score,
        },
        "cards": [
            _dashboard_card("return_score", "Return Score", return_score, "TWR, active return, win rate", "score"),
            _dashboard_card("risk_score", "Risk Score", risk_score, "drawdown, vol, down capture", "score"),
            _dashboard_card("efficiency_score", "Efficiency Score", efficiency_score, "Sharpe, Sortino, tail ratio", "score"),
            _dashboard_card("benchmark_score", "Benchmark Score", benchmark_score, "alpha quality vs benchmark", "score"),
            _dashboard_card("cash_drag_score", "Cash Drag Score", cash_drag_score, "cash drag penalty", "score"),
            _dashboard_card("overall_score", "Overall Score", overall_score, "average of available dimensions", "score"),
        ],
        "decision_notes": notes,
        "notes": ["Scores are transparent 0-100 review proxies for scanability; raw metrics remain the source of truth."],
    }


def _factor_row(key: str, label: str, value: Any, unit: str, note: str, direction: str = "neutral") -> Dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": value,
        "unit": unit,
        "note": note,
        "direction": direction,
    }


def _factor_style_attribution(
    benchmark: str,
    benchmark_cards: List[Dict[str, Any]],
    benchmark_exposure: Dict[str, Any],
    exposure_dashboard: Dict[str, Any],
    risk_budget: Dict[str, Any],
) -> Dict[str, Any]:
    summary = benchmark_exposure.get("summary") or {}
    exposure_summary = exposure_dashboard.get("summary") or {}
    beta = _module_card_value(benchmark_cards, "beta")
    semiconductor_weight = _safe_float(summary.get("semiconductor_weight"))
    hk_china_weight = _safe_float(summary.get("hk_china_weight"))
    non_hkd_weight = _safe_float(summary.get("non_hkd_weight"))
    largest_weight = _safe_float(summary.get("largest_holding_weight"))
    largest_ticker = str(summary.get("largest_holding_ticker") or "")
    cash_weight = _safe_float(exposure_summary.get("latest_cash_pct"))
    if cash_weight is None:
        cash_weight = _safe_float(exposure_summary.get("average_cash_pct"))

    risk_summary = risk_budget.get("summary") or {}
    largest_risk_ticker = str(risk_summary.get("largest_risk_ticker") or "")
    largest_risk_contribution = _safe_float(risk_summary.get("largest_risk_contribution_pct"))
    tlt_weight = 0.0
    for row in risk_budget.get("rows") or []:
        ticker = str((row or {}).get("ticker") or "").upper()
        if ticker in {"TLT", "IEF", "SHY"}:
            tlt_weight += _safe_float((row or {}).get("weight_pct")) or 0.0

    rows = [
        _factor_row("market_beta", "Market Beta", _round(beta, 4), "number", f"return regression against {benchmark}", "risk_on" if beta is not None and beta >= 1 else "defensive"),
        _factor_row("semiconductor_growth", "Semiconductor / Growth", _round(semiconductor_weight, 4), "pct", "SOXX/SMH-like sleeve weight", "risk_on"),
        _factor_row("hk_china", "HK / China", _round(hk_china_weight, 4), "pct", "HK/CN listed sleeve weight", "regional"),
        _factor_row("defensive_duration", "Defensive / Duration", _round(tlt_weight, 4), "pct", "bond-duration style hedge proxy", "defensive"),
        _factor_row("cash_buffer", "Cash Buffer", _round(cash_weight, 4), "pct", "latest or average portfolio cash", "defensive"),
        _factor_row("fx_translation", "FX Translation", _round(non_hkd_weight, 4), "pct", "non-HKD exposure weight", "currency"),
        _factor_row("single_name_concentration", "Single-name Concentration", _round(largest_weight, 4), "pct", largest_ticker or "largest holding weight", "concentration"),
    ]
    ranked = sorted(
        rows,
        key=lambda row: abs(_safe_float(row.get("value")) or 0.0) if row.get("key") != "market_beta" else abs((_safe_float(row.get("value")) or 0.0) * 50.0),
        reverse=True,
    )
    dominant = ranked[0] if ranked else {}
    notes = [
        f"Action: dominant factor is {dominant.get('label') or '--'}; size sizing and review notes around that exposure.",
    ]
    if largest_weight is not None and largest_weight >= 25:
        notes.append(f"Action: {largest_ticker or 'largest holding'} is {largest_weight:.1f}% of holdings; stress-test single-name downside.")
    if semiconductor_weight is not None and semiconductor_weight >= 35:
        notes.append("Action: semiconductor/growth sleeve is large; compare against SOXX rather than only QQQ.")
    if cash_weight is not None and cash_weight >= 25:
        notes.append("Action: cash buffer is high; performance should be read as both portfolio return and invested-capital return.")
    if largest_risk_contribution is not None and largest_risk_contribution >= 35:
        notes.append(f"Action: largest risk contributor is {largest_risk_ticker}; verify position sizing is intentional.")

    return {
        "title": "Factor / Style Attribution",
        "summary": {
            "benchmark": benchmark,
            "dominant_factor": dominant.get("label"),
            "dominant_factor_value": dominant.get("value"),
            "largest_risk_ticker": largest_risk_ticker,
            "largest_risk_contribution_pct": _round(largest_risk_contribution, 4),
        },
        "rows": rows,
        "decision_notes": notes,
        "notes": ["Factor rows use local holdings/exposure proxies so they remain available without external factor datasets."],
    }


def _build_finance_toolkit_enrichment(
    analytics: Dict[str, Any],
    source_rows: List[Dict[str, Any]],
    returns: pd.Series,
    benchmark_returns: pd.Series,
    benchmark: str,
    reviews: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    if not analytics.get("success"):
        return {
            "status": "unavailable",
            "source": "local_financetoolkit_style",
            "reason": analytics.get("error") or "insufficient_return_series",
            "modules": {},
            "charts": [],
        }
    metrics = analytics.get("metrics") or {}
    relative = analytics.get("relative") or {}
    exposure = analytics.get("exposure") or {}
    tear_sheet = analytics.get("tear_sheet") or {}
    exposure_rows = _latest_cash_exposure_rows(tear_sheet)
    latest_exposure = exposure_rows[-1] if exposure_rows else {}
    benchmark_cards, benchmark_summary = _benchmark_attribution_cards(returns.dropna(), benchmark_returns.dropna(), relative, benchmark)
    benchmark_waterfall, benchmark_waterfall_summary = _benchmark_attribution_waterfall(
        metrics,
        relative,
        exposure,
        source_rows,
        benchmark_cards,
        benchmark,
    )
    holding_quality = _holding_quality_snapshot(reviews)
    downside_vol = _downside_volatility_pct(returns)
    exposure_dashboard = {
        "title": "Exposure Dashboard",
        "summary": {
            "average_exposure_pct": exposure.get("average_exposure_pct"),
            "average_cash_pct": exposure.get("average_cash_pct"),
            "latest_invested_pct": _round(_safe_float(latest_exposure.get("invested_pct")), 4),
            "latest_cash_pct": _round(_safe_float(latest_exposure.get("cash_pct")), 4),
            "cash_drag_ppt": exposure.get("cash_drag_ppt"),
        },
        "cards": [
            _dashboard_card("average_exposure", "Avg Exposure", exposure.get("average_exposure_pct"), "invested capital", "pct"),
            _dashboard_card("average_cash", "Avg Cash", exposure.get("average_cash_pct"), "cash balance", "pct"),
            _dashboard_card("latest_invested", "Latest Invested", _safe_float(latest_exposure.get("invested_pct")), "latest daily row", "pct"),
            _dashboard_card("cash_drag", "Cash Drag", exposure.get("cash_drag_ppt"), "vs invested return", "ppt"),
        ],
        "chart_rows": exposure_rows,
        "notes": ["Exposure uses daily NAV and cash rows from the local ledger simulator."],
    }
    trade_journal = _build_fifo_trade_journal(reviews)
    security_performance = _build_security_performance_table(trade_journal, reviews)
    security_performance_ledger = _security_performance_ledger(security_performance, relative)
    holding_return_attribution = _holding_return_attribution(security_performance, holding_quality)
    drawdown_attribution = _drawdown_attribution(
        analytics,
        returns,
        benchmark_returns,
        holding_quality,
        exposure_dashboard,
    )
    risk_budget = _risk_budget(holding_quality, source_rows)
    benchmark_exposure = _benchmark_exposure_decomposition(
        benchmark,
        benchmark_cards,
        holding_quality,
        exposure_dashboard,
    )
    scenario_stress = _scenario_stress(
        source_rows,
        benchmark_cards,
        holding_quality,
        benchmark_exposure,
    )
    portfolio_overview = _portfolio_overview(
        source_rows,
        holding_quality,
        exposure_dashboard,
        holding_return_attribution,
        benchmark_exposure,
    )
    performance_risk_scorecard = _performance_risk_scorecard(
        metrics,
        relative,
        exposure_dashboard,
        benchmark_cards,
    )
    factor_style_attribution = _factor_style_attribution(
        benchmark,
        benchmark_cards,
        benchmark_exposure,
        exposure_dashboard,
        risk_budget,
    )
    modules = {
        "portfolio_overview": portfolio_overview,
        "performance_risk_scorecard": performance_risk_scorecard,
        "factor_style_attribution": factor_style_attribution,
        "performance_metrics": {
            "title": "Performance Metrics",
            "cards": [
                _dashboard_card("cumulative_return", "Cumulative Return", metrics.get("cumulative_return_pct"), "daily linked TWR", "pct"),
                _dashboard_card("cagr", "CAGR", metrics.get("cagr_pct"), "annualized", "pct"),
                _dashboard_card("win_rate", "Win Rate", metrics.get("win_rate_pct"), "daily hit rate", "pct"),
                _dashboard_card("payoff_ratio", "Payoff Ratio", metrics.get("payoff_ratio"), "avg win / avg loss", "number"),
            ],
            "notes": ["Performance metrics are calculated from the daily TWR return series."],
        },
        "risk_metrics": {
            "title": "Risk Metrics",
            "cards": [
                _dashboard_card("volatility", "Volatility", metrics.get("volatility_annualized_pct"), "annualized", "pct"),
                _dashboard_card("downside_volatility", "Downside Vol", downside_vol, "annualized negative days", "pct"),
                _dashboard_card("max_drawdown", "Max Drawdown", metrics.get("max_drawdown_pct"), "peak-to-trough", "pct"),
                _dashboard_card("var_95", "VaR 95", metrics.get("var_95_pct"), "daily left tail", "pct"),
                _dashboard_card("cvar_95", "CVaR 95", metrics.get("cvar_95_pct"), "tail average", "pct"),
            ],
            "notes": ["Risk metrics mirror FinanceToolkit-style risk review, using the local return series only."],
        },
        "benchmark_attribution": {
            "title": "Benchmark Attribution",
            "summary": {**benchmark_summary, **benchmark_waterfall_summary},
            "cards": benchmark_cards,
            "waterfall": benchmark_waterfall,
            "notes": [f"Benchmark attribution is computed against {benchmark} over the exact portfolio date range."],
        },
        "holdings_quality_snapshot": holding_quality,
        "exposure_dashboard": exposure_dashboard,
        "security_performance_ledger": security_performance_ledger,
        "holding_return_attribution": holding_return_attribution,
        "drawdown_attribution": drawdown_attribution,
        "risk_budget": risk_budget,
        "benchmark_exposure_decomposition": benchmark_exposure,
        "scenario_stress": scenario_stress,
    }
    return {
        "status": "available",
        "source": "local_financetoolkit_style",
        "title": "FinanceToolkit-style Portfolio Enrichment",
        "modules": modules,
        "charts": [
            {
                "key": "finance_toolkit_exposure_mix",
                "title": "Exposure Mix",
                "kind": "line",
                "rows": exposure_rows,
            },
            {
                "key": "finance_toolkit_holding_quality",
                "title": "Holding Quality Score",
                "kind": "bar",
                "rows": holding_quality.get("rows") or [],
            },
            {
                "key": "finance_toolkit_return_contribution",
                "title": "Return Contribution",
                "kind": "bar",
                "rows": holding_return_attribution.get("rows") or [],
            },
            {
                "key": "finance_toolkit_risk_budget",
                "title": "Risk Budget",
                "kind": "bar",
                "rows": risk_budget.get("rows") or [],
            },
            {
                "key": "finance_toolkit_scenario_stress",
                "title": "Scenario Stress",
                "kind": "bar",
                "rows": scenario_stress.get("rows") or [],
            },
            {
                "key": "finance_toolkit_factor_style",
                "title": "Factor / Style Attribution",
                "kind": "bar",
                "rows": factor_style_attribution.get("rows") or [],
            },
        ],
        "notes": [
            "FinanceToolkit is treated as an analytics vocabulary here, not a dependency in the core ledger path.",
            "Core NAV/TWR remains local and deterministic; this layer enriches review and attribution only.",
        ],
    }


def _latest_chart_value(tear_sheet: Dict[str, Any], chart_key: str, row_key: str = "value") -> Optional[float]:
    for chart in tear_sheet.get("charts") or []:
        if (chart or {}).get("key") != chart_key:
            continue
        rows = (chart or {}).get("rows") or []
        if not rows:
            return None
        return _safe_float((rows[-1] or {}).get(row_key))
    return None


def _latest_review(reviews: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    review_list = [review for review in reviews or [] if isinstance(review, dict)]
    if not review_list:
        return {}
    return review_list[-1]


def _stock_label(stock_id: str, payload: Dict[str, Any], aliases: Optional[Dict[str, str]] = None) -> str:
    for key in ("ticker", "stock_id"):
        value = str((payload or {}).get(key) or "").strip()
        if value:
            return _canonical_ticker(value, aliases)
    stock_key = _canonical_ticker(stock_id, aliases)
    if stock_key:
        return stock_key
    for key in ("name", "stock_name"):
        value = str((payload or {}).get(key) or "").strip()
        if value:
            return value
    return "--"


def _holding_trade_stats(reviews: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    review_list = list(reviews or [])
    aliases = _ticker_aliases_from_reviews(review_list)
    latest = _latest_review(review_list)
    rows: List[Dict[str, Any]] = []

    for stock_id, payload in ((latest.get("stocks") or {}) if isinstance(latest, dict) else {}).items():
        if not isinstance(payload, dict):
            continue
        shares = _safe_float(payload.get("shares_held")) or 0.0
        if shares <= 0:
            continue
        metrics = payload.get("position_metrics") or {}
        pnl = _safe_float(metrics.get("unrealized_pnl_hkd"))
        if pnl is None:
            realized = _safe_float(metrics.get("realized_pnl_hkd")) or 0.0
            unrealized = _safe_float(metrics.get("unrealized_pnl_hkd")) or 0.0
            pnl = realized + unrealized if realized or unrealized else None
        rows.append(
            {
                "status": "current",
                "ticker": _stock_label(str(stock_id), payload, aliases),
                "week_id": str(latest.get("week_id") or ""),
                "pnl_hkd": _round(pnl, 2),
                "return_pct": _round(_safe_float(metrics.get("return_since_buy")), 4),
                "holding_value_hkd": _round(_safe_float(metrics.get("holding_value_hkd")), 2),
                "shares": _round(shares, 4),
            }
        )

    seen_closed: set[tuple[str, str, str, str, str]] = set()
    for review in review_list:
        if not isinstance(review, dict):
            continue
        week_id = str(review.get("week_id") or "")
        for item in review.get("closed_positions") or []:
            if not isinstance(item, dict):
                continue
            stock_id = _stock_label(str(item.get("stock_id") or ""), item, aliases)
            key = (
                stock_id,
                str(item.get("sell_date") or item.get("date") or week_id),
                str(item.get("shares_sold") or item.get("quantity") or ""),
                str(item.get("sell_price") or item.get("price") or ""),
                str(item.get("realized_pnl_hkd") or item.get("realized_pnl") or ""),
            )
            if key in seen_closed:
                continue
            seen_closed.add(key)
            pnl = _safe_float(item.get("realized_pnl_hkd"))
            if pnl is None:
                pnl = _safe_float(item.get("weekly_realized_pnl_hkd"))
            if pnl is None:
                pnl = _safe_float(item.get("realized_pnl"))
            rows.append(
                {
                    "status": "closed",
                    "ticker": stock_id,
                    "week_id": week_id,
                    "pnl_hkd": _round(pnl, 2),
                    "return_pct": _round(_safe_float(item.get("return_pct") or item.get("realized_return_pct")), 4),
                    "holding_value_hkd": None,
                    "shares": _round(_safe_float(item.get("shares_sold") or item.get("quantity")), 4),
                }
            )

    positive = [float(row["pnl_hkd"]) for row in rows if _safe_float(row.get("pnl_hkd")) is not None and float(row["pnl_hkd"]) > 0]
    negative = [abs(float(row["pnl_hkd"])) for row in rows if _safe_float(row.get("pnl_hkd")) is not None and float(row["pnl_hkd"]) < 0]
    breakeven = [row for row in rows if (_safe_float(row.get("pnl_hkd")) or 0.0) == 0.0 and row.get("pnl_hkd") is not None]
    denominator = len(positive) + len(negative)
    avg_win = float(np.mean(positive)) if positive else None
    avg_loss = float(np.mean(negative)) if negative else None
    rows.sort(key=lambda row: _safe_float(row.get("pnl_hkd")) or 0.0, reverse=True)
    return {
        "summary": {
            "total_positions": len(rows),
            "current_positions": sum(1 for row in rows if row.get("status") == "current"),
            "closed_positions": sum(1 for row in rows if row.get("status") == "closed"),
            "winning_positions": len(positive),
            "losing_positions": len(negative),
            "breakeven_positions": len(breakeven),
            "win_rate_pct": _round((len(positive) / denominator * 100.0) if denominator else None, 4),
            "average_win_hkd": _round(avg_win, 2),
            "average_loss_hkd": _round(avg_loss, 2),
            "profit_loss_ratio": _round((avg_win / avg_loss) if avg_win is not None and avg_loss and avg_loss > 0 else None, 4),
            "gross_profit_hkd": _round(sum(positive), 2),
            "gross_loss_hkd": _round(sum(negative), 2),
            "profit_factor": _round((sum(positive) / sum(negative)) if negative and sum(negative) > 0 else None, 4),
        },
        "rows": rows[:50],
        "notes": [
            "Current positions use unrealized P/L from the latest weekly review position metrics.",
            "Closed positions use realized P/L from closed_positions and are de-duplicated across selected reviews.",
        ],
    }


def _trade_row_sort_key(row: Dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("exit_date") or row.get("date") or row.get("week_id") or ""),
        str(row.get("entry_date") or ""),
        str(row.get("ticker") or ""),
    )


def _streak_summary(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    max_win = 0
    max_loss = 0
    current_direction = ""
    current_count = 0
    best_direction = ""

    for row in trades:
        pnl = _safe_float(row.get("pnl_hkd"))
        if pnl is None or abs(pnl) <= 1e-9:
            current_direction = ""
            current_count = 0
            continue
        direction = "win" if pnl > 0 else "loss"
        if direction == current_direction:
            current_count += 1
        else:
            current_direction = direction
            current_count = 1
        if direction == "win":
            max_win = max(max_win, current_count)
        else:
            max_loss = max(max_loss, current_count)
        best_direction = direction

    if trades:
        last_pnl = _safe_float(trades[-1].get("pnl_hkd"))
        if last_pnl is None or abs(last_pnl) <= 1e-9:
            current_direction = ""
            current_count = 0
        else:
            current_direction = "win" if last_pnl > 0 else "loss"
            current_count = 1
            for row in reversed(trades[:-1]):
                pnl = _safe_float(row.get("pnl_hkd"))
                if pnl is None or abs(pnl) <= 1e-9:
                    break
                direction = "win" if pnl > 0 else "loss"
                if direction != current_direction:
                    break
                current_count += 1

    return {
        "max_win_streak": max_win,
        "max_loss_streak": max_loss,
        "current_streak_direction": current_direction or "flat",
        "current_streak": current_count,
        "last_streak_direction": best_direction or "flat",
    }


def _histogram_rows(values: Iterable[float], *, buckets: int = 12, value_label: str = "value", bucket_unit: str = "") -> List[Dict[str, Any]]:
    cleaned = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not cleaned:
        return []
    array = np.array(cleaned, dtype=float)
    if np.allclose(array.min(), array.max()):
        label = f"{array.min():+.2f}{bucket_unit}"
        return [{"bucket": label, "count": int(len(array)), value_label: round(float(array.min()), 4)}]
    counts, edges = np.histogram(array, bins=buckets)
    rows: List[Dict[str, Any]] = []
    for idx, count in enumerate(counts):
        rows.append(
            {
                "bucket": f"{edges[idx]:+.2f}{bucket_unit} to {edges[idx + 1]:+.2f}{bucket_unit}",
                "count": int(count),
                value_label: round(float((edges[idx] + edges[idx + 1]) / 2.0), 4),
            }
        )
    return rows


def _daily_pnl_rows(source_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    previous_nav: Optional[float] = None
    for row in source_rows or []:
        date = str(row.get("date") or "")
        if not date:
            continue
        nav = _safe_float(row.get("portfolio_value_hkd"))
        if nav is None:
            continue
        explicit_flow = _safe_float(row.get("explicit_cash_flow_hkd")) or 0.0
        implied_flow = _safe_float(row.get("implied_cash_flow_hkd")) or 0.0
        if previous_nav is None:
            daily_pnl = 0.0
        else:
            daily_pnl = nav - previous_nav - explicit_flow - implied_flow
        rows.append(
            {
                "date": date,
                "weekday": pd.Timestamp(date).day_name(),
                "weekday_index": int(pd.Timestamp(date).dayofweek),
                "daily_pnl_hkd": round(float(daily_pnl), 2),
                "daily_return_pct": _round(_safe_float(row.get("period_return")) * 100.0 if _safe_float(row.get("period_return")) is not None else None, 4),
                "portfolio_value_hkd": _round(nav, 2),
                "is_win": daily_pnl > 0,
            }
        )
        previous_nav = nav
    return rows


def _weekday_pnl_rows(daily_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    grouped: Dict[str, List[Dict[str, Any]]] = {weekday: [] for weekday in weekday_order}
    for row in daily_rows or []:
        grouped.setdefault(str(row.get("weekday") or ""), []).append(row)
    rows: List[Dict[str, Any]] = []
    for weekday in weekday_order:
        items = grouped.get(weekday) or []
        pnl_values = [float(item.get("daily_pnl_hkd") or 0.0) for item in items]
        positive = [value for value in pnl_values if value > 0]
        rows.append(
            {
                "weekday": weekday,
                "count": len(items),
                "win_rate_pct": _round((len(positive) / len(items) * 100.0) if items else None, 4),
                "total_pnl_hkd": _round(sum(pnl_values), 2),
                "average_pnl_hkd": _round(float(np.mean(pnl_values)) if pnl_values else None, 2),
            }
        )
    return rows


def _rolling_trade_win_rate_rows(trades: List[Dict[str, Any]], window: int = 20) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    closed = [row for row in sorted(trades or [], key=_trade_row_sort_key) if _safe_float(row.get("pnl_hkd")) is not None]
    for index, row in enumerate(closed):
        start = max(0, index - window + 1)
        sample = closed[start : index + 1]
        wins = sum(1 for item in sample if (float(item.get("pnl_hkd") or 0.0)) > 0)
        count = len(sample)
        rows.append(
            {
                "date": str(row.get("exit_date") or row.get("date") or row.get("entry_date") or ""),
                "trade_index": index + 1,
                "win_rate_pct": _round((wins / count * 100.0) if count else None, 4),
                "sample_count": count,
            }
        )
    return rows


def _best_worst_trade_row(trades: List[Dict[str, Any]], *, best: bool) -> Optional[Dict[str, Any]]:
    closed = [row for row in trades or [] if _safe_float(row.get("pnl_hkd")) is not None]
    if not closed:
        return None
    row = max(closed, key=lambda item: float(item.get("pnl_hkd") or 0.0)) if best else min(closed, key=lambda item: float(item.get("pnl_hkd") or 0.0))
    return {
        "ticker": row.get("ticker"),
        "date": row.get("exit_date") or row.get("date") or row.get("entry_date") or "",
        "pnl_hkd": _round(_safe_float(row.get("pnl_hkd")), 2),
        "return_pct": _round(_safe_float(row.get("return_pct")), 4),
        "quantity": _round(_safe_float(row.get("quantity")), 4),
        "entry_price_hkd": _round(_safe_float(row.get("entry_price_hkd")), 4),
        "exit_price_hkd": _round(_safe_float(row.get("exit_price_hkd")), 4),
    }


def _trade_performance_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    closed = [row for row in trades or [] if str(row.get("status") or "").lower() == "closed" and _safe_float(row.get("pnl_hkd")) is not None]
    if not closed:
        return {
            "closed_trade_count": 0,
            "net_pnl_hkd": None,
            "win_rate_pct": None,
            "profit_factor": None,
            "expectancy_hkd": None,
            "average_win_hkd": None,
            "average_loss_hkd": None,
            "best_trade": None,
            "worst_trade": None,
        }

    pnl_values = [float(row.get("pnl_hkd") or 0.0) for row in closed]
    wins = [value for value in pnl_values if value > 0]
    losses = [abs(value) for value in pnl_values if value < 0]
    net_pnl = float(sum(pnl_values))
    avg_win = float(np.mean(wins)) if wins else None
    avg_loss = float(np.mean(losses)) if losses else None
    performance = {
        "closed_trade_count": len(closed),
        "net_pnl_hkd": _round(net_pnl, 2),
        "win_rate_pct": _round((len(wins) / len(closed) * 100.0) if closed else None, 4),
        "profit_factor": _round((sum(wins) / sum(losses)) if losses and sum(losses) > 0 else None, 4),
        "expectancy_hkd": _round(net_pnl / len(closed) if closed else None, 2),
        "average_win_hkd": _round(avg_win, 2),
        "average_loss_hkd": _round(avg_loss, 2),
        "best_trade": _best_worst_trade_row(closed, best=True),
        "worst_trade": _best_worst_trade_row(closed, best=False),
    }
    performance.update(_streak_summary(closed))
    return performance


def _risk_metrics_from_returns(returns: pd.Series) -> Dict[str, Any]:
    cleaned = returns.dropna()
    if cleaned.empty:
        return {
            "max_drawdown_pct": None,
            "max_drawdown_duration_days": None,
            "current_drawdown_duration_days": None,
            "longest_drawdown_duration_days": None,
        }
    wealth = (1.0 + cleaned).cumprod()
    peaks = wealth.cummax()
    drawdowns = wealth / peaks - 1.0
    longest = 0
    current = 0
    current_start = None
    current_trough = None
    current_trough_value = 0.0
    for index, value in drawdowns.items():
        drawdown_pct = float(value) * 100.0
        if drawdown_pct < 0:
            if current_start is None:
                current_start = index
                current_trough = index
                current_trough_value = drawdown_pct
                current = 1
            else:
                current += 1
                if drawdown_pct < current_trough_value:
                    current_trough_value = drawdown_pct
                    current_trough = index
            longest = max(longest, current)
        else:
            current_start = None
            current_trough = None
            current_trough_value = 0.0
            current = 0
    return {
        "max_drawdown_pct": _round(float(drawdowns.min()) * 100.0, 4),
        "max_drawdown_duration_days": int(longest),
        "current_drawdown_duration_days": int(current),
        "longest_drawdown_duration_days": longest,
    }


def _consistency_metrics(source_rows: List[Dict[str, Any]], trades: List[Dict[str, Any]], daily_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    trade_rows = [row for row in sorted(trades or [], key=_trade_row_sort_key) if str(row.get("status") or "").lower() == "closed" and _safe_float(row.get("pnl_hkd")) is not None]
    rolling_rows = _rolling_trade_win_rate_rows(trade_rows, window=20)
    weekday_rows = _weekday_pnl_rows(daily_rows)
    pnl_values = [float(row.get("daily_pnl_hkd") or 0.0) for row in daily_rows]
    positive_days = [value for value in pnl_values if value > 0]
    down_days = [value for value in pnl_values if value < 0]
    return {
        "rolling_trade_win_rate_rows": rolling_rows,
        "rolling_trade_win_rate_pct": _round(rolling_rows[-1]["win_rate_pct"] if rolling_rows else None, 4),
        "daily_pnl_distribution_rows": _histogram_rows(pnl_values, buckets=12, value_label="bucket_mid_hkd", bucket_unit="HKD"),
        "weekday_rows": weekday_rows,
        "positive_day_rate_pct": _round((len(positive_days) / len(pnl_values) * 100.0) if pnl_values else None, 4),
        "average_daily_pnl_hkd": _round(float(np.mean(pnl_values)) if pnl_values else None, 2),
        "win_days_count": len(positive_days),
        "loss_days_count": len(down_days),
        "daily_point_count": len(pnl_values),
        "source_point_count": len(source_rows or []),
    }


def _r_multiple_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    closed = [row for row in sorted(trades or [], key=_trade_row_sort_key) if str(row.get("status") or "").lower() == "closed" and _safe_float(row.get("pnl_hkd")) is not None]
    losses = [abs(float(row.get("pnl_hkd") or 0.0)) for row in closed if float(row.get("pnl_hkd") or 0.0) < 0]
    one_r = float(np.mean(losses)) if losses else None
    if one_r is None or one_r <= 0:
        return {
            "proxy_1r_hkd": None,
            "average_r": None,
            "expectancy_r": None,
            "r_multiple_rows": [],
            "r_histogram_rows": [],
        }

    r_values = [float(row.get("pnl_hkd") or 0.0) / one_r for row in closed]
    rows: List[Dict[str, Any]] = []
    for row, r_value in zip(closed, r_values):
        rows.append(
            {
                "ticker": row.get("ticker"),
                "date": row.get("exit_date") or row.get("date") or row.get("entry_date") or "",
                "pnl_hkd": _round(_safe_float(row.get("pnl_hkd")), 2),
                "r_multiple": _round(r_value, 4),
                "return_pct": _round(_safe_float(row.get("return_pct")), 4),
            }
        )
    average_r = float(np.mean(r_values)) if r_values else None
    return {
        "proxy_1r_hkd": _round(one_r, 2),
        "average_r": _round(average_r, 4),
        "expectancy_r": _round(average_r, 4),
        "r_multiple_rows": rows,
        "r_histogram_rows": _histogram_rows(r_values, buckets=12, value_label="bucket_mid_r", bucket_unit="R"),
    }


def _build_analytics_engine_dashboard(
    analytics: Dict[str, Any],
    source_rows: List[Dict[str, Any]],
    returns: pd.Series,
    benchmark_returns: pd.Series,
    trade_journal: Dict[str, Any],
) -> Dict[str, Any]:
    trade_rows = sorted(
        [row for row in trade_journal.get("rows") or [] if str(row.get("status") or "").lower() == "closed" and _safe_float(row.get("pnl_hkd")) is not None],
        key=_trade_row_sort_key,
    )
    daily_rows = _daily_pnl_rows(source_rows)
    performance = _trade_performance_metrics(trade_rows)
    risk = _risk_metrics_from_returns(returns)
    consistency = _consistency_metrics(source_rows, trade_rows, daily_rows)
    r_multiple = _r_multiple_metrics(trade_rows)
    risk_regime = _risk_regime_state_machine(source_rows, returns, benchmark_returns, analytics.get("drawdown_series") or [])
    metrics = analytics.get("metrics") or {}
    equity_rows = [
        {
            "date": row.get("date"),
            "equity_hkd": _round(_safe_float(row.get("portfolio_value_hkd")), 2),
            "cumulative_return_pct": _round((_safe_float(row.get("portfolio_twr")) or 0.0) * 100.0, 4),
            "daily_pnl_hkd": _round(_safe_float(next((daily.get("daily_pnl_hkd") for daily in daily_rows if daily.get("date") == row.get("date")), None)), 2),
        }
        for row in source_rows
        if row.get("date")
    ]
    drawdown_rows = _drawdown_periods(analytics.get("drawdown_series") or [])

    modules = {
        "performance_metrics": {
            "title": "Performance Metrics",
            "benchmark": analytics.get("relative", {}).get("benchmark") or "",
            "cards": [
                {"key": "net_pnl", "label": "Net P&L", "value": performance.get("net_pnl_hkd"), "unit": "hkd", "note": "closed trades"},
                {"key": "win_rate", "label": "Win Rate", "value": performance.get("win_rate_pct"), "unit": "pct", "note": "closed trades"},
                {"key": "profit_factor", "label": "Profit Factor", "value": performance.get("profit_factor"), "unit": "number", "note": "gross win / gross loss"},
                {"key": "expectancy", "label": "Expectancy", "value": performance.get("expectancy_hkd"), "unit": "hkd", "note": "per closed trade"},
                {"key": "average_win", "label": "Average Win", "value": performance.get("average_win_hkd"), "unit": "hkd", "note": "winning trades"},
                {"key": "average_loss", "label": "Average Loss", "value": performance.get("average_loss_hkd"), "unit": "hkd", "note": "losing trades"},
                {"key": "best_trade", "label": "Best Trade", "value": performance.get("best_trade") and f"{performance['best_trade'].get('ticker') or '--'} {performance['best_trade'].get('pnl_hkd'):+.0f}" if performance.get("best_trade") and performance["best_trade"].get("pnl_hkd") is not None else None, "unit": "text", "note": performance.get("best_trade", {}).get("date") if performance.get("best_trade") else "--"},
                {"key": "worst_trade", "label": "Worst Trade", "value": performance.get("worst_trade") and f"{performance['worst_trade'].get('ticker') or '--'} {performance['worst_trade'].get('pnl_hkd'):+.0f}" if performance.get("worst_trade") and performance["worst_trade"].get("pnl_hkd") is not None else None, "unit": "text", "note": performance.get("worst_trade", {}).get("date") if performance.get("worst_trade") else "--"},
                {"key": "max_win_streak", "label": "Max Win Streak", "value": performance.get("max_win_streak"), "unit": "number", "note": "closed trades"},
                {"key": "max_loss_streak", "label": "Max Loss Streak", "value": performance.get("max_loss_streak"), "unit": "number", "note": "closed trades"},
                {"key": "current_streak", "label": "Current Streak", "value": f"{performance.get('current_streak_direction', 'flat').title()} x{performance.get('current_streak') or 0}", "unit": "text", "note": "latest trade run"},
            ],
            "tables": [
                {
                    "key": "trade_performance_rows",
                    "title": "Closed Trade Ledger",
                    "columns": ["date", "ticker", "pnl_hkd", "return_pct"],
                    "rows": trade_rows[:30],
                },
            ],
            "charts": [
                {
                    "key": "equity_curve",
                    "title": "Equity Curve",
                    "kind": "line",
                    "rows": equity_rows,
                },
            ],
            "summary": performance,
        },
        "risk_metrics": {
            "title": "Risk Metrics",
            "benchmark": analytics.get("relative", {}).get("benchmark") or "",
            "cards": [
                {"key": "sharpe", "label": "Sharpe", "value": metrics.get("sharpe"), "unit": "number", "note": "annualized"},
                {"key": "sortino", "label": "Sortino", "value": metrics.get("sortino"), "unit": "number", "note": "downside only"},
                {"key": "calmar", "label": "Calmar", "value": metrics.get("calmar"), "unit": "number", "note": "CAGR / max DD"},
                {"key": "max_drawdown", "label": "Max Drawdown", "value": metrics.get("max_drawdown_pct"), "unit": "pct", "note": "peak to trough"},
                {"key": "drawdown_days", "label": "Longest DD", "value": risk.get("longest_drawdown_duration_days"), "unit": "number", "note": "days"},
                {"key": "current_dd_days", "label": "Current DD", "value": risk.get("current_drawdown_duration_days"), "unit": "number", "note": "days"},
            ],
            "tables": [
                {
                    "key": "drawdown_periods",
                    "title": "Drawdown Periods",
                    "columns": ["start_date", "trough_date", "end_date", "max_drawdown_pct", "duration_days"],
                    "rows": drawdown_rows[:10],
                },
            ],
            "charts": [
                {
                    "key": "drawdown_curve",
                    "title": "Drawdown Curve",
                    "kind": "line",
                    "rows": analytics.get("drawdown_series") or [],
                },
            ],
            "summary": risk,
        },
        "risk_regime": {
            "title": "Risk Regime",
            "benchmark": analytics.get("relative", {}).get("benchmark") or "",
            "cards": [
                {"key": "current_regime", "label": "Current Regime", "value": risk_regime.get("summary", {}).get("current_regime_label"), "unit": "text", "note": risk_regime.get("summary", {}).get("latest_reason") or "rolling state machine"},
                {"key": "regime_score", "label": "Regime Score", "value": risk_regime.get("summary", {}).get("current_score"), "unit": "score", "note": "0-100"},
                {"key": "transitions", "label": "Transitions", "value": risk_regime.get("summary", {}).get("transition_count"), "unit": "number", "note": "window changes"},
            ],
            "tables": [
                {
                    "key": "risk_regime_path",
                    "title": "Risk Regime Path",
                    "columns": ["date", "regime_label", "score", "drawdown_pct", "rolling_sharpe", "rolling_beta", "tracking_error_pct", "correlation"],
                    "rows": risk_regime.get("rows") or [],
                },
            ],
            "charts": [
                {
                    "key": "analytics_engine_risk_regime",
                    "title": "Risk Regime Path",
                    "kind": "line",
                    "rows": risk_regime.get("rows") or [],
                },
            ],
            "summary": risk_regime.get("summary") or {},
            "notes": risk_regime.get("notes") or [],
        },
        "consistency_analysis": {
            "title": "Consistency Analysis",
            "benchmark": analytics.get("relative", {}).get("benchmark") or "",
            "cards": [
                {"key": "rolling_trade_win_rate", "label": "Rolling 20-Trade Win Rate", "value": consistency.get("rolling_trade_win_rate_pct"), "unit": "pct", "note": "latest window"},
                {"key": "positive_day_rate", "label": "Positive Day Rate", "value": consistency.get("positive_day_rate_pct"), "unit": "pct", "note": "daily pnl > 0"},
                {"key": "average_daily_pnl", "label": "Average Daily P&L", "value": consistency.get("average_daily_pnl_hkd"), "unit": "hkd", "note": "net of flows"},
                {"key": "win_days", "label": "Win Days", "value": consistency.get("win_days_count"), "unit": "number", "note": "daily rows"},
                {"key": "loss_days", "label": "Loss Days", "value": consistency.get("loss_days_count"), "unit": "number", "note": "daily rows"},
                {"key": "daily_points", "label": "Daily Points", "value": consistency.get("daily_point_count"), "unit": "number", "note": "series rows"},
            ],
            "tables": [
                {
                    "key": "weekday_pnl",
                    "title": "P&L by Weekday",
                    "columns": ["weekday", "count", "win_rate_pct", "total_pnl_hkd", "average_pnl_hkd"],
                    "rows": consistency.get("weekday_rows") or [],
                },
                {
                    "key": "daily_pnl_distribution",
                    "title": "Daily P&L Distribution",
                    "columns": ["bucket", "count"],
                    "rows": consistency.get("daily_pnl_distribution_rows") or [],
                },
            ],
            "charts": [
                {
                    "key": "rolling_trade_win_rate",
                    "title": "Rolling 20-Trade Win Rate",
                    "kind": "line",
                    "rows": consistency.get("rolling_trade_win_rate_rows") or [],
                },
            ],
            "summary": consistency,
        },
        "r_multiple_analysis": {
            "title": "R-Multiple Analysis",
            "benchmark": analytics.get("relative", {}).get("benchmark") or "",
            "cards": [
                {"key": "proxy_1r", "label": "Proxy 1R", "value": r_multiple.get("proxy_1r_hkd"), "unit": "hkd", "note": "avg loss proxy"},
                {"key": "average_r", "label": "Average R", "value": r_multiple.get("average_r"), "unit": "number", "note": "per closed trade"},
                {"key": "expectancy_r", "label": "Expectancy R", "value": r_multiple.get("expectancy_r"), "unit": "number", "note": "mean R"},
                {"key": "closed_trades", "label": "Closed Trades", "value": performance.get("closed_trade_count"), "unit": "number", "note": "R sample"},
            ],
            "tables": [
                {
                    "key": "r_multiple_table",
                    "title": "R Multiples",
                    "columns": ["date", "ticker", "pnl_hkd", "r_multiple", "return_pct"],
                    "rows": r_multiple.get("r_multiple_rows") or [],
                },
                {
                    "key": "r_multiple_histogram",
                    "title": "R Histogram",
                    "columns": ["bucket", "count"],
                    "rows": r_multiple.get("r_histogram_rows") or [],
                },
            ],
            "charts": [
                {
                    "key": "r_multiple_histogram",
                    "title": "R-Multiple Histogram",
                    "kind": "bar",
                    "rows": r_multiple.get("r_histogram_rows") or [],
                },
            ],
            "summary": r_multiple,
        },
    }

    summary = {
        "closed_trade_count": performance.get("closed_trade_count"),
        "net_pnl_hkd": performance.get("net_pnl_hkd"),
        "win_rate_pct": performance.get("win_rate_pct"),
        "profit_factor": performance.get("profit_factor"),
        "expectancy_hkd": performance.get("expectancy_hkd"),
        "sharpe": metrics.get("sharpe"),
        "sortino": metrics.get("sortino"),
        "calmar": metrics.get("calmar"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "rolling_trade_win_rate_pct": consistency.get("rolling_trade_win_rate_pct"),
        "average_r": r_multiple.get("average_r"),
        "expectancy_r": r_multiple.get("expectancy_r"),
        "risk_regime": risk_regime.get("summary") or {},
    }

    return {
        "title": "Analytics Engine",
        "status": "available" if performance.get("closed_trade_count") else "partial",
        "source": "journedge_style_local",
        "summary": summary,
        "modules": modules,
        "charts": [
            {"key": "analytics_engine_equity_curve", "title": "Equity Curve", "kind": "line", "rows": equity_rows},
            {"key": "analytics_engine_daily_pnl_distribution", "title": "Daily P&L Distribution", "kind": "bar", "rows": consistency.get("daily_pnl_distribution_rows") or []},
            {"key": "analytics_engine_weekday_pnl", "title": "P&L by Weekday", "kind": "bar", "rows": consistency.get("weekday_rows") or []},
            {"key": "analytics_engine_rolling_trade_win_rate", "title": "Rolling 20-Trade Win Rate", "kind": "line", "rows": consistency.get("rolling_trade_win_rate_rows") or []},
            {"key": "analytics_engine_r_multiple_histogram", "title": "R-Multiple Histogram", "kind": "bar", "rows": r_multiple.get("r_histogram_rows") or []},
            {"key": "analytics_engine_risk_regime", "title": "Risk Regime Path", "kind": "line", "rows": risk_regime.get("rows") or []},
        ],
    }


def _normal_action(op_type: Any) -> str:
    if is_buy_like_op(op_type):
        return "buy"
    if is_sell_like_op(op_type):
        return "sell"
    return ""


def _op_sort_key(row: Dict[str, Any]) -> tuple[str, int]:
    return (str(row.get("date") or ""), int(row.get("_seq") or 0))


def _collect_rebalancing_ops(reviews: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    review_list = list(reviews or [])
    aliases = _ticker_aliases_from_reviews(review_list)
    rows: List[Dict[str, Any]] = []
    sequence = 0
    for review in review_list:
        if not isinstance(review, dict):
            continue
        week_id = str(review.get("week_id") or "")
        for op in review.get("rebalancing_ops") or []:
            if not isinstance(op, dict):
                continue
            action = _normal_action(op.get("op_type") or op.get("action"))
            if not action:
                continue
            quantity = _safe_float(op.get("quantity") or op.get("shares")) or 0.0
            price = _safe_float(op.get("price"))
            ticker = _canonical_ticker(op.get("ticker") or op.get("stock_id"), aliases)
            if not ticker or quantity <= 0 or price is None:
                continue
            currency = _detect_currency(ticker)
            fx_rate = _fx_rate_for(review, currency)
            rows.append(
                {
                    "_seq": sequence,
                    "week_id": week_id,
                    "date": _date_text_or_week_end(op.get("date"), week_id),
                    "ticker": ticker,
                    "action": action,
                    "quantity": quantity,
                    "price": price,
                    "currency": currency,
                    "fx_rate_to_hkd": fx_rate,
                    "price_hkd": price * fx_rate,
                }
            )
            sequence += 1
    rows.sort(key=_op_sort_key)
    return rows


def _latest_current_position_map(reviews: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    review_list = list(reviews or [])
    aliases = _ticker_aliases_from_reviews(review_list)
    latest = _latest_review(review_list)
    positions: Dict[str, Dict[str, Any]] = {}
    for stock_id, payload in ((latest.get("stocks") or {}) if isinstance(latest, dict) else {}).items():
        if not isinstance(payload, dict):
            continue
        ticker = _stock_label(str(stock_id), payload, aliases)
        shares = _safe_float(payload.get("shares_held")) or 0.0
        if not ticker or shares <= 0:
            continue
        positions[ticker] = payload
    return positions


def _position_entry_value_hkd(payload: Dict[str, Any], shares: float, currency: str, fx_rate: float) -> Optional[float]:
    metrics = (payload or {}).get("position_metrics") or {}
    holding_value = _safe_float(metrics.get("holding_value_hkd"))
    pnl = _safe_float(metrics.get("unrealized_pnl_hkd"))
    if holding_value is not None and pnl is not None:
        entry_value = holding_value - pnl
        if entry_value >= 0:
            return entry_value
    avg_cost = _safe_float((payload or {}).get("avg_cost"))
    if avg_cost is not None and shares > 0:
        return avg_cost * shares * fx_rate
    return None


def _initial_fifo_lots_from_reviews(reviews: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    review_list = list(reviews or [])
    aliases = _ticker_aliases_from_reviews(review_list)
    first = _latest_review(review_list[:1])
    week_id = str(first.get("week_id") or "")
    first_week_delta_by_ticker: Dict[str, float] = {}
    for op in _collect_rebalancing_ops([first]):
        ticker = str((op or {}).get("ticker") or "")
        quantity = _safe_float((op or {}).get("quantity")) or 0.0
        if not ticker or quantity <= 0:
            continue
        # First review stocks are end-of-week holdings. Back out same-week
        # buys/sells to seed only the position that existed before the window.
        signed_delta = quantity if (op or {}).get("action") == "buy" else -quantity
        first_week_delta_by_ticker[ticker] = first_week_delta_by_ticker.get(ticker, 0.0) + signed_delta
    lots: Dict[str, List[Dict[str, Any]]] = {}
    for stock_id, payload in ((first.get("stocks") or {}) if isinstance(first, dict) else {}).items():
        if not isinstance(payload, dict):
            continue
        ticker = _stock_label(str(stock_id), payload, aliases)
        end_shares = _safe_float(payload.get("shares_held")) or 0.0
        seed_shares = end_shares - first_week_delta_by_ticker.get(ticker, 0.0)
        if not ticker or seed_shares <= 1e-9 or end_shares <= 0:
            continue
        currency = _detect_currency(ticker)
        fx_rate = _fx_rate_for(first, currency)
        end_entry_value = _position_entry_value_hkd(payload, end_shares, currency, fx_rate)
        entry_price_hkd = end_entry_value / end_shares if end_entry_value is not None and end_shares > 0 else None
        if entry_price_hkd is None:
            continue
        entry_value = entry_price_hkd * seed_shares
        if entry_value is None or entry_value <= 0:
            continue
        entry_price = _safe_float(payload.get("avg_cost"))
        if entry_price is None:
            entry_price = entry_price_hkd / fx_rate if fx_rate > 0 else entry_price_hkd
        lots.setdefault(ticker, []).append(
            {
                "ticker": ticker,
                "entry_date": str(payload.get("buy_date") or first.get("week_id") or ""),
                "entry_week_id": week_id,
                "remaining_quantity": seed_shares,
                "entry_price": entry_price,
                "entry_price_hkd": entry_price_hkd,
                "currency": currency,
                "fx_rate_to_hkd": fx_rate,
                "source": "initial_position",
            }
        )
    return lots


def _build_fifo_trade_journal(reviews: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    review_list = list(reviews or [])
    ops = _collect_rebalancing_ops(review_list)
    lots_by_ticker = _initial_fifo_lots_from_reviews(review_list)
    rows: List[Dict[str, Any]] = []
    for op in ops:
        ticker = str(op.get("ticker") or "")
        quantity = float(op.get("quantity") or 0.0)
        price = float(op.get("price") or 0.0)
        price_hkd = float(op.get("price_hkd") or price)
        currency = str(op.get("currency") or _detect_currency(ticker))
        fx_rate = float(op.get("fx_rate_to_hkd") or 1.0)
        date = str(op.get("date") or "")
        week_id = str(op.get("week_id") or "")
        if op.get("action") == "buy":
            lots_by_ticker.setdefault(ticker, []).append(
                {
                    "ticker": ticker,
                    "entry_date": date,
                    "entry_week_id": week_id,
                    "remaining_quantity": quantity,
                    "entry_price": price,
                    "entry_price_hkd": price_hkd,
                    "currency": currency,
                    "fx_rate_to_hkd": fx_rate,
                }
            )
            continue

        remaining = quantity
        lots = lots_by_ticker.setdefault(ticker, [])
        while remaining > 1e-9 and lots:
            lot = lots[0]
            lot_quantity = float(lot.get("remaining_quantity") or 0.0)
            closed_quantity = min(lot_quantity, remaining)
            entry_price = float(lot.get("entry_price") or 0.0)
            entry_price_hkd = float(lot.get("entry_price_hkd") or entry_price)
            entry_value = closed_quantity * entry_price_hkd
            exit_value = closed_quantity * price_hkd
            pnl = exit_value - entry_value
            return_pct = pnl / entry_value * 100.0 if entry_value > 0 else None
            entry_date = str(lot.get("entry_date") or "")
            holding_days = None
            if entry_date and date:
                try:
                    holding_days = int((pd.Timestamp(date) - pd.Timestamp(entry_date)).days)
                except (TypeError, ValueError):
                    holding_days = None
            rows.append(
                {
                    "status": "closed",
                    "ticker": ticker,
                    "entry_date": entry_date,
                    "exit_date": date,
                    "week_id": week_id,
                    "quantity": _round(closed_quantity, 4),
                    "entry_price": _round(entry_price, 4),
                    "exit_price": _round(price, 4),
                    "currency": str(lot.get("currency") or currency),
                    "entry_price_hkd": _round(entry_price_hkd, 4),
                    "exit_price_hkd": _round(price_hkd, 4),
                    "entry_fx_rate_to_hkd": _round(_safe_float(lot.get("fx_rate_to_hkd")), 6),
                    "exit_fx_rate_to_hkd": _round(fx_rate, 6),
                    "entry_value_hkd": _round(entry_value, 2),
                    "exit_value_hkd": _round(exit_value, 2),
                    "pnl_hkd": _round(pnl, 2),
                    "return_pct": _round(return_pct, 4),
                    "holding_days": holding_days,
                }
            )
            lot["remaining_quantity"] = lot_quantity - closed_quantity
            remaining -= closed_quantity
            if float(lot.get("remaining_quantity") or 0.0) <= 1e-9:
                lots.pop(0)

    current_positions = _latest_current_position_map(reviews)
    for ticker, lots in lots_by_ticker.items():
        position = current_positions.get(ticker) or {}
        metrics = position.get("position_metrics") or {}
        current_value = _safe_float(metrics.get("holding_value_hkd"))
        current_shares = _safe_float(position.get("shares_held")) or 0.0
        value_per_share = current_value / current_shares if current_value is not None and current_shares > 0 else None
        for lot in lots:
            quantity = float(lot.get("remaining_quantity") or 0.0)
            if quantity <= 1e-9:
                continue
            entry_price = float(lot.get("entry_price") or 0.0)
            entry_price_hkd = float(lot.get("entry_price_hkd") or entry_price)
            entry_value = quantity * entry_price_hkd
            market_value = quantity * value_per_share if value_per_share is not None else None
            pnl = None if market_value is None else market_value - entry_value
            rows.append(
                {
                    "status": "open",
                    "ticker": ticker,
                    "entry_date": str(lot.get("entry_date") or ""),
                    "exit_date": None,
                    "week_id": str(lot.get("entry_week_id") or ""),
                    "quantity": _round(quantity, 4),
                    "entry_price": _round(entry_price, 4),
                    "exit_price": None,
                    "currency": str(lot.get("currency") or _detect_currency(ticker)),
                    "entry_price_hkd": _round(entry_price_hkd, 4),
                    "exit_price_hkd": _round(value_per_share, 4),
                    "entry_fx_rate_to_hkd": _round(_safe_float(lot.get("fx_rate_to_hkd")), 6),
                    "exit_fx_rate_to_hkd": None,
                    "entry_value_hkd": _round(entry_value, 2),
                    "exit_value_hkd": _round(market_value, 2),
                    "pnl_hkd": _round(pnl, 2),
                    "return_pct": _round((pnl / entry_value * 100.0) if pnl is not None and entry_value > 0 else None, 4),
                    "holding_days": None,
                }
            )

    for ticker, payload in current_positions.items():
        if ticker in lots_by_ticker:
            continue
        shares = _safe_float(payload.get("shares_held")) or 0.0
        metrics = payload.get("position_metrics") or {}
        holding_value = _safe_float(metrics.get("holding_value_hkd"))
        pnl = _safe_float(metrics.get("unrealized_pnl_hkd"))
        entry_value = holding_value - pnl if holding_value is not None and pnl is not None else None
        rows.append(
            {
                "status": "open",
                "ticker": ticker,
                "entry_date": str(payload.get("buy_date") or ""),
                "exit_date": None,
                "week_id": str(_latest_review(reviews).get("week_id") or ""),
                "quantity": _round(shares, 4),
                "entry_price": _round((entry_value / shares) if entry_value is not None and shares > 0 else _safe_float(payload.get("avg_cost")), 4),
                "exit_price": _round((holding_value / shares) if holding_value is not None and shares > 0 else None, 4),
                "currency": _detect_currency(ticker),
                "entry_price_hkd": _round((entry_value / shares) if entry_value is not None and shares > 0 else None, 4),
                "exit_price_hkd": _round((holding_value / shares) if holding_value is not None and shares > 0 else None, 4),
                "entry_fx_rate_to_hkd": None,
                "exit_fx_rate_to_hkd": None,
                "entry_value_hkd": _round(entry_value, 2),
                "exit_value_hkd": _round(holding_value, 2),
                "pnl_hkd": _round(pnl, 2),
                "return_pct": _round(_safe_float(metrics.get("return_since_buy")), 4),
                "holding_days": None,
            }
        )

    closed = [row for row in rows if row.get("status") == "closed"]
    opened = [row for row in rows if row.get("status") == "open"]
    wins = [float(row.get("pnl_hkd") or 0.0) for row in closed if _safe_float(row.get("pnl_hkd")) is not None and float(row.get("pnl_hkd") or 0.0) > 0]
    losses = [abs(float(row.get("pnl_hkd") or 0.0)) for row in closed if _safe_float(row.get("pnl_hkd")) is not None and float(row.get("pnl_hkd") or 0.0) < 0]
    rows.sort(key=lambda row: (0 if row.get("status") == "open" else 1, str(row.get("ticker") or ""), str(row.get("entry_date") or "")))
    return {
        "summary": {
            "trade_count": len(rows),
            "open_trade_count": len(opened),
            "closed_trade_count": len(closed),
            "winning_closed_trades": len(wins),
            "losing_closed_trades": len(losses),
            "closed_win_rate_pct": _round((len(wins) / (len(wins) + len(losses)) * 100.0) if wins or losses else None, 4),
            "closed_average_win_hkd": _round(float(np.mean(wins)) if wins else None, 2),
            "closed_average_loss_hkd": _round(float(np.mean(losses)) if losses else None, 2),
            "closed_profit_loss_ratio": _round((float(np.mean(wins)) / float(np.mean(losses))) if wins and losses else None, 4),
            "closed_realized_pnl_hkd": _round(sum(float(row.get("pnl_hkd") or 0.0) for row in closed), 2),
            "open_unrealized_pnl_hkd": _round(sum(float(row.get("pnl_hkd") or 0.0) for row in opened if _safe_float(row.get("pnl_hkd")) is not None), 2),
        },
        "rows": rows,
        "notes": ["Trades are reconstructed from recorded rebalancing ops using FIFO lots; open rows use latest position metrics when available."],
    }


def _build_security_performance_table(trade_journal: Dict[str, Any], reviews: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    review_list = list(reviews or [])
    aliases = _ticker_aliases_from_reviews(review_list)
    by_ticker: Dict[str, Dict[str, Any]] = {}
    for row in trade_journal.get("rows") or []:
        ticker = _canonical_ticker(row.get("ticker"), aliases)
        if not ticker:
            continue
        item = by_ticker.setdefault(
            ticker,
            {
                "ticker": ticker,
                "status": "closed",
                "open_quantity": 0.0,
                "closed_quantity": 0.0,
                "purchase_value_hkd": 0.0,
                "market_value_hkd": 0.0,
                "exit_value_hkd": 0.0,
                "realized_pnl_hkd": 0.0,
                "unrealized_pnl_hkd": 0.0,
                "total_pnl_hkd": 0.0,
                "trade_count": 0,
            },
        )
        quantity = _safe_float(row.get("quantity")) or 0.0
        entry_value = _safe_float(row.get("entry_value_hkd")) or 0.0
        exit_value = _safe_float(row.get("exit_value_hkd")) or 0.0
        pnl = _safe_float(row.get("pnl_hkd")) or 0.0
        item["trade_count"] = int(item.get("trade_count") or 0) + 1
        item["purchase_value_hkd"] = float(item.get("purchase_value_hkd") or 0.0) + entry_value
        if row.get("status") == "closed":
            item["closed_quantity"] = float(item.get("closed_quantity") or 0.0) + quantity
            item["exit_value_hkd"] = float(item.get("exit_value_hkd") or 0.0) + exit_value
            item["realized_pnl_hkd"] = float(item.get("realized_pnl_hkd") or 0.0) + pnl
        else:
            item["status"] = "open"
            item["open_quantity"] = float(item.get("open_quantity") or 0.0) + quantity
            item["market_value_hkd"] = float(item.get("market_value_hkd") or 0.0) + exit_value
            item["unrealized_pnl_hkd"] = float(item.get("unrealized_pnl_hkd") or 0.0) + pnl
        item["total_pnl_hkd"] = float(item.get("realized_pnl_hkd") or 0.0) + float(item.get("unrealized_pnl_hkd") or 0.0)

    closed_by_ticker: Dict[str, float] = {}
    for review in review_list:
        if not isinstance(review, dict):
            continue
        for closed in review.get("closed_positions") or []:
            if not isinstance(closed, dict):
                continue
            ticker = _stock_label(str(closed.get("stock_id") or ""), closed, aliases)
            pnl = _safe_float(closed.get("realized_pnl_hkd"))
            if ticker and pnl is not None and ticker not in by_ticker:
                closed_by_ticker[ticker] = closed_by_ticker.get(ticker, 0.0) + pnl
    for ticker, pnl in closed_by_ticker.items():
        by_ticker[ticker] = {
            "ticker": ticker,
            "status": "closed",
            "open_quantity": 0.0,
            "closed_quantity": None,
            "purchase_value_hkd": None,
            "market_value_hkd": 0.0,
            "exit_value_hkd": None,
            "realized_pnl_hkd": _round(pnl, 2),
            "unrealized_pnl_hkd": 0.0,
            "total_pnl_hkd": _round(pnl, 2),
            "trade_count": 0,
        }

    rows = []
    for item in by_ticker.values():
        purchase_value = _safe_float(item.get("purchase_value_hkd"))
        total_pnl = _safe_float(item.get("total_pnl_hkd")) or 0.0
        row = dict(item)
        row.update(
            {
                "open_quantity": _round(_safe_float(item.get("open_quantity")), 4),
                "closed_quantity": _round(_safe_float(item.get("closed_quantity")), 4),
                "purchase_value_hkd": _round(purchase_value, 2),
                "market_value_hkd": _round(_safe_float(item.get("market_value_hkd")), 2),
                "exit_value_hkd": _round(_safe_float(item.get("exit_value_hkd")), 2),
                "realized_pnl_hkd": _round(_safe_float(item.get("realized_pnl_hkd")), 2),
                "unrealized_pnl_hkd": _round(_safe_float(item.get("unrealized_pnl_hkd")), 2),
                "total_pnl_hkd": _round(total_pnl, 2),
                "total_return_pct": _round((total_pnl / purchase_value * 100.0) if purchase_value and purchase_value > 0 else None, 4),
            }
        )
        rows.append(row)
    rows.sort(key=lambda row: _safe_float(row.get("total_pnl_hkd")) or 0.0, reverse=True)
    return {
        "summary": {
            "security_count": len(rows),
            "current_security_count": sum(1 for row in rows if row.get("status") == "open"),
            "closed_security_count": sum(1 for row in rows if row.get("status") == "closed"),
            "total_realized_pnl_hkd": _round(sum(float(row.get("realized_pnl_hkd") or 0.0) for row in rows), 2),
            "total_unrealized_pnl_hkd": _round(sum(float(row.get("unrealized_pnl_hkd") or 0.0) for row in rows), 2),
            "total_pnl_hkd": _round(sum(float(row.get("total_pnl_hkd") or 0.0) for row in rows), 2),
        },
        "rows": rows,
        "notes": ["Security performance aggregates FIFO trade rows by ticker, including current open and closed securities."],
    }


def _decision_attribution(reviews: Iterable[Dict[str, Any]], source_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    review_list = list(reviews or [])
    aliases = _ticker_aliases_from_reviews(review_list)
    rows: List[Dict[str, Any]] = []
    buy_count = 0
    sell_count = 0
    unknown_count = 0
    gross_notional = 0.0
    for review in review_list:
        if not isinstance(review, dict):
            continue
        week_id = str(review.get("week_id") or "")
        for op in review.get("rebalancing_ops") or []:
            if not isinstance(op, dict):
                continue
            op_type = op.get("op_type") or op.get("action")
            action = "other"
            if is_buy_like_op(op_type):
                action = "buy"
                buy_count += 1
            elif is_sell_like_op(op_type):
                action = "sell"
                sell_count += 1
            else:
                unknown_count += 1
            qty = _safe_float(op.get("quantity")) or 0.0
            price = _safe_float(op.get("price"))
            ticker = _canonical_ticker(op.get("ticker") or op.get("stock_id"), aliases)
            currency = _detect_currency(ticker)
            fx_rate = _fx_rate_for(review, currency)
            notional = qty * price * fx_rate if price is not None else None
            if notional is not None:
                gross_notional += abs(notional)
            rows.append(
                {
                    "week_id": week_id,
                    "date": str(op.get("date") or ""),
                    "ticker": ticker,
                    "action": action,
                    "quantity": _round(qty, 4),
                    "price": _round(price, 4),
                    "currency": currency,
                    "fx_rate_to_hkd": _round(fx_rate, 6),
                    "notional_hkd": _round(notional, 2),
                    "notional": _round(notional, 2),
                }
            )
    internal_cash = sum(abs(_row_value(row, "internal_rebalancing_cash_hkd") or 0.0) for row in source_rows or [])
    return {
        "summary": {
            "buy_count": buy_count,
            "sell_count": sell_count,
            "other_count": unknown_count,
            "operation_count": len(rows),
            "gross_trade_notional": _round(gross_notional, 2),
            "internal_cash_turnover_hkd": _round(internal_cash, 2),
        },
        "rows": rows[-30:],
        "notes": [
            "Decision attribution summarizes recorded weekly rebalancing operations.",
            "Internal cash turnover is from the daily ledger and is not treated as external cash flow.",
        ],
    }


def _build_review_dashboard(
    analytics: Dict[str, Any],
    source_rows: List[Dict[str, Any]],
    returns: pd.Series,
    benchmark_returns: pd.Series,
    benchmark: str,
    reviews: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    metrics = analytics.get("metrics") or {}
    relative = analytics.get("relative") or {"benchmark": benchmark}
    exposure = analytics.get("exposure") or {}
    review = analytics.get("review") or {}
    tear_sheet = analytics.get("tear_sheet") or {}
    trade_journal = _build_fifo_trade_journal(reviews)
    holding_quality = _holding_quality_snapshot(reviews)
    drawdown_rows = analytics.get("drawdown_series") or []
    drawdown_periods = _tear_sheet_table_rows(tear_sheet, "drawdown_periods") or _drawdown_periods(drawdown_rows)
    best_periods = _tear_sheet_table_rows(tear_sheet, "best_periods")
    worst_periods = _tear_sheet_table_rows(tear_sheet, "worst_periods")
    latest = source_rows[-1] if source_rows else {}

    cumulative_return = metrics.get("cumulative_return_pct")
    benchmark_return = relative.get("benchmark_cumulative_return_pct")
    active_return = relative.get("active_return_ppt")
    latest_tracking_error = _latest_chart_value(tear_sheet, "rolling_tracking_error")
    latest_information_ratio = _latest_chart_value(tear_sheet, "rolling_information_ratio")
    current_drawdown = _safe_float((drawdown_rows[-1] if drawdown_rows else {}).get("drawdown_pct"))
    explicit_flow = sum(_row_value(row, "explicit_cash_flow_hkd") or 0.0 for row in source_rows or [])
    implied_flow = sum(_row_value(row, "implied_cash_flow_hkd") or 0.0 for row in source_rows or [])
    drawdown_attribution = _drawdown_attribution(analytics, returns, benchmark_returns, holding_quality, {})
    drawdown_attribution["episodes"] = [
        {
            **episode,
            "context": _drawdown_episode_context(episode, source_rows, drawdown_rows, trade_journal.get("rows") or []),
        }
        for episode in drawdown_attribution.get("episodes") or []
    ]
    risk_regime = _risk_regime_state_machine(source_rows, returns, benchmark_returns, drawdown_rows)

    performance_storyline = {
        "title": "Performance Storyline",
        "cards": [
            _dashboard_card("portfolio_return", "Portfolio TWR", cumulative_return, "daily linked", "pct"),
            _dashboard_card("benchmark_return", f"{benchmark} Return", benchmark_return, "same window", "pct"),
            _dashboard_card("active_return", "Active Return", active_return, "portfolio minus benchmark", "ppt"),
            _dashboard_card("cash_flow", "External Flow", _round(explicit_flow + implied_flow, 2), "known + reconciliation", "hkd"),
        ],
        "timeline": {
            "start_date": str((source_rows[0] if source_rows else {}).get("date") or ""),
            "end_date": str(latest.get("date") or ""),
            "period_count": metrics.get("period_count"),
        },
        "headline": review.get("headline") or "",
    }

    return_attribution = {
        "title": "Return Attribution",
        "waterfall": [
            {"key": "benchmark", "label": f"{benchmark} Return", "value": benchmark_return, "unit": "pct"},
            {"key": "active_return", "label": "Active Return", "value": active_return, "unit": "ppt"},
            {"key": "cash_drag", "label": "Cash Drag", "value": exposure.get("cash_drag_ppt"), "unit": "ppt"},
            {"key": "portfolio", "label": "Portfolio TWR", "value": cumulative_return, "unit": "pct"},
        ],
        "cards": [
            _dashboard_card("invested_return", "Invested Return", exposure.get("invested_capital_return_pct"), "cash-adjusted", "pct"),
            _dashboard_card("average_exposure", "Avg Exposure", exposure.get("average_exposure_pct"), "invested", "pct"),
            _dashboard_card("average_cash", "Avg Cash", exposure.get("average_cash_pct"), "cash", "pct"),
        ],
    }

    risk_attribution = {
        "title": "Risk Attribution",
        "cards": [
            _dashboard_card("volatility", "Volatility", metrics.get("volatility_annualized_pct"), "annualized", "pct"),
            _dashboard_card("max_drawdown", "Max Drawdown", metrics.get("max_drawdown_pct"), "peak-to-trough", "pct"),
            _dashboard_card("var_95", "VaR 95", metrics.get("var_95_pct"), "daily left tail", "pct"),
            _dashboard_card("tail_ratio", "Tail Ratio", metrics.get("tail_ratio"), "p95 / abs(p5)", "number"),
            _dashboard_card("tracking_error", "Tracking Error", latest_tracking_error, "latest rolling", "pct"),
            _dashboard_card("cash", "Cash Exposure", exposure.get("average_cash_pct"), "average", "pct"),
        ],
        "exposure": exposure,
    }

    active_return_attribution = {
        "title": "Active Return Attribution",
        "benchmark": benchmark,
        "cards": [
            _dashboard_card("active_return", "Active Return", active_return, "full window", "ppt"),
            _dashboard_card("information_ratio", "Information Ratio", relative.get("information_ratio"), "full window", "number"),
            _dashboard_card("rolling_ir", "Rolling IR", latest_information_ratio, "latest rolling", "number"),
            _dashboard_card("vol_matched_active", "Vol-Matched Active", relative.get("vol_matched_active_return_ppt"), "risk-normalized", "ppt"),
        ],
        "best_periods": best_periods[:5],
        "worst_periods": worst_periods[:5],
    }

    decision_attribution = _decision_attribution(reviews, source_rows)

    drawdown_lab = {
        "title": "Drawdown Lab",
        "cards": [
            _dashboard_card("current_drawdown", "Current Drawdown", current_drawdown, str(latest.get("date") or ""), "pct"),
            _dashboard_card("max_drawdown", "Max Drawdown", metrics.get("max_drawdown_pct"), "window worst", "pct"),
            _dashboard_card("worst_day", "Worst Day", (metrics.get("worst_day") or {}).get("return_pct"), (metrics.get("worst_day") or {}).get("date") or "", "pct"),
            _dashboard_card("best_day", "Best Day", (metrics.get("best_day") or {}).get("return_pct"), (metrics.get("best_day") or {}).get("date") or "", "pct"),
        ],
        "episodes": drawdown_periods,
        "attribution": drawdown_attribution,
        "recent_path": drawdown_rows[-20:],
    }

    summary_bullets = []
    if cumulative_return is not None:
        summary_bullets.append(f"Portfolio TWR is {float(cumulative_return):+.1f}% over the selected window.")
    if active_return is not None:
        summary_bullets.append(f"Active return versus {benchmark} is {float(active_return):+.1f} percentage points.")
    if metrics.get("max_drawdown_pct") is not None:
        summary_bullets.append(f"Maximum drawdown is {float(metrics.get('max_drawdown_pct')):+.1f}%.")
    if exposure.get("average_cash_pct") is not None:
        summary_bullets.append(f"Average cash weight is {float(exposure.get('average_cash_pct')):.1f}%, so exposure should be read with cash drag.")
    summary_bullets.extend(str(note) for note in (review.get("notes") or [])[:2])
    weekly_summary = {
        "title": "Weekly PM Summary",
        "bullets": summary_bullets,
        "tags": [review.get("risk_label") or "Risk --", review.get("quality_label") or "Quality --", benchmark],
    }

    security_performance = _build_security_performance_table(trade_journal, reviews)
    finance_toolkit = _build_finance_toolkit_enrichment(
        analytics,
        source_rows,
        returns,
        benchmark_returns,
        benchmark,
        reviews,
    )
    analytics_engine = _build_analytics_engine_dashboard(analytics, source_rows, returns, benchmark_returns, trade_journal)
    return {
        "performance_storyline": performance_storyline,
        "return_attribution": return_attribution,
        "risk_attribution": risk_attribution,
        "active_return_attribution": active_return_attribution,
        "decision_attribution": decision_attribution,
        "drawdown_lab": drawdown_lab,
        "risk_regime": risk_regime,
        "weekly_summary": weekly_summary,
        "holding_trade_stats": _holding_trade_stats(reviews),
        "trade_journal": trade_journal,
        "security_performance": security_performance,
        "finance_toolkit": finance_toolkit,
        "analytics_engine": analytics_engine,
    }


def build_portfolio_quant_analytics(
    series_rows: Iterable[Dict[str, Any]],
    *,
    benchmark: str = "QQQ",
    reviews: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    rows = list(series_rows or [])
    review_rows = list(reviews or [])
    data_quality = _data_quality_from_rows(rows)
    returns = _series_from_rows(rows, "period_return")
    benchmark_returns = _benchmark_period_returns(rows)
    benchmark_key = str(benchmark or "QQQ").strip().upper() or "QQQ"
    analytics = _fallback_metrics(rows, returns, benchmark_returns, benchmark_key)
    analytics["data_quality"] = data_quality
    analytics["data_trust"] = {
        "summary": {
            "missing_price_count": len(data_quality.get("ibkr_missing_price_tickers") or []),
            "fallback_price_count": len(data_quality.get("ibkr_price_fallback_tickers") or []),
            "reconciliation_gap_count": int(data_quality.get("reconciliation_gap_count") or 0),
            "stale_price_count": len(data_quality.get("stale_price_tickers") or []),
        },
        "rows": [
            {"label": "Missing prices", "value": len(data_quality.get("ibkr_missing_price_tickers") or [])},
            {"label": "Fallback prices", "value": len(data_quality.get("ibkr_price_fallback_tickers") or [])},
            {"label": "Reconciliation gaps", "value": int(data_quality.get("reconciliation_gap_count") or 0)},
            {"label": "Stale prices", "value": len(data_quality.get("stale_price_tickers") or [])},
        ],
    }
    quality_notes = _data_quality_notes(data_quality)
    if quality_notes:
        analytics.setdefault("metric_notes", []).extend(quality_notes)
    try:
        import quantstats as qs  # type: ignore

        if analytics.get("success"):
            metrics = analytics.setdefault("metrics", {})
            clean_returns = returns.dropna()
            if len(clean_returns) > 1 and abs(float(clean_returns.iloc[0])) <= 1e-12:
                clean_returns = clean_returns.iloc[1:]
            if len(clean_returns) >= 2:
                metrics["sharpe"] = _round(_safe_float(qs.stats.sharpe(clean_returns)), 4)
                metrics["sortino"] = _round(_safe_float(qs.stats.sortino(clean_returns)), 4)
                metrics["volatility_annualized_pct"] = _round(_safe_float(qs.stats.volatility(clean_returns)) * 100.0, 4)
                metrics["max_drawdown_pct"] = _round(_safe_float(qs.stats.max_drawdown(clean_returns)) * 100.0, 4)
                analytics["source"] = "quantstats"
                tear_sheet = analytics.get("tear_sheet") or {}
                if tear_sheet.get("available"):
                    analytics["tear_sheet"] = _build_strategy_tear_sheet(
                        metrics=metrics,
                        relative=analytics.get("relative") or {},
                        review=analytics.get("review") or {},
                        exposure=analytics.get("exposure") or {},
                        monthly_returns=analytics.get("monthly_returns") or [],
                        drawdown_rows=analytics.get("drawdown_series") or [],
                        drawdown_periods=_tear_sheet_table_rows(tear_sheet, "drawdown_periods"),
                        best_periods=_tear_sheet_table_rows(tear_sheet, "best_periods"),
                        worst_periods=_tear_sheet_table_rows(tear_sheet, "worst_periods"),
                        charts=tear_sheet.get("charts") or [],
                        source="quantstats",
                    )
    except Exception:
        pass
    analytics["dashboard"] = _build_review_dashboard(analytics, rows, returns, benchmark_returns, benchmark_key, review_rows)
    analytics["visualization"] = _visualization_payload(returns, analytics.get("drawdown_series") or [], analytics)
    return analytics
