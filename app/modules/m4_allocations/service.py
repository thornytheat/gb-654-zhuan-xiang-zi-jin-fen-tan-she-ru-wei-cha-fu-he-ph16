from __future__ import annotations

from decimal import Decimal

from app.errors import DomainError
from app.manifest import canonical_digest

from . import core

SCOPE = "allocation"


def _matches(version: dict, expected) -> bool:
    if version is None:
        return False
    return str(expected) in {version["version_id"], version["digest"], str(version["seq"])}


def _resolve_version(store, payload: dict) -> dict:
    requested = payload.get("version_id")
    if requested in (None, ""):
        requested = payload.get("expected_version")
    if requested in (None, ""):
        requested = payload.get("version")
    latest = store.latest_version()
    if latest is None:
        raise DomainError("NO_VERSION", "尚无采信版本，请先导入预算")
    if requested in (None, ""):
        return latest
    version = store.version(str(requested))
    if version is None:
        candidate = store.version_by_digest(str(requested))
        if candidate is not None:
            version = candidate
        elif str(requested).isdigit():
            version = next((v for v in store.versions() if v["seq"] == int(str(requested))), None)
    if version is None:
        raise DomainError("STALE_VERSION", "提交版本已过期或不存在，请基于最新采信版本重试")
    # 过期版本拒绝发布：只有当前最新采信版本可以生成新的分摊批次。
    if version["version_id"] != latest["version_id"]:
        raise DomainError("STALE_VERSION", "提交版本已过期，请基于最新采信版本重新计算分摊")
    return version


def _batch_idempotency_key(version: dict, total: str | None, rule_version: str) -> str:
    normalized_total = None
    if total is not None:
        value = Decimal(str(total))
        normalized_total = format(value.normalize(), "f")
    return canonical_digest({
        "kind": "allocation-batch",
        "scope": SCOPE,
        "version_digest": version["digest"],
        "version_id": version["version_id"],
        "total": normalized_total,
        "rule_version": rule_version,
    })


def _batch_payload(batch: dict, version: dict, previous_version: dict | None,
                   latest_version: dict | None, latest_batch: dict | None) -> dict:
    result = dict(batch["result"])
    result.update({
        "batch_id": batch["batch_id"],
        "batch_seq": batch["seq"],
        "seq": batch["seq"],
        "batch_status": batch["status"],
        "is_current": batch["is_current"],
        "parent_batch_id": batch["parent_batch_id"],
        "idempotency_key": batch["idempotency_key"],
        "created_at": batch["created_at"],
        "version_id": version["version_id"],
        "version_seq": version["seq"],
        "version_digest": version["digest"],
        "previous_version_id": previous_version["version_id"] if previous_version else version["parent_version_id"],
        "next_version_id": latest_version["version_id"] if latest_version and latest_version["version_id"] != version["version_id"] else None,
        "version_status": "current" if latest_version and latest_version["version_id"] == version["version_id"] else "superseded",
        "current_batch_id": latest_batch["batch_id"] if latest_batch else batch["batch_id"],
        "detail_url": f"/api/allocations/batches/{batch['batch_id']}",
        "is_current_batch": batch["is_current"],
        "superseded": batch["status"] != "active",
    })
    result.setdefault("status", "READY")
    result.setdefault("reason", "OK")
    return result


def _items_from_payload(payload: dict) -> list[dict] | None:
    if payload.get("items") is not None:
        return payload["items"]
    rows = payload.get("rows")
    if rows is None:
        return None
    items = []
    for row in rows:
        unit = str(row.get("unit", "元"))
        amount = row.get("amount", row.get("amount_yuan"))
        cap = row.get("cap", row.get("cap_yuan", "0"))
        try:
            cap_value = Decimal(str(cap))
        except Exception:
            cap_value = Decimal(0)
        items.append({
            "id": str(row.get("project_id", row.get("id"))),
            "weight": str(row.get("weight", "1")),
            "cap": str(cap_value),
            "unit": unit,
            "source_amount": row.get("source_amount", amount),
            "amount": amount,
        })
    return items


def calculate(payload: dict | None = None, store=None, rule_version: str = "2026.11",
              persist: bool = True) -> dict:
    payload = payload or {}
    items = _items_from_payload(payload)
    if items is not None and not payload.get("version_id"):
        # 直接给总额与条目的兼容入口：只做测算，不绑定采信版本、不落批次。
        plan = core.plan_items(payload.get("total", core.SAMPLE["total"]), items)
        plan.update({"status": "READY", "persisted": False,
                     "reason": "OK" if Decimal(plan["unallocated"]) == 0 else "CAP_LIMIT"})
        return plan
    if store is None or not persist:
        return core.run(payload)
    if store.latest_version() is None:
        # 尚无采信版本：只给样例预览，不生成无版本依据的批次。
        preview = core.run(payload)
        preview["persisted"] = False
        preview["notice"] = "尚无采信版本，请先导入预算"
        return preview
    version = _resolve_version(store, payload)
    total = payload.get("total")
    if total is not None:
        try:
            Decimal(str(total))
        except Exception:
            raise DomainError("INVALID_VALUE", "分摊总额不是有效金额")
    plan = core.plan(version["snapshot"], total)
    plan.update({
        "status": "READY",
        "version_id": version["version_id"],
        "version_seq": version["seq"],
        "version_digest": version["digest"],
        "digest": version["digest"],
        "version_status": "current",
        "rule_version": version["rule_version"],
    })
    if payload.get("dry_run"):
        plan["persisted"] = False
        return plan

    key = str(payload.get("idempotency_key") or _batch_idempotency_key(version, total, rule_version))
    previous_head = store.head_batch(SCOPE)
    try:
        batch, reused = store.save_batch(
            SCOPE, version["version_id"], key, plan,
            parent_batch_id=payload.get("parent_batch_id"),
        )
    except Exception:
        # 切换失败保留旧版：事务已回滚，这里再把指针显式指回已保存位置。
        if previous_head is not None:
            try:
                store.restore_batch(previous_head["batch_id"])
            except Exception:
                pass
        raise DomainError(
            "BATCH_WRITE_FAILED",
            "批次保存失败，已恢复至上一已保存批次"
            + (f" {previous_head['batch_id']}" if previous_head else ""),
        )
    latest_version = store.latest_version()
    latest_batch = store.head_batch(SCOPE)
    result = _batch_payload(batch, version, None, latest_version, latest_batch)
    result["reused"] = reused
    result["persisted"] = True
    # 同版本同参数重试：指针回到已保存批次，但历史结果保持不变。
    result["previous_batch_id"] = batch["parent_batch_id"] if reused else (
        previous_head["batch_id"] if previous_head else None
    )
    result["returned_to_saved"] = reused and previous_head is not None and previous_head["batch_id"] != batch["batch_id"]
    return result


def current(payload: dict | None = None, store=None) -> dict:
    payload = payload or {}
    batch_id = payload.get("batch_id")
    if batch_id:
        return review(payload, store)
    if store is None:
        return core.run(payload)
    batch = store.head_batch(SCOPE)
    if batch is None:
        # 尚无批次时只做预览，不写入批次。
        return calculate(payload, store=store, persist=False)
    version = store.version(batch["version_id"])
    latest_version = store.latest_version()
    latest_batch = store.head_batch(SCOPE)
    result = _batch_payload(batch, version, None, latest_version, latest_batch)
    result["reused"] = True
    result["persisted"] = False
    result["stale"] = bool(latest_version and latest_version["version_id"] != version["version_id"])
    if result["stale"]:
        # 上游已修正但尚未重算：提示需要生成新批次，不自动改写旧批次。
        result["pending_version_id"] = latest_version["version_id"]
        result["notice"] = "采信版本已更新，需重算分摊基数"
    return result


def review(payload: dict, store) -> dict:
    batch = store.batch(str(payload["batch_id"]))
    if batch is None:
        raise DomainError("NOT_FOUND", "批次不存在")
    version = store.version(batch["version_id"])
    latest_version = store.latest_version()
    latest_batch = store.head_batch(SCOPE)
    result = _batch_payload(batch, version, None, latest_version, latest_batch)
    result["reused"] = True
    result["persisted"] = False
    result["stale"] = bool(latest_version and latest_version["version_id"] != version["version_id"])
    if batch["status"] != "active":
        result["superseded"] = True
        result["superseded_by"] = latest_batch["batch_id"] if latest_batch else None
    else:
        result["superseded"] = False
    if result["stale"]:
        result["pending_version_id"] = latest_version["version_id"]
        result["notice"] = "采信版本已更新，需重算分摊基数"
    if result["superseded"]:
        result["notice"] = "该批次已被重算替代，仅供回看，未改写历史结果"
    return result


def batches(store) -> list[dict]:
    latest_version = store.latest_version()
    latest_batch = store.head_batch(SCOPE)
    values = []
    for batch in store.batches(SCOPE):
        version = store.version(batch["version_id"])
        values.append(_batch_payload(batch, version, None, latest_version, latest_batch))
    return values


def restore(batch_id: str, store) -> dict:
    target = store.batch(batch_id)
    if target is None:
        raise DomainError("NOT_FOUND", "批次不存在")
    restored = store.restore_batch(batch_id)
    version = store.version(restored["version_id"])
    latest_version = store.latest_version()
    result = _batch_payload(restored, version, None, latest_version, store.head_batch(SCOPE))
    result["restored"] = True
    result["previous_batch_id"] = restored.get("previous_batch_id")
    result["reused"] = True
    return result
