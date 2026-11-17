from fastapi import APIRouter, Body, Request

from .service import current, resolve, review, reviews

router = APIRouter(tags=["M5-尾差裁决"])


@router.get("/remainders")
def page(request: Request):
    data = current(request.app.state.store)
    data["reviews"] = reviews(request.app.state.store)
    return request.app.state.templates.TemplateResponse(request, "m5_remainders.html", {"title": "尾差裁决", "data": data})


@router.post("/api/remainders/resolve")
def api(request: Request, payload: dict = Body(default={})):
    settings = request.app.state.settings
    return resolve(payload or None, store=request.app.state.store, rule_version=settings.rule_version)


@router.get("/api/remainders")
def api_current(request: Request):
    return current(request.app.state.store)


@router.get("/api/remainders/{review_id}")
def api_review(review_id: str, request: Request):
    return review(request.app.state.store, review_id)
