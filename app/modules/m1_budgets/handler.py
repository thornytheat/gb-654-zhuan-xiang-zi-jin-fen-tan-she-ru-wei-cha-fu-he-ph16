from fastapi import APIRouter, Body, Request
from .service import execute, preview, versions

router = APIRouter(tags=["M1-预算采信"])


@router.get("/budgets")
def page(request: Request):
    settings = request.app.state.settings
    store = request.app.state.store
    data = preview(store, settings.rule_version)
    data["versions"] = [
        {key: value for key, value in version.items() if key != "snapshot"}
        for version in versions(store)
    ]
    return request.app.state.templates.TemplateResponse(request, "m1_budgets.html", {"title": "预算采信", "data": data})


@router.post("/api/budgets/import")
def api(request: Request, payload: dict = Body(default={})):
    settings = request.app.state.settings
    data = execute(payload or None, store=request.app.state.store,
                   rule_version=settings.rule_version, persist=not payload.get("dry_run", False))
    return data


@router.get("/api/budgets/versions")
def api_versions(request: Request):
    store = request.app.state.store
    return {
        "versions": [
            {key: value for key, value in version.items() if key != "snapshot"}
            for version in versions(store)
        ],
        "current_version_id": (store.latest_version() or {}).get("version_id"),
    }


@router.get("/api/budgets/versions/{version_id}")
def api_version(version_id: str, request: Request):
    from app.errors import DomainError
    from app.modules.m1_budgets import core

    store = request.app.state.store
    version = store.version(version_id)
    if version is None:
        raise DomainError("NOT_FOUND", "采信版本不存在")
    latest = store.latest_version()
    data = core.summarize_snapshot(version["snapshot"])
    data.update({
        "version_id": version["version_id"],
        "version_seq": version["seq"],
        "version_digest": version["digest"],
        "version_status": "current" if latest and latest["version_id"] == version["version_id"] else "superseded",
        "rows": version["snapshot"]["rows"],
        "quarantined": version["snapshot"]["quarantined"],
        "note": version["note"],
        "created_at": version["created_at"],
    })
    return data
