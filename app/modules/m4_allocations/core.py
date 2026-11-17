from __future__ import annotations

from decimal import Decimal

from app.modules.m5_remainders.core import largest_remainder

SAMPLE = {
    "total": "1000.00",
    "items": [
        {"id": "A", "weight": "5", "cap": "300.00"},
        {"id": "B", "weight": "3", "cap": "500.00"},
        {"id": "C", "weight": "2", "cap": "500.00"},
    ],
}
CENT = Decimal("0.01")


def allocate(total, items):
    """先迭代封顶，再按有效权重分配；返回 (精确分摊, 未分配余额)。"""
    remaining = Decimal(str(total))
    pending = {
        str(item["id"]): {
            "weight": Decimal(str(item["weight"])),
            "cap": Decimal(str(item["cap"])),
        }
        for item in items
    }
    out = {key: Decimal(0) for key in pending}
    while pending and remaining > 0:
        total_weight = sum((item["weight"] for item in pending.values()), Decimal(0))
        if total_weight <= 0:
            break
        capped = []
        for key, item in pending.items():
            proposal = remaining * item["weight"] / total_weight
            room = item["cap"] - out[key]
            if proposal >= room:
                out[key] += room
                capped.append(key)
        if not capped:
            for key, item in pending.items():
                out[key] += remaining * item["weight"] / total_weight
            remaining = Decimal(0)
            break
        remaining = Decimal(str(total)) - sum(out.values())
        for key in capped:
            pending.pop(key)
    return out, remaining


def _plain(value: Decimal, places: str = "0.000001") -> str:
    value = value.quantize(Decimal(places))
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT)


def items_from_snapshot(snapshot: dict) -> list[dict]:
    """把采信快照转成分摊条目；金额与封顶额一律使用折算后的元。"""
    items = []
    for row in snapshot.get("rows", []):
        items.append({
            "id": row["project_id"],
            "weight": row["weight"],
            "cap": row.get("cap_yuan", row.get("cap", "0")),
            "unit": row.get("source_unit", row.get("unit", "元")),
            "source_amount": row.get("source_amount", row.get("amount_yuan")),
            "amount_yuan": row.get("amount_yuan", row.get("amount")),
        })
    return items


def allocation_base(snapshot: dict) -> Decimal:
    """分摊基数 = 当前采信版本的预算合计（元）。"""
    return sum((Decimal(row.get("amount_yuan", "0")) for row in snapshot.get("rows", [])), Decimal(0))


def settle(exact: dict[str, Decimal]) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    """按最大余额法把精确分摊落到分，并给出每行的舍入尾差。"""
    allocated_total = sum(exact.values(), Decimal(0))
    if not exact:
        return {}, {}
    if allocated_total == 0:
        rounded = {key: Decimal("0.00") for key in exact}
    else:
        shares = {key: value / allocated_total for key, value in exact.items()}
        rounded = largest_remainder(allocated_total, shares)
    remainders = {key: rounded[key] - exact[key] for key in exact}
    return rounded, remainders


def plan(snapshot: dict, total=None) -> dict:
    """根据采信版本计算分摊与尾差，保持行顺序并输出来源单位。"""
    items = items_from_snapshot(snapshot)
    base = allocation_base(snapshot)
    exact, unallocated = allocate(base if total is None else total, items)
    rounded, remainders = settle(exact)

    rows = []
    for item in items:
        key = item["id"]
        rows.append({
            "project_id": key,
            "unit": item["unit"],
            "source_amount": item["source_amount"],
            "amount_yuan": str(cents(Decimal(str(item["amount_yuan"])))),
            "weight": str(Decimal(str(item["weight"]))),
            "cap_yuan": str(cents(Decimal(str(item["cap"])))),
            "allocated": str(rounded.get(key, Decimal("0.00"))),
            "allocated_exact": _plain(exact.get(key, Decimal(0))),
            "remainder": _plain(remainders.get(key, Decimal(0))),
            "remainder_yuan": str(cents(remainders.get(key, Decimal(0)))),
        })
    allocated_total = sum(rounded.values(), Decimal(0))
    remainder_total = sum(remainders.values(), Decimal(0))
    unit_totals = _unit_totals(snapshot)
    return {
        "base_yuan": str(cents(base)),
        "total_yuan": str(cents(base if total is None else Decimal(str(total)))),
        "allocated": {key: str(value) for key, value in rounded.items()},
        "allocated_exact": {key: _plain(value) for key, value in exact.items()},
        "allocated_total": str(cents(allocated_total)),
        "total": str(cents(allocated_total)),
        "unallocated": str(cents(unallocated)),
        "remainders": {key: _plain(value) for key, value in remainders.items()},
        "tail": {key: _plain(value) for key, value in remainders.items()},
        "remainder_total": _plain(remainder_total),
        "tail_total": _plain(remainder_total),
        "tail_count": sum(1 for value in remainders.values() if value != 0),
        "tail_abs_total": _plain(sum((abs(value) for value in remainders.values()), Decimal(0))),
        "rows": rows,
        "item_count": len(rows),
        "unit_totals": unit_totals,
        "units": sorted(unit_totals),
    }


def _unit_totals(snapshot: dict) -> dict:
    from app.modules.m1_budgets.core import unit_text

    totals: dict[str, Decimal] = {}
    for row in snapshot.get("rows", []):
        unit = row.get("source_unit", row.get("unit", "元"))
        amount = Decimal(row.get("amount_yuan", "0"))
        converted = amount / Decimal("10000") if unit == "万元" else amount
        totals[unit] = totals.get(unit, Decimal(0)) + converted
    return {unit: unit_text(unit, value) for unit, value in totals.items()}


def plan_items(total, items) -> dict:
    """兼容入口：直接给定总额与条目，输出与采信版本分摊一致的字段。"""
    exact, unallocated = allocate(total, items)
    rounded, remainders = settle(exact)
    units: dict[str, Decimal] = {}
    rows = []
    for item in items:
        key = str(item["id"])
        unit = str(item.get("unit", "元"))
        units[unit] = units.get(unit, Decimal(0)) + (
            Decimal(str(item.get("amount", "0"))) if item.get("amount") is not None else Decimal(0)
        )
        rows.append({
            "project_id": key,
            "unit": unit,
            "source_amount": item.get("source_amount", item.get("amount")),
            "amount_yuan": str(cents(Decimal(str(item.get("amount", "0"))))),
            "weight": str(Decimal(str(item["weight"]))),
            "cap_yuan": str(cents(Decimal(str(item["cap"])))),
            "allocated": str(rounded.get(key, Decimal("0.00"))),
            "allocated_exact": _plain(exact.get(key, Decimal(0))),
            "remainder": _plain(remainders.get(key, Decimal(0))),
            "remainder_yuan": str(cents(remainders.get(key, Decimal(0)))),
        })
    allocated_total = sum(rounded.values(), Decimal(0))
    remainder_total = sum(remainders.values(), Decimal(0))
    return {
        "base_yuan": str(cents(Decimal(str(total)))),
        "total_yuan": str(cents(Decimal(str(total)))),
        "allocated": {key: str(value) for key, value in rounded.items()},
        "allocated_exact": {key: _plain(value) for key, value in exact.items()},
        "allocated_total": str(cents(allocated_total)),
        "total": str(cents(allocated_total)),
        "unallocated": str(cents(unallocated)),
        "remainders": {key: _plain(value) for key, value in remainders.items()},
        "tail": {key: _plain(value) for key, value in remainders.items()},
        "remainder_total": _plain(remainder_total),
        "tail_total": _plain(remainder_total),
        "tail_count": sum(1 for value in remainders.values() if value != 0),
        "tail_abs_total": _plain(sum((abs(value) for value in remainders.values()), Decimal(0))),
        "rows": rows,
        "item_count": len(rows),
        "unit_totals": {unit: str(value.quantize(CENT)) for unit, value in units.items()},
        "units": sorted(units),
    }


def run(payload):
    """兼容原有单模块入口：直接给定总额与条目。"""
    d = SAMPLE | payload
    plan = plan_items(d["total"], d["items"])
    return {
        "status": "READY",
        "input_count": plan["item_count"],
        "valid_count": plan["item_count"],
        "quarantine_count": 0,
        "allocated": plan["allocated"],
        "allocated_total": plan["allocated_total"],
        "unallocated": plan["unallocated"],
        "remainders": plan["remainders"],
        "remainder_total": plan["remainder_total"],
        "rows": plan["rows"],
        "unit_totals": plan["unit_totals"],
        "units": plan["units"],
        "reason": "OK" if Decimal(plan["unallocated"]) == 0 else "CAP_LIMIT",
    }
