"""Alpha Vantage Text

Text Alpha Vantage API (https://www.alphavantage.co) TextHistoryText, 
Text. 
Text: 25 Text/Text, 5 Text/Text. 
"""

import os
import json
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

# Alpha Vantage API Key(Text config.json Text ALPHAVANTAGE_API_KEY Text)
ALPHAVANTAGE_API_KEY = "REDACTED"


def _get_api_key(storage=None) -> str:
    """Text Alpha Vantage API Key"""
    if storage:
        config = storage.get_config()
        key = config.get("alphavantage_api_key") or os.getenv("ALPHAVANTAGE_API_KEY")
        if key:
            return key
    return os.getenv("ALPHAVANTAGE_API_KEY") or ALPHAVANTAGE_API_KEY


def get_weekly_performance(
    ticker: str,
    days: int = 7,
    api_key: Optional[str] = None,
    storage=None
) -> Dict[str, Any]:
    """TextStockText(Text Alpha Vantage)

    Args:
        ticker: StockTicker(Text AAPL, GOOGL)
        days: Text, Default 7 Text
        api_key: Alpha Vantage API Key, Text
        storage: Storage Text, Text config Text key, Text

    Returns:
        {
            "success": bool,
            "ticker": str,
            "performance_summary": str,
            "data": {...},
            "error": str
        }
    """
    result = {
        "success": False,
        "ticker": ticker,
        "performance_summary": "",
        "data": {},
        "error": ""
    }

    if not ticker or not str(ticker).strip():
        result["error"] = "TextSettingsStockTicker, TextDetailsTextTicker(Text AAPL, GOOGL)"
        return result

    ticker = str(ticker).strip().upper()
    key = api_key or _get_api_key(storage)

    if not key:
        result["error"] = "Text Alpha Vantage API Key, Text config.json TextSettings alphavantage_api_key"
        return result

    # Text: Alpha Vantage Text(Text SAMPLE → SAMPLE)
    def _fetch_av(symbol: str):
        params = {
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": "compact",
            "apikey": key
        }
        req = Request(f"https://www.alphavantage.co/query?{urlencode(params)}", headers={"User-Agent": "InvestmentAssistant/1.0"})
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())

    try:
        data = _fetch_av(ticker)

        # Text API Error
        if "Error Message" in data:
            err_msg = data["Error Message"]
            # Text 4 TextTicker(Text SAMPLE)FailedText, Text(SAMPLE)
            if "invalid" in err_msg.lower() and ticker.endswith(".HK"):
                num_part = ticker[:-3].strip()
                if num_part.isdigit() and len(num_part) == 4:
                    alt_ticker = "0" + num_part + ".HK"
                    data = _fetch_av(alt_ticker)
                    if "Error Message" not in data and "Time Series (Daily)" in data:
                        ticker = alt_ticker
                    else:
                        result["error"] = f"Alpha Vantage Text({ticker}), Text. Text 0{num_part}.HK Text. "
                        return result
                else:
                    result["error"] = err_msg
                    return result
            else:
                result["error"] = err_msg
                return result
        if "Note" in data and "rate limit" in str(data.get("Note", "")).lower():
            result["error"] = "Alpha Vantage Text(5Text/Text), Text"
            return result

        # TIME_SERIES_DAILY Text "Time Series (Daily)"
        ts_key = "Time Series (Daily)" if "Time Series (Daily)" in data else None
        if not ts_key:
            for k in data.keys():
                if "Time Series" in str(k) and "Daily" in str(k):
                    ts_key = k
                    break
        if not ts_key:
            # Text API Text
            err_extra = ""
            if "Information" in data:
                err_extra = f" API Text: {str(data.get('Information', ''))[:100]}"
            elif "Error Message" in data:
                err_extra = f" {data['Error Message']}"
            result["error"] = f"Text {ticker} TextHistoryText, TextConfirmTickerText. {err_extra}"
            return result

        time_series = data[ts_key]
        if not time_series:
            result["error"] = f"{ticker} Text"
            return result

        # Text(Text "4. close" Text "5. close" Text)
        def _close(day_data):
            for k in ("4. close", "5. close", "close"):
                if k in day_data:
                    return float(day_data.get(k, 0))
            vals = list(day_data.values())
            return float(vals[-2]) if len(vals) >= 2 else 0

        def _high(day_data):
            for k in ("2. high", "3. high", "high"):
                if k in day_data:
                    return float(day_data.get(k, 0))
            return 0

        def _low(day_data):
            for k in ("3. low", "4. low", "low"):
                if k in day_data:
                    return float(day_data.get(k, 0))
            return 0

        def _vol(day_data):
            for k in ("5. volume", "6. volume", "volume"):
                if k in day_data:
                    return float(day_data.get(k, 0))
            return 0

        # Text 35 Text
        sorted_dates = sorted(time_series.keys(), reverse=True)
        cutoff = (datetime.now() - timedelta(days=35)).strftime("%Y-%m-%d")
        recent_dates = [d for d in sorted_dates if d >= cutoff]

        def _weekday(dstr):
            parts = dstr.split("-")
            if len(parts) == 3:
                try:
                    return datetime(int(parts[0]), int(parts[1]), int(parts[2])).weekday()
                except (ValueError, IndexError):
                    pass
            return -1

        fridays = [d for d in recent_dates if _weekday(d) == 4]
        if len(fridays) >= 2:
            fridays_sorted = sorted(fridays)
            last_fri_date = fridays_sorted[-2]
            this_fri_date = fridays_sorted[-1]
            week_dates = [d for d in recent_dates if last_fri_date < d <= this_fri_date]
        else:
            # Text: TextTradeText(Text)
            if len(recent_dates) < 2:
                result["error"] = f"{ticker} Text, Text"
                return result
            last_fri_date = recent_dates[-1]
            this_fri_date = recent_dates[0]
            week_dates = recent_dates[:min(10, len(recent_dates))]

        start_price = _close(time_series[last_fri_date])
        end_price = _close(time_series[this_fri_date])
        highs = [_high(time_series[d]) for d in week_dates if d in time_series]
        lows = [_low(time_series[d]) for d in week_dates if d in time_series]
        vols = [_vol(time_series[d]) for d in week_dates if d in time_series]

        high = max(highs) if highs else end_price
        low = min(lows) if lows else start_price
        volume_avg = sum(vols) / len(vols) if vols else 0

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
            "start_date": last_fri_date,
            "end_date": this_fri_date,
        }
        result["performance_summary"] = (
            f"Text: {direction} {abs(change_pct):.2f}% "
            f"(Last WeekText {last_fri_date} Text {start_price:.2f} → This WeekText {this_fri_date} Text {end_price:.2f}), "
            f"Text {high:.2f}, Text {low:.2f}"
        )
        result["success"] = True

    except HTTPError as e:
        if e.code == 429:
            result["error"] = "Alpha Vantage Text, Text"
        else:
            result["error"] = f"Text {ticker} TextFailed: HTTP {e.code}"
    except URLError as e:
        result["error"] = f"Text {ticker} TextFailed: TextError {str(e.reason)}"
    except Exception as e:
        result["error"] = f"Text {ticker} TextFailed: {str(e)}"

    return result


def get_news_sentiment(
    ticker: str,
    limit: int = 50,
    api_key: Optional[str] = None,
    storage=None,
) -> list:
    """TextNews(Alpha Vantage News Sentiment). 

    Args:
        ticker: StockTicker, Text AAPL, MSFT, 0700.HK
        limit: Text(Text 50)
        api_key: Text
        storage: Text, Text config

    Returns:
        [ {"date", "title", "summary", "dimension", "relevance", "importance", "source", "url"}, ... ]
    """
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return []
    key = api_key or _get_api_key(storage)
    if not key:
        return []
    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": ticker,
        "limit": min(limit, 200),
        "apikey": key,
        "sort": "LATEST",
    }
    try:
        req = Request(
            f"https://www.alphavantage.co/query?{urlencode(params)}",
            headers={"User-Agent": "InvestmentAssistant/1.0"},
        )
        with urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return []
    if "feed" not in data:
        if "Error Message" in data:
            return []
        return []
    out = []
    for item in data.get("feed", [])[:limit]:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        tp = item.get("time_published") or ""
        if len(tp) >= 8 and tp[:8].isdigit():
            date_str = f"{tp[:4]}-{tp[4:6]}-{tp[6:8]}"
        else:
            date_str = datetime.now().strftime("%Y-%m-%d")
        summary = (item.get("summary") or "")[:300].strip()
        out.append({
            "date": date_str,
            "title": title,
            "summary": summary,
            "dimension": "Text",
            "relevance": "Alpha Vantage News",
            "importance": "Text",
            "source": (item.get("source") or "Alpha Vantage").strip(),
            "url": (item.get("url") or "").strip(),
        })
    return out
