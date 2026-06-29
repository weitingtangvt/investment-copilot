from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.environment import EnvironmentCollector
from core.storage import Storage
from core.weekly_review import WeeklyReviewManager


class _NoopClient:
    pass


def _top_rows(events: List[Dict[str, Any]], key: str, limit: int = 5, *, positive_only: bool = False) -> List[Dict[str, Any]]:
    rows = [event for event in events if event.get(key) is not None]
    if positive_only:
        rows = [event for event in rows if float(event.get(key) or 0.0) > 0]
    rows.sort(key=lambda event: abs(float(event.get(key) or 0.0)), reverse=True)
    return [
        {
            "week_id": row.get("week_id"),
            "stock_id": row.get("stock_id"),
            "sell_date": row.get("sell_date"),
            key: row.get(key),
        }
        for row in rows[:limit]
    ]


def _dedupe_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for event in events or []:
        row_key = (
            str(event.get("week_id") or "").strip(),
            str(event.get("stock_id") or "").strip(),
            str(event.get("sell_date") or event.get("date") or "").strip(),
        )
        existing = deduped.get(row_key)
        if existing is None:
            deduped[row_key] = event
            continue
        existing_score = sum(1 for field in ("original_hold_12w", "actual_result_12w", "original_hold_30d", "actual_result_30d") if (existing.get(field) or {}).get("pnl") is not None)
        event_score = sum(1 for field in ("original_hold_12w", "actual_result_12w", "original_hold_30d", "actual_result_30d") if (event.get(field) or {}).get("pnl") is not None)
        if event_score > existing_score:
            deduped[row_key] = event
    return list(deduped.values())


def _actual_basis_counts(windows: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for window in windows or []:
        mark = ((window.get("marks") or {}).get("now") or {})
        basis = str(mark.get("actual_basis") or "unknown").strip() or "unknown"
        counts[basis] = counts.get(basis, 0) + 1
    return dict(sorted(counts.items()))


def build_report(base_dir: str | None = None) -> Dict[str, Any]:
    storage = Storage(base_dir=base_dir)
    client = _NoopClient()
    manager = WeeklyReviewManager(client, storage, EnvironmentCollector(client, storage))
    payload = manager.build_decision_review_index(limit=10_000)
    events = _dedupe_events(payload.get("all_events") or [])
    windows = payload.get("counterfactual_windows") or []

    quality_rows = [window.get("data_quality") or {} for window in windows]
    return {
        "sell_event_count": len(events),
        "window_count": len(windows),
        "data_quality": {
            "high_confidence_windows": sum(1 for row in quality_rows if row.get("confidence") == "high"),
            "medium_confidence_windows": sum(1 for row in quality_rows if row.get("confidence") == "medium"),
            "low_confidence_windows": sum(1 for row in quality_rows if row.get("confidence") == "low"),
            "checkpoint_windows": sum(1 for row in quality_rows if row.get("confidence") == "checkpoint"),
            "now_evaluable_count": sum(int(row.get("now_evaluable_count") or 0) for row in quality_rows),
            "mark_30d_evaluable_count": sum(int(row.get("mark_30d_evaluable_count") or 0) for row in quality_rows),
            "missing_now_count": sum(int(row.get("missing_now_count") or 0) for row in quality_rows),
            "missing_30d_count": sum(int(row.get("missing_30d_count") or 0) for row in quality_rows),
        },
        "actual_basis_counts": _actual_basis_counts(windows),
        "top_opportunity_cost": _top_rows(events, "opportunity_cost_12w", positive_only=True),
        "top_opportunity_cost_30d": _top_rows(events, "opportunity_cost_30d", positive_only=True),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a decision review data-quality sanity report.")
    parser.add_argument("--base-dir", default=None, help="Storage base directory. Defaults to ~/REDACTED.")
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    args = parser.parse_args()

    report = build_report(args.base_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"Sell events: {report['sell_event_count']}")
    print(f"Counterfactual windows: {report['window_count']}")
    quality = report["data_quality"]
    print(
        "Data quality: "
        f"high={quality['high_confidence_windows']}, "
        f"medium={quality['medium_confidence_windows']}, "
        f"low={quality['low_confidence_windows']}, "
        f"checkpoint={quality['checkpoint_windows']}"
    )
    print(f"Evaluable: Now {quality['now_evaluable_count']}, 30D {quality['mark_30d_evaluable_count']}")
    print(f"Actual basis: {report['actual_basis_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
