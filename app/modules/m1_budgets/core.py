from __future__ import annotations

from decimal import Decimal, InvalidOperation

SAMPLE = {
    "rows": [
        {"project_id": "000101", "amount": "1,200.50", "unit": "元", "weight": "2", "cap": "900"},
        {"project_id": "9007199254740993", "amount": "0.08", "unit": "万元", "weight": "1", "cap": "800"},
        {"project_id": "BAD", "amount": "N/A", "unit": "元", "weight": "-1", "cap": "0"},
    ]
}
NULLS = {"", "N/A", "NULL", "-"}
# 采信口径：万元一律折算为元后再入账，页面与导出同时保留来源单位。
UNITS = {"元": Decimal("1"), "万元": Decimal("10000")}
CENT = Decimal("0.01")


def money(value, unit="元") -> Decimal:
    """把来源金额折算为元，保留两位小数。"""
    unit = str(unit).strip() or "元"
    if unit not in UNITS:
        raise ValueError("INVALID_UNIT")
    text = str(value).strip()
    if text in NULLS:
        raise ValueError("EMPTY_VALUE")
    try:
        amount = Decimal(text.replace(",", ""))
    except InvalidOperation:
        raise ValueError("INVALID_VALUE")
    if not amount.is_finite():
        raise ValueError("INVALID_VALUE")
    amount *= UNITS[unit]
    return amount.quantize(CENT)


def parse(rows):
    """返回 (有效行, 隔离行)。金额统一为元，来源单位与原始值一并保留。"""
    good = []
    bad = []
    seen: dict[str, dict] = {}
    for line, row in enumerate(rows, 2):
        try:
            if not isinstance(row, dict):
                raise ValueError("BAD_COLUMNS")
            project_id = str(row["project_id"])
            unit = str(row.get("unit", "元")).strip() or "元"
            if unit not in UNITS:
                raise ValueError("INVALID_UNIT")
            # 兼容旧批次：cap 默认按元解释；如需按来源单位解释可显式给 cap_unit。
            cap_unit = str(row.get("cap_unit", "元")).strip() or "元"
            source_amount = row["amount"]
            amount = money(source_amount, unit)
            cap = money(row["cap"], cap_unit)
            weight = Decimal(str(row["weight"]))
            if not weight.is_finite():
                raise ValueError("INVALID_VALUE")
            if weight < 0:
                raise ValueError("NEGATIVE_WEIGHT")
            if amount < 0 or cap < 0:
                raise ValueError("NEGATIVE_AMOUNT")
            item = {
                "project_id": project_id,
                "amount": amount,
                "amount_yuan": amount,
                "unit": unit,
                "source_unit": unit,
                "source_amount": str(source_amount),
                "weight": weight,
                "cap": cap,
                "cap_yuan": cap,
                "source_line": line,
            }
            if project_id in seen:
                # 同一业务键只采信首行，其余行（无论相同还是冲突）都隔离留证。
                raise ValueError("DUPLICATE_CONFLICT")
            seen[project_id] = item
            good.append(item)
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            code = exc.args[0] if isinstance(exc, ValueError) and exc.args else "INVALID_VALUE"
            if isinstance(exc, KeyError):
                code = "MISSING_FIELD"
            bad.append({"source_line": line, "reason": str(code), "raw": row})
    return good, bad


def _decimals(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def unit_text(unit: str, value: Decimal) -> str:
    """按来源单位输出金额文本：元保留两位，万元保留六位。"""
    if unit == "万元":
        return format(value.quantize(Decimal("0.000001")), "f")
    return format(value.quantize(CENT), "f")


def empty_unit_totals() -> dict:
    return {unit: Decimal(0) for unit in UNITS}


def summarize(good, bad, input_count: int) -> dict:
    total = sum((row["amount"] for row in good), Decimal(0))
    unit_totals = empty_unit_totals()
    for row in good:
        unit_totals[row["unit"]] += row["amount"] / UNITS[row["unit"]]
    return {
        "status": "READY",
        "input_count": input_count,
        "valid_count": len(good),
        "quarantine_count": len(bad),
        "total_yuan": str(total.quantize(CENT)),
        "total_wan": str((total / UNITS["万元"]).quantize(Decimal("0.000001"))),
        "unit_totals": {unit: unit_text(unit, value) for unit, value in unit_totals.items()},
        "units": sorted(unit_totals),
        "first_id": good[0]["project_id"] if good else None,
        "reason": bad[0]["reason"] if bad else "OK",
    }


def snapshot(good, bad, rule_version: str) -> dict:
    """生成可持久化的采信快照（金额一律以元记录为字符串）。"""
    rows = [
        {
            "project_id": row["project_id"],
            "unit": row["unit"],
            "source_unit": row["source_unit"],
            "source_amount": row["source_amount"],
            "amount_yuan": str(row["amount_yuan"]),
            "weight": str(row["weight"]),
            "cap_yuan": str(row["cap_yuan"]),
            "source_line": row["source_line"],
            "rule_version": rule_version,
        }
        for row in good
    ]
    quarantined = [
        {
            "source_line": item["source_line"],
            "reason": item["reason"],
            "raw": item["raw"],
            "rule_version": rule_version,
        }
        for item in bad
    ]
    return {"rows": rows, "quarantined": quarantined, "rule_version": rule_version}


def totals_from_snapshot(snapshot: dict) -> dict:
    """按来源单位汇总已采信金额，同时给出折算到元/万元的总额。"""
    unit_totals = empty_unit_totals()
    total = Decimal(0)
    for row in snapshot.get("rows", []):
        unit = row["source_unit"]
        amount = Decimal(row["amount_yuan"])
        total += amount
        unit_totals[unit] += amount / UNITS[unit]
    return {
        "unit_totals": {unit: unit_text(unit, value) for unit, value in unit_totals.items()},
        "units": sorted(unit_totals),
        "total_yuan": str(total.quantize(CENT)),
        "total_wan": str((total / UNITS["万元"]).quantize(Decimal("0.000001"))),
    }


def summarize_snapshot(snapshot: dict, input_count: int | None = None) -> dict:
    """从已保存快照还原摘要，供页面与旧批次回看时使用。"""
    rows = snapshot.get("rows", [])
    quarantined = snapshot.get("quarantined", [])
    totals = totals_from_snapshot(snapshot)
    if input_count is None:
        input_count = len(rows) + len(quarantined)
    return {
        "status": "READY",
        "input_count": input_count,
        "valid_count": len(rows),
        "quarantine_count": len(quarantined),
        "first_id": rows[0]["project_id"] if rows else None,
        "reason": quarantined[0]["reason"] if quarantined else "OK",
        "rule_version": snapshot.get("rule_version"),
        **totals,
    }


def ingest(payload: dict, rule_version: str = "2026.11"):
    rows = payload.get("rows", SAMPLE["rows"])
    good, bad = parse(rows)
    return summarize(good, bad, len(rows)), snapshot(good, bad, rule_version)


def run(payload):
    result, _ = ingest(payload)
    return result
