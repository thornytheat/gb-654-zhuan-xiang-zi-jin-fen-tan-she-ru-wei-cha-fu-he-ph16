"""批次与采信版本的行为测试：修正、重算、回看、恢复与幂等。"""

from decimal import Decimal

V1 = {
    "rows": [
        {"project_id": "A", "amount": "100", "unit": "元", "weight": "1", "cap": "100"},
        {"project_id": "B", "amount": "0.01", "unit": "万元", "weight": "1", "cap": "100"},
    ]
}
V2 = {
    "rows": [
        {"project_id": "A", "amount": "100", "unit": "元", "weight": "1", "cap": "100"},
        {"project_id": "B", "amount": "0.02", "unit": "万元", "weight": "1", "cap": "100"},
    ]
}


def _import(client, payload=V1, **extra):
    return client.post("/api/budgets/import", json={**payload, **extra})


def _allocate(client, **payload):
    return client.post("/api/allocations/calculate", json=payload)


def _resolve(client, **payload):
    return client.post("/api/remainders/resolve", json=payload)


def test_units_normalized_and_totals_exposed(client):
    data = _import(client).json()
    assert data["total_yuan"] == "200.00"
    assert data["unit_totals"]["元"] == "100.00"
    assert data["unit_totals"]["万元"] == "0.010000"
    assert set(data["units"]) == {"元", "万元"}
    rows = {row["project_id"]: row for row in data["rows"]}
    assert rows["B"]["source_unit"] == "万元"
    assert rows["B"]["amount_yuan"] == "100.00"


def test_same_digest_reimport_reuses_version(client):
    first = _import(client).json()
    second = _import(client).json()
    assert first["version_id"] == second["version_id"]
    assert second["reused"] is True
    versions = client.get("/api/budgets/versions").json()["versions"]
    assert len(versions) == 1


def test_upstream_correction_appends_version(client):
    first = _import(client).json()
    corrected = _import(client, V2).json()
    assert corrected["version_id"] != first["version_id"]
    assert corrected["version_seq"] == first["version_seq"] + 1
    assert corrected["parent_version_id"] == first["version_id"]
    assert corrected["total_yuan"] == "300.00"
    assert corrected["version_status"] == "current"
    old = client.get(f"/api/budgets/versions/{first['version_id']}").json()
    assert old["version_status"] == "superseded"
    # 原地替换会改写审计：旧版本快照仍然保留当时的采信值。
    assert old["total_yuan"] == "200.00"
    assert old["rows"][1]["amount_yuan"] == "100.00"


def test_recalculation_keeps_old_batch_and_creates_new(client):
    first = _import(client).json()
    old_batch = _allocate(client).json()
    _import(client, V2)
    new_batch = _allocate(client).json()
    assert new_batch["batch_id"] != old_batch["batch_id"]
    assert new_batch["parent_batch_id"] == old_batch["batch_id"]
    assert new_batch["base_yuan"] == "300.00"
    assert old_batch["base_yuan"] == "200.00"
    old_review = client.get(f"/api/allocations/batches/{old_batch['batch_id']}").json()
    assert old_review["superseded"] is True
    assert old_review["allocated_total"] == old_batch["allocated_total"]
    assert old_review["version_id"] == first["version_id"]


def test_stale_version_rejected_for_publish(client):
    old = _import(client).json()
    _import(client, V2)
    response = _allocate(client, version_id=old["version_id"])
    assert response.status_code == 422
    assert response.json()["error"] == "STALE_VERSION"
    # 拒绝发布不产生批次。
    batches = client.get("/api/allocations/batches").json()["batches"]
    assert batches == []


def test_stale_version_rejected_on_import(client):
    old = _import(client).json()
    _import(client, V2)
    response = _import(client, {"rows": V2["rows"]}, expected_version=old["version_id"])
    assert response.status_code == 422
    assert response.json()["error"] == "STALE_VERSION"


def test_remainder_review_uses_bound_version(client):
    old_version = _import(client).json()
    old_batch = _allocate(client).json()
    _import(client, V2)
    new_batch = _allocate(client).json()
    old_review = _resolve(client, batch_id=old_batch["batch_id"]).json()
    new_review = _resolve(client).json()
    assert old_review["version_id"] == old_version["version_id"]
    assert old_review["allocated_total"] == old_batch["allocated_total"] == "200.00"
    assert old_review["superseded"] is True
    assert old_review["version_status"] == "superseded"
    assert new_review["batch_id"] == new_batch["batch_id"]
    assert new_review["allocated_total"] == new_batch["allocated_total"]
    # 最新来源纠正分摊，但旧复核仍按当时采信值回看。
    assert old_review["unit_totals"]["万元"] == "0.010000"
    assert new_review["unit_totals"]["万元"] == "0.020000"


def test_review_and_batch_retry_do_not_duplicate(client):
    _import(client)
    _allocate(client)
    first = _resolve(client).json()
    second = _resolve(client).json()
    assert first["review_id"] == second["review_id"]
    assert second["reused"] is True
    reviews = client.get("/api/runs").json()["reviews"]
    assert len(reviews) == 1
    batches = client.get("/api/allocations/batches").json()["batches"]
    assert len(batches) == 1


def test_restore_saved_batch_without_new_row(client):
    _import(client)
    old_batch = _allocate(client).json()
    _import(client, V2)
    new_batch = _allocate(client).json()
    restored = client.post(f"/api/allocations/batches/{old_batch['batch_id']}/restore").json()
    assert restored["batch_id"] == old_batch["batch_id"]
    assert restored["previous_batch_id"] == new_batch["batch_id"]
    head = client.get("/api/allocations").json()
    assert head["batch_id"] == old_batch["batch_id"]
    # 恢复只是切换指针，不新增批次、不改写历史结果。
    batches = client.get("/api/allocations/batches").json()["batches"]
    assert len(batches) == 2
    assert {batch["batch_id"] for batch in batches} == {old_batch["batch_id"], new_batch["batch_id"]}
    assert client.get(f"/api/allocations/batches/{new_batch['batch_id']}").json()["allocated_total"] == new_batch["allocated_total"]


def test_failed_calculation_keeps_saved_batch(client):
    _import(client)
    saved = _allocate(client).json()
    response = _allocate(client, version_id="ver-missing")
    assert response.status_code == 422
    head = client.get("/api/allocations").json()
    assert head["batch_id"] == saved["batch_id"]
    assert head["status"] == "READY"
    assert len(client.get("/api/allocations/batches").json()["batches"]) == 1


def test_write_failure_restores_saved_position(client, monkeypatch):
    from app.store import Store

    _import(client)
    saved = _allocate(client).json()

    def broken(self, scope, version_id, key, result, parent_batch_id=None):
        raise RuntimeError("write failed")

    monkeypatch.setattr(Store, "save_batch", broken)
    response = _allocate(client)
    assert response.status_code == 422
    assert response.json()["error"] == "BATCH_WRITE_FAILED"
    monkeypatch.undo()
    head = client.get("/api/allocations").json()
    assert head["batch_id"] == saved["batch_id"]
    assert head["allocated_total"] == saved["allocated_total"]
    assert len(client.get("/api/allocations/batches").json()["batches"]) == 1
    # 恢复后重试成功，且不重复入账。
    retried = _allocate(client).json()
    assert retried["batch_id"] == saved["batch_id"]
    assert retried["reused"] is True
    assert len(client.get("/api/allocations/batches").json()["batches"]) == 1


def test_export_shows_unit_total_and_remainder(client):
    _import(client)
    _allocate(client)
    text = client.get("/api/export/allocations.csv").text
    assert "unit" in text.splitlines()[0]
    assert "remainder" in text.splitlines()[0]
    assert "TOTAL" in text
    assert "万元" in text
    assert "200.00" in text


def test_export_old_batch_uses_its_own_version(client):
    _import(client)
    old_batch = _allocate(client).json()
    _import(client, V2)
    _allocate(client)
    text = client.get(f"/api/export/allocations.csv?batch_id={old_batch['batch_id']}").text
    assert "0.01" in text
    assert "0.02" not in text


def test_remainder_rounding_tail_is_reported(client):
    payload = {
        "rows": [
            {"project_id": "A", "amount": "10", "unit": "元", "weight": "1", "cap": "100"},
            {"project_id": "B", "amount": "10", "unit": "元", "weight": "1", "cap": "100"},
            {"project_id": "C", "amount": "10", "unit": "元", "weight": "1", "cap": "100"},
        ]
    }
    _import(client, payload)
    data = _allocate(client).json()
    # 30 元按 1/3 分配无法在分位整除，最大余额法补足，尾差合计归零。
    assert data["allocated_total"] == data["base_yuan"] == "30.00"
    assert data["remainder_total"] == "0"
    assert sum(int(value.replace(".", "")) for value in data["allocated"].values()) == 3000
    review = _resolve(client).json()
    assert review["remainder_total"] == "0"
    assert review["reason"] == "OK"


def test_rounding_tail_per_row_is_visible(client):
    payload = {
        "rows": [
            {"project_id": "A", "amount": "3.34", "unit": "元", "weight": "1", "cap": "100"},
            {"project_id": "B", "amount": "3.33", "unit": "元", "weight": "1", "cap": "100"},
            {"project_id": "C", "amount": "3.33", "unit": "元", "weight": "1", "cap": "100"},
        ]
    }
    _import(client, payload)
    data = _allocate(client).json()
    # 每行分摊 3.3333…，按分舍入后合计仍为 10.00，尾差在行内可追溯。
    assert data["allocated_total"] == "10.00"
    assert data["tail_count"] == 3
    assert data["remainder_total"] == "0"
    assert Decimal(data["tail_abs_total"]) > 0
    review = _resolve(client).json()
    assert review["allocated_total"] == "10.00"
    assert review["remainder_total"] == "0"
    assert any(Decimal(value) != 0 for value in review["remainders"].values())


def test_retry_after_new_batch_restores_saved_position(client):
    _import(client)
    old_batch = _allocate(client).json()
    _import(client, V2)
    new_batch = _allocate(client).json()
    # 回到旧版本请求被拒绝，旧指针不动，重试不重复入账。
    assert _allocate(client, version_id=new_batch["version_id"]).json()["batch_id"] == new_batch["batch_id"]
    retry = _allocate(client, version_id=new_batch["version_id"], idempotency_key=new_batch["idempotency_key"]).json()
    assert retry["batch_id"] == new_batch["batch_id"]
    assert retry["reused"] is True
    assert len(client.get("/api/allocations/batches").json()["batches"]) == 2


def test_page_shows_units_and_totals(client):
    _import(client)
    _allocate(client)
    _resolve(client)
    for path, markers in [
        ("/budgets", ["万元", "采信版本"]),
        ("/allocations", ["尾差", "采信版本"]),
        ("/remainders", ["尾差", "复核"]),
        ("/runs", ["尾差", "对账"]),
    ]:
        body = client.get(path).text
        assert all(marker in body for marker in markers), path
