import logging
import os

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .rest import router as rest_router
from .ws import router as ws_router
from .utils import STATIC, _ensure_config, _cu_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pelorus")

app = FastAPI(title="Pelorus")

if STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

app.include_router(rest_router)
app.include_router(ws_router)

_ensure_config()


@app.on_event("shutdown")
async def shutdown():
    await _cu_client.aclose()


def main():
    port = int(os.getenv("PELORUS_PORT", "5100"))
    uvicorn.run("src.app:app", host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
