from __future__ import annotations

from decimal import Decimal

from app.errors import DomainError
from app.manifest import canonical_digest

from . import core

SCOPE = "remainder"


def _resolve_batch(store, payload: dict) -> dict:
    batch_id = payload.get("batch_id")
    if batch_id:
        batch = store.batch(str(batch_id))
        if batch is None:
            raise DomainError("NOT_FOUND", "分摊批次不存在")
        return batch
    return store.head_batch("allocation")


def _payload(batch: dict, version: dict, latest_batch: dict | None,
             latest_version: dict | None) -> dict:
    plan = batch["result"]
    total = plan["allocated_total"]
    rows = plan.get("rows", [])
    units = plan.get("unit_totals", {})
    return {
        "status": "READY",
        "input_count": len(rows),
        "valid_count": len(rows),
        "quarantine_count": 0,
        "allocated": plan.get("allocated", {}),
        "amounts": plan.get("allocated", {}),
        "allocated_total": total,
        "total": total,
        "unallocated": plan.get("unallocated", "0.00"),
        "remainders": plan.get("remainders", {}),
        "tail": plan.get("tail", plan.get("remainders", {})),
        "remainder_total": plan.get("remainder_total", "0"),
        "tail_total": plan.get("tail_total", plan.get("remainder_total", "0")),
        "tail_count": plan.get("tail_count", 0),
        "base_yuan": plan.get("base_yuan"),
        "unit_totals": units,
        "units": sorted(units.keys()),
        "rows": rows,
        "batch_id": batch["batch_id"],
        "version_id": version["version_id"],
        "version_seq": version["seq"],
        "version_digest": version["digest"],
        "version_status": "current" if latest_version and latest_version["version_id"] == version["version_id"] else "superseded",
        "reviewed_version_id": version["version_id"],
        "reviewed_at_version": version["version_id"],
        "latest_version_id": latest_version["version_id"] if latest_version else None,
        "current_batch_id": latest_batch["batch_id"] if latest_batch else batch["batch_id"],
        "latest_batch_id": latest_batch["batch_id"] if latest_batch else None,
        "batch_status": batch["status"],
        "superseded": batch["status"] != "active",
        "reason": "OK" if Decimal(plan.get("remainder_total", "0")) == Decimal(0) else "TAIL_DIFF",
    }


def resolve(payload: dict | None = None, store=None, rule_version: str = "2026.11",
            persist: bool = True) -> dict:
    """出具尾差复核：始终按批次绑定的采信版本回看当时采信值。"""
    payload = payload or {}
    if store is None or not persist:
        return core.run(payload)
    batch = _resolve_batch(store, payload)
    if batch is None:
        # 没有批次时只返回样例预览，不生成无版本依据的复核记录。
        preview = core.run(payload)
        preview["persisted"] = False
        preview["notice"] = "尚无分摊批次，请先计算分摊"
        return preview
    version = store.version(batch["version_id"])
    latest_batch = store.head_batch("allocation")
    latest_version = store.latest_version()
    result = _payload(batch, version, latest_batch, latest_version)
    review_id = "tail-" + canonical_digest({"batch": batch["batch_id"]})[:12]
    result["review_id"] = review_id
    summary = {
        "batch_id": batch["batch_id"],
        "version_id": version["version_id"],
        "allocated_total": result["allocated_total"],
        "remainder_total": result["remainder_total"],
        "unit_totals": result["unit_totals"],
        "reason": result["reason"],
    }
    record, reused = store.save_review(
        SCOPE, batch["batch_id"], version["version_id"], review_id, summary,
    )
    result["reused"] = reused
    result["persisted"] = True
    result["reviewed_at"] = record["created_at"]
    return result


def current(store, payload: dict | None = None) -> dict:
    payload = payload or {}
    if store is None:
        return core.run(payload)
    batch_id = payload.get("batch_id")
    batch = store.batch(str(batch_id)) if batch_id else store.head_batch("allocation")
    if batch is None:
        return core.run(payload)
    version = store.version(batch["version_id"])
    result = _payload(batch, version, store.head_batch("allocation"), store.latest_version())
    result["persisted"] = False
    return result


def review(store, review_id: str) -> dict:
    record = store.review(review_id)
    if record is None:
        raise DomainError("NOT_FOUND", "尾差复核记录不存在")
    batch = store.batch(record["batch_id"])
    version = store.version(record["version_id"])
    result = _payload(batch, version, store.head_batch("allocation"), store.latest_version())
    result.update({
        "review_id": record["review_id"],
        "reviewed_at": record["created_at"],
        "persisted": False,
        "reused": True,
    })
    return result


def reviews(store, batch_id: str | None = None) -> list[dict]:
    """已出具的复核清单；按批次绑定当时采信版本，不随上游修正改写。"""
    values = []
    for record in store.reviews(SCOPE):
        if batch_id and record["batch_id"] != batch_id:
            continue
        summary = record["result"]
        values.append({
            "review_id": record["review_id"],
            "batch_id": record["batch_id"],
            "version_id": record["version_id"],
            "reviewed_at": record["created_at"],
            "allocated_total": summary.get("allocated_total"),
            "remainder_total": summary.get("remainder_total"),
            "unit_totals": summary.get("unit_totals", {}),
            "reason": summary.get("reason"),
        })
    return values


def run(payload):
    return core.run(payload)
