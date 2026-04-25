"""FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import get_config
from .controller import get_controller
from .db import close_db, init_db
from .ha_client import get_ha_client
from .routes import actions, pages, ws

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    log.info("Starting ac-controller (HA: %s)", cfg.ha_url)

    # Init DB
    await init_db()

    # Start HA client (async reconnect loop)
    ha = get_ha_client()
    await ha.start()

    # Start controller (subscribes to HA, starts tick loop)
    ctrl = get_controller()
    await ctrl.start()

    yield

    # Shutdown — do NOT alter AC state
    log.info("Shutting down — leaving AC in its current state")
    await ctrl.stop()
    await ha.stop()
    await close_db()


app = FastAPI(title="AC Controller", lifespan=lifespan)

# Routes
app.include_router(pages.router)
app.include_router(actions.router, prefix="/action")
app.include_router(ws.router)


@app.get("/health")
async def health():
    ha = get_ha_client()
    return JSONResponse({"status": "ok", "ha_connected": ha.connected})
