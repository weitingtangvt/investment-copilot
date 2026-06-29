from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from .base import BaseDataSource
from .models import DataSourceResult


def _result_from_payload(source_name: str, payload: Dict[str, Any]) -> DataSourceResult:
    metadata = dict(payload.get("search_metadata") or {})
    news = list(payload.get("news") or [])
    warnings = metadata.get("search_warnings") or []
    return DataSourceResult(
        success=bool(news),
        data={"news": news, "search_metadata": metadata},
        warning="; ".join(str(item) for item in warnings[:2]),
        source_name=source_name,
        fetched_at=datetime.now().isoformat(timespec="seconds"),
        degraded=bool(warnings),
        meta={"news_count": len(news)},
    )


def ak_get_chinese_stock_news(
    *,
    ticker: str = "",
    stock_name: str = "",
    time_range_days: int = 7,
    playbook: Dict[str, Any] | None = None,
) -> list[Dict[str, Any]]:
    """Best-effort AKShare Chinese coverage for A shares and HK stocks."""
    raw_ticker = str(ticker or (playbook or {}).get("ticker") or "").strip().upper()
    stock_id = str((playbook or {}).get("stock_id") or "").strip().upper()
    symbol = raw_ticker or stock_id
    numeric = "".join(ch for ch in symbol.split(".")[0] if ch.isdigit())
    rows: list[Dict[str, Any]] = []

    if numeric and not symbol.endswith(".HK"):
        try:
            from utils.akshare_client import get_stock_news_em

            rows.extend(get_stock_news_em(numeric, max_items=20))
        except Exception:
            rows = []

    if numeric and symbol.endswith(".HK"):
        try:
            import akshare as ak

            hk_code = numeric.zfill(5)
            rank_df = ak.stock_hk_hot_rank_em()
            if rank_df is not None and not rank_df.empty:
                text_df = rank_df.astype(str)
                mask = text_df.apply(lambda col: col.str.contains(hk_code, case=False, na=False)).any(axis=1)
                matched = rank_df[mask]
                if not matched.empty:
                    row = matched.iloc[0].to_dict()
                    rows.append(
                        {
                            "date": datetime.now().strftime("%Y-%m-%d"),
                            "title": f"{stock_name or raw_ticker} Text",
                            "summary": "Text. ",
                            "dimension": "Text",
                            "relevance": "Text",
                            "importance": "medium",
                            "source": "AKShare Chinese",
                            "url": "",
                            "raw": {str(key): str(value) for key, value in row.items()},
                            "is_verifiable": True,
                            "is_synthetic": False,
                        }
                    )
        except Exception:
            pass

    for row in rows:
        row.setdefault("source", "AKShare Chinese")
        row.setdefault("is_verifiable", True)
        row.setdefault("is_synthetic", False)
    return rows


class AKShareChineseNewsDataSource(BaseDataSource):
    source_name = "akshare_chinese"

    def fetch(self, **kwargs):
        news = ak_get_chinese_stock_news(
            ticker=kwargs.get("ticker", ""),
            stock_name=kwargs.get("stock_name", ""),
            time_range_days=int(kwargs.get("time_range_days") or 7),
            playbook=kwargs.get("playbook") or {},
        )
        payload = {
            "news": news,
            "search_metadata": {
                "_is_metadata": True,
                "total_dimensions": 1,
                "successful_dimensions": 1 if news else 0,
                "failed_dimensions": [] if news else [{"source": "akshare_chinese", "error": "no Chinese AKShare rows"}],
                "search_warnings": [],
                "search_source": "akshare_chinese",
            },
        }
        return _result_from_payload(self.source_name, payload)


class GoogleRSSNewsDataSource(BaseDataSource):
    source_name = "rss"

    def fetch(self, **kwargs):
        from .. import rss_news_client

        payload = rss_news_client.collect_news_structured(
            stock_name=kwargs.get("stock_name", ""),
            ticker=kwargs.get("ticker", ""),
            search_keywords=kwargs.get("search_keywords") or [],
            related_entities=kwargs.get("related_entities") or [],
            time_range_days=int(kwargs.get("time_range_days") or 7),
            search_name=kwargs.get("search_name", ""),
        )
        return _result_from_payload(self.source_name, payload)


class NewsAPIDataSource(BaseDataSource):
    source_name = "newsapi"

    def fetch(self, **kwargs):
        api_key = str(kwargs.get("api_key") or "").strip()
        if not api_key:
            return DataSourceResult(
                success=False,
                error="missing_api_key",
                source_name=self.source_name,
                fetched_at=datetime.now().isoformat(timespec="seconds"),
            )

        from .. import newsapi_client

        payload = newsapi_client.collect_news_structured(
            api_key=api_key,
            stock_name=kwargs.get("stock_name", ""),
            related_entities=kwargs.get("related_entities") or [],
            time_range_days=int(kwargs.get("time_range_days") or 7),
            playbook=kwargs.get("playbook"),
        )
        return _result_from_payload(self.source_name, payload)


class TavilyNewsDataSource(BaseDataSource):
    source_name = "tavily"

    def fetch(self, **kwargs):
        api_key = str(kwargs.get("api_key") or "").strip()
        if not api_key:
            return DataSourceResult(
                success=False,
                error="missing_api_key",
                source_name=self.source_name,
                fetched_at=datetime.now().isoformat(timespec="seconds"),
            )

        from .. import tavily_client

        payload = tavily_client.collect_news_structured(
            api_key=api_key,
            stock_name=kwargs.get("stock_name", ""),
            related_entities=kwargs.get("related_entities") or [],
            time_range_days=int(kwargs.get("time_range_days") or 7),
            playbook=kwargs.get("playbook"),
        )
        return _result_from_payload(self.source_name, payload)
