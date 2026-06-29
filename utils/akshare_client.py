"""AKShare Text

Text(.HK), A Text(.SH/.SZ), Text, Text(.AS/.DE/.VI)Text(.T/.KS/.KQ)TextHistoryText. 
Text: https://github.com/akfamily/akshare
Text stock_us_hist(Text), TextFailedText stock_us_daily(Text). 
"""

import hashlib
import time
from datetime import datetime, timedelta, timezone
from http.client import RemoteDisconnected
from typing import Dict, Any, List, Optional
from urllib.parse import urlencode

import pandas as pd
import requests

_ak = None

_DEUTSCHE_BOERSE_API_BASE = "https://api.live.deutsche-boerse.com/v1"
_DEUTSCHE_BOERSE_TRACING_SALT = "af5a8d16eb5dc49f8a72b26fd9185475c7a"
_KNOWN_INTL_INSTRUMENTS = {
    "SAMPLE": {"isin": "DE000A0WMPJ6", "mic": "XFRA", "search_term": "SAMPLE"},
    "BESI.AS": {"isin": "NL0012866412", "mic": "XFRA", "search_term": "BSI"},
    "SAMPLE": {"isin": "AT0000969985", "mic": "XWBO", "search_term": "SAMPLE"},
}


def _get_ak():
    """TextImport akshare"""
    global _ak
    if _ak is None:
        try:
            import akshare as ak_module
            _ak = ak_module
        except ImportError:
            _ak = False
    return _ak if _ak else None


def _to_ak_code(ticker: str) -> tuple:
    """Normalize ticker into (symbol, market)."""
    t = (ticker or "").strip().upper()
    if not t:
        return "", "a"
    if t.endswith(".HK"):
        num = t[:-3].strip().lstrip("0") or "0"
        return num.zfill(5), "hk"
    if t.endswith(".SS"):
        return t[:-3], "a"
    if t.endswith(".SH") or t.endswith(".SZ"):
        return t[:-3], "a"
    if t.endswith((".AS", ".DE", ".VI", ".T", ".KS", ".KQ")):
        return t, "intl"
    return t, "us"


def _fetch_intl_stock(symbol: str, start_date: str, end_date: str):
    """Fetch supported international equities (.AS/.DE/.VI/.T/.KS/.KQ).

    Yahoo Finance has recently started returning HTTP 403 for some Europe
    tickers in this environment. When that happens, fall back to Deutsche
    Boerse's public chart API so weekly review price refresh can still work.
    Japan and Korea Yahoo-style tickers are not Deutsche Boerse instruments,
    so they fail fast with the Yahoo error context instead of trying a
    misleading Europe fallback.
    """
    ticker = str(symbol or "").strip().upper()
    if ticker.endswith(".VI"):
        try:
            return _fetch_intl_stock_deutsche_boerse(symbol, start_date, end_date)
        except Exception:
            return _fetch_us_stock_yahoo(symbol, start_date, end_date)
    try:
        return _fetch_us_stock_yahoo(symbol, start_date, end_date)
    except Exception as yahoo_error:
        if ticker.endswith((".T", ".KS", ".KQ")):
            raise RuntimeError(f"intl yahoo source failed for {symbol}: {yahoo_error}") from yahoo_error
        try:
            return _fetch_intl_stock_deutsche_boerse(symbol, start_date, end_date)
        except Exception as boerse_error:
            raise RuntimeError(
                f"intl source failed for {symbol}: yahoo={yahoo_error}; deutsche_boerse={boerse_error}"
            ) from boerse_error


def _deutsche_boerse_auth_headers(url: str, now: Optional[datetime] = None) -> Dict[str, str]:
    moment = now or datetime.now(timezone.utc)
    client_date = moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    trace = hashlib.md5(f"{client_date}{url}{_DEUTSCHE_BOERSE_TRACING_SALT}".encode("ascii")).hexdigest()
    security = hashlib.md5(moment.strftime("%Y%m%d%H%M").encode("ascii")).hexdigest()
    return {
        "Accept": "application/json, text/plain, */*",
        "Client-Date": client_date,
        "X-Client-TraceId": trace,
        "X-Security": security,
        "User-Agent": "Mozilla/5.0",
    }


def _resolve_intl_instrument(symbol: str) -> Dict[str, str]:
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        raise RuntimeError("empty intl ticker")

    known = _KNOWN_INTL_INSTRUMENTS.get(ticker)
    if known:
        return dict(known)

    if ticker.endswith(".DE"):
        search_term = ticker[:-3]
        params = [
            ("searchTerms", search_term),
            ("page", "1"),
            ("pageSize", "10"),
        ]
        query = urlencode(params)
        url = f"{_DEUTSCHE_BOERSE_API_BASE}/global_search/pagedsearch/equity/en?{query}"
        response = requests.get(url, headers=_deutsche_boerse_auth_headers(url), timeout=20)
        response.raise_for_status()
        payload = response.json() or {}
        for item in payload.get("result") or []:
            if str(item.get("type") or "").upper() != "EQUITY":
                continue
            candidate = str(item.get("symbol") or "").strip().upper()
            isin = str(item.get("isin") or "").strip().upper()
            if candidate == search_term and isin:
                return {"isin": isin, "mic": "XFRA", "search_term": search_term}

    raise RuntimeError(f"no Deutsche Boerse instrument mapping for {ticker}")


def _fetch_intl_stock_deutsche_boerse(symbol: str, start_date: str, end_date: str):
    instrument = _resolve_intl_instrument(symbol)
    start_dt = datetime.strptime(start_date, "%Y%m%d")
    end_dt = datetime.strptime(end_date, "%Y%m%d")
    params = [
        ("resolution", "D"),
        ("isKeepResolutionForLatestWeeksIfPossible", "false"),
        ("from", str(int(start_dt.replace(tzinfo=timezone.utc).timestamp()))),
        ("to", str(int((end_dt + timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp()))),
        ("isBidAskPrice", "false"),
        ("symbols", f"{instrument['mic']}:{instrument['isin']}"),
    ]
    query = urlencode(params)
    url = f"{_DEUTSCHE_BOERSE_API_BASE}/tradingview/lightweight/history/single?{query}"
    response = requests.get(url, headers=_deutsche_boerse_auth_headers(url), timeout=20)
    response.raise_for_status()
    payload = response.json() or []
    if not isinstance(payload, list) or not payload:
        raise RuntimeError(f"empty Deutsche Boerse history for {symbol}")

    quotes = (((payload[0] or {}).get("quotes") or {}).get("timeValuePairs") or [])
    rows: List[Dict[str, Any]] = []
    for item in quotes:
        ts = item.get("time")
        value = item.get("value")
        if ts is None or value is None:
            continue
        close_value = float(value)
        date_value = datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")
        rows.append(
            {
                "date": date_value,
                "open": close_value,
                "high": close_value,
                "low": close_value,
                "close": close_value,
                "volume": 0.0,
            }
        )

    if not rows:
        raise RuntimeError(f"no Deutsche Boerse candles for {symbol}")

    return pd.DataFrame(rows)

def _parse_date(v) -> datetime:
    """TextDateText datetime, FailedText None"""
    s = str(v)[:10].replace("-", "")
    if len(s) == 8 and s.isdigit():
        try:
            return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            pass
    return None


def _fmt_date(v) -> str:
    """Text YYYY-MM-DD"""
    s = str(v)[:10]
    if len(s) == 8 and s.isdigit():
        return s[:4] + "-" + s[4:6] + "-" + s[6:8]
    return s


def _weekly_anchor_label(prefix: str, dt: datetime) -> str:
    return f"{prefix}Text" if dt.weekday() == 4 else f"{prefix}TextTradeText"


def _resolve_weekly_period(df: pd.DataFrame) -> tuple:
    """Resolve week-over-week comparison using the latest available trading day.

    If the current week's Friday is missing from the source data, we compare the
    latest available trading day in the current week against the previous week's
    last available trading day.
    """
    if df is None or df.empty:
        raise ValueError("empty dataframe")

    latest_row = df.iloc[-1]
    latest_dt = latest_row["_dt"]
    current_week_start = latest_dt - timedelta(days=latest_dt.weekday())

    prev_candidates = df[df["_dt"] < current_week_start]
    if prev_candidates.empty:
        raise ValueError("previous_week_anchor_missing")
    prev_row = prev_candidates.iloc[-1]

    current_week_df = df[(df["_dt"] >= current_week_start) & (df["_dt"] <= latest_dt)].copy()
    if current_week_df.empty:
        current_week_df = df.tail(5).copy()

    return prev_row, latest_row, current_week_df


def _is_connection_error(e: Exception) -> bool:
    s = str(e).lower()
    return "connection" in s or "remotedisconnected" in s or "remote end" in s or "aborted" in s


def _is_parse_error(e: Exception) -> bool:
    """TextError(Text NoneType subscript), Text"""
    s = str(e).lower()
    return "nonetype" in s and ("not subscriptable" in s or "object is not subscriptable" in s)


def _is_policy_block_error(e: Exception) -> bool:
    s = str(e).lower()
    return "winerror 4551" in s or "Text" in str(e)


def _iter_us_yahoo_symbols(symbol: str) -> List[str]:
    base = str(symbol or "").strip().upper()
    if not base:
        return []

    candidates: List[str] = [base]
    if "." in base:
        candidates.append(base.replace(".", "-"))
    if "-" not in base and "." not in base and len(base) >= 4 and base[-1].isalpha():
        candidates.append(f"{base[:-1]}-{base[-1]}")

    seen = set()
    ordered: List[str] = []
    for candidate in candidates:
        clean = str(candidate or "").strip().upper()
        if clean and clean not in seen:
            ordered.append(clean)
            seen.add(clean)
    return ordered


def _fetch_us_stock_yahoo(symbol: str, start_date: str, end_date: str):
    """Fetch US daily candles from Yahoo chart API without local JS runtime dependencies."""
    start_dt = datetime.strptime(start_date, "%Y%m%d")
    end_dt = datetime.strptime(end_date, "%Y%m%d")
    headers = {"User-Agent": "Mozilla/5.0"}
    last_error: Optional[Exception] = None

    for yahoo_symbol in _iter_us_yahoo_symbols(symbol):
        try:
            response = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}",
                params={
                    "period1": int(start_dt.timestamp()),
                    "period2": int((end_dt + timedelta(days=1)).timestamp()),
                    "interval": "1d",
                    "includeAdjustedClose": "true",
                    "events": "div,splits",
                },
                headers=headers,
                timeout=20,
            )
            if response.status_code != 200:
                last_error = RuntimeError(f"Yahoo Finance HTTP {response.status_code}")
                continue

            payload = response.json()
            chart = payload.get("chart") or {}
            if chart.get("error"):
                description = str((chart.get("error") or {}).get("description") or "").strip() or "unknown error"
                last_error = RuntimeError(description)
                continue

            results = chart.get("result") or []
            if not results:
                last_error = RuntimeError(f"Yahoo Finance Text {yahoo_symbol} Text")
                continue

            rows: List[Dict[str, Any]] = []
            for item in results:
                timestamps = item.get("timestamp") or []
                quote = ((item.get("indicators") or {}).get("quote") or [{}])[0]
                opens = quote.get("open") or []
                highs = quote.get("high") or []
                lows = quote.get("low") or []
                closes = quote.get("close") or []
                volumes = quote.get("volume") or []

                for idx, ts in enumerate(timestamps):
                    if idx >= len(opens) or idx >= len(highs) or idx >= len(lows) or idx >= len(closes):
                        continue
                    open_value = opens[idx]
                    high_value = highs[idx]
                    low_value = lows[idx]
                    close_value = closes[idx]
                    if None in (open_value, high_value, low_value, close_value):
                        continue
                    volume_value = volumes[idx] if idx < len(volumes) else None
                    rows.append(
                        {
                            "Date": datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d"),
                            "Text": float(open_value),
                            "Text": float(high_value),
                            "Text": float(low_value),
                            "Text": float(close_value),
                            "Text": None if volume_value is None else float(volume_value),
                        }
                    )

            if not rows:
                last_error = RuntimeError(f"Yahoo Finance Text {yahoo_symbol} TextKText")
                continue

            df = pd.DataFrame(rows)
            mask = (df["Date"] >= start_dt.strftime("%Y-%m-%d")) & (df["Date"] <= end_dt.strftime("%Y-%m-%d"))
            df = df.loc[mask].copy()
            if df.empty:
                last_error = RuntimeError(f"Yahoo Finance Text {yahoo_symbol} Text")
                continue
            return df
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Yahoo Finance Text {symbol} Text: {last_error}")


def _fetch_us_stock_fallbacks(ak, symbol: str, start_date: str, end_date: str, primary_label: str):
    try:
        return _fetch_us_stock_yahoo(symbol, start_date, end_date)
    except Exception as yahoo_error:
        try:
            return _fetch_us_stock_sina(ak, symbol, start_date, end_date)
        except Exception as sina_error:
            if _is_policy_block_error(sina_error):
                raise RuntimeError(f"{primary_label}, Yahoo Finance TextFailed, Text Windows Text") from sina_error
            raise RuntimeError(f"{primary_label}, Yahoo Finance TextFailed, TextFailed: {sina_error}") from yahoo_error


def _fetch_us_stock_legacy(ak, symbol: str, start_date: str, end_date: str):
    """Text: Text(Text), Text/TextFailedText"""
    last_err = None
    for attempt in range(3):
        for sym in (symbol, "105." + symbol, "106." + symbol):
            try:
                df = ak.stock_us_hist(symbol=sym, period="daily", start_date=start_date, end_date=end_date, adjust="")
                if df is not None and not df.empty:
                    return df
            except Exception as e:
                last_err = e
                # TextError(Text NoneType not subscriptable)TextErrorText, Text
                if _is_parse_error(e):
                    try:
                        return _fetch_us_stock_sina(ak, symbol, start_date, end_date)
                    except Exception as sina_e:
                        raise RuntimeError(f"TextFailed, TextFailed: {sina_e}") from e
                if _is_connection_error(e):
                    if attempt < 2:
                        time.sleep(2)
                        break
                    try:
                        return _fetch_us_stock_sina(ak, symbol, start_date, end_date)
                    except Exception as sina_e:
                        raise RuntimeError(f"TextFailed, TextFailed: {sina_e}") from e
                raise
    if last_err and _is_connection_error(last_err):
        try:
            return _fetch_us_stock_sina(ak, symbol, start_date, end_date)
        except Exception as sina_e:
            raise RuntimeError(f"TextFailed, TextFailed: {sina_e}") from last_err
    return None


def _fetch_hk_stock(ak, symbol: str, start_date: str, end_date: str):
    """Text: Text(Text), TextFailedText"""
    for attempt in range(3):
        try:
            df = ak.stock_hk_hist(symbol=symbol, period="daily", start_date=start_date, end_date=end_date, adjust="")
            if df is not None and not df.empty:
                return df
        except Exception as e:
            if _is_connection_error(e):
                if attempt < 2:
                    time.sleep(2)
                    continue
                try:
                    return _fetch_hk_stock_sina(ak, symbol, start_date, end_date)
                except Exception as sina_e:
                    raise RuntimeError(f"TextFailed, TextFailed: {sina_e}") from e
            raise
    return None


def _fetch_hk_stock_sina(ak, symbol: str, start_date: str, end_date: str):
    """Text stock_hk_daily(Text)Text, Text stock_hk_hist Text DataFrame"""
    df = ak.stock_hk_daily(symbol=symbol, adjust="")
    if df is None or df.empty:
        raise RuntimeError(f"Text {symbol} Text")
    df = df.copy()
    if df.index.name == "date" or (isinstance(df.index, pd.DatetimeIndex) and "date" not in df.columns):
        df = df.reset_index()
    col_map = {"date": "Date", "close": "Text", "high": "Text", "low": "Text", "volume": "Text"}
    df = df.rename(columns={c: col_map[c] for c in col_map if c in df.columns})
    if "Date" not in df.columns and len(df.columns) > 0:
        df = df.rename(columns={df.columns[0]: "Date"})
    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    start_s = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
    end_s = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
    mask = (df["Date"] >= start_s) & (df["Date"] <= end_s)
    df = df.loc[mask] if mask.any() else df.tail(50)
    return df


def _fetch_us_stock_sina(ak, symbol: str, start_date: str, end_date: str):
    """Text stock_us_daily(Text)Text, Text stock_us_hist Text DataFrame"""
    df = ak.stock_us_daily(symbol=symbol, adjust="")
    if df is None or df.empty:
        raise RuntimeError(f"Text {symbol} Text")
    df = df.copy()
    if df.index.name == "date" or (isinstance(df.index, pd.DatetimeIndex) and "date" not in df.columns):
        df = df.reset_index()
    # Text: Date, Text, Text, Text, Text
    col_map = {"date": "Date", "close": "Text", "high": "Text", "low": "Text", "volume": "Text"}
    df = df.rename(columns={c: col_map[c] for c in col_map if c in df.columns})
    if "Date" not in df.columns and df.columns[0] in ("date", "Date"):
        df = df.rename(columns={df.columns[0]: "Date"})
    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    start_s = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
    end_s = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
    mask = (df["Date"] >= start_s) & (df["Date"] <= end_s)
    df = df.loc[mask] if mask.any() else df.tail(50)
    return df


def _fetch_us_stock(ak, symbol: str, start_date: str, end_date: str):
    """Fetch US daily data from Eastmoney first, then fall back to Yahoo/Sina."""
    last_err = None
    for attempt in range(3):
        for sym in (symbol, "105." + symbol, "106." + symbol):
            try:
                df = ak.stock_us_hist(symbol=sym, period="daily", start_date=start_date, end_date=end_date, adjust="")
                if df is not None and not df.empty:
                    return df
            except Exception as e:
                last_err = e
                if _is_parse_error(e):
                    return _fetch_us_stock_fallbacks(ak, symbol, start_date, end_date, "TextFailed")
                if _is_connection_error(e):
                    if attempt < 2:
                        time.sleep(2)
                        break
                    return _fetch_us_stock_fallbacks(ak, symbol, start_date, end_date, "TextFailed")
                raise
    if last_err and _is_connection_error(last_err):
        return _fetch_us_stock_fallbacks(ak, symbol, start_date, end_date, "TextFailed")
    return _fetch_us_stock_fallbacks(ak, symbol, start_date, end_date, "Text")


def _fetch_a_stock(ak, symbol: str, start_date: str, end_date: str):
    """Fetch A-share daily data with retry to avoid transient disconnects."""
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_date, end_date=end_date, adjust="")
            if df is not None and not df.empty:
                return df
            if df is not None:
                # Text
                return df
        except (requests.exceptions.RequestException, RemoteDisconnected) as exc:
            last_err = exc
            time.sleep(1.2 * (attempt + 1))
            continue
        except Exception as exc:
            # Text, Text
            raise
    if last_err:
        raise last_err
    return pd.DataFrame()


def get_weekly_performance(ticker: str, days: int = 7, **kwargs) -> Dict[str, Any]:
    """TextStockThis WeekText: This WeekText Text Last WeekText Text(Text AKShare)

    Text: Text, Text = (This WeekText - Last WeekText) / Last WeekText * 100%
    TextThis WeekTextTrade, TextTradeTextThis WeekText. 

    Text(.HK), A Text(.SH, .SZ, .SS), Text(Text AAPL, GOOGL). 

    Returns:
        {"success", "ticker", "performance_summary", "data", "error"}
    """
    result = {"success": False, "ticker": ticker, "performance_summary": "", "data": {}, "error": ""}

    if not ticker or not str(ticker).strip():
        result["error"] = "TextSettingsStockTicker"
        return result

    ak = _get_ak()
    if not ak:
        result["error"] = "Text akshare, Text: pip install akshare"
        return result

    ticker = str(ticker).strip()
    symbol, market = _to_ak_code(ticker)
    if not symbol:
        result["error"] = "StockTickerText"
        return result

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=35)).strftime("%Y%m%d")

    try:
        if market == "hk":
            df = _fetch_hk_stock(ak, symbol, start_date, end_date)
        elif market == "us":
            df = _fetch_us_stock(ak, symbol, start_date, end_date)
        elif market == "intl":
            df = _fetch_intl_stock(symbol, start_date, end_date)
        else:
            df = _fetch_a_stock(ak, symbol, start_date, end_date)

        if df is None or df.empty or len(df) < 2:
            result["error"] = f"{ticker} Text, Text"
            return result

        date_col = "Date" if "Date" in df.columns else df.columns[0]
        close_col = "Text" if "Text" in df.columns else "close"
        high_col = "Text" if "Text" in df.columns else "high"
        low_col = "Text" if "Text" in df.columns else "low"
        vol_col = "Text" if "Text" in df.columns else "volume"

        df = df.sort_values(date_col)
        df = df.copy()
        df["_dt"] = df[date_col].apply(_parse_date)
        df = df[df["_dt"].notna()]  # TextDateTextFailedText
        if df.empty or len(df) < 2:
            result["error"] = f"{ticker} TextDateText"
            return result
        try:
            last_week_end, this_week_end, week_df = _resolve_weekly_period(df)
        except ValueError:
            result["error"] = f"{ticker} Text, Text"
            return result

        start_price = float(last_week_end[close_col])
        end_price = float(this_week_end[close_col])

        # This WeekText, Text, Text
        high = float(week_df[high_col].max())
        low = float(week_df[low_col].min())
        volume_avg = float(week_df[vol_col].mean()) if vol_col in week_df.columns else 0

        first_date = _fmt_date(last_week_end[date_col])
        last_date = _fmt_date(this_week_end[date_col])
        first_label = _weekly_anchor_label("Text", last_week_end["_dt"])
        last_label = _weekly_anchor_label("Text", this_week_end["_dt"])

        change = end_price - start_price
        change_pct = (change / start_price * 100) if start_price else 0
        direction = "Text" if change >= 0 else "Text"

        result["data"] = {
            "start_price": round(start_price, 2),
            "end_price": round(end_price, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "volume_avg": int(volume_avg),
            "start_date": first_date,
            "end_date": last_date,
        }
        result["performance_summary"] = (
            f"Text: {direction} {abs(change_pct):.2f}% "
            f"({first_label} {first_date} Text {start_price:.2f} → {last_label} {last_date} Text {end_price:.2f}), "
            f"Text {high:.2f}, Text {low:.2f}"
        )
        result["success"] = True

    except Exception as e:
        result["error"] = f"Text {ticker} TextFailed: {str(e)}"

    return result


def get_portfolio_and_weekly(ticker: str, buy_date: Optional[str] = None) -> Dict[str, Any]:
    """Text: This WeekText + BuyTo Date/YTD/6Text/1TextReturnText(Text, TextRefresh)"""
    out = {
        "success": False,
        "performance_data": None,
        "performance_summary": "",
        "portfolio_returns": {},
        "error": "",
    }
    if not ticker or not str(ticker).strip():
        out["error"] = "TextSettingsStockTicker"
        return out

    ak = _get_ak()
    if not ak:
        out["error"] = "Text akshare"
        return out

    ticker = str(ticker).strip()
    symbol, market = _to_ak_code(ticker)
    if not symbol:
        out["error"] = "StockTickerText"
        return out

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=750)).strftime("%Y%m%d")

    try:
        if market == "hk":
            df = _fetch_hk_stock(ak, symbol, start_date, end_date)
        elif market == "us":
            df = _fetch_us_stock(ak, symbol, start_date, end_date)
        elif market == "intl":
            df = _fetch_intl_stock(symbol, start_date, end_date)
        else:
            df = _fetch_a_stock(ak, symbol, start_date, end_date)

        if df is None or df.empty or len(df) < 2:
            out["error"] = f"{ticker} Text"
            return out

        date_col = "Date" if "Date" in df.columns else df.columns[0]
        close_col = "Text" if "Text" in df.columns else "close"
        high_col = "Text" if "Text" in df.columns else "high"
        low_col = "Text" if "Text" in df.columns else "low"
        vol_col = "Text" if "Text" in df.columns else "volume"
        df = df.sort_values(date_col).copy()
        df["_dt"] = df[date_col].apply(_parse_date)
        df = df[df["_dt"].notna()]
        if df.empty or len(df) < 2:
            out["error"] = f"{ticker} TextDateText"
            return out

        current_price = float(df.iloc[-1][close_col])

        def _price_on_or_before(target: datetime):
            before = df[df["_dt"] <= target]
            return None if before.empty else float(before.iloc[-1][close_col])

        # 1. Text(TextThis WeekTextTradeText vs Last WeekTextTradeText)
        try:
            last_week_end, this_week_end, week_df = _resolve_weekly_period(df)
            start_price = float(last_week_end[close_col])
            end_price = float(this_week_end[close_col])
            change = end_price - start_price
            change_pct = (change / start_price * 100) if start_price else 0
            high = float(week_df[high_col].max())
            low = float(week_df[low_col].min())
            volume_avg = float(week_df[vol_col].mean()) if vol_col in week_df.columns else 0
            first_date = _fmt_date(last_week_end[date_col])
            last_date = _fmt_date(this_week_end[date_col])
            first_label = _weekly_anchor_label("Text", last_week_end["_dt"])
            last_label = _weekly_anchor_label("Text", this_week_end["_dt"])
            direction = "Text" if change >= 0 else "Text"
            out["performance_data"] = {
                "start_price": round(start_price, 2),
                "end_price": round(end_price, 2),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "volume_avg": int(volume_avg),
                "start_date": first_date,
                "end_date": last_date,
            }
            out["performance_summary"] = f"Text: {direction} {abs(change_pct):.2f}% ({first_label} {first_date} Text {start_price:.2f} → {last_label} {last_date} Text {end_price:.2f}), Text {high:.2f}, Text {low:.2f}"
        except ValueError:
            out["performance_data"] = out.get("performance_data") or {}

        # 2. ReturnText
        today = datetime.now()
        price_1d = _price_on_or_before(today - timedelta(days=1))
        price_1w = _price_on_or_before(today - timedelta(days=7))
        price_1m = _price_on_or_before(today - timedelta(days=30))
        price_1q = _price_on_or_before(today - timedelta(days=92))
        price_ytd = _price_on_or_before(datetime(today.year, 1, 1))
        price_6m = _price_on_or_before(today - timedelta(days=185))
        price_1y = _price_on_or_before(today - timedelta(days=370))
        pr = {
            "success": True,
            "current_price": round(current_price, 2),
        }
        if price_1d and price_1d > 0:
            pr["return_1d"] = round((current_price / price_1d - 1) * 100, 2)
        if price_1w and price_1w > 0:
            pr["return_1w"] = round((current_price / price_1w - 1) * 100, 2)
        if price_1m and price_1m > 0:
            pr["return_1m"] = round((current_price / price_1m - 1) * 100, 2)
        if price_1q and price_1q > 0:
            pr["return_1q"] = round((current_price / price_1q - 1) * 100, 2)
        if price_ytd and price_ytd > 0:
            pr["ytd_return"] = round((current_price / price_ytd - 1) * 100, 2)
        if price_6m and price_6m > 0:
            pr["return_6m"] = round((current_price / price_6m - 1) * 100, 2)
        if price_1y and price_1y > 0:
            pr["return_1y"] = round((current_price / price_1y - 1) * 100, 2)
        if buy_date:
            buy_s = str(buy_date).replace("-", "")[:8]
            if len(buy_s) == 8:
                buy_dt = datetime(int(buy_s[:4]), int(buy_s[4:6]), int(buy_s[6:8]))
                buy_date_price = _price_on_or_before(buy_dt)
                if buy_date_price and buy_date_price > 0:
                    pr["buy_date_price"] = round(buy_date_price, 2)
                    pr["return_since_buy"] = round((current_price / buy_date_price - 1) * 100, 2)
        out["portfolio_returns"] = pr
        out["success"] = bool(out["performance_data"]) or bool(pr)
    except Exception as e:
        out["error"] = str(e)

    if not out["success"]:
        out["error"] = out.get("error") or f"{ticker} No dataTextMarket DataText"

    return out


def get_close_price_on_date(ticker: str, date_str: str) -> Optional[float]:
    """TextDate(TextTradeText)Text. date_str: YYYY-MM-DD Text YYYYMMDD"""
    if not ticker or not date_str:
        return None
    s = str(date_str).replace("-", "").replace("/", "")[:8]
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        target = datetime(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except (ValueError, IndexError):
        return None
    end_date = (target + timedelta(days=7)).strftime("%Y%m%d")
    start_date = (target - timedelta(days=14)).strftime("%Y%m%d")
    ak = _get_ak()
    if not ak:
        return None
    ticker = str(ticker).strip()
    symbol, market = _to_ak_code(ticker)
    if not symbol:
        return None
    try:
        if market == "hk":
            df = _fetch_hk_stock(ak, symbol, start_date, end_date)
        elif market == "us":
            df = _fetch_us_stock(ak, symbol, start_date, end_date)
        elif market == "intl":
            df = _fetch_intl_stock(symbol, start_date, end_date)
        else:
            df = _fetch_a_stock(ak, symbol, start_date, end_date)
        if df is None or df.empty:
            return None
        date_col = "Date" if "Date" in df.columns else df.columns[0]
        close_col = "Text" if "Text" in df.columns else "close"
        df = df.sort_values(date_col).copy()
        df["_dt"] = df[date_col].apply(_parse_date)
        df = df[df["_dt"].notna()]
        before = df[df["_dt"] <= target]
        if before.empty:
            return None
        return round(float(before.iloc[-1][close_col]), 2)
    except Exception:
        return None


def get_portfolio_returns(ticker: str, buy_date: Optional[str] = None) -> Dict[str, Any]:
    """TextHoldingsReturnText: CurrentText, BuyText, BuyTo Date, YTD, 6Text, 1TextReturnText

    buy_date: YYYY-MM-DD Text YYYYMMDD
    Returns: {success, current_price, buy_date_price, return_since_buy, ytd_return, return_6m, return_1y, error}
    """
    result = {
        "success": False,
        "current_price": None,
        "buy_date_price": None,
        "return_since_buy": None,
        "return_1d": None,
        "return_1w": None,
        "return_1m": None,
        "return_1q": None,
        "ytd_return": None,
        "return_6m": None,
        "return_1y": None,
        "error": "",
    }
    if not ticker or not str(ticker).strip():
        result["error"] = "TextSettingsStockTicker"
        return result

    ak = _get_ak()
    if not ak:
        result["error"] = "Text akshare"
        return result

    ticker = str(ticker).strip()
    symbol, market = _to_ak_code(ticker)
    if not symbol:
        result["error"] = "StockTickerText"
        return result

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=750)).strftime("%Y%m%d")  # Text2TextHistory

    try:
        if market == "hk":
            df = _fetch_hk_stock(ak, symbol, start_date, end_date)
        elif market == "us":
            df = _fetch_us_stock(ak, symbol, start_date, end_date)
        elif market == "intl":
            df = _fetch_intl_stock(symbol, start_date, end_date)
        else:
            df = _fetch_a_stock(ak, symbol, start_date, end_date)

        if df is None or df.empty or len(df) < 2:
            result["error"] = f"{ticker} Text"
            return result

        date_col = "Date" if "Date" in df.columns else df.columns[0]
        close_col = "Text" if "Text" in df.columns else "close"
        df = df.sort_values(date_col)
        df = df.copy()
        df["_dt"] = df[date_col].apply(_parse_date)
        df = df[df["_dt"].notna()]
        if df.empty or len(df) < 2:
            result["error"] = f"{ticker} TextDateText"
            return result

        current_price = float(df.iloc[-1][close_col])
        result["current_price"] = round(current_price, 2)

        def _price_on_or_before(target: datetime):
            before = df[df["_dt"] <= target]
            if before.empty:
                return None
            return float(before.iloc[-1][close_col])

        today = datetime.now()
        d1_dt = today - timedelta(days=1)
        w1_dt = today - timedelta(days=7)
        m1_dt = today - timedelta(days=30)
        q1_dt = today - timedelta(days=92)
        ytd_dt = datetime(today.year, 1, 1)
        m6_dt = today - timedelta(days=185)
        y1_dt = today - timedelta(days=370)

        price_1d = _price_on_or_before(d1_dt)
        price_1w = _price_on_or_before(w1_dt)
        price_1m = _price_on_or_before(m1_dt)
        price_1q = _price_on_or_before(q1_dt)
        price_ytd = _price_on_or_before(ytd_dt)
        price_6m = _price_on_or_before(m6_dt)
        price_1y = _price_on_or_before(y1_dt)

        if price_1d and price_1d > 0:
            result["return_1d"] = round((current_price / price_1d - 1) * 100, 2)
        if price_1w and price_1w > 0:
            result["return_1w"] = round((current_price / price_1w - 1) * 100, 2)
        if price_1m and price_1m > 0:
            result["return_1m"] = round((current_price / price_1m - 1) * 100, 2)
        if price_1q and price_1q > 0:
            result["return_1q"] = round((current_price / price_1q - 1) * 100, 2)
        if price_ytd and price_ytd > 0:
            result["ytd_return"] = round((current_price / price_ytd - 1) * 100, 2)
        if price_6m and price_6m > 0:
            result["return_6m"] = round((current_price / price_6m - 1) * 100, 2)
        if price_1y and price_1y > 0:
            result["return_1y"] = round((current_price / price_1y - 1) * 100, 2)

        if buy_date:
            buy_s = str(buy_date).replace("-", "")[:8]
            if len(buy_s) == 8:
                buy_dt = datetime(int(buy_s[:4]), int(buy_s[4:6]), int(buy_s[6:8]))
                buy_date_price = _price_on_or_before(buy_dt)
                if buy_date_price and buy_date_price > 0:
                    result["buy_date_price"] = round(buy_date_price, 2)
                    result["return_since_buy"] = round((current_price / buy_date_price - 1) * 100, 2)

        result["success"] = True
    except Exception as e:
        result["error"] = str(e)

    return result


def get_stock_news_em(symbol: str, max_items: int = 20) -> list:
    """Text A TextNews(Text), TextReviewText. 

    Args:
        symbol: A TextTicker, Text "600519", "000001"(Text .SH/.SZ Text)
        max_items: Text

    Returns:
        [ {"date": "YYYY-MM-DD", "title", "summary", "dimension", "relevance", "importance", "source", "url"}, ... ]
    """
    ak = _get_ak()
    if not ak:
        return []
    symbol = (symbol or "").strip()
    if not symbol or not symbol.isdigit():
        return []
    try:
        df = ak.stock_news_em(symbol=symbol)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    out = []
    # Text Text NewsText NewsText Text Text NewsText
    title_col = "NewsText" if "NewsText" in df.columns else (df.columns[1] if len(df.columns) > 1 else None)
    time_col = "Text" if "Text" in df.columns else (df.columns[3] if len(df.columns) > 3 else None)
    summary_col = "NewsText" if "NewsText" in df.columns else (df.columns[2] if len(df.columns) > 2 else None)
    url_col = "NewsText" if "NewsText" in df.columns else (df.columns[5] if len(df.columns) > 5 else None)
    source_col = "Text" if "Text" in df.columns else None
    for _, row in df.head(max_items).iterrows():
        title = str(row.get(title_col, "") or "").strip()
        if not title:
            continue
        pub = str(row.get(time_col, "") or "")[:19].replace("/", "-")
        if len(pub) >= 10:
            date_str = pub[:10]
        else:
            date_str = datetime.now().strftime("%Y-%m-%d")
        summary = (str(row.get(summary_col, "") or "") or "")[:300].strip()
        out.append({
            "date": date_str,
            "title": title,
            "summary": summary,
            "dimension": "Text",
            "relevance": "TextNews",
            "importance": "Text",
            "source": str(row.get(source_col, "") or "Text").strip() or "Text",
            "url": str(row.get(url_col, "") or "").strip(),
        })
    return out


def _pick_existing_column(df: pd.DataFrame, candidates: List[str], default: Optional[str] = None) -> Optional[str]:
    for name in candidates:
        if name in df.columns:
            return name
    return default


def _normalize_daily_ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    date_col = _pick_existing_column(df, ["Date", "date", "Date"], df.columns[0] if len(df.columns) else None)
    close_col = _pick_existing_column(df, ["Text", "close", "Close"])
    if not date_col or not close_col:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    open_col = _pick_existing_column(df, ["Text", "open", "Open"], close_col)
    high_col = _pick_existing_column(df, ["Text", "high", "High"], close_col)
    low_col = _pick_existing_column(df, ["Text", "low", "Low"], close_col)
    volume_col = _pick_existing_column(df, ["Text", "volume", "Volume"])

    data = df.copy()
    data["_dt"] = pd.to_datetime(data[date_col].apply(_parse_date), errors="coerce")
    data["_open"] = pd.to_numeric(data[open_col], errors="coerce")
    data["_high"] = pd.to_numeric(data[high_col], errors="coerce")
    data["_low"] = pd.to_numeric(data[low_col], errors="coerce")
    data["_close"] = pd.to_numeric(data[close_col], errors="coerce")
    data["_volume"] = pd.to_numeric(data[volume_col], errors="coerce") if volume_col else 0.0
    data = data.dropna(subset=["_dt", "_open", "_high", "_low", "_close"]).sort_values("_dt").copy()
    if data.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    out = pd.DataFrame(
        {
            "Open": data["_open"].astype(float).values,
            "High": data["_high"].astype(float).values,
            "Low": data["_low"].astype(float).values,
            "Close": data["_close"].astype(float).values,
            "Volume": data["_volume"].fillna(0.0).astype(float).values,
        },
        index=pd.to_datetime(data["_dt"]).dt.normalize(),
    )
    return out[~out.index.duplicated(keep="last")].sort_index()


def _format_history_window_date(value) -> str:
    if isinstance(value, str):
        text = value.strip()
        if len(text) == 8 and text.isdigit():
            return text
        parsed = pd.to_datetime(text, errors="coerce")
    else:
        parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid history date: {value}")
    return pd.Timestamp(parsed).strftime("%Y%m%d")


def get_daily_ohlcv_frames(tickers: List[str], *, start_date, end_date) -> Dict[str, pd.DataFrame]:
    """Fetch daily OHLCV frames normalized for SQLiteOHLCVCache.

    Returned frames use a DatetimeIndex and canonical Open/High/Low/Close/Volume
    columns, so callers do not need to know each upstream provider's column names.
    Individual ticker failures are skipped to allow mixed-source cache refreshes to
    return all successfully fetched symbols.
    """
    start_text = _format_history_window_date(start_date)
    end_text = _format_history_window_date(end_date)
    ak = _get_ak()
    output: Dict[str, pd.DataFrame] = {}

    for raw_ticker in tickers or []:
        ticker = str(raw_ticker or "").strip().upper()
        if not ticker:
            continue
        symbol, market = _to_ak_code(ticker)
        if not symbol:
            continue
        try:
            if market == "hk":
                if not ak:
                    continue
                raw = _fetch_hk_stock(ak, symbol, start_text, end_text)
            elif market == "us":
                if not ak:
                    continue
                raw = _fetch_us_stock(ak, symbol, start_text, end_text)
            elif market == "intl":
                raw = _fetch_intl_stock(symbol, start_text, end_text)
            else:
                if not ak:
                    continue
                raw = _fetch_a_stock(ak, symbol, start_text, end_text)
            frame = _normalize_daily_ohlcv_frame(raw)
            if not frame.empty:
                output[ticker] = frame
        except Exception:
            continue
    return output


def _calc_ma_series(close_series: pd.Series, window: int) -> List[Dict[str, Any]]:
    rolling = close_series.rolling(window=window, min_periods=window).mean()
    out: List[Dict[str, Any]] = []
    for idx, value in rolling.items():
        out.append(
            {
                "date": str(idx),
                "value": None if pd.isna(value) else round(float(value), 4),
            }
        )
    return out


def get_price_chart_series(ticker: str, range: str = "365d") -> Dict[str, Any]:
    """Return recent daily candles with MA50/100/200 for watchlist chart modal."""
    resolved_range = str(range or "365d").strip().lower() or "365d"
    result: Dict[str, Any] = {
        "success": False,
        "ticker": str(ticker or "").strip().upper(),
        "range": resolved_range,
        "as_of_date": "",
        "series": {
            "candles": [],
            "ma50": [],
            "ma100": [],
            "ma200": [],
        },
        "meta": {
            "latest_close": None,
            "change_1y_pct": None,
            "has_enough_history_for_ma200": False,
            "provider": "akshare",
        },
        "error": "",
    }

    if not ticker or not str(ticker).strip():
        result["error"] = "TextSettingsStockTicker"
        return result

    if result["range"] not in {"365d", "1y", "12m"}:
        result["error"] = f"Text: {result['range']}"
        return result

    ak = _get_ak()
    if not ak:
        result["error"] = "Text akshare, Text: pip install akshare"
        return result

    symbol, market = _to_ak_code(result["ticker"])
    if not symbol:
        result["error"] = "StockTickerText"
        return result

    end_dt = datetime.now()
    # Keep the visible window at 1 year, but fetch substantially more history so
    # MA50/100/200 are already "warmed up" near the left edge of the chart.
    start_dt = end_dt - timedelta(days=730)
    start_date = start_dt.strftime("%Y%m%d")
    end_date = end_dt.strftime("%Y%m%d")

    try:
        if market == "hk":
            df = _fetch_hk_stock(ak, symbol, start_date, end_date)
        elif market == "us":
            df = _fetch_us_stock(ak, symbol, start_date, end_date)
        elif market == "intl":
            df = _fetch_intl_stock(symbol, start_date, end_date)
        else:
            df = _fetch_a_stock(ak, symbol, start_date, end_date)

        if df is None or df.empty:
            result["error"] = f"{ticker} No dataText"
            return result

        date_col = _pick_existing_column(df, ["Date", "date", "Date"], df.columns[0] if len(df.columns) else None)
        open_col = _pick_existing_column(df, ["Text", "open", "Open"])
        close_col = _pick_existing_column(df, ["Text", "close", "Close"])
        high_col = _pick_existing_column(df, ["Text", "high", "High"])
        low_col = _pick_existing_column(df, ["Text", "low", "Low"])
        volume_col = _pick_existing_column(df, ["Text", "volume", "Volume"])

        if not date_col or not open_col or not close_col or not high_col or not low_col:
            result["error"] = f"{ticker} Text"
            return result

        data = df.copy()
        data["_dt"] = data[date_col].apply(_parse_date)
        data = data[data["_dt"].notna()].sort_values("_dt").copy()
        if data.empty:
            result["error"] = f"{ticker} TextDateText"
            return result

        data["_date"] = data["_dt"].dt.strftime("%Y-%m-%d")
        data["_open"] = pd.to_numeric(data[open_col], errors="coerce")
        data["_high"] = pd.to_numeric(data[high_col], errors="coerce")
        data["_low"] = pd.to_numeric(data[low_col], errors="coerce")
        data["_close"] = pd.to_numeric(data[close_col], errors="coerce")
        if volume_col:
            data["_volume"] = pd.to_numeric(data[volume_col], errors="coerce")
        else:
            data["_volume"] = None
        data = data.dropna(subset=["_open", "_high", "_low", "_close"]).copy()
        if data.empty:
            result["error"] = f"{ticker} Text"
            return result

        one_year_cutoff = end_dt - timedelta(days=365)
        visible = data[data["_dt"] >= one_year_cutoff].copy()
        if visible.empty:
            visible = data.tail(min(len(data), 260)).copy()

        close_indexed = data.set_index("_date")["_close"]
        ma50_map = {item["date"]: item["value"] for item in _calc_ma_series(close_indexed, 50)}
        ma100_map = {item["date"]: item["value"] for item in _calc_ma_series(close_indexed, 100)}
        ma200_map = {item["date"]: item["value"] for item in _calc_ma_series(close_indexed, 200)}

        candles: List[Dict[str, Any]] = []
        ma50: List[Dict[str, Any]] = []
        ma100: List[Dict[str, Any]] = []
        ma200: List[Dict[str, Any]] = []
        for _, row in visible.iterrows():
            date_text = str(row["_date"])
            candles.append(
                {
                    "date": date_text,
                    "open": round(float(row["_open"]), 4),
                    "high": round(float(row["_high"]), 4),
                    "low": round(float(row["_low"]), 4),
                    "close": round(float(row["_close"]), 4),
                    "volume": None if pd.isna(row["_volume"]) else float(row["_volume"]),
                }
            )
            ma50.append({"date": date_text, "value": ma50_map.get(date_text)})
            ma100.append({"date": date_text, "value": ma100_map.get(date_text)})
            ma200.append({"date": date_text, "value": ma200_map.get(date_text)})

        latest_close = float(visible.iloc[-1]["_close"])
        first_close = float(visible.iloc[0]["_close"]) if len(visible) else latest_close
        change_1y_pct = None
        if first_close > 0:
            change_1y_pct = round((latest_close / first_close - 1) * 100, 2)

        result["as_of_date"] = str(visible.iloc[-1]["_date"])
        result["series"] = {
            "candles": candles,
            "ma50": ma50,
            "ma100": ma100,
            "ma200": ma200,
        }
        result["meta"] = {
            "latest_close": round(latest_close, 4),
            "change_1y_pct": change_1y_pct,
            "has_enough_history_for_ma200": len(data) >= 200,
            "provider": "akshare",
        }
        result["success"] = True
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result
