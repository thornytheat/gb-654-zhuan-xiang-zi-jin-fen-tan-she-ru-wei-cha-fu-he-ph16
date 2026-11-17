from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.errors import DomainError

from . import core
from .service import build, empty_view, execute, export_text

router = APIRouter(tags=["M6-批次复核"])


def _record_evidence(store):
    """保留原有证据批次：同一输入摘要重复访问复用原 run_id，不重复入账。"""
    try:
        core.run({"store": store})
    except Exception:
        pass


@router.get("/runs")
def page(request: Request):
    store = request.app.state.store
    _record_evidence(store)
    try:
        data = execute({}, store=store, rule_version=request.app.state.settings.rule_version)
    except DomainError:
        data = empty_view()
    return request.app.state.templates.TemplateResponse(request, "m6_runs.html", {"title": "批次复核", "data": data})


@router.get("/api/runs")
def runs(request: Request):
    store = request.app.state.store
    _record_evidence(store)
    response = {
        "runs": store.all(),
        "batches": store.batches(),
        "versions": [
            {key: value for key, value in version.items() if key != "snapshot"}
            for version in store.versions()
        ],
        "reviews": store.reviews(),
    }
    return response


@router.get("/api/runs/batches/{batch_id}")
def batch(batch_id: str, request: Request):
    return build(request.app.state.store, {"batch_id": batch_id})


@router.get("/api/export/allocations.csv")
def export(request: Request):
    store = request.app.state.store
    batch_id = request.query_params.get("batch_id")
    try:
        text, _ = export_text(store, {"batch_id": batch_id} if batch_id else {})
    except Exception:
        text = execute({})["csv"]
    return Response(text, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=evidence.csv"})
