"""POST action handlers (HTMX form targets)."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..config import get_config
from ..controller import get_controller
from ..models import Override

router = APIRouter()

_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _status_fragment(request: Request) -> HTMLResponse:
    ctrl = get_controller()
    cfg = get_config()
    return _templates.TemplateResponse(
        "fragments/status_card.html",
        {"request": request, "state": ctrl.state, "cfg": cfg},
    )


@router.post("/override/{mode}", response_class=HTMLResponse)
async def set_override(request: Request, mode: str):
    ctrl = get_controller()
    try:
        override = Override(mode)
    except ValueError:
        return HTMLResponse(f"Unknown mode: {mode}", status_code=400)
    ctrl.set_override(override)
    return _status_fragment(request)


@router.post("/setpoints", response_class=HTMLResponse)
async def update_setpoints(
    request: Request,
    heat_below: float = Form(...),
    cool_above: float = Form(...),
):
    if heat_below >= cool_above:
        return HTMLResponse("heat_below must be less than cool_above", status_code=400)
    ctrl = get_controller()
    ctrl.update_setpoints(heat_below, cool_above)
    return _status_fragment(request)


@router.post("/schedule", response_class=HTMLResponse)
async def update_schedule(
    request: Request,
    enabled: str = Form("off"),
    start: str = Form("08:00"),
    end: str = Form("23:00"),
):
    cfg = get_config()
    cfg.schedule.enabled = enabled == "on"
    cfg.schedule.start = start
    cfg.schedule.end = end
    ctrl = get_controller()
    # Trigger re-evaluation
    asyncio.create_task(ctrl._decide())  # noqa: SLF001
    return _status_fragment(request)


@router.post("/control", response_class=HTMLResponse)
async def update_control(
    request: Request,
    debounce_seconds: int = Form(300),
    min_cycle_seconds: int = Form(600),
    stale_sensor_seconds: int = Form(600),
    hysteresis: float = Form(1.0),
    cool_outdoor_gate_enabled: str = Form("off"),
    min_outdoor_c: float = Form(17.0),
):
    cfg = get_config()
    cfg.control.debounce_seconds = max(0, debounce_seconds)
    cfg.control.min_cycle_seconds = max(0, min_cycle_seconds)
    cfg.control.stale_sensor_seconds = max(60, stale_sensor_seconds)
    cfg.control.hysteresis = max(0.0, hysteresis)
    cfg.cooling_outdoor_gate.enabled = cool_outdoor_gate_enabled == "on"
    cfg.cooling_outdoor_gate.min_outdoor_c = min_outdoor_c
    return _status_fragment(request)
