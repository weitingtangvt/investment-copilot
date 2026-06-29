"""Calculate portfolio NAV and time-weighted return versus QQQ.

The script is intentionally standalone:
- edit the mock ``df_trades`` and ``df_cash_flows`` section, or import the
  functions from another workflow;
- market data is fetched with yfinance in the CLI path only;
- the calculation core accepts a price DataFrame directly so it is testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

import pandas as pd
import plotly.graph_objects as go
import yfinance as yf


REQUIRED_TRADE_COLUMNS = {"Date", "Ticker", "Action", "Quantity", "Price"}
REQUIRED_CASH_FLOW_COLUMNS = {"Date", "Amount"}
BENCHMARK_TICKER = "QQQ"


@dataclass(frozen=True)
class PortfolioInputs:
    trades: pd.DataFrame
    cash_flows: pd.DataFrame


def build_mock_inputs() -> PortfolioInputs:
    """Small executable example with irregular deposits and same-day trades."""

    df_trades = pd.DataFrame(
        [
            {"Date": "2026-01-05", "Ticker": "AAPL", "Action": "BUY", "Quantity": 8, "Price": 190.0},
            {"Date": "2026-01-06", "Ticker": "NVDA", "Action": "BUY", "Quantity": 4, "Price": 510.0},
            {"Date": "2026-01-06", "Ticker": "AAPL", "Action": "BUY", "Quantity": 2, "Price": 192.0},
            {"Date": "2026-01-20", "Ticker": "AAPL", "Action": "SELL", "Quantity": 3, "Price": 205.0},
            {"Date": "2026-02-03", "Ticker": "MSFT", "Action": "BUY", "Quantity": 5, "Price": 415.0},
        ]
    )
    df_cash_flows = pd.DataFrame(
        [
            {"Date": "2026-01-05", "Amount": 5000.0},
            {"Date": "2026-02-01", "Amount": 2500.0},
            {"Date": "2026-03-10", "Amount": 1500.0},
        ]
    )
    return PortfolioInputs(df_trades, df_cash_flows)


def _normalize_trades(df_trades: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_TRADE_COLUMNS - set(df_trades.columns)
    if missing:
        raise ValueError(f"df_trades missing required columns: {sorted(missing)}")

    trades = df_trades.copy()
    trades["Date"] = pd.to_datetime(trades["Date"]).dt.normalize()
    trades["Ticker"] = trades["Ticker"].astype(str).str.strip().str.upper()
    trades["Action"] = trades["Action"].astype(str).str.strip().str.upper()
    trades["Quantity"] = pd.to_numeric(trades["Quantity"], errors="raise")
    trades["Price"] = pd.to_numeric(trades["Price"], errors="raise")

    invalid_actions = sorted(set(trades["Action"]) - {"BUY", "SELL"})
    if invalid_actions:
        raise ValueError(f"Unsupported trade actions: {invalid_actions}")
    if (trades["Ticker"] == "").any():
        raise ValueError("Ticker cannot be blank")
    if (trades["Quantity"] <= 0).any():
        raise ValueError("Trade quantities must be positive")
    if (trades["Price"] < 0).any():
        raise ValueError("Trade prices cannot be negative")
    return trades.sort_values(["Date", "Ticker", "Action"]).reset_index(drop=True)


def _normalize_cash_flows(df_cash_flows: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_CASH_FLOW_COLUMNS - set(df_cash_flows.columns)
    if missing:
        raise ValueError(f"df_cash_flows missing required columns: {sorted(missing)}")

    cash_flows = df_cash_flows.copy()
    cash_flows["Date"] = pd.to_datetime(cash_flows["Date"]).dt.normalize()
    cash_flows["Amount"] = pd.to_numeric(cash_flows["Amount"], errors="raise")
    return cash_flows.sort_values("Date").reset_index(drop=True)


def _date_range(
    trades: pd.DataFrame,
    cash_flows: pd.DataFrame,
    *,
    end_date: Optional[str | date | pd.Timestamp] = None,
) -> pd.DatetimeIndex:
    dates = []
    if not trades.empty:
        dates.append(trades["Date"].min())
    if not cash_flows.empty:
        dates.append(cash_flows["Date"].min())
    if not dates:
        raise ValueError("At least one trade or cash flow is required")

    start = min(dates)
    end = pd.Timestamp.today().normalize() if end_date is None else pd.to_datetime(end_date).normalize()
    if end < start:
        raise ValueError("end_date cannot be earlier than the first trade/cash-flow date")
    return pd.date_range(start=start, end=end, freq="D")


def _extract_close_prices(raw: pd.DataFrame, tickers: Iterable[str]) -> pd.DataFrame:
    if raw.empty:
        raise ValueError("No market data returned by yfinance")

    requested = [str(t).strip().upper() for t in tickers if str(t).strip()]
    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" in raw.columns.get_level_values(0):
            close = raw["Close"].copy()
        elif "Adj Close" in raw.columns.get_level_values(0):
            close = raw["Adj Close"].copy()
        else:
            raise ValueError("yfinance response has neither Close nor Adj Close columns")
    else:
        if "Close" not in raw.columns:
            raise ValueError("yfinance response has no Close column")
        if len(requested) != 1:
            raise ValueError("Single-level yfinance response only supports one ticker")
        close = raw[["Close"]].rename(columns={"Close": requested[0]})

    close.columns = [str(c).strip().upper() for c in close.columns]
    missing = sorted(set(requested) - set(close.columns))
    if missing:
        raise ValueError(f"Missing close-price data for tickers: {missing}")
    return close[requested]


def fetch_close_prices(tickers: Iterable[str], start_date: pd.Timestamp, end_date: pd.Timestamp) -> pd.DataFrame:
    """Fetch adjusted daily close prices for all portfolio tickers plus QQQ."""

    unique = sorted({str(t).strip().upper() for t in tickers if str(t).strip()})
    if BENCHMARK_TICKER not in unique:
        unique.append(BENCHMARK_TICKER)

    # yfinance end is exclusive; add one day so the requested final date is included.
    raw = yf.download(
        unique,
        start=start_date.strftime("%Y-%m-%d"),
        end=(end_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        auto_adjust=True,
        progress=False,
        group_by="column",
        threads=True,
    )
    close = _extract_close_prices(raw, unique)
    close.index = pd.to_datetime(close.index).normalize()
    return close


def calculate_nav_twr(
    df_trades: pd.DataFrame,
    df_cash_flows: pd.DataFrame,
    close_prices: pd.DataFrame,
    *,
    end_date: Optional[str | date | pd.Timestamp] = None,
) -> pd.DataFrame:
    """Reconstruct daily NAV and TWR with start-of-day cash-flow treatment.

    Formula:
        Daily_Return_t = End_Value_t / (Start_Value_t + Cash_Flow_t) - 1

    Cash flows are assumed to happen at the start of the day, before trades and
    before the closing valuation. Trade proceeds/costs affect cash but are not
    external flows, so they remain inside the portfolio return.
    """

    trades = _normalize_trades(df_trades)
    cash_flows = _normalize_cash_flows(df_cash_flows)
    calendar = _date_range(trades, cash_flows, end_date=end_date)

    prices = close_prices.copy()
    if prices.empty:
        raise ValueError("close_prices cannot be empty")
    prices.index = pd.to_datetime(prices.index).normalize()
    prices.columns = [str(c).strip().upper() for c in prices.columns]
    portfolio_tickers = sorted(set(trades["Ticker"]))
    required_price_cols = sorted(set(portfolio_tickers + [BENCHMARK_TICKER]))
    missing_cols = sorted(set(required_price_cols) - set(prices.columns))
    if missing_cols:
        raise ValueError(f"close_prices missing required columns: {missing_cols}")
    prices = prices.reindex(calendar).ffill()

    if prices[portfolio_tickers].isna().any().any():
        missing = sorted(prices[portfolio_tickers].columns[prices[portfolio_tickers].isna().any()])
        raise ValueError(f"Missing initial price data for tickers: {missing}")
    if prices[BENCHMARK_TICKER].isna().any():
        raise ValueError(f"Missing initial price data for benchmark: {BENCHMARK_TICKER}")

    cash_by_date = cash_flows.groupby("Date")["Amount"].sum()
    trades_by_date = {day: day_trades for day, day_trades in trades.groupby("Date")}

    positions = {ticker: 0.0 for ticker in portfolio_tickers}
    cash_balance = 0.0
    previous_end_value = 0.0
    cumulative_growth = 1.0
    qqq_base = float(prices[BENCHMARK_TICKER].dropna().iloc[0])
    records = []

    for current_day in calendar:
        cash_flow = float(cash_by_date.get(current_day, 0.0))
        investable_start_value = previous_end_value + cash_flow
        cash_balance += cash_flow

        for _, trade in trades_by_date.get(current_day, pd.DataFrame()).iterrows():
            ticker = trade["Ticker"]
            quantity = float(trade["Quantity"])
            notional = quantity * float(trade["Price"])
            if trade["Action"] == "BUY":
                positions[ticker] += quantity
                cash_balance -= notional
            else:
                positions[ticker] -= quantity
                cash_balance += notional

        price_row = prices.loc[current_day]
        market_value = sum(positions[ticker] * float(price_row[ticker]) for ticker in portfolio_tickers)
        end_value = market_value + cash_balance

        if investable_start_value == 0:
            daily_return = 0.0 if end_value == 0 else end_value - investable_start_value
        else:
            daily_return = end_value / investable_start_value - 1.0
        cumulative_growth *= 1.0 + daily_return

        records.append(
            {
                "Date": current_day,
                "portfolio_value": end_value,
                "market_value": market_value,
                "cash_balance": cash_balance,
                "cash_flow": cash_flow,
                "daily_return": daily_return,
                "cumulative_twr": cumulative_growth - 1.0,
                "qqq_close": float(price_row[BENCHMARK_TICKER]),
                "qqq_cumulative_return": float(price_row[BENCHMARK_TICKER]) / qqq_base - 1.0,
            }
        )
        previous_end_value = end_value

    return pd.DataFrame.from_records(records).set_index("Date")


def build_nav_cash_chart(nav: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=nav.index,
            y=nav["portfolio_value"],
            name="Portfolio NAV",
            mode="lines",
            line=dict(color="#0f766e", width=2.5),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=nav.index,
            y=nav["cash_balance"],
            name="Cash Balance",
            mode="lines",
            line=dict(color="#64748b", width=2),
        )
    )

    deposits = nav[nav["cash_flow"] > 0]
    if not deposits.empty:
        fig.add_trace(
            go.Scatter(
                x=deposits.index,
                y=deposits["portfolio_value"],
                name="Deposits",
                mode="markers",
                marker=dict(color="#2563eb", size=8, symbol="triangle-up"),
                text=[f"Deposit: {amount:,.2f}" for amount in deposits["cash_flow"]],
                hovertemplate="%{x|%Y-%m-%d}<br>%{text}<extra></extra>",
            )
        )
        for deposit_date in deposits.index:
            fig.add_vline(x=deposit_date, line_width=1, line_dash="dot", line_color="#94a3b8")

    fig.update_layout(
        title="Portfolio NAV and Cash Balance",
        xaxis_title="Date",
        yaxis_title="Value",
        hovermode="x unified",
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    return fig


def build_relative_performance_chart(nav: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=nav.index,
            y=nav["cumulative_twr"] * 100.0,
            name="Portfolio TWR",
            mode="lines",
            line=dict(color="#dc2626", width=2.5),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=nav.index,
            y=nav["qqq_cumulative_return"] * 100.0,
            name="QQQ Cumulative Return",
            mode="lines",
            line=dict(color="#334155", width=2.2),
        )
    )
    fig.update_layout(
        title="Relative Performance: Portfolio TWR vs QQQ",
        xaxis_title="Date",
        yaxis_title="Cumulative Return (%)",
        hovermode="x unified",
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    return fig


def main() -> None:
    inputs = build_mock_inputs()
    trades = _normalize_trades(inputs.trades)
    cash_flows = _normalize_cash_flows(inputs.cash_flows)
    calendar = _date_range(trades, cash_flows)
    tickers = sorted(set(trades["Ticker"]) | {BENCHMARK_TICKER})
    close_prices = fetch_close_prices(tickers, calendar.min(), calendar.max())
    nav = calculate_nav_twr(trades, cash_flows, close_prices, end_date=calendar.max())

    print(nav.tail().to_string())
    build_nav_cash_chart(nav).show()
    build_relative_performance_chart(nav).show()


if __name__ == "__main__":
    main()
