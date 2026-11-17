from __future__ import annotations

import csv
import io

from app.errors import DomainError
from app.manifest import canonical_digest

from . import core


def _rows_from_payload(payload: dict) -> dict:
    """兼容 JSON 行数组与 CSV 文本两种导入方式，单位列按原值采信。"""
    if payload.get("rows") is not None:
        return payload
    text = payload.get("csv") or payload.get("content") or payload.get("text")
    if not text:
        return payload
    reader = csv.DictReader(io.StringIO(str(text).lstrip("﻿")))
    rows = []
    for row in reader:
        rows.append({key: (value if value is not None else "") for key, value in row.items()})
    return {**payload, "rows": rows}


def version_digest(snapshot: dict, rule_version: str) -> str:
    return canonical_digest({
        "kind": "budget-acceptance",
        "rule_version": rule_version,
        "snapshot": snapshot,
    })


def _matches(version: dict, expected) -> bool:
    if version is None:
        return False
    text = str(expected)
    return text in {version["version_id"], version["digest"], str(version["seq"])}


def check_expected_version(store, expected) -> None:
    """带版本号提交时，只接受当前最新采信版本，过期版本拒绝入账。"""
    if expected in (None, ""):
        return
    latest = store.latest_version()
    if not _matches(latest, expected):
        raise DomainError("STALE_VERSION", "提交版本已过期，请基于最新采信版本重试")


def _version_payload(version: dict, previous: dict | None, reused: bool, latest: dict | None) -> dict:
    is_current = latest is None or latest["version_id"] == version["version_id"]
    return {
        "version_id": version["version_id"],
        "version_seq": version["seq"],
        "version_digest": version["digest"],
        "digest": version["digest"],
        "previous_version_id": version["parent_version_id"],
        "parent_version_id": version["parent_version_id"],
        "next_version_id": None,
        "reused": reused,
        "version_status": "current" if is_current else "superseded",
        "is_current": is_current,
    }


def execute(payload: dict | None = None, store=None, rule_version: str = "2026.11",
            persist: bool = True) -> dict:
    payload = _rows_from_payload(payload or {})
    if store is None or not persist:
        return core.run(payload)
    check_expected_version(store, payload.get("expected_version", payload.get("expected_version_id")))
    result, snapshot = core.ingest(payload, rule_version)
    digest = version_digest(snapshot, rule_version)
    previous = store.latest_version()
    version, reused = store.save_version(
        snapshot, digest, rule_version,
        note=payload.get("note"),
    )
    latest = store.latest_version()
    result.update(_version_payload(version, previous, reused, latest))
    result["note"] = version["note"]
    result["rows"] = snapshot["rows"]
    result["quarantined"] = snapshot["quarantined"]
    result["version_count"] = len(store.versions())
    if reused:
        # 同一摘要重试不新增版本，指针保持在首次采信位置。
        result["reused_version"] = True
    return result


def preview(store, rule_version: str = "2026.11") -> dict:
    """页面只读展示：优先展示当前采信版本，否则展示样例预览（不写入）。"""
    latest = store.latest_version() if store is not None else None
    if latest is None:
        return core.run({})
    snapshot = latest["snapshot"]
    result = core.summarize_snapshot(snapshot)
    result.update({
        "version_id": latest["version_id"],
        "version_seq": latest["seq"],
        "version_digest": latest["digest"],
        "digest": latest["digest"],
        "version_status": "current",
        "previous_version_id": latest["parent_version_id"],
        "rows": snapshot["rows"],
        "quarantined": snapshot["quarantined"],
    })
    return result


def versions(store) -> list[dict]:
    if store is None:
        return []
    values = store.versions()
    for index, value in enumerate(values):
        value["next_version_id"] = values[index + 1]["version_id"] if index + 1 < len(values) else None
    return values
