from __future__ import annotations

from decimal import Decimal

from app.errors import DomainError
from app.manifest import canonical_digest

from . import core

SCOPE = "run-review"


def _batch(store, payload: dict) -> dict:
    batch_id = payload.get("batch_id")
    if batch_id:
        batch = store.batch(str(batch_id))
        if batch is None:
            raise DomainError("NOT_FOUND", "分摊批次不存在")
        return batch
    batch = store.head_batch("allocation")
    if batch is None:
        raise DomainError("NO_BATCH", "尚无分摊批次，请先计算分摊")
    return batch


def empty_view(reason: str = "NO_BATCH") -> dict:
    """尚无分摊批次时的只读页面数据，字段与批次视图一致。"""
    return {
        "status": "EMPTY",
        "input_count": 0,
        "valid_count": 0,
        "quarantine_count": 0,
        "run_id": None,
        "batch_id": None,
        "batch_status": None,
        "parent_batch_id": None,
        "version_id": None,
        "version_seq": None,
        "version_digest": None,
        "version_status": None,
        "current_batch_id": None,
        "review_id": None,
        "reviewed_version_id": None,
        "units": [],
        "unit_totals": {},
        "source_total": "0.00",
        "allocated_total": "0.00",
        "remainder_total": "0",
        "unallocated": "0.00",
        "approved": "0.00",
        "balanced": False,
        "rows": [],
        "reason": reason,
    }


def build(store, payload: dict | None = None) -> dict:
    payload = payload or {}
    batch = _batch(store, payload)
    version = store.version(batch["version_id"])
    latest_version = store.latest_version()
    latest_batch = store.head_batch("allocation")
    review = store.review_for_batch(batch["batch_id"])
    summary = core.summarize_batch(batch)
    remainder_total = Decimal(summary["remainder_total"])
    tail_abs_total = plan_tail_abs(batch)
    approved = Decimal(str(payload.get("approved") or summary["source_total"]))
    units = summary["units"]
    balanced = Decimal(summary["allocated_total"]) + Decimal(summary["unallocated"]) == approved and remainder_total == Decimal(0)
    return {
        "status": "READY",
        "input_count": len(summary["rows"]),
        "valid_count": len(summary["rows"]),
        "quarantine_count": 0,
        "run_id": "run-" + canonical_digest({"batch": batch["batch_id"]})[:12],
        "batch_id": batch["batch_id"],
        "batch_status": batch["status"],
        "parent_batch_id": batch["parent_batch_id"],
        "version_id": version["version_id"],
        "version_seq": version["seq"],
        "version_digest": version["digest"],
        "version_status": "current" if latest_version and latest_version["version_id"] == version["version_id"] else "superseded",
        "current_batch_id": latest_batch["batch_id"] if latest_batch else batch["batch_id"],
        "latest_version_id": latest_version["version_id"] if latest_version else None,
        "review_id": review["review_id"] if review else None,
        "reviewed_version_id": review["version_id"] if review else version["version_id"],
        "units": units,
        "unit_totals": summary["unit_totals"],
        "source_total": summary["source_total"],
        "base_yuan": summary["source_total"],
        "allocated_total": summary["allocated_total"],
        "remainder_total": summary["remainder_total"],
        "tail_total": summary["remainder_total"],
        "tail_abs_total": tail_abs_total,
        "unallocated": summary["unallocated"],
        "approved": str(approved),
        "balanced": balanced,
        "rows": summary["rows"],
        "reason": "OK" if balanced else "DIFF",
    }


def plan_tail_abs(batch: dict) -> str:
    plan = batch["result"]
    total = Decimal(0)
    for row in plan.get("rows", []):
        value = row.get("remainder", "0")
        total += abs(Decimal(str(value or 0)))
    return str(total)


def export_text(store, payload: dict | None = None) -> tuple[str, dict]:
    payload = payload or {}
    data = build(store, payload)
    text = core.batch_csv(
        data["rows"],
        source_total=data["source_total"],
        allocated_total=data["allocated_total"],
        remainder_total=data["remainder_total"],
        unallocated=data["unallocated"],
        units=data["units"],
    )
    return text, data


def execute(payload: dict | None = None, store=None, rule_version: str = "2026.11") -> dict:
    payload = payload or {}
    if store is None:
        return core.run(payload)
    data = build(store, payload)
    data["csv"] = core.batch_csv(
        data["rows"],
        source_total=data["source_total"],
        allocated_total=data["allocated_total"],
        remainder_total=data["remainder_total"],
        unallocated=data["unallocated"],
        units=data["units"],
    )
    return data


def run(payload):
    return core.run(payload)
