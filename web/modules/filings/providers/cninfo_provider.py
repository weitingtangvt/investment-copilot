from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from core.filings import normalize_filing_item


CNINFO_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "http://www.cninfo.com.cn/",
}


def normalize_cninfo_entry(*, stock_id: str, ticker: str, company_name: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    title = str(raw.get("announcementTitle") or raw.get("title") or "").strip()
    importance = "medium"
    if any(token in title for token in ("Text", "Text", "Text", "Text", "Text")):
        importance = "high"
    return normalize_filing_item(
        {
            "source": "cninfo",
            "market": "CN",
            "stock_id": stock_id,
            "ticker": ticker,
            "company_name": company_name,
            "filed_at": raw.get("announcementTime") or raw.get("filed_at"),
            "doc_type": raw.get("doc_type") or _infer_doc_type(title),
            "title": title,
            "summary": title,
            "url": raw.get("adjunctUrl") or raw.get("url") or "",
            "period_of_report": raw.get("period_of_report") or "",
            "importance": importance,
            "tags": [],
        }
    )


def _infer_doc_type(title: str) -> str:
    if "Text" in title:
        return "annual"
    if "Text" in title:
        return "semi-annual"
    if "Text" in title:
        return "quarterly"
    if "Text" in title:
        return "earnings-preview"
    return "announcement"


class CNInfoFilingsProvider:
    def __init__(self, *, session: Optional[requests.Session] = None, timeout: float = 8.0) -> None:
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch(self, *, stock_id: str, stock_name: str, ticker: str, limit: int = 12) -> List[Dict[str, Any]]:
        code = "".join(ch for ch in str(ticker or stock_id or "").strip() if ch.isdigit())[:6]
        if len(code) != 6:
            return []
        org_id = self._lookup_org_id(code)
        if not org_id:
            return []
        column = "szse" if code.startswith(("000", "001", "002", "003", "300")) else "sse"
        plate = "sz" if column == "szse" else "sh"
        response = self._session.post(
            "http://www.cninfo.com.cn/new/hisAnnouncement/query",
            headers=CNINFO_HEADERS,
            data={
                "pageNum": "1",
                "pageSize": str(max(1, int(limit))),
                "column": column,
                "tabName": "fulltext",
                "plate": plate,
                "stock": f"{code},{org_id}",
                "searchkey": "",
                "secid": "",
                "category": "",
                "trade": "",
                "seDate": "",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        items: List[Dict[str, Any]] = []
        for raw in list((payload or {}).get("announcements") or []):
            adjunct_url = str(raw.get("adjunctUrl") or "").strip()
            url = f"http://static.cninfo.com.cn/{adjunct_url}" if adjunct_url and not adjunct_url.startswith("http") else adjunct_url
            items.append(
                normalize_cninfo_entry(
                    stock_id=stock_id or code,
                    ticker=ticker or code,
                    company_name=stock_name or str(raw.get("secName") or code),
                    raw={
                        "announcementTitle": raw.get("announcementTitle") or "",
                        "announcementTime": _cninfo_time_to_day(raw.get("announcementTime")),
                        "adjunctUrl": url,
                        "period_of_report": "",
                    },
                )
            )
        return items

    def _lookup_org_id(self, code: str) -> Optional[str]:
        response = self._session.post(
            "http://www.cninfo.com.cn/new/information/topSearch/query",
            headers=CNINFO_HEADERS,
            data={"keyWord": code},
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        for entry in list(payload or []):
            if str((entry or {}).get("code") or "").strip() == code:
                return str((entry or {}).get("orgId") or "").strip() or None
        if payload:
            return str((payload[0] or {}).get("orgId") or "").strip() or None
        return None


def _cninfo_time_to_day(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:10]
