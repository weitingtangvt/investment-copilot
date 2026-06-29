"""Text"""

import json
import math
import os
import re
import time
import threading
import logging
import tomllib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any
import shutil
import hashlib

logger = logging.getLogger(__name__)

SUPPORTED_EXCHANGE_SUFFIXES = (".HK", ".SH", ".SZ", ".SS", ".US", ".AS", ".DE", ".VI", ".T", ".KS", ".KQ")
PRIMARY_CODE_SUFFIXES = (".HK", ".SH", ".SZ", ".SS", ".US", ".AS", ".DE", ".VI", ".T", ".KS", ".KQ")


def _legacy_gbk_mojibake(text: str) -> str:
    return text.encode("utf-8").decode("gbk", errors="ignore")


REBALANCING_BUY_TYPES = {"buy", "Buy", "add", _legacy_gbk_mojibake("Buy")}
REBALANCING_SELL_TYPES = {"sell", "Sell", "trim", "Text", _legacy_gbk_mojibake("Sell"), _legacy_gbk_mojibake("Text")}

from .us_screener import build_default_us_screener_payload, normalize_us_screener_payload

try:
    from utils.akshare_client import get_weekly_performance as _ak_get_perf
    from utils.akshare_client import get_close_price_on_date as _ak_price_on_date
except ImportError:
    _ak_get_perf = None
    _ak_price_on_date = None



def _json_safe_value(obj: Any) -> Any:
    """Text nan/inf Text JSON Text, Text JSONDecodeError  on read"""
    if obj is None:
        return None
    if isinstance(obj, (bool, int)):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return {k: _json_safe_value(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe_value(v) for v in obj]
    return str(obj)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _current_week_id(dt: Optional[datetime] = None) -> str:
    target = dt or datetime.now()
    year, week, _ = target.isocalendar()
    return f"{year}-W{week:02d}"


def _copy_json_default(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _copy_json_default(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy_json_default(item) for item in value]
    return value


def _primary_code_without_exchange_suffix(stock_id: str) -> str:
    code = str(stock_id or "").strip().upper()
    for suffix in PRIMARY_CODE_SUFFIXES:
        if code.endswith(suffix):
            return code[: -len(suffix)]
    return code


def _normalize_rebalancing_buy_date(raw_date: Any) -> str:
    op_date = str(raw_date or "").strip()
    if not op_date or len(op_date) < 8:
        return ""
    normalized = op_date.replace("/", "-").replace(" ", "")[:10]
    if len(normalized) == 10 and normalized[4] == "-" and normalized[7] == "-":
        return normalized
    digits = "".join(char for char in op_date[:10] if char.isdigit())[:8]
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return ""


def _rebalancing_op_delta(op_type: Any, quantity: Any) -> float:
    try:
        qty = float(quantity or 0)
    except (TypeError, ValueError):
        return 0.0
    text = str(op_type or "").strip().lower()
    if text in REBALANCING_BUY_TYPES:
        return qty
    if text in REBALANCING_SELL_TYPES:
        return -qty
    return 0.0


def _gross_rebalancing_amounts(ops: List[Dict]) -> tuple[float, float]:
    gross_buy_amount = 0.0
    gross_sell_amount = 0.0
    for op in ops:
        try:
            qty = float(op.get("quantity") or 0)
            price = float(op.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or price <= 0:
            continue
        op_type = str(op.get("op_type") or "").strip().lower()
        if op_type in REBALANCING_BUY_TYPES:
            gross_buy_amount += qty * price
        elif op_type in REBALANCING_SELL_TYPES:
            gross_sell_amount += qty * price
    return gross_buy_amount, gross_sell_amount


class Storage:
    """Text JSON Text"""

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = Path(base_dir or os.path.expanduser("~/REDACTED"))
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_path = self.base_dir / "tasks" / "task_records.json"

        # Text
        (self.base_dir / "stocks").mkdir(exist_ok=True)
        (self.base_dir / "logs").mkdir(exist_ok=True)
        (self.base_dir / "tasks").mkdir(exist_ok=True)
        self.broker_trade_snapshots_dir = self.base_dir / "broker_trade_snapshots"
        self.broker_trade_snapshots_dir.mkdir(exist_ok=True)

        # Text
        self.config_path = self.base_dir / "config.json"
        self.portfolio_playbook_path = self.base_dir / "portfolio_playbook.json"
        self.decision_logs_path = self.base_dir / "decision_logs.json"
        self.broker_trade_ledger_path = self.base_dir / "broker_trade_ledger.json"
        self.ibkr_portfolio_baselines_path = self.base_dir / "ibkr_portfolio_baselines.json"
        self.portfolio_external_cash_flows_path = self.base_dir / "portfolio_external_cash_flows.json"
        self.broker_trade_snapshots_dir = self.base_dir / "broker_trade_snapshots"

        # Text, Text
        self._config_lock = threading.Lock()
        self._portfolio_lock = threading.Lock()
        self._weekly_lock = threading.Lock()
        self._stock_locks: Dict[str, threading.Lock] = {}
        self._stock_locks_mutex = threading.Lock()
        self._prefs_lock = threading.Lock()
        self._watchlist_lock = threading.Lock()
        self._ima_sync_lock = threading.Lock()
        self._us_screener_lock = threading.Lock()
        self._task_records_lock = threading.Lock()
        self._decision_logs_lock = threading.Lock()
        self._broker_trade_ledger_lock = threading.Lock()
        self._ibkr_portfolio_baseline_lock = threading.Lock()
        self._portfolio_external_cash_flows_lock = threading.Lock()

    def _atomic_write_json(self, path: Path, data: Any) -> None:
        """Text JSON Text + Text, TextFailedText"""
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        safe = _json_safe_value(data)
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(safe, f, ensure_ascii=False, indent=2, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    def _quarantine_corrupt_file(self, path: Path, label: str) -> Optional[Path]:
        if not path.exists():
            return None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = path.parent / f"{path.name}.corrupt.{timestamp}"
        try:
            os.replace(path, backup_path)
        except Exception:
            try:
                shutil.copy2(path, backup_path)
                path.unlink(missing_ok=True)
            except Exception:
                logger.exception("Failed to quarantine corrupt %s file: %s", label, path)
                return None
        logger.warning("Quarantined corrupt %s file to %s", label, backup_path)
        return backup_path

    def _load_json_file_with_default(
        self,
        path: Path,
        default: Any,
        *,
        expected_type: type | tuple[type, ...] | None,
        label: str,
    ) -> Any:
        if not path.exists():
            return _copy_json_default(default)
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("Failed to read %s from %s: %s", label, path, exc)
            self._quarantine_corrupt_file(path, label)
            return _copy_json_default(default)
        if expected_type is not None and not isinstance(payload, expected_type):
            logger.warning("Unexpected %s payload type in %s: %s", label, path, type(payload).__name__)
            self._quarantine_corrupt_file(path, label)
            return _copy_json_default(default)
        return payload

    def _read_decision_logs_unlocked(self) -> List[Dict[str, Any]]:
        payload = self._load_json_file_with_default(
            self.decision_logs_path,
            [],
            expected_type=list,
            label="decision logs",
        )
        return payload if isinstance(payload, list) else []

    def _normalize_decision_log(
        self,
        payload: Dict[str, Any],
        existing: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        base = dict(existing or {})
        base.update(payload or {})
        outcome = dict((existing or {}).get("outcome") or {})
        outcome.update((payload or {}).get("outcome") or {})
        ticker = str(base.get("ticker") or base.get("stock_id") or "").strip().upper()
        stock_id = str(base.get("stock_id") or ticker).strip()
        return {
            "id": str(base.get("id") or uuid.uuid4()),
            "week_id": str(base.get("week_id") or "").strip(),
            "date": str(base.get("date") or "").strip(),
            "ticker": ticker,
            "stock_id": stock_id,
            "stock_name": str(base.get("stock_name") or "").strip(),
            "action": str(base.get("action") or "watch").strip().lower(),
            "decision_type": str(base.get("decision_type") or "unknown").strip().lower(),
            "source_module": str(base.get("source_module") or "weekly_review").strip(),
            "capital_hkd": base.get("capital_hkd"),
            "position_delta_pct": base.get("position_delta_pct"),
            "thesis": str(base.get("thesis") or "").strip(),
            "expected_outcome": str(base.get("expected_outcome") or "").strip(),
            "risk_case": str(base.get("risk_case") or "").strip(),
            "review_trigger": str(base.get("review_trigger") or "").strip(),
            "confidence": base.get("confidence"),
            "horizon_days": _safe_int(base.get("horizon_days"), 28),
            "tags": list(base.get("tags") or []),
            "linked_rebalancing_op_id": str(base.get("linked_rebalancing_op_id") or "").strip(),
            "created_at": str(base.get("created_at") or now),
            "updated_at": now,
            "status": str(base.get("status") or "open").strip().lower(),
            "outcome": {
                "reviewed_at": outcome.get("reviewed_at"),
                "price_return": outcome.get("price_return"),
                "relative_return": outcome.get("relative_return"),
                "decision_result": str(outcome.get("decision_result") or "pending").strip().lower(),
                "reflection": str(outcome.get("reflection") or "").strip(),
            },
        }

    def list_decision_logs(
        self,
        week_id: Optional[str] = None,
        ticker: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._decision_logs_lock:
            rows = self._read_decision_logs_unlocked()
        if week_id:
            rows = [row for row in rows if str(row.get("week_id") or "") == str(week_id)]
        if ticker:
            target = str(ticker).strip().upper()
            rows = [
                row
                for row in rows
                if str(row.get("ticker") or "").upper() == target
                or str(row.get("stock_id") or "").upper() == target
            ]
        if status:
            target_status = str(status).strip().lower()
            rows = [row for row in rows if str(row.get("status") or "").lower() == target_status]
        return sorted(rows, key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")), reverse=True)

    def create_decision_log(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._decision_logs_lock:
            rows = self._read_decision_logs_unlocked()
            row = self._normalize_decision_log(payload)
            rows.append(row)
            self._atomic_write_json(self.decision_logs_path, rows)
            return row

    def get_decision_log(self, log_id: str) -> Optional[Dict[str, Any]]:
        target = str(log_id or "").strip()
        if not target:
            return None
        with self._decision_logs_lock:
            rows = self._read_decision_logs_unlocked()
        for row in rows:
            if str(row.get("id") or "") == target:
                return dict(row)
        return None

    def update_decision_log(self, log_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._decision_logs_lock:
            rows = self._read_decision_logs_unlocked()
            for index, row in enumerate(rows):
                if str(row.get("id") or "") == str(log_id):
                    updated = self._normalize_decision_log(patch, existing=row)
                    rows[index] = updated
                    self._atomic_write_json(self.decision_logs_path, rows)
                    return updated
        return None

    def delete_decision_log(self, log_id: str) -> bool:
        with self._decision_logs_lock:
            rows = self._read_decision_logs_unlocked()
            next_rows = [row for row in rows if str(row.get("id") or "") != str(log_id)]
            if len(next_rows) == len(rows):
                return False
            self._atomic_write_json(self.decision_logs_path, next_rows)
            return True

    def _empty_ibkr_portfolio_baselines(self) -> Dict[str, Any]:
        return {"version": 1, "baselines": {}}

    def _read_ibkr_portfolio_baselines_unlocked(self) -> Dict[str, Any]:
        payload = self._load_json_file_with_default(
            self.ibkr_portfolio_baselines_path,
            self._empty_ibkr_portfolio_baselines(),
            expected_type=dict,
            label="IBKR portfolio baselines",
        )
        baselines = payload.get("baselines") if isinstance(payload.get("baselines"), dict) else {}
        return {
            "version": 1,
            "baselines": {
                str(week_id): dict(value)
                for week_id, value in baselines.items()
                if isinstance(value, dict)
            },
        }

    def get_ibkr_portfolio_baseline(self, week_id: str) -> Dict[str, Any]:
        target = str(week_id or "").strip()
        if not target:
            return {}
        with self._ibkr_portfolio_baseline_lock:
            payload = self._read_ibkr_portfolio_baselines_unlocked()
        return dict((payload.get("baselines") or {}).get(target) or {})

    def save_ibkr_portfolio_baseline(self, week_id: str, baseline: Dict[str, Any]) -> Dict[str, Any]:
        target = str(week_id or "").strip()
        if not target:
            raise ValueError("week_id is required")
        now = datetime.now().isoformat(timespec="seconds")
        with self._ibkr_portfolio_baseline_lock:
            payload = self._read_ibkr_portfolio_baselines_unlocked()
            baselines = payload.setdefault("baselines", {})
            row = dict(baseline or {})
            row["baseline_week_id"] = str(row.get("baseline_week_id") or target)
            row["updated_at"] = now
            row.setdefault("created_at", now)
            baselines[target] = row
            self._atomic_write_json(self.ibkr_portfolio_baselines_path, payload)
        return dict(row)

    def _normalize_portfolio_external_cash_flows(self, rows: Any) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        if not isinstance(rows, list):
            return normalized
        for item in rows:
            if not isinstance(item, dict):
                continue
            date_text = str(item.get("date") or "").strip()[:10]
            try:
                datetime.fromisoformat(date_text)
            except ValueError:
                continue
            try:
                amount = float(item.get("amount_hkd"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(amount) or abs(amount) <= 1e-9:
                continue
            normalized.append(
                {
                    "date": date_text,
                    "amount_hkd": round(amount, 2),
                    "source": str(item.get("source") or "known_external_cash_flow").strip() or "known_external_cash_flow",
                }
            )
        return sorted(normalized, key=lambda row: (row["date"], row["source"], row["amount_hkd"]))

    def get_portfolio_external_cash_flows_hkd(self) -> List[Dict[str, Any]]:
        with self._portfolio_external_cash_flows_lock:
            payload = self._load_json_file_with_default(
                self.portfolio_external_cash_flows_path,
                {"version": 1, "cash_flows": []},
                expected_type=dict,
                label="portfolio external cash flows",
            )
        return self._normalize_portfolio_external_cash_flows(payload.get("cash_flows") if isinstance(payload, dict) else [])

    def save_portfolio_external_cash_flows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = self._normalize_portfolio_external_cash_flows(rows)
        with self._portfolio_external_cash_flows_lock:
            self._atomic_write_json(
                self.portfolio_external_cash_flows_path,
                {"version": 1, "cash_flows": normalized, "updated_at": datetime.now().isoformat(timespec="seconds")},
            )
        return normalized

    def _empty_broker_trade_ledger(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "imports": [],
            "trades": [],
            "summary": {
                "import_count": 0,
                "trade_count": 0,
                "last_imported_at": "",
            },
        }

    def _normalize_broker_trade_ledger(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return self._empty_broker_trade_ledger()
        imports = payload.get("imports") if isinstance(payload.get("imports"), list) else []
        trades = payload.get("trades") if isinstance(payload.get("trades"), list) else []
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        return {
            "version": 1,
            "imports": [dict(item) for item in imports if isinstance(item, dict)],
            "trades": [dict(item) for item in trades if isinstance(item, dict)],
            "summary": {
                "import_count": len(imports),
                "trade_count": len(trades),
                "last_imported_at": str(summary.get("last_imported_at") or ""),
            },
        }

    def _broker_trade_ledger_dedupe_key(self, trade: Dict[str, Any]) -> str:
        external_trade_id = str(trade.get("external_trade_id") or trade.get("execution_id") or "").strip()
        if external_trade_id:
            parts = [
                "execution_id",
                external_trade_id,
                str(trade.get("trade_datetime") or ""),
                str(trade.get("symbol") or trade.get("ticker") or trade.get("stock_id") or ""),
                str(trade.get("side") or ""),
                str(trade.get("quantity") or ""),
                str(trade.get("price") or ""),
                str(trade.get("currency") or ""),
            ]
        else:
            parts = [
                "economic",
                str(trade.get("trade_datetime") or trade.get("trade_date") or ""),
                str(trade.get("symbol") or trade.get("ticker") or trade.get("stock_id") or ""),
                str(trade.get("side") or ""),
                str(trade.get("quantity") or ""),
                str(trade.get("price") or ""),
                str(trade.get("currency") or ""),
                str(trade.get("net_cash") or ""),
                str(trade.get("net_cash_hkd") or trade.get("base_net_cash_hkd") or trade.get("base_net_cash") or ""),
                str(trade.get("commission") or ""),
            ]
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _archive_broker_trade_snapshot(self, *, source_filename: str, raw_text: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_source = re.sub(r"[^A-Za-z0-9._-]+", "_", str(source_filename or "ibkr.csv").strip()) or "ibkr.csv"
        snapshot_path = self.broker_trade_snapshots_dir / f"{timestamp}_{safe_source}"
        snapshot_path.write_text(raw_text, encoding="utf-8")
        return str(snapshot_path)

    def rebuild_broker_trade_ledger(self, *, limit_import_records: int | None = None) -> Dict[str, Any]:
        with self._broker_trade_ledger_lock:
            ledger = self._read_broker_trade_ledger_unlocked()
            deduped: list[Dict[str, Any]] = []
            seen: set[str] = set()
            removed = 0
            for trade in ledger.get("trades") or []:
                if not isinstance(trade, dict):
                    continue
                clean_trade = dict(trade)
                dedupe_key = self._broker_trade_ledger_dedupe_key(clean_trade)
                clean_trade["dedupe_key"] = dedupe_key
                if dedupe_key in seen:
                    removed += 1
                    continue
                seen.add(dedupe_key)
                deduped.append(clean_trade)

            imports = [dict(item) for item in (ledger.get("imports") or []) if isinstance(item, dict)]
            if limit_import_records is not None and int(limit_import_records) >= 0:
                imports = imports[: int(limit_import_records)]

            rebuilt = {
                "version": 1,
                "imports": imports,
                "trades": deduped,
                "summary": {
                    "import_count": len(imports),
                    "trade_count": len(deduped),
                    "last_imported_at": str((ledger.get("summary") or {}).get("last_imported_at") or ""),
                },
            }
            self._atomic_write_json(self.broker_trade_ledger_path, rebuilt)
            return {
                "success": True,
                "removed_count": removed,
                "trade_count": len(deduped),
                "import_count": len(imports),
                "ledger": rebuilt,
            }

    def replace_broker_trade_ledger(
        self,
        *,
        broker: str,
        source_filename: str,
        trades: List[Dict[str, Any]],
        errors: List[Dict[str, Any]],
        raw_text: str = "",
    ) -> Dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        batch_id = f"broker_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        snapshot_path = ""
        if raw_text:
            snapshot_path = self._archive_broker_trade_snapshot(source_filename=source_filename, raw_text=raw_text)
        clean_trades: List[Dict[str, Any]] = []
        seen: set[str] = set()
        duplicate_count = 0
        for trade in trades:
            clean_trade = dict(trade or {})
            dedupe_key = str(clean_trade.get("dedupe_key") or "").strip()
            if not dedupe_key:
                dedupe_key = self._broker_trade_ledger_dedupe_key(clean_trade)
                clean_trade["dedupe_key"] = dedupe_key
            has_execution_id = bool(str(clean_trade.get("external_trade_id") or clean_trade.get("execution_id") or "").strip())
            if has_execution_id and dedupe_key in seen:
                duplicate_count += 1
                continue
            if has_execution_id:
                seen.add(dedupe_key)
            clean_trade["import_batch_id"] = batch_id
            clean_trade["imported_at"] = now
            clean_trades.append(clean_trade)

        import_record = {
            "id": batch_id,
            "broker": str(broker or "").strip() or "IBKR",
            "source_filename": str(source_filename or "").strip(),
            "imported_at": now,
            "snapshot_path": snapshot_path,
            "recognized_count": len(trades),
            "inserted_count": len(clean_trades),
            "duplicate_count": duplicate_count,
            "error_count": len(errors),
            "errors": list(errors or [])[:25],
            "mode": "snapshot_replace",
        }
        with self._broker_trade_ledger_lock:
            ledger = {
                "version": 1,
                "imports": [import_record],
                "trades": clean_trades,
                "summary": {
                "import_count": 1,
                "trade_count": len(clean_trades),
                "last_imported_at": now,
                "latest_snapshot_path": snapshot_path,
            },
        }
            self._atomic_write_json(self.broker_trade_ledger_path, ledger)
        return {
            "success": True,
            "import": import_record,
            "recognized_count": len(trades),
            "inserted_count": len(clean_trades),
            "duplicate_count": duplicate_count,
            "error_count": len(errors),
            "errors": list(errors or [])[:25],
        }

    def _read_broker_trade_ledger_unlocked(self) -> Dict[str, Any]:
        payload = self._load_json_file_with_default(
            self.broker_trade_ledger_path,
            self._empty_broker_trade_ledger(),
            expected_type=dict,
            label="broker trade ledger",
        )
        return self._normalize_broker_trade_ledger(payload)

    def get_broker_trade_ledger(self, limit: Optional[int] = None) -> Dict[str, Any]:
        with self._broker_trade_ledger_lock:
            ledger = self._read_broker_trade_ledger_unlocked()
        trades = sorted(
            ledger.get("trades") or [],
            key=lambda row: (str(row.get("trade_datetime") or ""), str(row.get("dedupe_key") or "")),
            reverse=True,
        )
        if limit is not None and int(limit) > 0:
            trades = trades[: int(limit)]
        ledger["trades"] = trades
        return ledger

    def add_broker_trade_import(
        self,
        *,
        broker: str,
        source_filename: str,
        trades: List[Dict[str, Any]],
        errors: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        batch_id = f"broker_import_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        with self._broker_trade_ledger_lock:
            ledger = self._read_broker_trade_ledger_unlocked()
            existing_keys = {str(trade.get("dedupe_key") or "") for trade in ledger.get("trades", [])}
            inserted: List[Dict[str, Any]] = []
            duplicate_count = 0
            for trade in trades:
                clean_trade = dict(trade or {})
                dedupe_key = str(clean_trade.get("dedupe_key") or "").strip()
                if not dedupe_key or dedupe_key in existing_keys:
                    duplicate_count += 1
                    continue
                clean_trade["import_batch_id"] = batch_id
                clean_trade["imported_at"] = now
                inserted.append(clean_trade)
                existing_keys.add(dedupe_key)

            import_record = {
                "id": batch_id,
                "broker": str(broker or "").strip() or "IBKR",
                "source_filename": str(source_filename or "").strip(),
                "imported_at": now,
                "recognized_count": len(trades),
                "inserted_count": len(inserted),
                "duplicate_count": duplicate_count,
                "error_count": len(errors),
                "errors": list(errors or [])[:25],
            }
            ledger["imports"].insert(0, import_record)
            ledger["trades"].extend(inserted)
            ledger["summary"] = {
                "import_count": len(ledger["imports"]),
                "trade_count": len(ledger["trades"]),
                "last_imported_at": now,
            }
            self._atomic_write_json(self.broker_trade_ledger_path, ledger)

        return {
            "success": True,
            "import": import_record,
            "recognized_count": len(trades),
            "inserted_count": len(inserted),
            "duplicate_count": duplicate_count,
            "error_count": len(errors),
            "errors": list(errors or [])[:25],
        }

    def _get_codex_auth(self) -> Dict[str, Any]:
        auth_path = Path.home() / '.codex' / 'auth.json'
        if not auth_path.exists():
            return {}
        try:
            return json.loads(auth_path.read_text(encoding='utf-8'))
        except Exception:
            return {}

    def _get_codex_config(self) -> Dict[str, Any]:
        config_path = Path.home() / '.codex' / 'config.toml'
        if not config_path.exists():
            return {}
        try:
            with open(config_path, 'rb') as f:
                return tomllib.load(f)
        except Exception:
            return {}

    def _get_codex_model_provider(self) -> Dict[str, Any]:
        codex_config = self._get_codex_config()
        provider_name = (codex_config.get('model_provider') or '').strip()
        providers = codex_config.get('model_providers') or {}
        provider = providers.get(provider_name) or {}
        return provider if isinstance(provider, dict) else {}

    def _first_non_empty(self, *values: Any) -> str:
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    def _get_stock_lock(self, stock_id: str) -> threading.Lock:
        """TextStockText"""
        with self._stock_locks_mutex:
            if stock_id not in self._stock_locks:
                self._stock_locks[stock_id] = threading.Lock()
            return self._stock_locks[stock_id]

    # ==================== Text ====================

    def get_config(self) -> Dict:
        """Text(TextAutoText)"""
        with self._config_lock:
            if self.config_path.exists():
                try:
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if not content:
                            # Text, Text
                            return self._restore_from_backup()
                        return json.loads(content)
                except (json.JSONDecodeError, ValueError) as e:
                    # JSON TextFailed, Text
                    logger.error("Text: %s", e)
                    return self._restore_from_backup()
            return {}

    def _restore_from_backup(self) -> Dict:
        """Text"""
        backup_path = self.config_path.with_suffix('.json.bak')
        if backup_path.exists():
            try:
                with open(backup_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        config = json.loads(content)
                        # TextSuccess, SaveTextCurrentText
                        logger.info("TextSuccess")
                        # Text
                        if self.config_path.exists():
                            corrupt_path = self.config_path.with_suffix('.json.corrupt')
                            shutil.copy(self.config_path, corrupt_path)
                        # Text
                        with open(self.config_path, "w", encoding="utf-8") as f:
                            json.dump(config, f, ensure_ascii=False, indent=2)
                        return config
            except Exception as e:
                logger.error("TextFailed: %s", e)

        # Text, Text
        logger.warning("Text, TextDefaultText")
        return {}

    def save_config(self, config: Dict):
        """SaveText(Text, Text)"""
        with self._config_lock:
            # Text
            temp_path = self.config_path.with_suffix('.json.tmp')
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(config, f, ensure_ascii=False, indent=2)
                    f.flush()  # Text
                    os.fsync(f.fileno())  # Text

                # Text(Text)
                if self.config_path.exists():
                    backup_path = self.config_path.with_suffix('.json.bak')
                    shutil.copy(self.config_path, backup_path)

                # Text(Windows Text)
                if os.name == 'nt' and self.config_path.exists():
                    os.remove(self.config_path)
                os.rename(temp_path, self.config_path)

            except Exception as e:
                # Text
                if temp_path.exists():
                    temp_path.unlink()
                raise e

    def get_llm_provider(self) -> str:
        """TextCurrent LLM Text. """
        return "gpt53"

    def set_llm_provider(self, provider: str):
        """Settings LLM Text"""
        config = self.get_config()
        config["llm_provider"] = "gpt53"
        self.save_config(config)

    def get_gpt53_api_key(self) -> Optional[str]:
        """Text GPT5.3 / OpenAI API Key. """
        config = self.get_config()
        codex_auth = self._get_codex_auth()
        raw = self._first_non_empty(
            config.get("gpt53_api_key"),
            os.getenv("OPENAI_API_KEY"),
            codex_auth.get("OPENAI_API_KEY"),
        )
        return raw if raw else None

    def set_gpt53_api_key(self, api_key: str):
        """Settings GPT5.3 / OpenAI API Key. """
        config = self.get_config()
        config["gpt53_api_key"] = (api_key or "").strip()
        self.save_config(config)

    def get_gpt53_base_url(self) -> Optional[str]:
        """Text GPT5.3 / OpenAI Base URL. """
        config = self.get_config()
        codex_provider = self._get_codex_model_provider()
        raw = self._first_non_empty(
            config.get("gpt53_base_url"),
            os.getenv("OPENAI_BASE_URL"),
            codex_provider.get("base_url"),
        )
        return raw if raw else None

    def set_gpt53_base_url(self, base_url: str):
        """Settings GPT5.3 / OpenAI Base URL. """
        config = self.get_config()
        clean = (base_url or "").strip()
        if clean:
            config["gpt53_base_url"] = clean
        else:
            config.pop("gpt53_base_url", None)
        self.save_config(config)

    def get_gpt53_model(self) -> str:
        """Text GPT5.3 / OpenAI Text. """
        config = self.get_config()
        codex_config = self._get_codex_config()
        return self._first_non_empty(
            config.get("gpt53_model"),
            os.getenv("GPT53_MODEL"),
            os.getenv("OPENAI_MODEL"),
            codex_config.get("model"),
            "gpt-5.2",
        )

    def set_gpt53_model(self, model: str):
        """Settings GPT5.3 / OpenAI Text. """
        config = self.get_config()
        config["gpt53_model"] = (model or "").strip() or "gpt-5.2"
        self.save_config(config)

    def get_newsapi_api_key(self) -> Optional[str]:
        """Text News API Key(TextRefreshNews, Text https://newsapi.org/docs)"""
        config = self.get_config()
        raw = (config.get("newsapi_api_key") or os.getenv("NEWSAPI_API_KEY") or "").strip()
        return raw if raw else None

    def set_newsapi_api_key(self, api_key: str):
        """Settings News API Key"""
        config = self.get_config()
        config["newsapi_api_key"] = (api_key or "").strip()
        self.save_config(config)

    def get_tavily_api_key(self) -> Optional[str]:
        """Text Tavily API Key"""
        config = self.get_config()
        raw = (config.get("tavily_api_key") or os.getenv("TAVILY_API_KEY") or "").strip()
        return raw if raw else None

    def set_tavily_api_key(self, api_key: str):
        """Settings Tavily API Key"""
        config = self.get_config()
        config["tavily_api_key"] = (api_key or "").strip()
        self.save_config(config)

    def get_alpaca_api_key(self) -> Optional[str]:
        config = self.get_config()
        raw = self._first_non_empty(
            config.get("alpaca_api_key"),
            os.getenv("ALPACA_API_KEY"),
            os.getenv("APCA-API-KEY-ID"),
            os.getenv("APCA_API_KEY_ID"),
        )
        return raw if raw else None

    def get_alpaca_api_secret(self) -> Optional[str]:
        config = self.get_config()
        raw = self._first_non_empty(
            config.get("alpaca_api_secret"),
            os.getenv("ALPACA_API_SECRET"),
            os.getenv("APCA-API-SECRET-KEY"),
            os.getenv("APCA_API_SECRET_KEY"),
        )
        return raw if raw else None

    def get_alpaca_trading_base_url(self) -> str:
        config = self.get_config()
        return self._first_non_empty(
            config.get("alpaca_trading_base_url"),
            os.getenv("ALPACA_TRADING_BASE_URL"),
            os.getenv("ALPACA_ENDPOINT"),
            "https://paper-api.alpaca.markets/v2",
        )

    def get_alpaca_market_data_base_url(self) -> str:
        config = self.get_config()
        return self._first_non_empty(
            config.get("alpaca_market_data_base_url"),
            os.getenv("ALPACA_MARKET_DATA_BASE_URL"),
            "https://data.alpaca.markets/v2",
        )

    def get_alpaca_stock_feed(self) -> str:
        config = self.get_config()
        return self._first_non_empty(
            config.get("alpaca_stock_feed"),
            os.getenv("ALPACA_STOCK_FEED"),
            "iex",
        )

    def get_news_aggregation_strategy(self) -> str:
        """TextNewsText: 'priority' Text 'merge'"""
        config = self.get_config()
        return config.get("news_aggregation_strategy", "priority")

    def set_news_aggregation_strategy(self, strategy: str):
        """SettingsNewsText"""
        config = self.get_config()
        if strategy in ["priority", "merge"]:
            config["news_aggregation_strategy"] = strategy
            self.save_config(config)

    def get_gemini_api_key(self) -> Optional[str]:
        """Text Gemini API Key"""
        config = self.get_config()
        raw = (config.get("gemini_api_key") or os.getenv("GEMINI_API_KEY") or "").strip()
        return raw if raw else None

    def set_gemini_api_key(self, api_key: str):
        """Settings Gemini API Key"""
        config = self.get_config()
        config["gemini_api_key"] = (api_key or "").strip()
        self.save_config(config)

    def get_custom_models(self) -> List[Dict]:
        """Text"""
        config = self.get_config()
        return config.get("custom_models", [])

    def add_custom_model(self, name: str, base_url: str, model: str, api_key: str):
        """Text"""
        config = self.get_config()
        custom_models = config.get("custom_models", [])

        # Text
        for i, m in enumerate(custom_models):
            if m.get("name") == name:
                # Text
                custom_models[i] = {
                    "name": (name or "").strip(),
                    "base_url": (base_url or "").strip(),
                    "model": (model or "").strip(),
                    "api_key": (api_key or "").strip()
                }
                config["custom_models"] = custom_models
                self.save_config(config)
                return

        # Text
        custom_models.append({
            "name": (name or "").strip(),
            "base_url": (base_url or "").strip(),
            "model": (model or "").strip(),
            "api_key": (api_key or "").strip()
        })
        config["custom_models"] = custom_models
        self.save_config(config)

    def remove_custom_model(self, name: str):
        """Text"""
        config = self.get_config()
        custom_models = config.get("custom_models", [])
        custom_models = [m for m in custom_models if m.get("name") != name]
        config["custom_models"] = custom_models
        self.save_config(config)

    def get_custom_model(self, name: str) -> Optional[Dict]:
        """Text"""
        custom_models = self.get_custom_models()
        for model in custom_models:
            if model.get("name") == name:
                return model
        return None

    def get_active_model(self) -> str:
        """TextCurrentText"""
        config = self.get_config()
        return config.get("active_model", "gpt53")

    def set_active_model(self, model_name: str):
        """SettingsCurrentText"""
        config = self.get_config()
        config["active_model"] = (model_name or "").strip()
        self.save_config(config)

    def get_news_use_ai_search(self) -> bool:
        """TextNewsText AI TextSearch(Default True, TextDefaultText Gemini TextNews)"""
        config = self.get_config()
        return config.get("news_use_ai_search", True)

    def set_news_use_ai_search(self, use: bool):
        """SettingsText AI SearchNews"""
        config = self.get_config()
        config["news_use_ai_search"] = bool(use)
        self.save_config(config)

    def get_news_ai_enrich(self) -> bool:
        """TextNewsText AI Text(Text, TextSummary), Default True(P1)"""
        config = self.get_config()
        return config.get("news_ai_enrich", True)

    def set_news_ai_enrich(self, use: bool):
        """SettingsTextNewsText AI Text"""
        config = self.get_config()
        config["news_ai_enrich"] = bool(use)
        self.save_config(config)

    # ==================== Market FeedText ====================

    def get_rsshub_url(self) -> str:
        """Text RSSHub Text"""
        config = self.get_config()
        return config.get("rsshub_url", "http://localhost:1200")

    def set_rsshub_url(self, url: str):
        """Settings RSSHub Text"""
        config = self.get_config()
        config["rsshub_url"] = url.strip()
        self.save_config(config)

    def get_xueqiu_followed_users(self) -> List[Dict]:
        """TextMarket FeedText

        Returns:
            Text, Text user_id Text username
        """
        config = self.get_config()
        return config.get("xueqiu_followed_users", [])

    def add_xueqiu_user(self, user_id: str, username: str = None):
        """TextMarket FeedText

        Args:
            user_id: Market FeedText ID
            username: Text(Text)
        """
        config = self.get_config()
        users = config.get("xueqiu_followed_users", [])

        # Text
        for user in users:
            if user.get("user_id") == user_id:
                # Text
                if username:
                    user["username"] = username
                self.save_config(config)
                return

        # Text
        users.append({
            "user_id": user_id,
            "username": username or user_id
        })
        config["xueqiu_followed_users"] = users
        self.save_config(config)

    def remove_xueqiu_user(self, user_id: str):
        """TextMarket FeedText

        Args:
            user_id: Market FeedText ID
        """
        config = self.get_config()
        users = config.get("xueqiu_followed_users", [])
        users = [u for u in users if u.get("user_id") != user_id]
        config["xueqiu_followed_users"] = users
        self.save_config(config)

    # ==================== Text ====================

    def get_wewe_rss_url(self) -> str:
        """Text WeWe RSS Text"""
        config = self.get_config()
        return config.get("wewe_rss_url", "http://localhost:4000")

    def set_wewe_rss_url(self, url: str):
        """Settings WeWe RSS Text"""
        config = self.get_config()
        config["wewe_rss_url"] = url.strip()
        self.save_config(config)

    def get_wechat_followed_accounts(self) -> List[Dict]:
        """Text

        Returns:
            Text, Text mp_id Text name
        """
        config = self.get_config()
        return config.get("wechat_followed_accounts", [])

    def add_wechat_account(self, mp_id: str, name: str = None):
        """Text

        Args:
            mp_id: Text ID
            name: Text(Text)
        """
        config = self.get_config()
        accounts = config.get("wechat_followed_accounts", [])

        # Text
        for account in accounts:
            if account.get("mp_id") == mp_id:
                # Text
                if name:
                    account["name"] = name
                self.save_config(config)
                return

        # Text
        accounts.append({
            "mp_id": mp_id,
            "name": name or mp_id
        })
        config["wechat_followed_accounts"] = accounts
        self.save_config(config)

    def remove_wechat_account(self, mp_id: str):
        """Text

        Args:
            mp_id: Text ID
        """
        config = self.get_config()
        accounts = config.get("wechat_followed_accounts", [])
        accounts = [a for a in accounts if a.get("mp_id") != mp_id]
        config["wechat_followed_accounts"] = accounts
        self.save_config(config)

    def get_manual_wechat_articles(self) -> List[Dict]:
        """TextManualText

        Returns:
            Text, Text title, link, content, created_at
        """
        config = self.get_config()
        return config.get("manual_wechat_articles", [])

    def add_manual_wechat_article(self, title: str, content: str, link: str = "", date: str = ""):
        """TextManualText

        Args:
            title: Text
            content: Text
            link: Text(Text)
            date: TextDate YYYY-MM-DD(Text)
        """
        config = self.get_config()
        articles = config.get("manual_wechat_articles", [])

        # TextDate, TextDate; TextCurrentText
        if date:
            # TextDateText
            try:
                from datetime import datetime as dt
                dt.strptime(date, '%Y-%m-%d')
                created_at = f"{date}T00:00:00"
            except ValueError:
                created_at = datetime.now().isoformat()
        else:
            created_at = datetime.now().isoformat()

        # Text
        articles.append({
            "title": title,
            "link": link,
            "content": content,
            "created_at": created_at
        })

        # Text30Text
        if len(articles) > 30:
            articles = articles[-30:]

        config["manual_wechat_articles"] = articles
        self.save_config(config)

    def clear_manual_wechat_articles(self):
        """TextManualText"""
        config = self.get_config()
        config["manual_wechat_articles"] = []
        self.save_config(config)

    # ==================== WatchText ====================

    def _get_watchlist_path(self) -> Path:
        return self.base_dir / "watchlist.json"

    def _default_revisit_thresholds(self) -> Dict[str, float]:
        return {
            "weekly": 7.0,
            "monthly": 14.0,
            "since_added": 21.0,
        }

    def _default_revisit_metrics(self) -> Dict[str, Any]:
        return {
            "weekly_change_pct": None,
            "monthly_change_pct": None,
            "since_added_change_pct": None,
            "current_price": None,
            "baseline_price": None,
        }

    def _normalize_watch_candidate_record(self, candidate_id: str, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        raw = dict(payload or {})
        thresholds = dict(self._default_revisit_thresholds())
        thresholds.update(raw.get("revisit_thresholds") or {})

        metrics = dict(self._default_revisit_metrics())
        metrics.update(raw.get("revisit_metrics") or {})

        weekly_notes_raw = raw.get("weekly_notes") or {}
        weekly_notes: Dict[str, Dict[str, Any]] = {}
        if isinstance(weekly_notes_raw, dict):
            for key, value in weekly_notes_raw.items():
                week_id = str(key or "").strip()
                if not week_id:
                    continue
                entry = value if isinstance(value, dict) else {"content": value}
                weekly_notes[week_id] = {
                    "content": str(entry.get("content") or "").strip(),
                    "updated_at": entry.get("updated_at"),
                }
        legacy_weekly_note = str(raw.get("weekly_note") or "").strip()
        current_week = _current_week_id()
        if legacy_weekly_note and not weekly_notes.get(current_week):
            weekly_notes[current_week] = {
                "content": legacy_weekly_note,
                "updated_at": raw.get("updated_at"),
            }

        return {
            "stock_id": raw.get("stock_id") or candidate_id,
            "stock_name": raw.get("stock_name") or candidate_id,
            "ticker": (raw.get("ticker") or "").strip().upper(),
            "industry": raw.get("industry") or "",
            "theme": raw.get("theme") or "",
            "status": raw.get("status") or "WatchText",
            "profit_driver": raw.get("profit_driver") or "",
            "price_contains": raw.get("price_contains") or "",
            "odds_assessment": raw.get("odds_assessment") or "",
            "watch_reason": raw.get("watch_reason") or "",
            "not_buy_reason": raw.get("not_buy_reason") or "",
            "weekly_note": legacy_weekly_note or weekly_notes.get(current_week, {}).get("content", ""),
            "weekly_notes": weekly_notes,
            "watch_started_at": str(raw.get("watch_started_at") or "")[:10],
            "performance_summary": raw.get("performance_summary") or "",
            "performance_data": raw.get("performance_data") or {},
            "prices_updated_at": raw.get("prices_updated_at"),
            "news": list(raw.get("news") or []),
            "news_updated_at": raw.get("news_updated_at"),
            "news_search_warnings": list(raw.get("news_search_warnings") or []),
            "news_fallback_summary": raw.get("news_fallback_summary") or "",
            "news_cache_hit": bool(raw.get("news_cache_hit")),
            "news_deep_search_summary": raw.get("news_deep_search_summary") or "",
            "news_deep_search_meta": dict(raw.get("news_deep_search_meta") or {}),
            "revisit_metrics": metrics,
            "revisit_thresholds": thresholds,
            "revisit_active_rules": list(raw.get("revisit_active_rules") or []),
            "revisit_signature": raw.get("revisit_signature") or "",
            "revisit_ack_signature": raw.get("revisit_ack_signature") or "",
            "revisit_ack_at": raw.get("revisit_ack_at"),
            "revisit_updated_at": raw.get("revisit_updated_at"),
            "ai_watch_judgment": raw.get("ai_watch_judgment") or "",
            "ai_watch_judgment_generated_at": raw.get("ai_watch_judgment_generated_at"),
            "ai_watch_judgment_signature": raw.get("ai_watch_judgment_signature") or "",
            "ai_watch_judgment_error": raw.get("ai_watch_judgment_error") or "",
            "created_at": raw.get("created_at"),
            "updated_at": raw.get("updated_at"),
        }

    def _make_watch_candidate_id(self, stock_id: str, ticker: str = "", stock_name: str = "") -> str:
        raw = (stock_id or ticker or stock_name or "").strip()
        raw = re.sub(r"\s+", "_", raw)
        if not raw:
            raise ValueError("watchlist candidate id is required")
        return raw.upper()

    def _load_watchlist_locked(self) -> Dict[str, Any]:
        path = self._get_watchlist_path()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        data.setdefault("candidates", {})
                        return data
            except Exception:
                logger.exception("LoadWatchTextFailed, Text")
        now = datetime.now().isoformat()
        return {
            "created_at": now,
            "updated_at": now,
            "candidates": {}
        }

    def _save_watchlist_locked(self, data: Dict[str, Any]) -> None:
        path = self._get_watchlist_path()
        self._atomic_write_json(path, data)

    def get_watchlist(self) -> Dict[str, Any]:
        with self._watchlist_lock:
            data = self._load_watchlist_locked()
            candidates = [
                self._normalize_watch_candidate_record(key, item)
                for key, item in (data.get("candidates") or {}).items()
            ]
            candidates.sort(
                key=lambda item: (
                    str(item.get("theme") or "Text"),
                    str(item.get("industry") or "Text"),
                    str(item.get("stock_name") or item.get("stock_id") or "")
                )
            )
            return {
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "candidates": candidates,
            }

    def upsert_watch_candidate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._watchlist_lock:
            data = self._load_watchlist_locked()
            now = datetime.now().isoformat()

            candidate_id = self._make_watch_candidate_id(
                str(payload.get("stock_id") or ""),
                str(payload.get("ticker") or ""),
                str(payload.get("stock_name") or ""),
            )

            existing = self._normalize_watch_candidate_record(
                candidate_id,
                (data.get("candidates") or {}).get(candidate_id, {}),
            )
            created_at = existing.get("created_at") or now
            watch_started_at = existing.get("watch_started_at") or payload.get("watch_started_at") or now[:10]

            candidate = {
                "stock_id": candidate_id,
                "stock_name": (payload.get("stock_name") or existing.get("stock_name") or candidate_id).strip(),
                "ticker": (payload.get("ticker") or existing.get("ticker") or "").strip().upper(),
                "industry": (payload.get("industry") or existing.get("industry") or "").strip(),
                "theme": (payload.get("theme") or existing.get("theme") or "").strip(),
                "status": (payload.get("status") or existing.get("status") or "WatchText").strip() or "WatchText",
                "profit_driver": (payload.get("profit_driver") if payload.get("profit_driver") is not None else existing.get("profit_driver") or "").strip(),
                "price_contains": (payload.get("price_contains") if payload.get("price_contains") is not None else existing.get("price_contains") or "").strip(),
                "odds_assessment": (payload.get("odds_assessment") if payload.get("odds_assessment") is not None else existing.get("odds_assessment") or "").strip(),
                "watch_reason": (payload.get("watch_reason") if payload.get("watch_reason") is not None else existing.get("watch_reason") or "").strip(),
                "not_buy_reason": (payload.get("not_buy_reason") if payload.get("not_buy_reason") is not None else existing.get("not_buy_reason") or "").strip(),
                "weekly_note": (payload.get("weekly_note") if payload.get("weekly_note") is not None else existing.get("weekly_note") or "").strip(),
                "weekly_notes": existing.get("weekly_notes") or {},
                "watch_started_at": str(watch_started_at)[:10],
                "performance_summary": existing.get("performance_summary") or "",
                "performance_data": existing.get("performance_data") or {},
                "prices_updated_at": existing.get("prices_updated_at"),
                "news": existing.get("news") or [],
                "news_updated_at": existing.get("news_updated_at"),
                "news_search_warnings": existing.get("news_search_warnings") or [],
                "news_fallback_summary": existing.get("news_fallback_summary") or "",
                "news_cache_hit": bool(existing.get("news_cache_hit")),
                "news_deep_search_summary": existing.get("news_deep_search_summary") or "",
                "news_deep_search_meta": existing.get("news_deep_search_meta") or {},
                "revisit_metrics": existing.get("revisit_metrics") or self._default_revisit_metrics(),
                "revisit_thresholds": existing.get("revisit_thresholds") or self._default_revisit_thresholds(),
                "revisit_active_rules": existing.get("revisit_active_rules") or [],
                "revisit_signature": existing.get("revisit_signature") or "",
                "revisit_ack_signature": existing.get("revisit_ack_signature") or "",
                "revisit_ack_at": existing.get("revisit_ack_at"),
                "revisit_updated_at": existing.get("revisit_updated_at"),
                "ai_watch_judgment": existing.get("ai_watch_judgment") or "",
                "ai_watch_judgment_generated_at": existing.get("ai_watch_judgment_generated_at"),
                "ai_watch_judgment_signature": existing.get("ai_watch_judgment_signature") or "",
                "ai_watch_judgment_error": existing.get("ai_watch_judgment_error") or "",
                "created_at": created_at,
                "updated_at": now,
            }

            data.setdefault("candidates", {})[candidate_id] = candidate
            data["updated_at"] = now
            self._save_watchlist_locked(data)
            return self._normalize_watch_candidate_record(candidate_id, candidate)

    def update_watch_candidate(self, candidate_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        key = self._make_watch_candidate_id(candidate_id)
        with self._watchlist_lock:
            data = self._load_watchlist_locked()
            current = (data.get("candidates") or {}).get(key)
            if not current:
                return None
            current = self._normalize_watch_candidate_record(key, current)

            now = datetime.now().isoformat()
            for field in (
                "stock_name",
                "ticker",
                "industry",
                "theme",
                "status",
                "profit_driver",
                "price_contains",
                "odds_assessment",
                "watch_reason",
                "not_buy_reason",
                "weekly_note",
            ):
                if field in payload:
                    value = payload.get(field)
                    current[field] = (str(value).strip().upper() if field == "ticker" else str(value).strip()) if value is not None else ""
            if "watch_started_at" in payload and payload.get("watch_started_at"):
                current["watch_started_at"] = str(payload.get("watch_started_at"))[:10]
            current["updated_at"] = now
            data["updated_at"] = now
            data["candidates"][key] = current
            self._save_watchlist_locked(data)
            return self._normalize_watch_candidate_record(key, current)

    def update_watch_candidate_ai_judgment(
        self,
        candidate_id: str,
        judgment: str,
        signature: str,
        error: str = "",
    ) -> Optional[Dict[str, Any]]:
        key = self._make_watch_candidate_id(candidate_id)
        with self._watchlist_lock:
            data = self._load_watchlist_locked()
            current = (data.get("candidates") or {}).get(key)
            if not current:
                return None
            current = self._normalize_watch_candidate_record(key, current)
            now = datetime.now().isoformat()
            current["ai_watch_judgment"] = str(judgment or "").strip()
            current["ai_watch_judgment_signature"] = str(signature or "").strip()
            current["ai_watch_judgment_error"] = str(error or "").strip()
            current["ai_watch_judgment_generated_at"] = now if judgment or error else current.get("ai_watch_judgment_generated_at")
            current["updated_at"] = now
            data["updated_at"] = now
            data["candidates"][key] = current
            self._save_watchlist_locked(data)
            return self._normalize_watch_candidate_record(key, current)

    def update_watch_candidate_news(
        self,
        candidate_id: str,
        *,
        news: Optional[List[Dict[str, Any]]] = None,
        news_search_warnings: Optional[List[str]] = None,
        news_fallback_summary: Optional[str] = None,
        news_cache_hit: Optional[bool] = None,
        news_deep_search_summary: Optional[str] = None,
        news_deep_search_meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        key = self._make_watch_candidate_id(candidate_id)
        with self._watchlist_lock:
            data = self._load_watchlist_locked()
            current = (data.get("candidates") or {}).get(key)
            if not current:
                return None
            current = self._normalize_watch_candidate_record(key, current)
            now = datetime.now().isoformat()
            current["news"] = list(news or [])
            current["news_updated_at"] = now
            if news_search_warnings is not None:
                current["news_search_warnings"] = list(news_search_warnings or [])
            if news_fallback_summary is not None:
                current["news_fallback_summary"] = str(news_fallback_summary or "").strip()
            if news_cache_hit is not None:
                current["news_cache_hit"] = bool(news_cache_hit)
            if news_deep_search_summary is not None:
                current["news_deep_search_summary"] = str(news_deep_search_summary or "").strip()
            if news_deep_search_meta is not None:
                current["news_deep_search_meta"] = dict(news_deep_search_meta or {})
            current["updated_at"] = now
            data["updated_at"] = now
            data["candidates"][key] = current
            self._save_watchlist_locked(data)
            return self._normalize_watch_candidate_record(key, current)

    def update_watch_candidate_performance(
        self,
        candidate_id: str,
        performance_summary: str = "",
        performance_data: Optional[Dict[str, Any]] = None,
        revisit_metrics: Optional[Dict[str, Any]] = None,
        revisit_thresholds: Optional[Dict[str, Any]] = None,
        revisit_active_rules: Optional[List[Dict[str, Any]]] = None,
        revisit_signature: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        key = self._make_watch_candidate_id(candidate_id)
        with self._watchlist_lock:
            data = self._load_watchlist_locked()
            current = (data.get("candidates") or {}).get(key)
            if not current:
                return None
            current = self._normalize_watch_candidate_record(key, current)
            now = datetime.now().isoformat()
            current["performance_summary"] = performance_summary or ""
            current["performance_data"] = performance_data or {}
            current["prices_updated_at"] = now
            if revisit_metrics is not None:
                metrics = self._default_revisit_metrics()
                metrics.update(revisit_metrics or {})
                current["revisit_metrics"] = metrics
            if revisit_thresholds is not None:
                thresholds = self._default_revisit_thresholds()
                thresholds.update(revisit_thresholds or {})
                current["revisit_thresholds"] = thresholds
            if revisit_active_rules is not None:
                current["revisit_active_rules"] = list(revisit_active_rules or [])
            if revisit_signature is not None:
                current["revisit_signature"] = str(revisit_signature or "")
            current["revisit_updated_at"] = now
            current["updated_at"] = now
            data["updated_at"] = now
            data["candidates"][key] = current
            self._save_watchlist_locked(data)
            return self._normalize_watch_candidate_record(key, current)

    def save_watch_candidate_weekly_note(self, candidate_id: str, week_id: str, content: str) -> Optional[Dict[str, Any]]:
        key = self._make_watch_candidate_id(candidate_id)
        resolved_week = str(week_id or _current_week_id()).strip() or _current_week_id()
        note_text = str(content or "").strip()
        with self._watchlist_lock:
            data = self._load_watchlist_locked()
            current = (data.get("candidates") or {}).get(key)
            if not current:
                return None
            current = self._normalize_watch_candidate_record(key, current)
            weekly_notes = dict(current.get("weekly_notes") or {})
            now = datetime.now().isoformat()
            if note_text:
                weekly_notes[resolved_week] = {
                    "content": note_text,
                    "updated_at": now,
                }
            else:
                weekly_notes.pop(resolved_week, None)
            current["weekly_notes"] = weekly_notes
            current["weekly_note"] = weekly_notes.get(_current_week_id(), {}).get("content", "")
            current["updated_at"] = now
            data["candidates"][key] = current
            data["updated_at"] = now
            self._save_watchlist_locked(data)
            return self._normalize_watch_candidate_record(key, current)

    def acknowledge_watch_candidate_revisit(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        key = self._make_watch_candidate_id(candidate_id)
        with self._watchlist_lock:
            data = self._load_watchlist_locked()
            current = (data.get("candidates") or {}).get(key)
            if not current:
                return None
            current = self._normalize_watch_candidate_record(key, current)
            now = datetime.now().isoformat()
            current["revisit_ack_signature"] = current.get("revisit_signature") or ""
            current["revisit_ack_at"] = now
            current["updated_at"] = now
            data["updated_at"] = now
            data["candidates"][key] = current
            self._save_watchlist_locked(data)
            return self._normalize_watch_candidate_record(key, current)

    def delete_watch_candidate(self, candidate_id: str) -> bool:
        key = self._make_watch_candidate_id(candidate_id)
        with self._watchlist_lock:
            data = self._load_watchlist_locked()
            candidates = data.get("candidates") or {}
            if key not in candidates:
                return False
            candidates.pop(key, None)
            data["updated_at"] = datetime.now().isoformat()
            self._save_watchlist_locked(data)
            return True

    # ==================== IMA Text ====================

    def _get_ima_sync_history_path(self) -> Path:
        return self.base_dir / "ima_sync_history.json"

    def get_ima_config(self) -> Dict[str, Any]:
        config = self.get_config()
        return {
            "client_id": str(config.get("ima_client_id") or "").strip(),
            "api_key": str(config.get("ima_api_key") or "").strip(),
            "knowledge_base_id": str(config.get("ima_target_kb_id") or "").strip(),
            "knowledge_base_name": str(config.get("ima_target_kb_name") or "").strip(),
        }

    def save_ima_config(
        self,
        *,
        client_id: Optional[str] = None,
        api_key: Optional[str] = None,
        knowledge_base_id: Optional[str] = None,
        knowledge_base_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        config = self.get_config()

        if client_id is not None:
            clean = str(client_id).strip()
            if clean:
                config["ima_client_id"] = clean
        if api_key is not None:
            clean = str(api_key).strip()
            if clean:
                config["ima_api_key"] = clean
        if knowledge_base_id is not None:
            clean = str(knowledge_base_id).strip()
            if clean:
                config["ima_target_kb_id"] = clean
            else:
                config.pop("ima_target_kb_id", None)
        if knowledge_base_name is not None:
            clean = str(knowledge_base_name).strip()
            if clean:
                config["ima_target_kb_name"] = clean
            else:
                config.pop("ima_target_kb_name", None)

        self.save_config(config)
        return self.get_ima_config()

    def get_ima_export_dir(self, snapshot_type: str) -> Path:
        subdirs = {
            "zsxq_daily": "zsxq_daily",
            "weekly_reviews": "weekly_reviews",
            "watchlist_snapshots": "watchlist_snapshots",
        }
        dirname = subdirs.get(str(snapshot_type or "").strip(), str(snapshot_type or "").strip() or "misc")
        path = self.base_dir / "ima_exports" / dirname
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_ima_export_path(self, snapshot_type: str, filename: str) -> Path:
        return self.get_ima_export_dir(snapshot_type) / str(filename or "").strip()

    # ==================== Text ====================

    def get_us_screener_dir(self) -> Path:
        path = self.base_dir / "us_screener"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_us_screener_results_dir(self) -> Path:
        path = self.get_us_screener_dir() / "results"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _get_us_screener_latest_path(self) -> Path:
        return self.get_us_screener_dir() / "latest.json"

    def _get_us_screener_job_status_path(self) -> Path:
        return self.get_us_screener_dir() / "job_status.json"

    def _get_us_screener_partial_path(self) -> Path:
        return self.get_us_screener_dir() / "partial.json"

    def _get_us_screener_company_profiles_path(self) -> Path:
        return self.get_us_screener_dir() / "company_profiles.json"

    def _get_us_screener_ai_briefs_path(self) -> Path:
        return self.get_us_screener_dir() / "ai_briefs.json"

    def _get_us_screener_universe_snapshot_path(self) -> Path:
        return self.get_us_screener_dir() / "universe_snapshot.json"

    def _get_us_screener_alerts_path(self) -> Path:
        return self.get_us_screener_dir() / "alerts.json"

    def _get_us_screener_research_queue_path(self) -> Path:
        return self.get_us_screener_dir() / "research_queue.json"

    def _get_us_screener_result_path(self, market_date: str) -> Path:
        safe_market_date = str(market_date or "").strip() or datetime.now().strftime("%Y-%m-%d")
        return self.get_us_screener_results_dir() / f"{safe_market_date}.json"

    def _default_us_screener_latest(self) -> Dict[str, Any]:
        return build_default_us_screener_payload()

    def _build_us_screener_cache_meta(self, path: Path, source: str, *, recovered: bool) -> Dict[str, Any]:
        cached_at = ""
        if path.exists():
            try:
                cached_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
            except Exception:
                cached_at = ""
        return {
            "source": source,
            "path": str(path),
            "cached_at": cached_at,
            "recovered": recovered,
            "is_stale": False,
        }

    def _attach_us_screener_cache_meta(
        self,
        payload: Dict[str, Any],
        *,
        path: Path,
        source: str,
        recovered: bool,
    ) -> Dict[str, Any]:
        normalized = normalize_us_screener_payload(payload)
        cache = self._build_us_screener_cache_meta(path, source, recovered=recovered)
        market_date_text = str(normalized.get("as_of_market_date") or "").strip()
        if market_date_text:
            try:
                age_days = (datetime.now().date() - datetime.strptime(market_date_text, "%Y-%m-%d").date()).days
                cache["is_stale"] = age_days > 3
            except ValueError:
                cache["is_stale"] = False
        normalized["cache"] = cache
        return normalized

    def _has_usable_us_screener_latest(self, payload: Dict[str, Any]) -> bool:
        normalized = normalize_us_screener_payload(payload)
        if not normalized.get("success"):
            return False
        if str(normalized.get("generated_at") or "").strip():
            return True
        if str(normalized.get("as_of_market_date") or "").strip():
            return True
        return False

    def _prepare_us_screener_payload_for_save(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = normalize_us_screener_payload(payload)
        normalized.pop("cache", None)
        return normalized

    def _recover_latest_us_screener_from_results_locked(self) -> Optional[Dict[str, Any]]:
        results_dir = self.get_us_screener_results_dir()
        candidates = sorted(results_dir.glob("*.json"), reverse=True)
        for path in candidates:
            payload = self._load_us_screener_json_locked(path, self._default_us_screener_latest())
            if not self._has_usable_us_screener_latest(payload):
                continue
            recovered = self._attach_us_screener_cache_meta(
                payload,
                path=path,
                source=f"results/{path.name}",
                recovered=True,
            )
            self._save_us_screener_json_locked(
                self._get_us_screener_latest_path(),
                self._prepare_us_screener_payload_for_save(recovered),
            )
            return recovered
        return None

    def _default_us_screener_job_status(self) -> Dict[str, Any]:
        return {
            "state": "idle",
            "started_at": "",
            "finished_at": "",
            "step": "",
            "progress": {
                "total_batches": 0,
                "completed_batches": 0,
                "current_batch_size": 0,
            },
            "message": "",
            "last_error": "",
            "partial_summary": None,
        }

    def _load_us_screener_json_locked(self, path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
        if path.exists():
            data = self._load_json_file_with_default(
                path,
                {},
                expected_type=dict,
                label="us screener payload",
            )
            if isinstance(data, dict):
                merged = dict(default)
                merged.update(data)
                return merged
        return dict(default)

    def _save_us_screener_json_locked(self, path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._atomic_write_json(path, payload or {})
        safe = _json_safe_value(payload or {})
        return dict(safe) if isinstance(safe, dict) else {}

    def get_us_screener_latest(self) -> Dict[str, Any]:
        with self._us_screener_lock:
            latest_path = self._get_us_screener_latest_path()
            payload = self._load_us_screener_json_locked(
                latest_path,
                self._default_us_screener_latest(),
            )
            if self._has_usable_us_screener_latest(payload):
                healed = self._attach_us_screener_cache_meta(
                    payload,
                    path=latest_path,
                    source="latest",
                    recovered=False,
                )
                raw_normalized = normalize_us_screener_payload(payload)
                if raw_normalized != self._prepare_us_screener_payload_for_save(healed):
                    self._save_us_screener_json_locked(
                        latest_path,
                        self._prepare_us_screener_payload_for_save(healed),
                    )
                return healed
            recovered = self._recover_latest_us_screener_from_results_locked()
            if recovered:
                return recovered
            return self._attach_us_screener_cache_meta(
                payload,
                path=latest_path,
                source="latest",
                recovered=False,
            )

    def save_us_screener_latest(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._us_screener_lock:
            saved = self._save_us_screener_json_locked(
                self._get_us_screener_latest_path(),
                self._prepare_us_screener_payload_for_save(payload),
            )
            return self._attach_us_screener_cache_meta(
                saved,
                path=self._get_us_screener_latest_path(),
                source="latest",
                recovered=False,
            )

    def get_us_screener_job_status(self) -> Dict[str, Any]:
        with self._us_screener_lock:
            return self._load_us_screener_json_locked(
                self._get_us_screener_job_status_path(),
                self._default_us_screener_job_status(),
            )

    def save_us_screener_job_status(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._us_screener_lock:
            current = self._load_us_screener_json_locked(
                self._get_us_screener_job_status_path(),
                self._default_us_screener_job_status(),
            )
            merged = dict(current)
            merged.update(payload or {})
            if isinstance(current.get("progress"), dict) or isinstance((payload or {}).get("progress"), dict):
                progress = dict(current.get("progress") or {})
                progress.update((payload or {}).get("progress") or {})
                merged["progress"] = progress
            return self._save_us_screener_json_locked(self._get_us_screener_job_status_path(), merged)

    def get_us_screener_partial(self) -> Dict[str, Any]:
        with self._us_screener_lock:
            path = self._get_us_screener_partial_path()
            if not path.exists():
                return {}
            return self._load_us_screener_json_locked(path, {})

    def save_us_screener_partial(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._us_screener_lock:
            normalized = dict(payload or {})
            return self._save_us_screener_json_locked(self._get_us_screener_partial_path(), normalized)

    def clear_us_screener_partial(self) -> None:
        with self._us_screener_lock:
            path = self._get_us_screener_partial_path()
            if path.exists():
                path.unlink()

    def get_us_screener_company_profiles(self) -> Dict[str, Any]:
        with self._us_screener_lock:
            return self._load_us_screener_json_locked(
                self._get_us_screener_company_profiles_path(),
                {},
            )

    def save_us_screener_company_profiles(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._us_screener_lock:
            return self._save_us_screener_json_locked(self._get_us_screener_company_profiles_path(), payload)

    def get_us_screener_ai_briefs(self) -> Dict[str, Any]:
        with self._us_screener_lock:
            return self._load_us_screener_json_locked(
                self._get_us_screener_ai_briefs_path(),
                {},
            )

    def get_us_screener_universe_snapshot(self) -> Dict[str, Any]:
        with self._us_screener_lock:
            return self._load_us_screener_json_locked(
                self._get_us_screener_universe_snapshot_path(),
                {},
            )

    def save_us_screener_universe_snapshot(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._us_screener_lock:
            return self._save_us_screener_json_locked(self._get_us_screener_universe_snapshot_path(), payload)

    def save_us_screener_ai_briefs(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._us_screener_lock:
            normalized = payload if isinstance(payload, dict) else {}
            return self._save_us_screener_json_locked(self._get_us_screener_ai_briefs_path(), normalized)

    def upsert_us_screener_ai_brief(self, cache_key: str, record: Dict[str, Any]) -> Dict[str, Any]:
        if not cache_key:
            raise ValueError("cache_key is required")
        with self._us_screener_lock:
            cache = self._load_us_screener_json_locked(
                self._get_us_screener_ai_briefs_path(),
                {},
            )
            cache[cache_key] = record or {}
            saved = self._save_us_screener_json_locked(
                self._get_us_screener_ai_briefs_path(),
                cache,
            )
            return saved.get(cache_key, cache[cache_key])

    def get_us_screener_alerts(self) -> List[Dict[str, Any]]:
        with self._us_screener_lock:
            payload = self._load_us_screener_json_locked(
                self._get_us_screener_alerts_path(),
                {"alerts": []},
            )
            alerts = payload.get("alerts")
            return list(alerts) if isinstance(alerts, list) else []

    def upsert_us_screener_alert(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        ticker = str(payload.get("ticker") or "").strip().upper()
        alert_type = str(payload.get("alert_type") or "").strip()
        if not ticker:
            raise ValueError("ticker is required")
        if not alert_type:
            raise ValueError("alert_type is required")

        threshold = payload.get("threshold")
        try:
            threshold = None if threshold in (None, "") else float(threshold)
        except (TypeError, ValueError):
            threshold = None

        now_iso = datetime.now().isoformat(timespec="seconds")
        record = {
            "id": str(payload.get("id") or f"alert_{uuid.uuid4().hex[:12]}"),
            "ticker": ticker,
            "stock_name": str(payload.get("stock_name") or ticker).strip() or ticker,
            "alert_type": alert_type,
            "threshold": threshold,
            "note": str(payload.get("note") or "").strip(),
            "source": str(payload.get("source") or "manual").strip() or "manual",
            "created_at": str(payload.get("created_at") or now_iso).strip() or now_iso,
            "expires_at": str(payload.get("expires_at") or "").strip(),
            "active": bool(payload.get("active", True)),
            "updated_at": now_iso,
        }

        with self._us_screener_lock:
            cache = self._load_us_screener_json_locked(
                self._get_us_screener_alerts_path(),
                {"alerts": []},
            )
            alerts = list(cache.get("alerts") or [])
            replaced = False
            for index, existing in enumerate(alerts):
                if str(existing.get("id") or "") == record["id"]:
                    existing_created = str(existing.get("created_at") or "").strip()
                    if existing_created:
                        record["created_at"] = existing_created
                    alerts[index] = record
                    replaced = True
                    break
            if not replaced:
                alerts.insert(0, record)
            cache["alerts"] = sorted(
                alerts,
                key=lambda item: str(item.get("created_at") or ""),
                reverse=True,
            )
            self._save_us_screener_json_locked(self._get_us_screener_alerts_path(), cache)
            return record

    def delete_us_screener_alert(self, alert_id: str) -> bool:
        target = str(alert_id or "").strip()
        if not target:
            return False
        with self._us_screener_lock:
            cache = self._load_us_screener_json_locked(
                self._get_us_screener_alerts_path(),
                {"alerts": []},
            )
            alerts = list(cache.get("alerts") or [])
            filtered = [item for item in alerts if str(item.get("id") or "") != target]
            if len(filtered) == len(alerts):
                return False
            cache["alerts"] = filtered
            self._save_us_screener_json_locked(self._get_us_screener_alerts_path(), cache)
            return True

    def get_us_screener_research_queue(self) -> List[Dict[str, Any]]:
        with self._us_screener_lock:
            payload = self._load_us_screener_json_locked(
                self._get_us_screener_research_queue_path(),
                {"items": []},
            )
            items = payload.get("items")
            rows = list(items) if isinstance(items, list) else []
            return sorted(rows, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)

    def upsert_us_screener_research_queue_item(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        ticker = str(payload.get("ticker") or payload.get("stock_id") or "").strip().upper()
        if not ticker:
            raise ValueError("ticker is required")
        now_iso = datetime.now().isoformat(timespec="seconds")
        allowed_statuses = {"new", "researching", "dismissed", "added_to_watchlist"}
        status = str(payload.get("status") or "new").strip().lower() or "new"
        if status not in allowed_statuses:
            status = "new"
        queue_id = str(payload.get("id") or f"idea_{ticker}_{uuid.uuid4().hex[:10]}").strip()
        signal_tags = payload.get("signal_tags")
        if not isinstance(signal_tags, list):
            signal_tags = []
        record = {
            "id": queue_id,
            "ticker": ticker,
            "stock_id": str(payload.get("stock_id") or ticker).strip().upper() or ticker,
            "stock_name": str(payload.get("stock_name") or ticker).strip() or ticker,
            "signal_date": str(payload.get("signal_date") or "").strip(),
            "strategy": str(payload.get("strategy") or "").strip().lower(),
            "reason": str(payload.get("reason") or "").strip(),
            "signal_tags": [str(item).strip() for item in signal_tags if str(item).strip()][:8],
            "research_priority": _safe_int(payload.get("research_priority"), 0),
            "status": status,
            "note": str(payload.get("note") or "").strip(),
            "dismiss_reason": str(payload.get("dismiss_reason") or "").strip(),
            "created_at": str(payload.get("created_at") or now_iso).strip() or now_iso,
            "updated_at": now_iso,
        }
        with self._us_screener_lock:
            cache = self._load_us_screener_json_locked(
                self._get_us_screener_research_queue_path(),
                {"items": []},
            )
            items = list(cache.get("items") or [])
            replaced = False
            for index, existing in enumerate(items):
                same_id = str(existing.get("id") or "") == record["id"]
                existing_ticker = str(existing.get("ticker") or existing.get("stock_id") or "").strip().upper()
                existing_signal_date = str(existing.get("signal_date") or "").strip()
                existing_strategy = str(existing.get("strategy") or "").strip().lower()
                same_signal = (
                    existing_ticker == ticker
                    and existing_signal_date == record["signal_date"]
                    and existing_strategy == record["strategy"]
                )
                legacy_same_ticker = (
                    existing_ticker == ticker
                    and not existing_signal_date
                    and not existing_strategy
                    and not record["signal_date"]
                    and not record["strategy"]
                )
                if same_id or same_signal or legacy_same_ticker:
                    existing_created = str(existing.get("created_at") or "").strip()
                    if existing_created:
                        record["created_at"] = existing_created
                    if not record["reason"]:
                        record["reason"] = str(existing.get("reason") or "").strip()
                    if not record["signal_tags"]:
                        record["signal_tags"] = list(existing.get("signal_tags") or [])
                    items[index] = record
                    replaced = True
                    break
            if not replaced:
                items.insert(0, record)
            cache["items"] = sorted(
                items,
                key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
                reverse=True,
            )
            self._save_us_screener_json_locked(self._get_us_screener_research_queue_path(), cache)
            return record

    def update_us_screener_research_queue_item(self, item_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        target = str(item_id or "").strip()
        if not target:
            return None
        merged: Optional[Dict[str, Any]] = None
        with self._us_screener_lock:
            cache = self._load_us_screener_json_locked(
                self._get_us_screener_research_queue_path(),
                {"items": []},
            )
            items = list(cache.get("items") or [])
            for existing in items:
                if str(existing.get("id") or "") == target:
                    merged = dict(existing)
                    merged.update(patch or {})
                    merged["id"] = target
                    break
        if merged is None:
            return None
        return self.upsert_us_screener_research_queue_item(merged)

    def delete_us_screener_research_queue_item(self, item_id: str) -> bool:
        target = str(item_id or "").strip()
        if not target:
            return False
        with self._us_screener_lock:
            cache = self._load_us_screener_json_locked(
                self._get_us_screener_research_queue_path(),
                {"items": []},
            )
            items = list(cache.get("items") or [])
            filtered = [item for item in items if str(item.get("id") or "") != target]
            if len(filtered) == len(items):
                return False
            cache["items"] = filtered
            self._save_us_screener_json_locked(self._get_us_screener_research_queue_path(), cache)
            return True

    def get_us_screener_result(self, market_date: str) -> Dict[str, Any]:
        with self._us_screener_lock:
            result_path = self._get_us_screener_result_path(market_date)
            payload = self._load_us_screener_json_locked(
                result_path,
                self._default_us_screener_latest(),
            )
            return self._attach_us_screener_cache_meta(
                payload,
                path=result_path,
                source=f"results/{result_path.name}",
                recovered=False,
            )

    def save_us_screener_result(self, market_date: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._us_screener_lock:
            result_path = self._get_us_screener_result_path(market_date)
            saved = self._save_us_screener_json_locked(
                result_path,
                self._prepare_us_screener_payload_for_save(payload),
            )
            return self._attach_us_screener_cache_meta(
                saved,
                path=result_path,
                source=f"results/{result_path.name}",
                recovered=False,
            )

    def _load_ima_sync_history_locked(self) -> Dict[str, Any]:
        path = self._get_ima_sync_history_path()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
            except Exception:
                logger.exception("Load IMA TextFailed, Text")
        return {"items": {}}

    def _save_ima_sync_history_locked(self, data: Dict[str, Any]) -> None:
        path = self._get_ima_sync_history_path()
        self._atomic_write_json(path, data)

    def get_ima_sync_history(self) -> Dict[str, Any]:
        with self._ima_sync_lock:
            data = self._load_ima_sync_history_locked()
            data.setdefault("items", {})
            return data

    def get_ima_sync_record(self, sync_key: str) -> Optional[Dict[str, Any]]:
        key = str(sync_key or "").strip()
        if not key:
            return None
        with self._ima_sync_lock:
            data = self._load_ima_sync_history_locked()
            item = (data.get("items") or {}).get(key)
            return dict(item) if isinstance(item, dict) else None

    def save_ima_sync_record(self, sync_key: str, record: Dict[str, Any]) -> Dict[str, Any]:
        key = str(sync_key or "").strip()
        if not key:
            raise ValueError("sync_key is required")
        with self._ima_sync_lock:
            data = self._load_ima_sync_history_locked()
            data.setdefault("items", {})[key] = dict(record or {})
            self._save_ima_sync_history_locked(data)
            saved = data["items"][key]
            return dict(saved) if isinstance(saved, dict) else {}

    # ==================== Text Playbook ====================

    def get_portfolio_playbook(self) -> Optional[Dict]:
        """Text Playbook"""
        with self._portfolio_lock:
            if self.portfolio_playbook_path.exists():
                with open(self.portfolio_playbook_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            return None

    def save_portfolio_playbook(self, playbook: Dict):
        """SaveText Playbook"""
        playbook["updated_at"] = datetime.now().isoformat()
        if "created_at" not in playbook:
            playbook["created_at"] = playbook["updated_at"]
        with self._portfolio_lock:
            self._atomic_write_json(self.portfolio_playbook_path, playbook)

    def has_portfolio_playbook(self) -> bool:
        """Text Playbook"""
        return self.portfolio_playbook_path.exists()

    # ==================== Text Playbook ====================

    def _get_stock_dir(self, stock_id: str) -> Path:
        """TextStockText"""
        stock_dir = self.base_dir / "stocks" / stock_id.lower().replace(" ", "_")
        stock_dir.mkdir(parents=True, exist_ok=True)
        (stock_dir / "uploads").mkdir(exist_ok=True)
        return stock_dir

    def get_stock_playbook(self, stock_id: str) -> Optional[Dict]:
        """Text Playbook"""
        lock = self._get_stock_lock(stock_id)
        with lock:
            playbook_path = self._get_stock_dir(stock_id) / "playbook.json"
            if playbook_path.exists():
                with open(playbook_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            return None

    def save_stock_playbook(self, stock_id: str, playbook: Dict):
        """SaveText Playbook"""
        playbook["stock_id"] = stock_id
        playbook["updated_at"] = datetime.now().isoformat()
        if "created_at" not in playbook:
            playbook["created_at"] = playbook["updated_at"]
        lock = self._get_stock_lock(stock_id)
        with lock:
            playbook_path = self._get_stock_dir(stock_id) / "playbook.json"
            self._atomic_write_json(playbook_path, playbook)

    def list_stocks(self) -> List[Dict]:
        """TextStock"""
        stocks = []
        stocks_dir = self.base_dir / "stocks"
        if stocks_dir.exists():
            for stock_dir in stocks_dir.iterdir():
                if stock_dir.is_dir():
                    playbook = self.get_stock_playbook(stock_dir.name)
                    if playbook:
                        stock_id = str(playbook.get("stock_id") or playbook.get("ticker") or stock_dir.name).strip()
                        ticker = str(playbook.get("ticker") or stock_id).strip()
                        stocks.append({
                            "stock_id": stock_id,
                            "stock_name": playbook.get("stock_name", stock_id or stock_dir.name),
                            "ticker": ticker,
                            "summary": playbook.get("core_thesis", {}).get("summary", ""),
                            "updated_at": playbook.get("updated_at", "")
                        })
        return stocks

    def list_weekly_review_stocks(self) -> List[Dict[str, Any]]:
        """Text weekly_reviews.json TextStock(TextHoldings)"""
        data = self._load_weekly_reviews_file()
        if not data:
            return []

        stock_catalog = self.list_stocks()

        def _resolve_catalog_entry(sid: str, stock_data: Dict[str, Any]) -> Dict[str, str]:
            aliases = [
                sid,
                (stock_data or {}).get("stock_id"),
                (stock_data or {}).get("ticker"),
                (stock_data or {}).get("stock_name"),
                (stock_data or {}).get("search_name"),
            ]
            for item in stock_catalog:
                item_aliases = [
                    item.get("stock_id"),
                    item.get("ticker"),
                    item.get("stock_name"),
                    item.get("search_name"),
                ]
                if any(
                    alias
                    and item_alias
                    and self._canonical_code(str(alias)) == self._canonical_code(str(item_alias))
                    for alias in aliases
                    for item_alias in item_aliases
                ):
                    return {
                        "stock_id": str(item.get("stock_id") or sid).strip(),
                        "stock_name": str(item.get("stock_name") or (stock_data or {}).get("stock_name") or sid).strip(),
                        "ticker": str(item.get("ticker") or (stock_data or {}).get("ticker") or sid).strip(),
                    }
            return {
                "stock_id": sid,
                "stock_name": str((stock_data or {}).get("stock_name") or sid).strip(),
                "ticker": str((stock_data or {}).get("ticker") or "").strip(),
            }

        merged: Dict[str, Dict[str, Any]] = {}
        weeks = (data.get("weeks") or {}) if isinstance(data, dict) else {}
        for _, review in weeks.items():
            stocks = (review or {}).get("stocks") or {}
            if not isinstance(stocks, dict):
                continue
            for stock_id, stock_data in stocks.items():
                sid = str(stock_id or "").strip()
                if not sid:
                    continue
                stock_payload = stock_data if isinstance(stock_data, dict) else {}
                resolved = _resolve_catalog_entry(sid, stock_payload)
                merge_key = resolved["stock_id"] or sid
                current = merged.setdefault(
                    merge_key,
                    {
                        "stock_id": merge_key,
                        "stock_name": resolved.get("stock_name") or "",
                        "ticker": resolved.get("ticker") or "",
                        "updated_at": "",
                    },
                )
                stock_name = str(resolved.get("stock_name") or stock_payload.get("stock_name") or "").strip()
                if stock_name and (not current["stock_name"] or current["stock_name"] == current["stock_id"]):
                    current["stock_name"] = stock_name
                ticker = str(resolved.get("ticker") or stock_payload.get("ticker") or "").strip()
                if ticker and not current.get("ticker"):
                    current["ticker"] = ticker
                updated_at = str((review or {}).get("updated_at") or "").strip()
                if updated_at and updated_at > str(current.get("updated_at") or ""):
                    current["updated_at"] = updated_at
        return list(merged.values())

    def delete_stock(self, stock_id: str) -> bool:
        """TextStock"""
        stock_dir = self._get_stock_dir(stock_id)
        if stock_dir.exists():
            shutil.rmtree(stock_dir)
            return True
        return False

    # ==================== ResearchHistory ====================

    def get_research_history(self, stock_id: str) -> Dict:
        """TextResearchHistory"""
        lock = self._get_stock_lock(stock_id)
        with lock:
            history_path = self._get_stock_dir(stock_id) / "history.json"
            if history_path.exists():
                with open(history_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            return {"stock_id": stock_id, "records": []}

    def add_research_record(self, stock_id: str, record: Dict):
        """TextResearchText"""
        lock = self._get_stock_lock(stock_id)
        with lock:
            history_path = self._get_stock_dir(stock_id) / "history.json"
            if history_path.exists():
                with open(history_path, "r", encoding="utf-8") as f:
                    history = json.load(f)
            else:
                history = {"stock_id": stock_id, "records": []}

            record["id"] = f"research_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            record["date"] = datetime.now().isoformat()
            history["records"].insert(0, record)

            self._atomic_write_json(history_path, history)

    def get_recent_research(self, stock_id: str, limit: int = 3) -> List[Dict]:
        """TextResearchText(Text)"""
        history = self.get_research_history(stock_id)
        records = history.get("records", [])

        # Text
        milestones = [r for r in records if r.get("is_milestone")]
        regular = [r for r in records if not r.get("is_milestone")]

        # Text limit Text
        recent = regular[:limit]

        # Text(Text)
        recent_ids = {r.get("id") for r in recent}
        for m in milestones:
            if m.get("id") not in recent_ids:
                recent.append(m)

        # TextDateText(Text)
        recent.sort(key=lambda x: x.get("date", ""), reverse=True)

        return recent

    def toggle_milestone(self, stock_id: str, record_id: str) -> bool:
        """TextResearchTextStatus"""
        lock = self._get_stock_lock(stock_id)
        with lock:
            history_path = self._get_stock_dir(stock_id) / "history.json"
            if history_path.exists():
                with open(history_path, "r", encoding="utf-8") as f:
                    history = json.load(f)
            else:
                return False

            for record in history.get("records", []):
                if record.get("id") == record_id:
                    record["is_milestone"] = not record.get("is_milestone", False)
                    record["milestone_updated_at"] = datetime.now().isoformat()
                    self._atomic_write_json(history_path, history)
                    return record["is_milestone"]

        return False

    def get_milestone_records(self, stock_id: str) -> List[Dict]:
        """Text"""
        history = self.get_research_history(stock_id)
        return [r for r in history.get("records", []) if r.get("is_milestone")]

    def update_research_feedback(self, stock_id: str, record_id: str, feedback: Dict) -> bool:
        """TextResearchText"""
        lock = self._get_stock_lock(stock_id)
        with lock:
            history_path = self._get_stock_dir(stock_id) / "history.json"
            if history_path.exists():
                with open(history_path, "r", encoding="utf-8") as f:
                    history = json.load(f)
            else:
                return False

            for record in history.get("records", []):
                if record.get("id") == record_id:
                    record["user_feedback"] = {
                        "research_valuable": feedback.get("research_valuable", True),
                        "direction_correct": feedback.get("direction_correct", ""),
                        "continue_research": feedback.get("continue_research", False),
                        "next_direction": feedback.get("next_direction", ""),
                        "decision": feedback.get("decision", "Text"),
                        "tracking_metrics": feedback.get("tracking_metrics", []),
                        "notes": feedback.get("notes", ""),
                        "follow_up_conversation": feedback.get("follow_up_conversation", []),
                        "feedback_date": datetime.now().isoformat()
                    }
                    self._atomic_write_json(history_path, history)
                    return True

        return False

    def get_latest_research_with_feedback(self, stock_id: str) -> Optional[Dict]:
        """TextResearchText"""
        history = self.get_research_history(stock_id)

        for record in history.get("records", []):
            if record.get("user_feedback"):
                return record

        return None

    def get_research_context(self, stock_id: str, limit: int = 3) -> List[Dict]:
        """TextResearchTextHistoryText(Text, HistoryEnvironmentText)"""
        history = self.get_research_history(stock_id)
        records = history.get("records", [])

        # Text
        milestones = []
        regular_with_context = []

        for record in records:
            is_milestone = record.get("is_milestone", False)
            has_feedback = record.get("user_feedback")
            has_uploaded = record.get("environment_input", {}).get("user_uploaded", [])

            record_context = {
                "date": record.get("date", ""),
                "research_result": record.get("research_result", {}),
                "user_feedback": record.get("user_feedback", {}),
                "environment_input": record.get("environment_input", {}),
                "is_milestone": is_milestone
            }

            if is_milestone:
                milestones.append(record_context)
            elif has_feedback or has_uploaded:
                if len(regular_with_context) < limit:
                    regular_with_context.append(record_context)

        # Text: Text + Text(Text)
        result = regular_with_context.copy()
        existing_dates = {r["date"] for r in result}

        for m in milestones:
            if m["date"] not in existing_dates:
                result.append(m)

        # TextDateText(Text)
        result.sort(key=lambda x: x.get("date", ""), reverse=True)

        return result

    def get_historical_uploads(self, stock_id: str, limit: int = 5) -> List[Dict]:
        """TextHistoryUploadText(TextResearchText)"""
        history = self.get_research_history(stock_id)
        all_uploads = []

        for record in history.get("records", []):
            env_input = record.get("environment_input", {})
            user_uploaded = env_input.get("user_uploaded", [])

            for upload in user_uploaded:
                all_uploads.append({
                    "date": record.get("date", "")[:10],
                    "filename": upload.get("filename", ""),
                    "summary": upload.get("summary", ""),
                    "analyzed_at": upload.get("analyzed_at", "")
                })

        return all_uploads[:limit]

    # ==================== TextUpload ====================

    def save_uploaded_file(self, stock_id: str, source_path: str) -> str:
        """SaveUploadText, Text"""
        source = Path(source_path).expanduser()
        if not source.exists():
            raise FileNotFoundError(f"Text: {source_path}")

        uploads_dir = self._get_stock_dir(stock_id) / "uploads"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = uploads_dir / f"{timestamp}_{source.name}"

        shutil.copy2(source, dest)
        return str(dest)

    # ==================== Text ====================

    def _get_preferences_path(self) -> Path:
        """Text"""
        return self.base_dir / "user_preferences.json"

    def save_user_preferences(self, prefs: Dict):
        """SaveText"""
        prefs["updated_at"] = datetime.now().isoformat()
        with self._prefs_lock:
            self._atomic_write_json(self._get_preferences_path(), prefs)

    def get_user_preferences(self) -> Dict:
        """Text"""
        with self._prefs_lock:
            path = self._get_preferences_path()
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        return {
            "preferences": [],
            "preference_summary": {
                "decision_style": "",
                "risk_tolerance": "",
                "research_focus": [],
                "disliked_patterns": [],
                "custom_rules": []
            },
            "interaction_log": []
        }

    def add_preference(self, preference: Dict) -> str:
        """Text"""
        prefs = self.get_user_preferences()

        # Generate ID
        pref_id = f"pref_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(prefs['preferences'])}"
        preference["id"] = pref_id
        preference["created_at"] = datetime.now().isoformat()
        preference["updated_at"] = preference["created_at"]
        preference["active"] = True  # Text

        prefs["preferences"].insert(0, preference)
        self.save_user_preferences(prefs)
        return pref_id

    def update_preference(self, pref_id: str, updates: Dict) -> bool:
        """Text"""
        prefs = self.get_user_preferences()

        for pref in prefs["preferences"]:
            if pref["id"] == pref_id:
                pref.update(updates)
                pref["updated_at"] = datetime.now().isoformat()
                self.save_user_preferences(prefs)
                return True
        return False

    def delete_preference(self, pref_id: str) -> bool:
        """Text"""
        prefs = self.get_user_preferences()
        original_len = len(prefs["preferences"])
        prefs["preferences"] = [p for p in prefs["preferences"] if p["id"] != pref_id]

        if len(prefs["preferences"]) < original_len:
            self.save_user_preferences(prefs)
            return True
        return False

    def toggle_preference(self, pref_id: str) -> bool:
        """TextStatus"""
        prefs = self.get_user_preferences()

        for pref in prefs["preferences"]:
            if pref["id"] == pref_id:
                pref["active"] = not pref.get("active", True)
                pref["updated_at"] = datetime.now().isoformat()
                self.save_user_preferences(prefs)
                return True
        return False

    def get_active_preferences(self) -> List[Dict]:
        """Text"""
        prefs = self.get_user_preferences()
        return [p for p in prefs["preferences"] if p.get("active", True)]

    def update_preference_summary(self, summary: Dict):
        """Text"""
        prefs = self.get_user_preferences()
        prefs["preference_summary"].update(summary)
        self.save_user_preferences(prefs)

    def log_interaction(self, interaction: Dict):
        """Text(Text)"""
        prefs = self.get_user_preferences()

        interaction["timestamp"] = datetime.now().isoformat()
        interaction["id"] = f"int_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        prefs["interaction_log"].insert(0, interaction)

        # Text100Text
        prefs["interaction_log"] = prefs["interaction_log"][:100]

        self.save_user_preferences(prefs)

    def get_recent_interactions(self, limit: int = 20) -> List[Dict]:
        """Text"""
        prefs = self.get_user_preferences()
        return prefs.get("interaction_log", [])[:limit]

    def get_preferences_for_prompt(self) -> str:
        """Text prompt Text"""
        prefs = self.get_user_preferences()
        active_prefs = self.get_active_preferences()
        summary = prefs.get("preference_summary", {})

        lines = ["## Text\n"]

        # Text
        if summary.get("decision_style"):
            lines.append(f"**DecisionText:** {summary['decision_style']}")
        if summary.get("risk_tolerance"):
            lines.append(f"**RiskText:** {summary['risk_tolerance']}")
        if summary.get("research_focus"):
            lines.append(f"**ResearchText:** {', '.join(summary['research_focus'])}")
        if summary.get("disliked_patterns"):
            lines.append(f"**Text:** {', '.join(summary['disliked_patterns'])}")
        if summary.get("custom_rules"):
            lines.append(f"**Text:** {', '.join(summary['custom_rules'])}")

        # Text
        if active_prefs:
            lines.append("\n**HistoryText:**")
            for pref in active_prefs[:10]:  # Text10Text
                trigger = pref.get("trigger", "")
                response = pref.get("my_response", "")
                if trigger and response:
                    lines.append(f"- Text{trigger}Text, Text{response}")

        return "\n".join(lines) if len(lines) > 1 else "(No dataText)"

    # ==================== Weekly Review ====================

    def _get_weekly_reviews_path(self) -> Path:
        """Weekly ReviewText"""
        return self.base_dir / "weekly_reviews.json"

    def _load_weekly_reviews_file(self) -> Optional[Dict]:
        """Text weekly_reviews.json(Text, Text)"""
        with self._weekly_lock:
            return self._load_weekly_reviews_file_locked()

    def _load_weekly_reviews_file_locked(self) -> Optional[Dict]:
        """Text, Text _weekly_lock"""
        path = self._get_weekly_reviews_path()
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            # Text: Text NaN/Infinity, Text null Text
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
                text = re.sub(r"\bNaN\b", "null", text)
                text = re.sub(r"\bInfinity\b", "null", text)
                text = re.sub(r"\b-Infinity\b", "null", text)
                data = json.loads(text)
                # TextSuccessText, Text
                self._atomic_write_json(path, data)
                return data
            except Exception:
                pass
            # TextFailedText None
            backup = path.parent / f"weekly_reviews.json.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            try:
                shutil.copy2(path, backup)
            except Exception:
                pass
            return None

    def _save_weekly_reviews_file(self, data: Dict) -> None:
        """Text weekly_reviews.json(Text, Text JSON Text, Text nan Text)"""
        path = self._get_weekly_reviews_path()
        with self._weekly_lock:
            self._atomic_write_json(path, data)

    def get_weekly_review(self, week_id: str) -> Optional[Dict]:
        """TextReviewText"""
        data = self._load_weekly_reviews_file()
        if data is None:
            return None
        review = data.get("weeks", {}).get(week_id)
        if not isinstance(review, dict):
            return review
        payload = dict(review)
        if "rebalancing_ops" in payload:
            payload["rebalancing_ops"] = self._normalize_rebalancing_ops(payload.get("rebalancing_ops"))
        return payload

    def save_weekly_review(self, week_id: str, review_data: Dict) -> None:
        """SaveWeekly ReviewText"""
        with self._weekly_lock:
            data = self._load_weekly_reviews_file_locked()
            if data is None:
                data = {"weeks": {}}

            payload = dict(review_data or {})
            if "rebalancing_ops" in payload:
                payload["rebalancing_ops"] = self._normalize_rebalancing_ops(payload.get("rebalancing_ops"))
            data["weeks"][week_id] = payload
            data["weeks"][week_id]["updated_at"] = datetime.now().isoformat()
            if "created_at" not in data["weeks"][week_id]:
                data["weeks"][week_id]["created_at"] = data["weeks"][week_id]["updated_at"]

            path = self._get_weekly_reviews_path()
            self._atomic_write_json(path, data)

    def update_weekly_portfolio_decision_memo(
        self,
        week_id: str,
        *,
        ai_summary: Optional[str] = None,
        user_judgment: Optional[str] = None,
        final_decision: Optional[str] = None,
        user_feedback: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update the portfolio-level weekly decision memo."""
        with self._weekly_lock:
            data = self._load_weekly_reviews_file_locked()
            if data is None:
                data = {"weeks": {}}

            review = dict((data.get("weeks") or {}).get(week_id) or {})
            memo = dict(review.get("portfolio_decision_memo") or {})
            if ai_summary is not None:
                memo["ai_summary"] = str(ai_summary or "").strip()
                memo["generated_at"] = datetime.now().isoformat()
            if user_judgment is not None:
                memo["user_judgment"] = str(user_judgment or "").strip()
            if final_decision is not None:
                memo["final_decision"] = str(final_decision or "").strip()
            if user_feedback is not None:
                memo["user_feedback"] = str(user_feedback or "").strip()
            memo["updated_at"] = datetime.now().isoformat()

            review["portfolio_decision_memo"] = memo
            data["weeks"][week_id] = review
            path = self._get_weekly_reviews_path()
            self._atomic_write_json(path, data)
            return memo

    def _prev_week_id(self, week_id: str) -> Optional[str]:
        """Text week_id, Text YYYY-Www"""
        try:
            parts = week_id.split("-W")
            if len(parts) != 2:
                return None
            year, week = int(parts[0]), int(parts[1])
            if week <= 1:
                prev_year, prev_week = year - 1, 52
            else:
                prev_year, prev_week = year, week - 1
            return f"{prev_year}-W{prev_week:02d}"
        except (ValueError, IndexError):
            return None

    def _main_display_codes(self, stock_list: List[Dict]) -> set:
        """TextStockTextTickerText(ticker Text, Text)"""
        codes = set()
        for s in stock_list:
            tid = s.get("stock_id", "")
            tick = (s.get("ticker") or "").strip()
            if tid:
                codes.add(tid)
            if tick:
                codes.add(tick)
            if tick and tid:
                codes.add(self._canonical_code(tick))
                codes.add(self._canonical_code(tid))
        return codes

    def _safe_float(self, value: Any) -> Optional[float]:
        try:
            if value in (None, ""):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _detect_currency_from_code(self, value: Any) -> Optional[str]:
        code = str(value or "").strip().upper()
        if not code:
            return None
        if code.endswith(".HK"):
            return "HKD"
        if code.endswith(".SH") or code.endswith(".SZ") or code.endswith(".SS"):
            return "CNY"
        if code.endswith(".AS") or code.endswith(".DE") or code.endswith(".VI"):
            return "EUR"
        if code.endswith(".T"):
            return "JPY"
        if code.endswith(".KS") or code.endswith(".KQ"):
            return "KRW"
        return None

    def _stock_currency(
        self,
        ticker: str,
        review: Optional[Dict[str, Any]] = None,
        stock_id: Optional[str] = None,
    ) -> str:
        for candidate in (ticker, stock_id):
            detected = self._detect_currency_from_code(candidate)
            if detected:
                return detected

        review_stocks = ((review or {}).get("stocks") or {}) if isinstance(review, dict) else {}
        targets = {
            str(value or "").strip().upper()
            for value in (ticker, stock_id)
            if str(value or "").strip()
        }
        for key, payload in review_stocks.items():
            stock_key = str(key or "").strip()
            stock_ticker = str((payload or {}).get("ticker") or "").strip() if isinstance(payload, dict) else ""
            aliases = {stock_key.upper(), stock_ticker.upper()}
            if targets and not aliases.intersection(targets):
                continue
            for candidate in (stock_ticker, stock_key):
                detected = self._detect_currency_from_code(candidate)
                if detected:
                    return detected
        return "USD"

    def _to_hkd(self, amount: Optional[float], currency: str, review: Dict[str, Any]) -> Optional[float]:
        if amount is None:
            return None
        if currency == "HKD":
            rate = 1.0
        elif currency == "CNY":
            rate = self._safe_float((review or {}).get("cny_to_hkd")) or 1.07
        elif currency == "EUR":
            rate = self._safe_float((review or {}).get("eur_to_hkd")) or 8.4
        elif currency == "JPY":
            rate = self._safe_float((review or {}).get("jpy_to_hkd")) or 0.052
        elif currency == "KRW":
            rate = self._safe_float((review or {}).get("krw_to_hkd")) or 0.0056
        else:
            rate = self._safe_float((review or {}).get("usd_to_hkd")) or 7.8
        return round(amount * rate, 2)

    def _rebalancing_amount_to_hkd(self, stock_id: str, amount: Optional[float], review: Dict[str, Any]) -> Optional[float]:
        ticker = str(stock_id or "").strip()
        stock_payload = (((review or {}).get("stocks") or {}).get(ticker) or {}) if isinstance(review, dict) else {}
        if isinstance(stock_payload, dict) and stock_payload.get("ticker"):
            ticker = str(stock_payload.get("ticker") or ticker).strip()
        currency = self._stock_currency(ticker or stock_id, review=review, stock_id=stock_id)
        return self._to_hkd(amount, currency, review)

    def _gross_rebalancing_amounts_hkd(self, ops: List[Dict], review: Dict[str, Any]) -> tuple[float, float]:
        gross_buy_amount = 0.0
        gross_sell_amount = 0.0
        for op in ops:
            try:
                qty = float(op.get("quantity") or 0)
                price = float(op.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if qty <= 0 or price <= 0:
                continue
            amount_hkd = self._rebalancing_amount_to_hkd(str(op.get("stock_id") or ""), qty * price, review)
            if amount_hkd is None:
                continue
            op_type = str(op.get("op_type") or "").strip().lower()
            if op_type in REBALANCING_BUY_TYPES:
                gross_buy_amount += amount_hkd
            elif op_type in REBALANCING_SELL_TYPES:
                gross_sell_amount += amount_hkd
        return gross_buy_amount, gross_sell_amount

    def _resolve_review_ticker(
        self,
        stock_id: str,
        stock_data: Dict[str, Any],
        stock_list: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        playbook = self.get_stock_playbook(stock_id) or {}
        if playbook.get("ticker"):
            return str(playbook.get("ticker") or "").strip()
        for item in stock_list or []:
            if str(item.get("stock_id") or "").strip() == stock_id and item.get("ticker"):
                return str(item.get("ticker") or "").strip()
        return str((stock_data or {}).get("ticker") or stock_id).strip()

    def _weekly_stock_aliases(
        self,
        stock_id: str,
        stock_data: Optional[Dict[str, Any]],
        ticker: Optional[str] = None,
        stock_list: Optional[List[Dict[str, Any]]] = None,
    ) -> set[str]:
        aliases: set[str] = set()

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if not text:
                return
            aliases.add(text.upper())
            aliases.add(self._canonical_code(text))
            aliases.add(self._normalize_market_ticker_key(text).upper())
            for suffix in PRIMARY_CODE_SUFFIXES:
                upper = text.upper()
                if upper.endswith(suffix):
                    aliases.add(upper[: -len(suffix)])
            upper = text.upper()
            hk_match = re.fullmatch(r"0*(\d{1,5})(?:\.HK)?", upper)
            if hk_match and (upper.endswith(".HK") or upper.isdigit()):
                numeric = str(int(hk_match.group(1)))
                aliases.add(numeric)
                aliases.add(f"{numeric}.HK")

        stock_payload = stock_data if isinstance(stock_data, dict) else {}
        add(stock_id)
        add(ticker)
        add(stock_payload.get("ticker"))
        add(stock_payload.get("stock_name"))
        for item in stock_list or []:
            if not isinstance(item, dict):
                continue
            item_values = [item.get("stock_id"), item.get("ticker"), item.get("stock_name")]
            item_aliases = {str(v or "").strip().upper() for v in item_values if str(v or "").strip()}
            if aliases.intersection(item_aliases):
                for value in item_values:
                    add(value)
        return aliases

    def _inherit_missing_weekly_cost_basis(
        self,
        stock_id: str,
        stock_payload: Dict[str, Any],
        prev_review: Dict[str, Any],
        ticker: str,
        stock_list: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        shares = self._safe_float(stock_payload.get("shares_held")) or 0.0
        if shares <= 0:
            return
        if self._safe_float(stock_payload.get("avg_cost")) not in (None, 0.0):
            return

        aliases = self._weekly_stock_aliases(stock_id, stock_payload, ticker, stock_list)
        for prev_id, prev_data in (prev_review.get("stocks") or {}).items():
            if not isinstance(prev_data, dict):
                continue
            prev_ticker = self._resolve_review_ticker(str(prev_id), prev_data, stock_list)
            prev_aliases = self._weekly_stock_aliases(str(prev_id), prev_data, prev_ticker, stock_list)
            if not aliases.intersection(prev_aliases):
                continue
            prev_cost = self._safe_float(prev_data.get("avg_cost"))
            if prev_cost not in (None, 0.0):
                stock_payload["avg_cost"] = prev_cost
                return

    def _previous_weekly_stock_by_alias(
        self,
        stock_id: str,
        stock_payload: Dict[str, Any],
        prev_review: Dict[str, Any],
        ticker: str,
        stock_list: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        prev_stocks = (prev_review or {}).get("stocks") or {}
        exact = prev_stocks.get(stock_id)
        if isinstance(exact, dict):
            return exact

        aliases = self._weekly_stock_aliases(stock_id, stock_payload, ticker, stock_list)
        for prev_id, prev_data in prev_stocks.items():
            if not isinstance(prev_data, dict):
                continue
            prev_ticker = self._resolve_review_ticker(str(prev_id), prev_data, stock_list)
            prev_aliases = self._weekly_stock_aliases(str(prev_id), prev_data, prev_ticker, stock_list)
            if aliases.intersection(prev_aliases):
                return prev_data
        return {}

    def _weekly_position_key(
        self,
        stock_id: str,
        stock_data: Optional[Dict[str, Any]],
        stock_list: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        payload = stock_data if isinstance(stock_data, dict) else {}
        ticker = self._resolve_review_ticker(stock_id, payload, stock_list)
        for candidate in (ticker, stock_id):
            text = str(candidate or "").strip()
            if text:
                return _primary_code_without_exchange_suffix(text)
        return ""

    def _build_weekly_position_alias_map(
        self,
        rows: Dict[str, Dict[str, Any]],
        stock_list: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, str]:
        alias_map: Dict[str, str] = {}
        for key, row in rows.items():
            values = {key, row.get("stock_id"), row.get("storage_key")}
            values.update(row.get("aliases") or set())
            for value in values:
                text = str(value or "").strip()
                if not text:
                    continue
                alias_map[text.upper()] = key
                alias_map[_primary_code_without_exchange_suffix(text)] = key
                alias_map[self._normalize_market_ticker_key(text).upper()] = key
        for item in stock_list or []:
            if not isinstance(item, dict):
                continue
            values = [item.get("stock_id"), item.get("ticker"), item.get("stock_name")]
            item_key = ""
            for value in values:
                text = str(value or "").strip()
                if not text:
                    continue
                for alias in (text.upper(), _primary_code_without_exchange_suffix(text), self._normalize_market_ticker_key(text).upper()):
                    if alias in alias_map:
                        item_key = alias_map[alias]
                        break
                if item_key:
                    break
            if not item_key:
                continue
            for value in values:
                text = str(value or "").strip()
                if not text:
                    continue
                alias_map[text.upper()] = item_key
                alias_map[_primary_code_without_exchange_suffix(text)] = item_key
                alias_map[self._normalize_market_ticker_key(text).upper()] = item_key
        return alias_map

    def _resolve_weekly_position_key_from_alias(
        self,
        raw_id: str,
        alias_map: Dict[str, str],
    ) -> str:
        text = str(raw_id or "").strip()
        if not text:
            return ""
        candidates = [
            text.upper(),
            _primary_code_without_exchange_suffix(text),
            self._normalize_market_ticker_key(text).upper(),
        ]
        for candidate in candidates:
            if candidate in alias_map:
                return alias_map[candidate]
        return _primary_code_without_exchange_suffix(text)

    def _weekly_position_rows(
        self,
        review: Optional[Dict[str, Any]],
        stock_list: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        rows: Dict[str, Dict[str, Any]] = {}
        stocks = ((review or {}).get("stocks") or {}) if isinstance(review, dict) else {}
        if not isinstance(stocks, dict):
            return rows
        for stock_id, stock_data in stocks.items():
            if not isinstance(stock_data, dict):
                continue
            raw_id = str(stock_id or "").strip()
            key = self._weekly_position_key(raw_id, stock_data, stock_list)
            if not key:
                continue
            shares = max(self._safe_float(stock_data.get("shares_held")) or 0.0, 0.0)
            aliases = self._weekly_stock_aliases(raw_id, stock_data, stock_data.get("ticker"), stock_list)
            existing_key = ""
            for row_key, row in rows.items():
                row_aliases = set(row.get("aliases") or set())
                if key == row_key or aliases.intersection(row_aliases):
                    existing_key = row_key
                    break
            target_key = existing_key or key
            if target_key in rows:
                rows[target_key]["shares"] = max(float(rows[target_key].get("shares") or 0.0), shares)
                rows[target_key].setdefault("aliases", set()).update(aliases)
                if raw_id:
                    rows[target_key]["aliases"].add(raw_id.upper())
                if not rows[target_key].get("stock_id") and (stock_data.get("ticker") or raw_id):
                    rows[target_key]["stock_id"] = str(stock_data.get("ticker") or raw_id).strip()
                continue
            rows[target_key] = {
                "stock_id": str(stock_data.get("ticker") or raw_id or target_key).strip() or target_key,
                "storage_key": raw_id,
                "shares": shares,
                "aliases": aliases,
            }
        return rows

    def _build_weekly_position_reconciliation(
        self,
        review: Optional[Dict[str, Any]],
        prev_review: Optional[Dict[str, Any]] = None,
        stock_list: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        payload = review if isinstance(review, dict) else {}
        previous = self._weekly_position_rows(prev_review, stock_list)
        current = self._weekly_position_rows(payload, stock_list)
        combined_rows: Dict[str, Dict[str, Any]] = {}
        key_redirects: Dict[str, str] = {}
        for source in (previous, current):
            for key, row in source.items():
                aliases = set(row.get("aliases") or set())
                target_key = ""
                for existing_key, existing_row in combined_rows.items():
                    if key == existing_key or aliases.intersection(set(existing_row.get("aliases") or set())):
                        target_key = existing_key
                        break
                if not target_key:
                    target_key = key
                    combined_rows[target_key] = dict(row)
                    combined_rows[target_key]["aliases"] = set(aliases)
                else:
                    combined_rows[target_key].setdefault("aliases", set()).update(aliases)
                    if not combined_rows[target_key].get("stock_id") and row.get("stock_id"):
                        combined_rows[target_key]["stock_id"] = row.get("stock_id")
                key_redirects[key] = target_key

        previous = {
            key_redirects.get(key, key): {
                **row,
                "aliases": set(row.get("aliases") or set()).union(
                    set(combined_rows.get(key_redirects.get(key, key), {}).get("aliases") or set())
                ),
            }
            for key, row in previous.items()
        }
        current = {
            key_redirects.get(key, key): {
                **row,
                "aliases": set(row.get("aliases") or set()).union(
                    set(combined_rows.get(key_redirects.get(key, key), {}).get("aliases") or set())
                ),
            }
            for key, row in current.items()
        }
        position_alias_map = self._build_weekly_position_alias_map(combined_rows, stock_list)
        op_delta_by_key: Dict[str, float] = {}
        op_id_by_key: Dict[str, str] = {}

        for op in payload.get("rebalancing_ops") or []:
            if not isinstance(op, dict):
                continue
            raw_sid = str(op.get("stock_id") or "").strip()
            if not raw_sid:
                continue
            key = self._resolve_weekly_position_key_from_alias(raw_sid, position_alias_map)
            if not key:
                continue
            op_delta_by_key[key] = op_delta_by_key.get(key, 0.0) + _rebalancing_op_delta(op.get("op_type"), op.get("quantity"))
            op_id_by_key.setdefault(key, raw_sid)
        baseline_available = bool(previous)

        closed_aliases: set[str] = set()
        for item in payload.get("closed_positions") or []:
            if not isinstance(item, dict):
                continue
            closed_aliases.update(
                self._weekly_stock_aliases(str(item.get("stock_id") or ""), item, item.get("ticker"), stock_list)
            )

        checks: List[Dict[str, Any]] = []
        for key in sorted(set(previous) | set(current) | set(op_delta_by_key)):
            before = previous.get(key, {}).get("shares", 0.0)
            after = current.get(key, {}).get("shares", 0.0)
            op_delta = op_delta_by_key.get(key, 0.0)
            expected = before + op_delta
            stock_id = (
                current.get(key, {}).get("stock_id")
                or previous.get(key, {}).get("stock_id")
                or op_id_by_key.get(key)
                or key
            )
            aliases = set()
            aliases.update(previous.get(key, {}).get("aliases") or set())
            aliases.update(current.get(key, {}).get("aliases") or set())
            aliases.update(self._weekly_stock_aliases(str(stock_id or ""), {"ticker": stock_id}, stock_id, stock_list))
            sold_out = before > 0 and after <= 0 and op_delta < 0
            has_closed_position = (not sold_out) or bool(aliases.intersection(closed_aliases))
            mismatch = baseline_available and abs(after - expected) > 1e-8
            checks.append(
                {
                    "stock_id": stock_id,
                    "before": round(before, 8),
                    "op_delta": round(op_delta, 8),
                    "expected": round(expected, 8),
                    "after": round(after, 8),
                    "delta": round(after - before, 8),
                    "mismatch": mismatch,
                    "sold_out": sold_out,
                    "has_closed_position": has_closed_position,
                }
            )

        mismatches = [row for row in checks if row.get("mismatch")]
        missing_closed = [row for row in checks if row.get("sold_out") and not row.get("has_closed_position")]
        status = "error" if mismatches or missing_closed else "healthy"
        return {
            "status": status,
            "baseline_available": baseline_available,
            "checked_position_count": len(checks),
            "position_mismatch_count": len(mismatches),
            "missing_closed_position_count": len(missing_closed),
            "mismatched_stock_ids": [str(row.get("stock_id") or "") for row in mismatches[:12]],
            "missing_closed_position_stock_ids": [str(row.get("stock_id") or "") for row in missing_closed[:12]],
            "checks": checks,
        }

    def _match_stock_op(self, raw_stock_id: str, stock_id: str, ticker: str) -> bool:
        canon_to_key = {self._canonical_code(stock_id): stock_id}
        if ticker:
            canon_to_key[self._canonical_code(ticker)] = stock_id
        resolved = self._resolve_op_to_key(raw_stock_id, canon_to_key)
        return resolved == stock_id

    def _build_position_metrics(
        self,
        stock_id: str,
        stock_data: Dict[str, Any],
        review: Dict[str, Any],
        prev_review: Dict[str, Any],
        ticker: str,
        stock_list: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        perf = (stock_data or {}).get("performance_data") or {}
        start_price = self._safe_float(perf.get("start_price"))
        end_price = self._safe_float(perf.get("end_price"))
        shares = max(self._safe_float((stock_data or {}).get("shares_held")) or 0.0, 0.0)
        avg_cost = self._safe_float((stock_data or {}).get("avg_cost"))
        currency = self._stock_currency(ticker or stock_id, review=review, stock_id=stock_id)
        prev_stock = self._previous_weekly_stock_by_alias(stock_id, stock_data or {}, prev_review, ticker, stock_list)
        prev_shares = max(self._safe_float(prev_stock.get("shares_held")) or 0.0, 0.0)
        prev_avg_cost = self._safe_float(prev_stock.get("avg_cost"))

        lots: List[Dict[str, Any]] = []
        if prev_shares > 0:
            lots.append({"source": "base", "qty": prev_shares, "cost": prev_avg_cost})

        weekly_base = 0.0
        weekly_new = 0.0
        weekly_capital = 0.0
        weekly_realized = 0.0
        realized_pnl = 0.0
        if prev_shares > 0 and start_price is not None:
            weekly_capital += prev_shares * start_price
        ops = list((review or {}).get("rebalancing_ops") or [])
        ops = [op for op in ops if self._match_stock_op(str(op.get("stock_id") or "").strip(), stock_id, ticker)]
        ops.sort(key=lambda op: str(op.get("date") or ""))

        for op in ops:
            qty = self._safe_float(op.get("quantity")) or 0.0
            if qty <= 0:
                continue
            op_type = str(op.get("op_type") or "").strip().lower()
            price = self._safe_float(op.get("price"))
            if op_type in REBALANCING_BUY_TYPES:
                lots.append({"source": "new", "qty": qty, "cost": price})
                if price is not None:
                    weekly_capital += qty * price
                continue
            if op_type not in REBALANCING_SELL_TYPES:
                continue

            remaining = qty
            while remaining > 1e-9 and lots:
                lot = lots[0]
                used = min(float(lot.get("qty") or 0.0), remaining)
                if used <= 0:
                    lots.pop(0)
                    continue

                if price is not None:
                    if lot.get("source") == "base":
                        if start_price is not None:
                            weekly_piece = used * (price - start_price)
                            weekly_base += weekly_piece
                            weekly_realized += weekly_piece
                    else:
                        lot_cost = self._safe_float(lot.get("cost"))
                        if lot_cost is not None:
                            weekly_piece = used * (price - lot_cost)
                            weekly_new += weekly_piece
                            weekly_realized += weekly_piece

                    lot_cost = self._safe_float(lot.get("cost"))
                    if lot_cost is not None:
                        realized_pnl += used * (price - lot_cost)

                lot["qty"] = float(lot.get("qty") or 0.0) - used
                remaining -= used
                if float(lot.get("qty") or 0.0) <= 1e-9:
                    lots.pop(0)

        remaining_qty = sum(float(lot.get("qty") or 0.0) for lot in lots)
        qty_gap = round(shares - remaining_qty, 8)
        if qty_gap > 0:
            inferred_source = "new" if shares > prev_shares else "base"
            inferred_cost = avg_cost if inferred_source == "new" else prev_avg_cost
            lots.append({"source": inferred_source, "qty": qty_gap, "cost": inferred_cost})
            if inferred_source == "new" and inferred_cost is not None:
                weekly_capital += qty_gap * inferred_cost
        elif qty_gap < 0:
            excess = abs(qty_gap)
            while excess > 1e-9 and lots:
                lot = lots[-1]
                lot_qty = float(lot.get("qty") or 0.0)
                used = min(lot_qty, excess)
                lot["qty"] = lot_qty - used
                excess -= used
                if float(lot.get("qty") or 0.0) <= 1e-9:
                    lots.pop()

        unrealized_pnl = None
        if end_price is not None:
            for lot in lots:
                qty = float(lot.get("qty") or 0.0)
                if qty <= 0:
                    continue
                lot_cost = self._safe_float(lot.get("cost"))
                if lot.get("source") == "base":
                    if start_price is not None:
                        weekly_base += qty * (end_price - start_price)
                elif lot_cost is not None:
                    weekly_new += qty * (end_price - lot_cost)
        if end_price is not None and avg_cost is not None and shares > 0:
            unrealized_pnl = shares * (end_price - avg_cost)

        holding_value = shares * end_price if end_price is not None and shares > 0 else None
        weekly_total = weekly_base + weekly_new if (start_price is not None or weekly_new or weekly_base) else None
        weekly_return_pct = None
        if weekly_total is not None and weekly_capital > 0:
            weekly_return_pct = (weekly_total / weekly_capital) * 100
        return_since_buy = None
        if avg_cost and avg_cost > 0 and end_price is not None:
            return_since_buy = (end_price / avg_cost - 1) * 100

        return {
            "ticker": ticker or stock_id,
            "currency": currency,
            "holding_value_hkd": self._to_hkd(holding_value, currency, review),
            "weekly_pnl_hkd": self._to_hkd(weekly_total, currency, review) if weekly_total is not None else None,
            "weekly_capital_hkd": self._to_hkd(weekly_capital, currency, review) if weekly_capital > 0 else None,
            "weekly_return_pct": weekly_return_pct,
            "weekly_pnl_base_hkd": self._to_hkd(weekly_base, currency, review) if weekly_total is not None else None,
            "weekly_pnl_new_hkd": self._to_hkd(weekly_new, currency, review) if weekly_total is not None else None,
            "weekly_realized_pnl_hkd": self._to_hkd(weekly_realized, currency, review),
            "realized_pnl_hkd": self._to_hkd(realized_pnl, currency, review),
            "unrealized_pnl_hkd": self._to_hkd(unrealized_pnl, currency, review) if unrealized_pnl is not None else None,
            "return_since_buy": return_since_buy,
        }

    def _augment_closed_position(
        self,
        position: Dict[str, Any],
        review: Dict[str, Any],
        stock_list: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        item = dict(position or {})
        stock_id = str(item.get("stock_id") or "").strip()
        ticker = self._resolve_review_ticker(stock_id, item, stock_list) if stock_id else ""
        currency = self._stock_currency(ticker or stock_id, review=review, stock_id=stock_id)
        shares_sold = self._safe_float(item.get("shares_sold")) or 0.0
        sell_price = self._safe_float(item.get("sell_price"))
        week_start_price = self._safe_float(item.get("week_start_price"))
        weekly_pnl = None
        if shares_sold > 0 and sell_price is not None and week_start_price is not None:
            weekly_pnl = shares_sold * (sell_price - week_start_price)
        weekly_capital = shares_sold * week_start_price if shares_sold > 0 and week_start_price is not None else None
        weekly_return_pct = (weekly_pnl / weekly_capital) * 100 if weekly_pnl is not None and weekly_capital and weekly_capital > 0 else None
        realized_pnl = self._safe_float(item.get("realized_pnl"))
        item["ticker"] = ticker or stock_id
        item["weekly_pnl_hkd"] = self._to_hkd(weekly_pnl, currency, review)
        item["weekly_capital_hkd"] = self._to_hkd(weekly_capital, currency, review)
        item["weekly_return_pct"] = weekly_return_pct
        item["weekly_pnl_base_hkd"] = self._to_hkd(weekly_pnl, currency, review)
        item["weekly_pnl_new_hkd"] = self._to_hkd(0.0, currency, review)
        item["weekly_realized_pnl_hkd"] = self._to_hkd(weekly_pnl, currency, review)
        if item.get("realized_pnl_hkd") in (None, "") and realized_pnl is not None:
            item["realized_pnl_hkd"] = self._to_hkd(realized_pnl, currency, review)
        return item

    def get_or_create_weekly_review(self, week_id: str, stock_list: List[Dict]) -> Dict:
        """TextWeekly ReviewText. Text stock_id Text key; TextLast WeekTextHoldingsTextTickerText. """
        main_ids = {s.get("stock_id", "") for s in stock_list if s.get("stock_id")}
        main_codes = self._main_display_codes(stock_list)

        def _is_main_or_duplicate(sid: str) -> bool:
            if sid in main_ids:
                return True
            sid_upper = self._canonical_code(sid)
            return sid_upper in main_codes or any(sid_upper == self._canonical_code(c) for c in main_codes)

        review = self.get_weekly_review(week_id)
        week_existed = review is not None  # TextSaveTextHoldingsText, TextAutoText
        if review is None:
            review = {
                "week_id": week_id,
                "stocks": {},
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }
            prev = self.get_weekly_review(self._prev_week_id(week_id))
            if prev and prev.get("stocks"):
                for sid, d in prev["stocks"].items():
                    if not _is_main_or_duplicate(sid):
                        review["stocks"][sid] = {
                            "stock_name": d.get("stock_name", sid),
                            "ticker": d.get("ticker") or sid,
                            "news": [],
                            "news_updated_at": None,
                            "performance_summary": "",
                            "performance_updated_at": None,
                            "user_view": "",
                            "user_view_updated_at": None,
                            "shares_held": d.get("shares_held"),
                            "buy_date": d.get("buy_date"),
                            "avg_cost": d.get("avg_cost"),
                        }

        modified = False
        prev = self.get_weekly_review(self._prev_week_id(week_id))

        # Text, TextLast WeekTextHoldings; TextSaveTextHoldingsText, TextLast WeekText(Text x Text loadWeekData TextStockText prev Text)
        if not week_existed and prev and prev.get("stocks"):
            for sid, d in prev["stocks"].items():
                if not _is_main_or_duplicate(sid) and sid not in review.get("stocks", {}):
                    review.setdefault("stocks", {})[sid] = {
                        "stock_name": d.get("stock_name", sid),
                        "ticker": d.get("ticker") or sid,
                        "news": [],
                        "news_updated_at": None,
                        "performance_summary": "",
                        "performance_updated_at": None,
                        "user_view": "",
                        "user_view_updated_at": None,
                        "shares_held": d.get("shares_held"),
                        "buy_date": d.get("buy_date"),
                        "avg_cost": d.get("avg_cost"),
                    }
                    modified = True

        # Text, Text stock_list TextHoldings; TextSaveTextHoldingsText, TextAutoText(Text x TextRefreshText)
        if not week_existed:
            for stock in stock_list:
                stock_id = stock.get("stock_id", "")
                stock_name = stock.get("stock_name", stock_id)
                if not stock_id:
                    continue
                if stock_id not in review["stocks"]:
                    entry = {
                        "stock_name": stock_name,
                        "ticker": stock.get("ticker") or stock_id,
                        "news": [],
                        "news_updated_at": None,
                        "performance_summary": "",
                        "performance_updated_at": None,
                        "user_view": "",
                        "user_view_updated_at": None
                    }
                    if prev and stock_id in prev.get("stocks", {}):
                        p = prev["stocks"][stock_id]
                        entry["shares_held"] = p.get("shares_held")
                        entry["buy_date"] = p.get("buy_date")
                        entry["avg_cost"] = p.get("avg_cost")
                    review["stocks"][stock_id] = entry
                    modified = True
                else:
                    cur = review["stocks"][stock_id]
                    if prev and stock_id in prev.get("stocks", {}):
                        p = prev["stocks"][stock_id]
                        if cur.get("shares_held") is None:
                            cur["shares_held"] = p.get("shares_held")
                            modified = True
                        if cur.get("buy_date") is None and p.get("buy_date"):
                            cur["buy_date"] = p.get("buy_date")
                            modified = True
                        if self._safe_float(cur.get("avg_cost")) in (None, 0.0) and self._safe_float(p.get("avg_cost")) not in (None, 0.0):
                            cur["avg_cost"] = p.get("avg_cost")
                            modified = True
        else:
            # Text: Text review TextStockText shares_held/buy_date(TextLast Week), Text stock_list Text
            for stock in stock_list:
                stock_id = stock.get("stock_id", "")
                if not stock_id or stock_id not in review["stocks"]:
                    continue
                cur = review["stocks"][stock_id]
                if prev:
                    ticker = self._resolve_review_ticker(stock_id, cur, stock_list)
                    p = self._previous_weekly_stock_by_alias(stock_id, cur, prev, ticker, stock_list)
                    if p:
                        if cur.get("shares_held") is None:
                            cur["shares_held"] = p.get("shares_held")
                            modified = True
                        if cur.get("buy_date") is None and p.get("buy_date"):
                            cur["buy_date"] = p.get("buy_date")
                            modified = True
                        if self._safe_float(cur.get("avg_cost")) in (None, 0.0) and self._safe_float(p.get("avg_cost")) not in (None, 0.0):
                            cur["avg_cost"] = p.get("avg_cost")
                            modified = True

        for sid in list(review["stocks"]):
            if sid not in main_ids:
                cur = review["stocks"][sid]
                if prev:
                    ticker = self._resolve_review_ticker(sid, cur, stock_list)
                    p = self._previous_weekly_stock_by_alias(sid, cur, prev, ticker, stock_list)
                    if not p:
                        continue
                    if cur.get("shares_held") is None:
                        cur["shares_held"] = p.get("shares_held")
                        modified = True
                    if cur.get("buy_date") is None and p.get("buy_date"):
                        cur["buy_date"] = p.get("buy_date")
                        modified = True
                    if self._safe_float(cur.get("avg_cost")) in (None, 0.0) and self._safe_float(p.get("avg_cost")) not in (None, 0.0):
                        cur["avg_cost"] = p.get("avg_cost")
                        modified = True

        if modified:
            self.save_weekly_review(week_id, review)

        return review

    def get_weekly_review_with_portfolio_state(
        self,
        week_id: str,
        stock_list: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """TextHoldingsStatusText, TextHistoryText. """
        stock_list = stock_list if stock_list is not None else self.list_stocks()
        review = self.get_or_create_weekly_review(week_id, stock_list)
        payload = json.loads(json.dumps(review or {"week_id": week_id, "stocks": {}}))
        prev_review = self.get_weekly_review(self._prev_week_id(week_id) or "") or {}

        for stock_id, stock_data in (payload.get("stocks") or {}).items():
            stock_payload = stock_data if isinstance(stock_data, dict) else {}
            ticker = self._resolve_review_ticker(stock_id, stock_payload, stock_list)
            stock_payload["ticker"] = ticker or stock_id
            self._inherit_missing_weekly_cost_basis(
                stock_id=stock_id,
                stock_payload=stock_payload,
                prev_review=prev_review,
                ticker=ticker,
                stock_list=stock_list,
            )
            stock_payload["position_metrics"] = self._build_position_metrics(
                stock_id=stock_id,
                stock_data=stock_payload,
                review=payload,
                prev_review=prev_review,
                ticker=ticker,
                stock_list=stock_list,
            )

        payload["closed_positions"] = [
            self._augment_closed_position(item, payload, stock_list)
            for item in (payload.get("closed_positions") or [])
            if isinstance(item, dict)
        ]
        return payload

    def build_weekly_review_data_health(
        self,
        review: Optional[Dict[str, Any]],
        prev_review: Optional[Dict[str, Any]] = None,
        stock_list: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        payload = review if isinstance(review, dict) else {}
        if prev_review is None:
            week_id = str(payload.get("week_id") or "").strip()
            prev_id = self._prev_week_id(week_id) if week_id else None
            prev_review = self.get_weekly_review(prev_id) if prev_id else None
        stocks = payload.get("stocks") or {}
        if not isinstance(stocks, dict):
            stocks = {}
        position_reconciliation = self._build_weekly_position_reconciliation(
            payload,
            prev_review=prev_review,
            stock_list=stock_list,
        )
        active_rows = []
        missing_buy_date = []
        missing_avg_cost = []
        missing_performance = []
        stale_performance = []
        now = datetime.now()
        for stock_id, stock_data in stocks.items():
            if not isinstance(stock_data, dict):
                continue
            shares = self._safe_float(stock_data.get("shares_held")) or 0.0
            if shares <= 0:
                continue
            active_rows.append((stock_id, stock_data))
            if not str(stock_data.get("buy_date") or "").strip():
                missing_buy_date.append(stock_id)
            if self._safe_float(stock_data.get("avg_cost")) in (None, 0.0):
                missing_avg_cost.append(stock_id)
            perf = stock_data.get("performance_data") or {}
            if not isinstance(perf, dict) or self._safe_float(perf.get("end_price")) is None:
                missing_performance.append(stock_id)
            updated_at = str(stock_data.get("performance_updated_at") or "").strip()
            if updated_at:
                try:
                    updated_dt = datetime.fromisoformat(updated_at)
                    if (now - updated_dt).total_seconds() > 3 * 24 * 3600:
                        stale_performance.append(stock_id)
                except ValueError:
                    stale_performance.append(stock_id)

        closed_positions = payload.get("closed_positions") or []
        closed_missing_weekly = [
            str(item.get("stock_id") or "").strip()
            for item in closed_positions
            if isinstance(item, dict) and item.get("weekly_pnl_hkd") in (None, "")
            and not item.get("quantity_only")
        ]
        closed_aliases = set()
        for item in closed_positions:
            if not isinstance(item, dict):
                continue
            closed_aliases.update(self._weekly_stock_aliases(str(item.get("stock_id") or ""), item, item.get("ticker")))
        sell_ops_missing_closed = []
        for op in payload.get("rebalancing_ops") or []:
            if not isinstance(op, dict):
                continue
            if _rebalancing_op_delta(op.get("op_type"), op.get("quantity")) >= 0:
                continue
            raw_sid = str(op.get("stock_id") or "").strip()
            if not raw_sid:
                continue
            op_aliases = self._weekly_stock_aliases(raw_sid, op, op.get("ticker"))
            if op_aliases.intersection(closed_aliases):
                continue
            still_has_active_holding = False
            for stock_id, stock_data in stocks.items():
                if not isinstance(stock_data, dict):
                    continue
                shares = self._safe_float(stock_data.get("shares_held")) or 0.0
                if shares <= 0:
                    continue
                ticker = self._resolve_review_ticker(str(stock_id), stock_data, [])
                stock_aliases = self._weekly_stock_aliases(str(stock_id), stock_data, ticker)
                if op_aliases.intersection(stock_aliases):
                    still_has_active_holding = True
                    break
            if still_has_active_holding:
                continue
            sell_ops_missing_closed.append(raw_sid)

        holding_total_hkd = 0.0
        has_holding_value = False
        for _, stock_data in active_rows:
            metrics = stock_data.get("position_metrics") or {}
            value_hkd = self._safe_float(metrics.get("holding_value_hkd"))
            if value_hkd is None:
                continue
            has_holding_value = True
            holding_total_hkd += value_hkd
        total_portfolio_value = self._safe_float(payload.get("total_portfolio_value"))
        portfolio_gap_hkd = None
        if total_portfolio_value is not None and has_holding_value:
            portfolio_gap_hkd = round(total_portfolio_value - holding_total_hkd, 2)
        cash_balance_hkd = self._safe_float(payload.get("cash_balance"))
        cash_balance_gap_hkd = None
        if cash_balance_hkd is not None and portfolio_gap_hkd is not None:
            cash_balance_gap_hkd = round(cash_balance_hkd - portfolio_gap_hkd, 2)

        issues = []
        if missing_buy_date:
            issues.append({"key": "missing_buy_date", "count": len(missing_buy_date), "stock_ids": missing_buy_date[:12]})
        if missing_avg_cost:
            issues.append({"key": "missing_avg_cost", "count": len(missing_avg_cost), "stock_ids": missing_avg_cost[:12]})
        if missing_performance:
            issues.append({"key": "missing_performance_data", "count": len(missing_performance), "stock_ids": missing_performance[:12]})
        if stale_performance:
            issues.append({"key": "stale_performance_data", "count": len(stale_performance), "stock_ids": stale_performance[:12]})
        if closed_missing_weekly:
            issues.append({"key": "closed_position_missing_weekly_pnl", "count": len(closed_missing_weekly), "stock_ids": closed_missing_weekly[:12]})
        if sell_ops_missing_closed:
            issues.append({"key": "sell_ops_missing_closed_positions", "count": len(sell_ops_missing_closed), "stock_ids": sell_ops_missing_closed[:12]})
        if position_reconciliation["position_mismatch_count"]:
            issues.append(
                {
                    "key": "position_reconciliation_mismatch",
                    "count": position_reconciliation["position_mismatch_count"],
                    "stock_ids": position_reconciliation["mismatched_stock_ids"],
                }
            )
        if portfolio_gap_hkd is not None and portfolio_gap_hkd < 0:
            issues.append({"key": "portfolio_total_below_holdings", "count": 1, "value_hkd": portfolio_gap_hkd})
        if cash_balance_gap_hkd is not None and abs(cash_balance_gap_hkd) > 1.0:
            issues.append({"key": "cash_balance_mismatch", "count": 1, "value_hkd": cash_balance_gap_hkd})

        status = "healthy"
        if issues:
            status = "warning"
        if missing_performance or (portfolio_gap_hkd is not None and portfolio_gap_hkd < 0) or position_reconciliation["position_mismatch_count"]:
            status = "error"

        return {
            "status": status,
            "active_holding_count": len(active_rows),
            "issue_count": len(issues),
            "issues": issues,
            "holding_value_hkd": round(holding_total_hkd, 2) if has_holding_value else None,
            "portfolio_total_value_hkd": total_portfolio_value,
            "portfolio_gap_hkd": portfolio_gap_hkd,
            "cash_balance_hkd": cash_balance_hkd,
            "implied_cash_balance_hkd": portfolio_gap_hkd,
            "cash_balance_gap_hkd": cash_balance_gap_hkd,
            "position_reconciliation": position_reconciliation,
            "prices_refreshed_at": str(payload.get("prices_refreshed_at") or ""),
        }

    def update_stock_weekly_data(
        self,
        week_id: str,
        stock_id: str,
        stock_name: str,
        news: Optional[List[Dict]] = None,
        performance_summary: Optional[str] = None,
        performance_data: Optional[Dict] = None,
        user_view: Optional[str] = None,
        news_summary: Optional[str] = None,
        shares_held: Optional[float] = None,
        portfolio_returns: Optional[Dict] = None,
        avg_cost: Optional[float] = None,
        broker_commentary_ai_summary: Optional[str] = None,
        broker_commentary_ai_summary_signature: Optional[str] = None,
        broker_commentary_ai_summary_error: Optional[str] = None,
        news_search_warnings: Optional[List[str]] = None,
        news_fallback_summary: Optional[str] = None,
        news_cache_hit: Optional[bool] = None,
        news_deep_search_summary: Optional[str] = None,
        news_deep_search_meta: Optional[Dict[str, Any]] = None,
        ticker: Optional[str] = None,
    ) -> None:
        """TextStockText. portfolio_returns Text return_since_buy, ytd_return, return_6m, return_1y Text"""
        with self._weekly_lock:
            data = self._load_weekly_reviews_file_locked()
            if data is None:
                data = {"weeks": {}}

            if week_id not in data["weeks"]:
                data["weeks"][week_id] = {
                    "week_id": week_id,
                    "stocks": {},
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat()
                }

            if stock_id not in data["weeks"][week_id]["stocks"]:
                data["weeks"][week_id]["stocks"][stock_id] = {
                    "stock_name": stock_name,
                    "news": [],
                    "news_updated_at": None,
                    "performance_summary": "",
                    "performance_updated_at": None,
                    "user_view": "",
                    "user_view_updated_at": None
                }

            stock_data = data["weeks"][week_id]["stocks"][stock_id]
            stock_data["stock_name"] = stock_name
            if ticker is not None:
                clean_ticker = str(ticker or "").strip()
                if clean_ticker:
                    stock_data["ticker"] = clean_ticker
            if news is not None:
                incoming_news = list(news)
                existing_news = stock_data.get("news") or []
                failed_refresh = not incoming_news and bool(existing_news) and bool(news_search_warnings)
                if not failed_refresh:
                    stock_data["news"] = incoming_news
                    stock_data["news_updated_at"] = datetime.now().isoformat()
                    stock_data.pop("news_summary", None)
            if performance_summary is not None:
                stock_data["performance_summary"] = performance_summary
                stock_data["performance_updated_at"] = datetime.now().isoformat()
            if performance_data is not None:
                stock_data["performance_data"] = performance_data
            if shares_held is not None:
                try:
                    stock_data["shares_held"] = float(shares_held) if shares_held != "" else None
                except (TypeError, ValueError):
                    stock_data["shares_held"] = None
            if user_view is not None:
                stock_data["user_view"] = user_view
                stock_data["user_view_updated_at"] = datetime.now().isoformat()
            if news_summary is not None:
                stock_data["news_summary"] = news_summary
            if portfolio_returns is not None:
                stock_data["portfolio_returns"] = portfolio_returns
            if avg_cost is not None:
                try:
                    stock_data["avg_cost"] = round(float(avg_cost), 4) if avg_cost != "" else None
                except (TypeError, ValueError):
                    stock_data["avg_cost"] = None
            if broker_commentary_ai_summary is not None:
                stock_data["broker_commentary_ai_summary"] = broker_commentary_ai_summary
                stock_data["broker_commentary_ai_summary_generated_at"] = datetime.now().isoformat()
            if broker_commentary_ai_summary_signature is not None:
                stock_data["broker_commentary_ai_summary_signature"] = broker_commentary_ai_summary_signature
            if broker_commentary_ai_summary_error is not None:
                stock_data["broker_commentary_ai_summary_error"] = broker_commentary_ai_summary_error
            if news_search_warnings is not None:
                stock_data["news_search_warnings"] = list(news_search_warnings or [])
            if news_fallback_summary is not None:
                stock_data["news_fallback_summary"] = str(news_fallback_summary or "")
            if news_cache_hit is not None:
                stock_data["news_cache_hit"] = bool(news_cache_hit)
            if news_deep_search_summary is not None:
                stock_data["news_deep_search_summary"] = str(news_deep_search_summary or "")
            if news_deep_search_meta is not None:
                stock_data["news_deep_search_meta"] = dict(news_deep_search_meta or {})

            data["weeks"][week_id]["updated_at"] = datetime.now().isoformat()

            path = self._get_weekly_reviews_path()
            self._atomic_write_json(path, data)

    def update_weekly_portfolio(
        self,
        week_id: str,
        total_portfolio_value: Optional[float] = None,
        holdings: Optional[Dict[str, float]] = None,
        stock_names: Optional[Dict[str, str]] = None,
        buy_dates: Optional[Dict[str, str]] = None,
        usd_to_hkd: Optional[float] = None,
        cny_to_hkd: Optional[float] = None,
        eur_to_hkd: Optional[float] = None,
        jpy_to_hkd: Optional[float] = None,
        krw_to_hkd: Optional[float] = None,
        cash_balance: Optional[float] = None,
        closed_positions: Optional[List[Dict]] = None,
        avg_costs: Optional[Dict[str, float]] = None,
        rebalancing_ops: Optional[List[Dict]] = None,
    ) -> None:
        """TextHoldingsText, TextStockTextSharesText. stock_names, buy_dates, avg_costs Text. """
        with self._weekly_lock:
            data = self._load_weekly_reviews_file_locked()
            if data is None:
                data = {"weeks": {}}

            if week_id not in data["weeks"]:
                data["weeks"][week_id] = {
                    "week_id": week_id,
                    "stocks": {},
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                }

            wk = data["weeks"][week_id]
            if total_portfolio_value is not None:
                try:
                    wk["total_portfolio_value"] = float(total_portfolio_value) if total_portfolio_value != "" else None
                except (TypeError, ValueError):
                    wk["total_portfolio_value"] = None
            if cash_balance is not None:
                try:
                    wk["cash_balance"] = float(cash_balance) if cash_balance != "" else None
                except (TypeError, ValueError):
                    wk["cash_balance"] = None
            if usd_to_hkd is not None:
                try:
                    wk["usd_to_hkd"] = float(usd_to_hkd) if usd_to_hkd != "" else None
                except (TypeError, ValueError):
                    wk["usd_to_hkd"] = None
            if cny_to_hkd is not None:
                try:
                    wk["cny_to_hkd"] = float(cny_to_hkd) if cny_to_hkd != "" else None
                except (TypeError, ValueError):
                    wk["cny_to_hkd"] = None
            if eur_to_hkd is not None:
                try:
                    wk["eur_to_hkd"] = float(eur_to_hkd) if eur_to_hkd != "" else None
                except (TypeError, ValueError):
                    wk["eur_to_hkd"] = None
            if jpy_to_hkd is not None:
                try:
                    wk["jpy_to_hkd"] = float(jpy_to_hkd) if jpy_to_hkd != "" else None
                except (TypeError, ValueError):
                    wk["jpy_to_hkd"] = None
            if krw_to_hkd is not None:
                try:
                    wk["krw_to_hkd"] = float(krw_to_hkd) if krw_to_hkd != "" else None
                except (TypeError, ValueError):
                    wk["krw_to_hkd"] = None
            if holdings is not None:
                normalized_holdings: Dict[str, Any] = {}
                normalized_stock_names: Dict[str, Any] = {}
                normalized_buy_dates: Dict[str, Any] = {}
                normalized_avg_costs: Dict[str, Any] = {}

                for stock_id, shares in (holdings or {}).items():
                    normalized_holdings[self._normalize_market_ticker_key(stock_id)] = shares
                for stock_id, name in (stock_names or {}).items():
                    normalized_stock_names[self._normalize_market_ticker_key(stock_id)] = name
                for stock_id, value in (buy_dates or {}).items():
                    normalized_buy_dates[self._normalize_market_ticker_key(stock_id)] = value
                for stock_id, value in (avg_costs or {}).items():
                    normalized_avg_costs[self._normalize_market_ticker_key(stock_id)] = value

                holdings = normalized_holdings
                stock_names = normalized_stock_names
                buy_dates = normalized_buy_dates
                avg_costs = normalized_avg_costs

                normalized_existing: Dict[str, Dict[str, Any]] = {}
                for stock_id, payload in list((wk.get("stocks") or {}).items()):
                    normalized_key = self._normalize_market_ticker_key(stock_id)
                    normalized_existing[normalized_key] = self._merge_weekly_stock_entries(
                        normalized_existing.get(normalized_key),
                        payload,
                    )
                wk["stocks"] = normalized_existing

                def _canon(s: str) -> str:
                    u = (s or "").strip().upper()
                    for suf in PRIMARY_CODE_SUFFIXES:
                        if u.endswith(suf):
                            return u[: -len(suf)]
                    return u

                written_canon = {_canon(sid) for sid in holdings}
                for sid in list(wk.get("stocks", {})):
                    if sid not in holdings and _canon(sid) not in written_canon:
                        if self._weekly_stock_entry_has_user_content(wk["stocks"].get(sid)):
                            wk["stocks"][sid]["shares_held"] = 0.0
                        else:
                            del wk["stocks"][sid]
                for sid in list(wk.get("stocks", {})):
                    if _canon(sid) in written_canon and sid not in holdings:
                        if self._weekly_stock_entry_has_user_content(wk["stocks"].get(sid)):
                            wk["stocks"][sid]["shares_held"] = 0.0
                        else:
                            del wk["stocks"][sid]

                for stock_id, shares in holdings.items():
                    if stock_id not in wk.get("stocks", {}):
                        wk.setdefault("stocks", {})[stock_id] = {"stock_name": stock_id}
                    try:
                        wk["stocks"][stock_id]["shares_held"] = float(shares) if shares != "" and shares is not None else None
                    except (TypeError, ValueError):
                        wk["stocks"][stock_id]["shares_held"] = None
                    if stock_names and stock_id in stock_names:
                        name = stock_names[stock_id]
                        wk["stocks"][stock_id]["stock_name"] = name if name else stock_id
                    if buy_dates and stock_id in buy_dates:
                        bd = buy_dates[stock_id]
                        wk["stocks"][stock_id]["buy_date"] = (bd or "").strip() or None
            for stock_id, bd in (buy_dates or {}).items():
                if stock_id and stock_id in wk.get("stocks", {}):
                    wk["stocks"][stock_id]["buy_date"] = (bd or "").strip() or None
            if closed_positions is not None:
                wk["closed_positions"] = closed_positions
            if rebalancing_ops is not None:
                wk["rebalancing_ops"] = self._normalize_rebalancing_ops(rebalancing_ops)
            if avg_costs is not None:
                for stock_id, cost in avg_costs.items():
                    if stock_id and stock_id in wk.get("stocks", ):
                        try:
                            wk["stocks"][stock_id]["avg_cost"] = round(float(cost), 4) if cost not in (None, "") else None
                        except (TypeError, ValueError):
                            wk["stocks"][stock_id]["avg_cost"] = None

            wk["updated_at"] = datetime.now().isoformat()

            path = self._get_weekly_reviews_path()
            self._atomic_write_json(path, data)

    def update_weekly_market_context(
        self,
        week_id: str,
        market_context: Dict[str, Any],
    ) -> None:
        """TextStatusText. """
        with self._weekly_lock:
            data = self._load_weekly_reviews_file_locked()
            if data is None:
                data = {"weeks": {}}

            if week_id not in data["weeks"]:
                data["weeks"][week_id] = {
                    "week_id": week_id,
                    "stocks": {},
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                }

            wk = data["weeks"][week_id]
            current = wk.get("market_context") or {}
            merged = dict(current)
            merged.update(_json_safe_value(market_context or {}))
            merged["updated_at"] = datetime.now().isoformat()
            wk["market_context"] = merged
            wk["updated_at"] = datetime.now().isoformat()

            path = self._get_weekly_reviews_path()
            self._atomic_write_json(path, data)

    def update_weekly_macro_events(
        self,
        week_id: str,
        macro_events: Dict[str, Any],
    ) -> None:
        """Text. """
        with self._weekly_lock:
            data = self._load_weekly_reviews_file_locked()
            if data is None:
                data = {"weeks": {}}

            if week_id not in data["weeks"]:
                data["weeks"][week_id] = {
                    "week_id": week_id,
                    "stocks": {},
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                }

            wk = data["weeks"][week_id]
            wk["macro_events"] = _json_safe_value(macro_events or {})
            wk["updated_at"] = datetime.now().isoformat()

            path = self._get_weekly_reviews_path()
            self._atomic_write_json(path, data)

    def update_weekly_factor_analysis(
        self,
        week_id: str,
        factor_analysis: Dict[str, Any],
    ) -> None:
        """TextAnalysisResult. """
        with self._weekly_lock:
            data = self._load_weekly_reviews_file_locked()
            if data is None:
                data = {"weeks": {}}

            if week_id not in data["weeks"]:
                data["weeks"][week_id] = {
                    "week_id": week_id,
                    "stocks": {},
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                }

            wk = data["weeks"][week_id]
            current = wk.get("factor_analysis") or {}
            merged = dict(current)
            merged.update(_json_safe_value(factor_analysis or {}))
            merged["updated_at"] = datetime.now().isoformat()
            wk["factor_analysis"] = merged
            wk["updated_at"] = datetime.now().isoformat()

            path = self._get_weekly_reviews_path()
            self._atomic_write_json(path, data)

    def save_rebalancing_ops(self, week_id: str, ops: List[Dict]) -> None:
        """SaveWeekly TradesText"""
        with self._weekly_lock:
            data = self._load_weekly_reviews_file_locked()
            if data is None:
                data = {"weeks": {}}
            if week_id not in data["weeks"]:
                data["weeks"][week_id] = {
                    "week_id": week_id,
                    "stocks": {},
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                }
            data["weeks"][week_id]["rebalancing_ops"] = self._normalize_rebalancing_ops(ops)
            data["weeks"][week_id]["updated_at"] = datetime.now().isoformat()
            path = self._get_weekly_reviews_path()
            self._atomic_write_json(path, data)

    def _normalize_rebalancing_ops(self, ops: Optional[List[Dict]]) -> List[Dict]:
        normalized: List[Dict] = []
        allowed_decision_types = {
            "take_profit",
            "risk_reduction",
            "thesis_change",
            "rotate",
            "raise_cash",
            "rebalance",
            "unknown",
        }
        allowed_destination_types = {
            "new_position",
            "multiple_positions",
            "cash",
            "partial_trim",
            "unknown",
        }
        allowed_review_horizons = {"week_end", "1m", "3m", "custom"}
        for op in ops or []:
            if not isinstance(op, dict):
                continue
            item = dict(op)
            decision_type = str(item.get("decision_type") or "").strip().lower()
            item["decision_type"] = decision_type if decision_type in allowed_decision_types else "unknown"
            destination_type = str(item.get("destination_type") or "").strip().lower()
            item["destination_type"] = destination_type if destination_type in allowed_destination_types else "unknown"
            review_horizon = str(item.get("review_horizon") or "").strip().lower()
            item["review_horizon"] = review_horizon if review_horizon in allowed_review_horizons else "week_end"
            item["benchmark"] = str(item.get("benchmark") or "").strip()
            item["decision_note"] = str(item.get("decision_note") or "").strip()
            if "pairing_mode" in item:
                mode = str(item.get("pairing_mode") or "").strip().lower()
                item["pairing_mode"] = mode if mode in {"auto", "manual"} else "auto"
            if "pairing_note" in item:
                item["pairing_note"] = str(item.get("pairing_note") or "").strip()
            if "paired_buys" in item:
                raw_paired = item.get("paired_buys")
                if isinstance(raw_paired, (list, tuple, set)):
                    paired = []
                    for entry in raw_paired:
                        if isinstance(entry, dict):
                            stock_id = str(entry.get("stock_id") or "").strip()
                            if not stock_id:
                                continue
                            amount = entry.get("amount")
                            ratio = entry.get("ratio")
                            source = str(entry.get("source") or "").strip() or "manual"
                            try:
                                amount_value = float(amount) if amount not in (None, "") else None
                            except (TypeError, ValueError):
                                amount_value = None
                            try:
                                ratio_value = float(ratio) if ratio not in (None, "") else None
                            except (TypeError, ValueError):
                                ratio_value = None
                            paired_entry = {
                                "stock_id": stock_id,
                                "source": source,
                            }
                            buy_date = str(entry.get("buy_date") or "").strip()
                            buy_week_id = str(entry.get("buy_week_id") or "").strip()
                            if buy_date:
                                paired_entry["buy_date"] = buy_date
                            if buy_week_id:
                                paired_entry["buy_week_id"] = buy_week_id
                            buy_index = entry.get("buy_index")
                            buy_amount = entry.get("buy_amount")
                            try:
                                buy_index_value = int(buy_index) if buy_index not in (None, "") else None
                            except (TypeError, ValueError):
                                buy_index_value = None
                            try:
                                buy_amount_value = float(buy_amount) if buy_amount not in (None, "") else None
                            except (TypeError, ValueError):
                                buy_amount_value = None
                            if amount_value is not None:
                                paired_entry["amount"] = amount_value
                                paired_entry["amount_currency"] = str(entry.get("amount_currency") or "HKD").strip().upper() or "HKD"
                            if ratio_value is not None:
                                paired_entry["ratio"] = ratio_value
                            if buy_index_value is not None:
                                paired_entry["buy_index"] = buy_index_value
                            if buy_amount_value is not None:
                                paired_entry["buy_amount"] = buy_amount_value
                            paired.append(paired_entry)
                        else:
                            text = str(entry or "").strip()
                            if text:
                                paired.append({"stock_id": text, "source": "manual"})
                elif isinstance(raw_paired, str):
                    paired = [
                        {"stock_id": chunk.strip(), "source": "manual"}
                        for chunk in raw_paired.split(",")
                        if chunk.strip()
                    ]
                elif str(raw_paired or "").strip():
                    paired = [{"stock_id": str(raw_paired).strip(), "source": "manual"}]
                else:
                    paired = []
                item["paired_buys"] = paired
            normalized.append(item)
        return normalized

    def update_prices_refreshed_at(self, week_id: str) -> None:
        """TextHoldingsTextRefreshText"""
        with self._weekly_lock:
            data = self._load_weekly_reviews_file_locked()
            if data is None or week_id not in data.get("weeks", {}):
                return
            data["weeks"][week_id]["prices_refreshed_at"] = datetime.now().isoformat()
            path = self._get_weekly_reviews_path()
            self._atomic_write_json(path, data)

    def _canonical_code(self, sid: str) -> str:
        """TextTickerText(Text, Text)"""
        return (sid or "").strip().upper()

    def _normalize_market_ticker_key(self, sid: str) -> str:
        raw = (sid or "").strip()
        if not raw:
            return ""
        upper = self._canonical_code(raw)
        for suffix in SUPPORTED_EXCHANGE_SUFFIXES:
            if upper.endswith(suffix):
                return upper
        return raw

    def _stock_entry_recency(self, entry: Optional[Dict[str, Any]]) -> datetime:
        best = datetime.min
        for field in (
            "performance_updated_at",
            "news_updated_at",
            "user_view_updated_at",
            "broker_commentary_ai_summary_generated_at",
        ):
            value = str((entry or {}).get(field) or "").strip()
            if not value:
                continue
            try:
                best = max(best, datetime.fromisoformat(value))
            except ValueError:
                continue
        return best

    def _merge_weekly_stock_entries(
        self,
        existing: Optional[Dict[str, Any]],
        incoming: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        existing = dict(existing or {})
        incoming = dict(incoming or {})
        if not existing:
            return incoming
        if not incoming:
            return existing

        preferred, secondary = (
            (incoming, existing)
            if self._stock_entry_recency(incoming) >= self._stock_entry_recency(existing)
            else (existing, incoming)
        )
        merged = dict(preferred)
        for key, value in secondary.items():
            if key not in merged or merged.get(key) in (None, "", [], {}):
                merged[key] = value
        return merged

    def _weekly_stock_entry_has_user_content(self, entry: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(entry, dict):
            return False
        text_fields = (
            "user_view",
            "news_summary",
            "news_deep_search_summary",
            "broker_commentary_ai_summary",
            "performance_summary",
        )
        for field in text_fields:
            if str(entry.get(field) or "").strip():
                return True
        list_fields = ("news", "filings", "zsxq_matches")
        for field in list_fields:
            value = entry.get(field)
            if isinstance(value, list) and value:
                return True
        return False

    def _resolve_op_to_key(self, raw_sid: str, canon_to_key: Dict[str, str]) -> Optional[str]:
        """TextTradesTextTickerText prev Text key. Text 00700/00700.HK Text. """
        ck = self._canonical_code(raw_sid)
        if ck in canon_to_key:
            return canon_to_key[ck]
        # TextTradeText: 00700.HK -> 00700
        for suffix in SUPPORTED_EXCHANGE_SUFFIXES:
            if ck.endswith(suffix):
                base = ck[: -len(suffix)]
                if base in canon_to_key:
                    return canon_to_key[base]
        # Text: 00700 -> 00700.HK
        for suffix in SUPPORTED_EXCHANGE_SUFFIXES:
            cand = ck + suffix
            if cand in canon_to_key:
                return canon_to_key[cand]
        return None

    def apply_rebalancing_ops(
        self,
        week_id: str,
        stock_names: Optional[Dict[str, str]] = None,
        ops_override: Optional[List[Dict]] = None,
        base_holdings_override: Optional[Dict[str, float]] = None,
        code_to_storage_key: Optional[Dict[str, str]] = None,
        display_codes: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """TextTradesTextHoldingsText. HoldingsTextTradesTextTickerText(00700.HK Text). 
        display_codes: TextCurrentTextTickerText(Holdings+Text), Text, Text. 
        base_holdings_override: TextCurrentTextShares, key TextTicker(ticker/stock_code)"""
        prev_id = self._prev_week_id(week_id)
        prev = self.get_weekly_review(prev_id) if prev_id else None
        review = self.get_weekly_review(week_id)
        if not review:
            review = {"stocks": {}}
        ops = ops_override if ops_override is not None else (review.get("rebalancing_ops") or [])
        valid_codes = set()
        if display_codes:
            for c in display_codes:
                if c and str(c).strip():
                    valid_codes.add(self._canonical_code(str(c).strip()))

        def _resolve_storage_key(raw_sid: str) -> str:
            text = (raw_sid or "").strip()
            if not text:
                return ""
            resolved = self._resolve_op_to_key(text, canon_to_key)
            if resolved is not None:
                return resolved
            playbook = self.get_stock_playbook(text) or {}
            pb_stock_id = str(playbook.get("stock_id") or "").strip()
            if pb_stock_id:
                canon_to_key[self._canonical_code(text)] = pb_stock_id
                canon_to_key[self._canonical_code(pb_stock_id)] = pb_stock_id
                pb_ticker = str(playbook.get("ticker") or "").strip()
                if pb_ticker:
                    canon_to_key[self._canonical_code(pb_ticker)] = pb_stock_id
                return pb_stock_id
            for stock_id, stock_data in ((review.get("stocks") or {}) if isinstance(review, dict) else {}).items():
                ticker_aliases = [str(stock_id or "").strip(), str((stock_data or {}).get("ticker") or "").strip()]
                for alias in ticker_aliases:
                    if alias and self._canonical_code(alias) == self._canonical_code(text):
                        canon_to_key[self._canonical_code(text)] = str(stock_id).strip()
                        return str(stock_id).strip()
            return text

        base_shares: Dict[str, float] = {}
        canon_to_key: Dict[str, str] = {}
        for disp_code, stor_key in (code_to_storage_key or {}).items():
            if disp_code and stor_key:
                canon_to_key[self._canonical_code(disp_code)] = stor_key
        source_review = prev if (prev and prev.get("stocks")) else review
        base_source = json.loads(json.dumps(source_review or {"stocks": {}}))

        if base_source and base_source.get("stocks"):
            for sid, d in base_source["stocks"].items():
                try:
                    v = d.get("shares_held")
                    base_shares[sid] = float(v) if v is not None and v != "" else 0
                except (TypeError, ValueError):
                    base_shares[sid] = 0
                canon_to_key[self._canonical_code(sid)] = sid
                ticker_aliases = []
                playbook = self.get_stock_playbook(sid) or {}
                playbook_ticker = str(playbook.get("ticker") or "").strip()
                if playbook_ticker:
                    ticker_aliases.append(playbook_ticker)
                data_ticker = str((d or {}).get("ticker") or "").strip()
                if data_ticker:
                    ticker_aliases.append(data_ticker)
                for alias in ticker_aliases:
                    canon_to_key[self._canonical_code(alias)] = sid

        if base_holdings_override and len(base_holdings_override) > 0:
            def _primary_code(sid: str) -> str:
                return _primary_code_without_exchange_suffix(sid)
            seen: Dict[str, str] = {}
            override_keys: set[str] = set()
            for k, v in base_holdings_override.items():
                k = (k or "").strip()
                if not k:
                    continue
                try:
                    val = float(v) if v is not None and v != "" else 0
                except (TypeError, ValueError):
                    val = 0
                resolved_key = _resolve_storage_key(k) or k
                pk = _primary_code(resolved_key)
                key = seen.get(pk, resolved_key)
                seen[pk] = key
                base_shares[key] = val
                canon_to_key[self._canonical_code(k)] = key
                canon_to_key[self._canonical_code(key)] = key
                override_keys.add(key)
            if not isinstance(base_source, dict):
                base_source = {"stocks": {}}
            base_source.setdefault("stocks", {})
            review_stocks = (review.get("stocks") or {}) if isinstance(review, dict) else {}
            prev_stocks = (prev.get("stocks") or {}) if isinstance(prev, dict) else {}
            for sid, qty in base_shares.items():
                if sid not in override_keys and sid in (base_source.get("stocks") or {}):
                    continue
                merged = {}
                if sid in prev_stocks and isinstance(prev_stocks[sid], dict):
                    merged.update(prev_stocks[sid])
                if sid in review_stocks and isinstance(review_stocks[sid], dict):
                    merged.update(review_stocks[sid])
                if not merged:
                    merged["stock_name"] = (stock_names or {}).get(sid, sid)
                merged["shares_held"] = qty
                base_source["stocks"][sid] = merged

        # Text avg_cost(Text base_source Text)
        avg_costs: Dict[str, float] = {}
        for sid, d in (base_source or {}).get("stocks", {}).items():
            ac = (d or {}).get("avg_cost")
            if ac is not None:
                try:
                    avg_costs[sid] = float(ac)
                except (TypeError, ValueError):
                    pass

        new_buy_dates: Dict[str, str] = {}
        for op in ops:
            raw_sid = (op.get("stock_id") or "").strip()
            if not raw_sid:
                continue
            delta = _rebalancing_op_delta(op.get("op_type"), op.get("quantity"))
            if delta == 0:
                continue
            ck = self._canonical_code(raw_sid)
            resolved = self._resolve_op_to_key(raw_sid, canon_to_key)
            if resolved is not None:
                key = resolved
            else:
                key = raw_sid
            canon_to_key[ck] = key
            prev_qty = base_shares.get(key, 0)
            base_shares[key] = prev_qty + delta
            # BuyText
            if delta > 0:
                op_price = op.get("price")
                op_date = (op.get("date") or "").strip()
                buy_price = None

                # TextManualText
                if op_price not in (None, ""):
                    try:
                        buy_price = float(op_price)
                    except (TypeError, ValueError):
                        pass

                # TextManualText, TextBuyText
                if buy_price is None and op_date and _ak_price_on_date:
                    try:
                        buy_price = _ak_price_on_date(key, op_date)
                        if buy_price:
                            time.sleep(0.1)  # Text
                    except Exception:
                        pass

                # Text
                if buy_price and buy_price > 0:
                    prev_cost = avg_costs.get(key, 0.0)
                    new_qty = prev_qty + delta
                    avg_costs[key] = (prev_cost * prev_qty + buy_price * delta) / new_qty if new_qty > 0 else buy_price

                if prev_qty == 0:
                    normalized_buy_date = _normalize_rebalancing_buy_date(op.get("date"))
                    if normalized_buy_date:
                        new_buy_dates[key] = normalized_buy_date

        holdings_float = {k: max(0, round(v, 0)) for k, v in base_shares.items()}
        buy_dates = dict(new_buy_dates)
        base_stocks = (base_source or {}).get("stocks", {})
        if base_stocks:
            for sid, d in base_stocks.items():
                if sid in holdings_float and sid not in buy_dates and d.get("buy_date"):
                    buy_dates[sid] = (d.get("buy_date") or "").strip()
        def _base_shares(sid):
            try:
                v = base_stocks.get(sid, {}).get("shares_held")
                return float(v) if v not in (None, "") else 0
            except (TypeError, ValueError):
                return 0
        closed_ids = {k for k, v in base_shares.items() if v <= 0 and _base_shares(k) > 0}

        closed_positions: List[Dict] = []
        for key in closed_ids:
            prev_qty = base_shares.get(key, 0)
            if prev_qty > 0:
                continue
            prev_data = (base_source or {}).get("stocks", {}).get(key, {})
            try:
                shares_sold = int(float(prev_data.get("shares_held") or 0))
            except (TypeError, ValueError):
                shares_sold = 0
            if shares_sold <= 0:
                continue
            sell_date = ""
            sell_price = None
            for op in ops:
                raw_sid = (op.get("stock_id") or "").strip()
                if not raw_sid:
                    continue
                ck = self._canonical_code(raw_sid)
                if canon_to_key.get(ck) != key and ck != self._canonical_code(key):
                    continue
                t = (op.get("op_type") or "").strip().lower()
                if t not in REBALANCING_SELL_TYPES:
                    continue
                try:
                    qty = float(op.get("quantity") or 0)
                except (TypeError, ValueError):
                    continue
                if qty <= 0:
                    continue
                sell_date = (op.get("date") or "").strip()
                try:
                    if op.get("price") not in (None, "",):
                        sell_price = float(op.get("price"))
                except (TypeError, ValueError):
                    pass
                break
            week_start_price = self._safe_float(
                (((review or {}).get("stocks") or {}).get(key) or {}).get("performance_data", {}).get("start_price")
            )
            if week_start_price is None:
                week_start_price = self._safe_float(
                    (prev_data.get("performance_data") or {}).get("end_price")
                )
            if _ak_get_perf:
                try:
                    if week_start_price is None:
                        perf = _ak_get_perf(key, 7)
                        if perf.get("success") and perf.get("data"):
                            week_start_price = perf["data"].get("start_price")
                        time.sleep(0.2)
                except Exception:
                    pass
            if sell_price is None and sell_date and _ak_price_on_date:
                try:
                    sell_price = _ak_price_on_date(key, sell_date)
                    time.sleep(0.2)
                except Exception:
                    pass
            realized_pnl = None
            avg_cost_for_key = avg_costs.get(key)
            if avg_cost_for_key and avg_cost_for_key > 0 and sell_price is not None:
                # TextP&L
                realized_pnl = shares_sold * (sell_price - avg_cost_for_key)
            elif week_start_price and week_start_price > 0 and sell_price is not None:
                # Text: TextThis WeekText(Text)Text
                realized_pnl = shares_sold * (sell_price - week_start_price)
            name = (stock_names or {}).get(key, prev_data.get("stock_name", key))
            closed_positions.append({
                "stock_id": key,
                "stock_name": name,
                "shares_sold": shares_sold,
                "sell_date": sell_date[:10] if sell_date else "",
                "sell_price": round(sell_price, 2) if sell_price is not None else None,
                "week_start_price": round(week_start_price, 2) if week_start_price else None,
                "realized_pnl": round(realized_pnl, 2) if realized_pnl is not None else None,
            })

        for cp in closed_positions:
            rp = cp.get("realized_pnl")
            if rp is not None:
                stock_id = str(cp.get("stock_id") or "").strip()
                ticker = str(((review.get("stocks") or {}).get(stock_id) or {}).get("ticker") or stock_id).strip()
                currency = self._stock_currency(ticker or stock_id, review=review, stock_id=stock_id)
                cp["realized_pnl_hkd"] = self._to_hkd(self._safe_float(rp), currency, review)
            else:
                cp["realized_pnl_hkd"] = None

        code_map = code_to_storage_key or {}
        holdings_for_storage = {code_map.get(k, k): v for k, v in holdings_float.items() if v > 0}

        def _primary(s: str) -> str:
            return _primary_code_without_exchange_suffix(s)

        seen_primary: Dict[str, str] = {}
        for k, v in holdings_for_storage.items():
            pk = _primary(k)
            if pk not in seen_primary:
                seen_primary[pk] = k
        holdings_for_storage = {seen_primary[pk]: holdings_for_storage[seen_primary[pk]] for pk in seen_primary}
        buy_dates_for_storage = {
            code_map.get(k, k): v
            for k, v in (buy_dates or {}).items()
            if holdings_float.get(k, 0) > 0
        }
        avg_costs_for_storage = {
            code_map.get(k, k): v
            for k, v in avg_costs.items()
            if v is not None and holdings_float.get(k, 0) > 0
        }
        gross_buy_amount, gross_sell_amount = self._gross_rebalancing_amounts_hkd(ops, review)

        if not dry_run:
            self.update_weekly_portfolio(
                week_id=week_id,
                holdings=holdings_for_storage,
                stock_names=stock_names,
                buy_dates=buy_dates_for_storage,
                closed_positions=closed_positions,
                avg_costs=avg_costs_for_storage,
                rebalancing_ops=ops,
            )
        # Return UI-facing sync fields keyed by display code so frontend can
        # refresh holdings + avg_cost + buy_date without a full page reload.
        avg_costs_sync = {
            k: round(float(v), 4)
            for k, v in avg_costs.items()
            if k in holdings_float and holdings_float.get(k, 0) > 0 and v is not None
        }
        buy_dates_sync = {
            k: v
            for k, v in (buy_dates or {}).items()
            if k in holdings_float and holdings_float.get(k, 0) > 0 and v
        }
        return {
            "holdings": holdings_float,
            "closed_positions": closed_positions,
            "avg_costs": avg_costs_sync,
            "buy_dates": buy_dates_sync,
            "preview_summary": {
                "gross_buy_hkd": round(gross_buy_amount, 2),
                "gross_sell_hkd": round(gross_sell_amount, 2),
                "net_cash_hkd": round(gross_sell_amount - gross_buy_amount, 2),
                "gross_buy_amount": round(gross_buy_amount, 2),
                "gross_sell_amount": round(gross_sell_amount, 2),
                "net_cash_amount": round(gross_sell_amount - gross_buy_amount, 2),
                "changed_position_count": len([k for k, v in holdings_float.items() if round(_base_shares(k), 8) != round(v, 8)]),
                "closed_position_count": len(closed_positions),
                "dry_run": bool(dry_run),
            },
        }

    def get_weekly_review_history(self, limit: int = 12) -> List[str]:
        """TextHistoryText(Text N Text)"""
        data = self._load_weekly_reviews_file()
        if data is None:
            return []
        weeks = sorted(data.get("weeks", {}).keys(), reverse=True)
        return weeks[:limit]

    # ==================== Text ====================

    def _load_task_records_locked(self) -> Dict[str, Any]:
        default = {"tasks": {}}
        data = self._load_json_file_with_default(
            self.tasks_path,
            default,
            expected_type=dict,
            label="task records",
        )
        tasks = data.get("tasks", {}) if isinstance(data, dict) else {}
        if not isinstance(tasks, dict):
            logger.warning("Unexpected task records payload shape in %s", self.tasks_path)
            self._quarantine_corrupt_file(self.tasks_path, "task records")
            return {"tasks": {}}
        return {"tasks": tasks}

    def _save_task_records_locked(self, data: Dict[str, Any]) -> None:
        tasks = data.get("tasks", {}) if isinstance(data, dict) else {}
        if not isinstance(tasks, dict):
            tasks = {}
        self._atomic_write_json(self.tasks_path, {"tasks": tasks})

    def save_task_record(self, task_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
        clean_id = str(task_id or "").strip()
        if not clean_id:
            raise ValueError("task_id is required")

        now = datetime.now().isoformat()
        patch = dict(record or {})
        with self._task_records_lock:
            data = self._load_task_records_locked()
            current = dict(data.get("tasks", {}).get(clean_id) or {})
            merged = dict(current)
            merged.update(patch)
            merged["task_id"] = clean_id
            merged["created_at"] = str(merged.get("created_at") or current.get("created_at") or now)
            merged["updated_at"] = str(patch.get("updated_at") or now)

            safe_record = _json_safe_value(merged)
            data["tasks"][clean_id] = safe_record
            self._save_task_records_locked(data)
            return dict(safe_record)

    def get_task_record(self, task_id: str) -> Optional[Dict[str, Any]]:
        clean_id = str(task_id or "").strip()
        if not clean_id:
            return None
        with self._task_records_lock:
            data = self._load_task_records_locked()
            record = data.get("tasks", {}).get(clean_id)
            return dict(record) if isinstance(record, dict) else None

    def list_task_records(
        self,
        statuses: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        with self._task_records_lock:
            data = self._load_task_records_locked()
            tasks = data.get("tasks", {})
            records = [dict(v) for v in tasks.values() if isinstance(v, dict)]

        if statuses:
            allowed = {str(item or "").strip().lower() for item in statuses if str(item or "").strip()}
            records = [item for item in records if str(item.get("status", "")).strip().lower() in allowed]

        records.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        if limit is not None and int(limit) > 0:
            return records[: int(limit)]
        return records

    def recover_pending_tasks(self) -> List[Dict[str, Any]]:
        recovered: List[Dict[str, Any]] = []
        now = datetime.now().isoformat()

        with self._task_records_lock:
            data = self._load_task_records_locked()
            changed = False
            tasks = data.get("tasks", {})

            for task_id, raw in list(tasks.items()):
                if not isinstance(raw, dict):
                    continue
                record = dict(raw)
                record["task_id"] = str(record.get("task_id") or task_id)
                status = str(record.get("status") or "").strip().lower()

                if status == "running":
                    record["status"] = "interrupted"
                    record["updated_at"] = now
                    tasks[task_id] = _json_safe_value(record)
                    status = "interrupted"
                    changed = True

                if status in {"queued", "interrupted"}:
                    recovered.append(record)

            if changed:
                self._save_task_records_locked(data)

        recovered.sort(key=lambda item: str(item.get("created_at") or ""))
        return recovered

    # ==================== Text ====================

    def log(self, message: str, level: str = "INFO"):
        """Text"""
        log_file = self.base_dir / "logs" / f"{datetime.now().strftime('%Y-%m-%d')}.log"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [{level}] {message}\n")
