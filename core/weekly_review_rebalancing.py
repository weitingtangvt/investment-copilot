from __future__ import annotations

from typing import Any, Dict, List, Tuple


def legacy_gbk_mojibake(text: str) -> str:
    return text.encode("utf-8").decode("gbk", errors="ignore")


SELL_LIKE_OP_TYPES = {"sell", "trim", "Sell", "Text", legacy_gbk_mojibake("Sell"), legacy_gbk_mojibake("Text")}
BUY_LIKE_OP_TYPES = {"buy", "Buy", "add", legacy_gbk_mojibake("Buy")}


def is_sell_like_op(op_type: Any) -> bool:
    return str(op_type or "").strip().lower() in SELL_LIKE_OP_TYPES


def is_buy_like_op(op_type: Any) -> bool:
    return str(op_type or "").strip().lower() in BUY_LIKE_OP_TYPES


def normalize_trim_reallocation_op(op: Dict[str, Any]) -> Dict[str, Any]:
    paired_buys = op.get("paired_buys") or []
    if not isinstance(paired_buys, list):
        paired_buys = []
    return {
        "stock_id": str(op.get("stock_id") or "").strip(),
        "op_type": str(op.get("op_type") or "").strip().lower(),
        "quantity": safe_float(op.get("quantity")) or 0.0,
        "price": safe_float(op.get("price")),
        "date": normalize_date_text(op.get("date")),
        "pairing_mode": str(op.get("pairing_mode") or "auto").strip().lower() or "auto",
        "paired_buys": list(paired_buys),
        "pairing_note": str(op.get("pairing_note") or "").strip(),
        "source": str(op.get("source") or "").strip(),
        "decision_type": str(op.get("decision_type") or "unknown").strip().lower() or "unknown",
        "destination_type": str(op.get("destination_type") or "unknown").strip().lower() or "unknown",
        "review_horizon": str(op.get("review_horizon") or "week_end").strip().lower() or "week_end",
        "benchmark": str(op.get("benchmark") or "").strip().upper(),
        "decision_note": str(op.get("decision_note") or op.get("note") or "").strip(),
    }


def auto_pair_trim_event(trim_op: Dict[str, Any], buy_ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    released_amount = (safe_float(trim_op.get("quantity")) or 0.0) * (safe_float(trim_op.get("price")) or 0.0)
    eligible: List[Tuple[Dict[str, Any], float]] = []
    for buy in buy_ops:
        buy_amount = (safe_float(buy.get("quantity")) or 0.0) * (safe_float(buy.get("price")) or 0.0)
        if buy_amount <= 0:
            continue
        eligible.append((buy, buy_amount))
    if released_amount <= 0:
        return []
    paired: List[Dict[str, Any]] = []
    remaining = released_amount
    for buy, buy_amount in eligible:
        if remaining <= 0:
            break
        allocated = min(remaining, buy_amount)
        remaining -= allocated
        paired.append(
            {
                "stock_id": buy.get("stock_id"),
                "amount": round(allocated, 2),
                "ratio": round(allocated / released_amount, 6),
                "source": "auto",
            }
        )
    return paired


def calc_trim_window_metrics(
    released_amount: float,
    original_stock_id: str,
    trim_date: str,
    end_date: str,
    paired_buys: List[Dict[str, Any]],
    price_lookup: Dict[Tuple[str, str], float],
    buy_entry_dates: Dict[str, str],
) -> Dict[str, Any]:
    original_start = safe_float(price_lookup.get((original_stock_id, trim_date)))
    original_end = safe_float(price_lookup.get((original_stock_id, end_date)))

    original_return = None
    original_pnl = None
    if released_amount > 0 and original_start and original_end:
        original_return = (original_end / original_start) - 1
        original_pnl = released_amount * original_return

    reallocated_pnl = 0.0
    matched_amount = 0.0
    missing_price_data = False
    computed_targets = 0
    target_rows: List[Dict[str, Any]] = []

    for paired in paired_buys:
        stock_id = str(paired.get("stock_id") or "").strip()
        allocated_amount = safe_float(paired.get("amount")) or 0.0
        if not stock_id or allocated_amount <= 0:
            continue
        entry_date = str(buy_entry_dates.get(stock_id) or trim_date)
        target_start = safe_float(price_lookup.get((stock_id, entry_date))) or safe_float(price_lookup.get((stock_id, trim_date)))
        target_end = safe_float(price_lookup.get((stock_id, end_date)))
        row = {
            "stock_id": stock_id,
            "entry_date": entry_date,
            "allocated_amount": allocated_amount,
        }
        if not target_start or not target_end:
            missing_price_data = True
            row["missing_price_data"] = True
            target_rows.append(row)
            continue
        target_return = (target_end / target_start) - 1
        target_pnl = allocated_amount * target_return
        reallocated_pnl += target_pnl
        matched_amount += allocated_amount
        computed_targets += 1
        row.update(
            {
                "target_return": target_return,
                "target_pnl": target_pnl,
                "missing_price_data": False,
            }
        )
        target_rows.append(row)

    reallocated_return = (reallocated_pnl / released_amount) if released_amount > 0 and computed_targets > 0 else None
    relative_pnl = None
    relative_return = None
    if original_pnl is not None and reallocated_return is not None:
        relative_pnl = reallocated_pnl - original_pnl
        relative_return = reallocated_return - original_return

    state = "fully_paired"
    if not paired_buys:
        state = "unallocated"
    elif missing_price_data:
        state = "missing_price_data"
    elif matched_amount + 0.01 < released_amount:
        state = "partially_paired"

    return {
        "window_end": end_date,
        "original_return": original_return,
        "original_pnl": original_pnl,
        "reallocated_return": reallocated_return,
        "reallocated_pnl": reallocated_pnl if computed_targets > 0 else None,
        "relative_return": relative_return,
        "relative_pnl": relative_pnl,
        "attribution_state": state,
        "matched_amount": matched_amount,
        "unallocated_amount": max(0.0, released_amount - matched_amount),
        "targets": target_rows,
    }


def safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_date_text(value: Any) -> str:
    import re

    text = str(value or "").strip()
    match = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return text[:10]
