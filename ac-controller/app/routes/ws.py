"""WebSocket endpoint — pushes controller state JSON to all connected GUIs."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..controller import get_controller

router = APIRouter()
log = logging.getLogger(__name__)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ctrl = get_controller()
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=10)
    ctrl.add_listener(q)
    log.debug("WS client connected")
    try:
        # Send current state immediately on connect
        await websocket.send_text(ctrl.state.model_dump_json())
        while True:
            data = await q.get()
            await websocket.send_text(data)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.debug("WS client error: %s", exc)
    finally:
        ctrl.remove_listener(q)
        log.debug("WS client disconnected")
