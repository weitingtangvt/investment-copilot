from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.storage import Storage


def _latest_week_id(storage: Storage) -> Optional[str]:
    reviews = storage.get_all_weekly_reviews()
    if not reviews:
        return None
    return sorted(reviews.keys())[-1]


def build_report(base_dir: str | None = None, week_id: str | None = None) -> Dict[str, Any]:
    storage = Storage(base_dir=base_dir)
    selected_week_id = week_id or _latest_week_id(storage)
    if not selected_week_id:
        return {"error": "No weekly reviews found."}

    review = storage.get_weekly_review(selected_week_id) or {}
    factor_analysis = review.get("factor_analysis") or {}
    summary = factor_analysis.get("risk_contribution_summary") or {}
    top_holding_rows = summary.get("top_holding_contributors") or []
    quality = factor_analysis.get("factor_data_quality") or {}

    return {
        "week_id": selected_week_id,
        "model_pack": factor_analysis.get("model_pack"),
        "holding_count": len(factor_analysis.get("eligible_holdings") or []),
        "unsupported_count": len(factor_analysis.get("unsupported_holdings") or []),
        "factor_data_quality": quality.get("summary") or {},
        "top_factor_risk": summary.get("top_factor") or {},
        "top_holding_risk": top_holding_rows[0] if top_holding_rows else {},
        "analysis_metadata": factor_analysis.get("analysis_metadata") or {},
        "factor_risk_delta": factor_analysis.get("factor_risk_delta") or {},
        "factor_input_lineage": factor_analysis.get("factor_input_lineage") or {},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a factor-analysis sanity report.")
    parser.add_argument("--base-dir", default=None, help="Storage base directory. Defaults to ~/REDACTED.")
    parser.add_argument("--week-id", default=None, help="Weekly review id, for example 2026-W14. Defaults to latest.")
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    args = parser.parse_args()

    report = build_report(args.base_dir, args.week_id)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if report.get("error"):
        print(report["error"])
        return 1

    quality = report.get("factor_data_quality") or {}
    metadata = report.get("analysis_metadata") or {}
    delta = report.get("factor_risk_delta") or {}
    lineage = report.get("factor_input_lineage") or {}
    top_factor = report.get("top_factor_risk") or {}
    top_holding = report.get("top_holding_risk") or {}
    print(f"Week: {report['week_id']}")
    print(f"Model pack: {report.get('model_pack') or '--'}")
    print(f"Holdings: {report['holding_count']} eligible, {report['unsupported_count']} unsupported")
    print(
        "Factor data quality: "
        f"high={quality.get('high_confidence_count', 0)}, "
        f"medium={quality.get('medium_confidence_count', 0)}, "
        f"low={quality.get('low_confidence_count', 0)}"
    )
    print(f"Top factor risk: {top_factor.get('label') or top_factor.get('factor') or '--'}")
    print(f"Top holding risk: {top_holding.get('ticker') or top_holding.get('stock_id') or '--'}")
    if metadata:
        print(
            "Metadata: "
            f"lookback={metadata.get('lookback_days', '--')}, "
            f"benchmark={metadata.get('benchmark_model') or '--'}, "
            f"covariance={metadata.get('covariance_estimator') or '--'}"
        )
    if delta:
        print(f"Factor risk delta: previous={delta.get('previous_week_id') or '--'}, changes={len(delta.get('top_changes') or [])}")
    if lineage:
        print(f"Input lineage: signature={lineage.get('input_signature') or '--'}, holdings={lineage.get('holding_count', '--')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
