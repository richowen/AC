"""HTML page routes."""
from __future__ import annotations

import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..config import get_config
from ..controller import get_controller
from ..db import get_events

router = APIRouter()

_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

# Custom Jinja2 filters
def _datetimeformat(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

_templates.env.filters["datetimeformat"] = _datetimeformat


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    ctrl = get_controller()
    cfg = get_config()
    return _templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "state": ctrl.state,
            "cfg": cfg,
        },
    )


@router.get("/events", response_class=HTMLResponse)
async def events_page(request: Request):
    entries = await get_events(limit=100)
    return _templates.TemplateResponse(
        "events.html",
        {"request": request, "events": entries},
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    cfg = get_config()
    return _templates.TemplateResponse(
        "settings.html",
        {"request": request, "cfg": cfg},
    )
