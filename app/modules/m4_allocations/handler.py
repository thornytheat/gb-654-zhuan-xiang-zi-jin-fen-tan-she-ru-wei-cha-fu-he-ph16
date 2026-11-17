from fastapi import APIRouter, Body, Request

from .service import batches, calculate, current, restore, review

router = APIRouter(tags=["M4-金额分摊"])


@router.get("/allocations")
def page(request: Request):
    settings = request.app.state.settings
    data = current({}, request.app.state.store)
    data["batches"] = batches(request.app.state.store)
    return request.app.state.templates.TemplateResponse(request, "m4_allocations.html", {"title": "金额分摊", "data": data})


@router.post("/api/allocations/calculate")
def api(request: Request, payload: dict = Body(default={})):
    settings = request.app.state.settings
    return calculate(payload or None, store=request.app.state.store, rule_version=settings.rule_version)


@router.get("/api/allocations")
def api_current(request: Request):
    return current({}, request.app.state.store)


@router.get("/api/allocations/batches")
def api_batches(request: Request):
    return {"batches": batches(request.app.state.store)}


@router.get("/api/allocations/batches/{batch_id}")
def api_batch(batch_id: str, request: Request):
    return review({"batch_id": batch_id}, request.app.state.store)


@router.post("/api/allocations/batches/{batch_id}/restore")
def api_restore(batch_id: str, request: Request):
    return restore(batch_id, request.app.state.store)
