from __future__ import annotations

import csv
import io
from decimal import Decimal

from app.manifest import canonical_digest, run_id

SAMPLE = {
    "store": None,
    "version": 2,
    "expected_version": 2,
    "rows": [
        {"project_id": "A", "amount": "300.00", "reason": "CAP"},
        {"project_id": "B", "amount": "420.00", "reason": "WEIGHT"},
        {"project_id": "C", "amount": "280.00", "reason": "WEIGHT"},
    ],
    "approved": "1000.00",
}

EXPORT_COLUMNS = [
    "project_id", "unit", "source_amount", "amount_yuan",
    "allocated", "remainder", "reason",
]


def csv_text(rows):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=["project_id", "amount", "reason"], lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()


def _field(row: dict, *names, default=""):
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return default


def batch_csv(rows, *, source_total=None, allocated_total=None,
              remainder_total=None, unallocated=None, units=None) -> str:
    """导出证据清单：每行保留来源单位、折算金额、分摊值与尾差；末尾给出合计。"""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({
            "project_id": row.get("project_id", ""),
            "unit": _field(row, "unit", "source_unit"),
            "source_amount": _field(row, "source_amount", "amount"),
            "amount_yuan": _field(row, "amount_yuan"),
            "allocated": _field(row, "allocated"),
            "remainder": _field(row, "remainder"),
            "reason": _field(row, "reason", default="OK"),
        })
    if source_total is not None or allocated_total is not None or remainder_total is not None:
        writer.writerow({
            "project_id": "TOTAL",
            "unit": "/".join(units or []),
            "source_amount": "" if source_total is None else str(source_total),
            "amount_yuan": "" if source_total is None else str(source_total),
            "allocated": "" if allocated_total is None else str(allocated_total),
            "remainder": "" if remainder_total is None else str(remainder_total),
            "reason": "" if unallocated in (None, "", "0", "0.00") else f"UNALLOCATED={unallocated}",
        })
    return out.getvalue()


def summarize_batch(batch: dict) -> dict:
    """从分摊批次结果得到单位、总额与尾差，供页面与导出共用。"""
    plan = batch["result"]
    rows = plan.get("rows", [])
    return {
        "rows": [
            {
                "project_id": row["project_id"],
                "unit": row.get("unit", "元"),
                "source_amount": row.get("source_amount"),
                "amount_yuan": row.get("amount_yuan"),
                "allocated": row.get("allocated"),
                "remainder": row.get("remainder"),
                "reason": "OK",
            }
            for row in rows
        ],
        "unit_totals": plan.get("unit_totals", {}),
        "units": sorted(plan.get("unit_totals", {}).keys()),
        "source_total": plan.get("base_yuan"),
        "allocated_total": plan.get("allocated_total"),
        "remainder_total": plan.get("remainder_total"),
        "unallocated": plan.get("unallocated"),
    }


def run(payload):
    d = SAMPLE | payload
    if int(d["version"]) != int(d["expected_version"]):
        raise ValueError("STALE_VERSION")
    rows = d["rows"]
    total = sum((Decimal(x["amount"]) for x in rows), Decimal(0))
    approved = Decimal(d["approved"])
    digest = canonical_digest(rows)
    rid = run_id(rows)
    remainder_total = sum((Decimal(str(x.get("remainder", "0") or 0)) for x in rows), Decimal(0))
    result = {
        "input_count": len(rows),
        "valid_count": len(rows),
        "quarantine_count": 0,
        "allocated_total": str(total),
        "approved": str(approved),
        "remainder_total": str(remainder_total),
        "units": sorted({str(x.get("unit", "元")) for x in rows}),
        "balanced": total == approved,
    }
    store = d.get("store")
    if store:
        rid = store.record(digest, rid, result)["run_id"]
    return {
        "status": "READY",
        "run_id": rid,
        "digest": digest,
        "csv": csv_text(rows),
        "batch_csv": batch_csv(
            rows, source_total=d.get("source_total", total), allocated_total=total,
            remainder_total=remainder_total, unallocated=d.get("unallocated"),
            units=result["units"],
        ),
        **result,
        "reason": "OK" if total == approved else "DIFF",
    }
