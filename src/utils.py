import asyncio
import base64
import json
import logging
import os
import re
import struct
import uuid
from pathlib import Path

import httpx
import tomllib

log = logging.getLogger(__name__)

CU_HOST = "127.0.0.1"
CU_PORT = os.getenv("PIXELFLUX_CU", "5000")
CU_URL = f"http://{CU_HOST}:{CU_PORT}/computer-use"
_cu_client = httpx.AsyncClient(timeout=30.0)

_screen_size: tuple[int, int] | None = None


async def _get_screen_size() -> tuple[int, int]:
    global _screen_size
    if _screen_size is not None:
        return _screen_size
    try:
        resp = await _cu_action({"action": "screenshot"})
        data = resp.get("data", "")
        if data:
            raw = base64.b64decode(data)
            w = struct.unpack(">I", raw[16:20])[0]
            h = struct.unpack(">I", raw[20:24])[0]
            _screen_size = (w, h)
            log.info("Detected screen size: %dx%d", w, h)
            return w, h
    except Exception:
        log.warning("Could not determine screen size from CU, using fallback")
    _screen_size = (1920, 1080)
    return _screen_size

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"

CONFIG_DIR = Path("/config/agent")
CONFIG_FILE = CONFIG_DIR / "config.toml"


def _toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _resolve_env(value: str) -> str:
    def _replacer(m: re.Match) -> str:
        return os.getenv(m.group(1), m.group(0))
    return re.sub(r'\$\{(\w+)\}', _replacer, value)


def _ensure_config():
    if CONFIG_FILE.exists():
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    provider = os.getenv("PELORUS_PROVIDER", "ollama")
    endpoint = os.getenv("PELORUS_ENDPOINT", "http://localhost:11434")
    model = os.getenv("PELORUS_MODEL", "gemma4:12b")
    api_key = os.getenv("PELORUS_API_KEY", "")
    servers = [{
        "id": f"svr_{uuid.uuid4().hex[:12]}",
        "name": "Default Server",
        "provider": provider,
        "endpoint": endpoint,
        "model": model,
        "api_key": api_key,
        "vision": False,
        "default": True,
    }]
    _save_servers(servers)
    log.info("Seeded config.toml with 1 server from env")


def _load_servers() -> list[dict[str, str]]:
    if not CONFIG_FILE.exists():
        return []
    try:
        data = tomllib.loads(CONFIG_FILE.read_bytes().decode())
        return data.get("servers", [])
    except Exception as e:
        log.warning("Failed to parse config.toml: %s", e)
        return []


def _save_servers(servers: list[dict[str, str]]):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for svr in servers:
        lines.append("[[servers]]")
        for key in ("id", "name", "provider", "endpoint", "model", "api_key", "vision", "default"):
            val = svr.get(key, "")
            if isinstance(val, bool):
                lines.append(f"{key} = {'true' if val else 'false'}")
            else:
                lines.append(f'{key} = "{_toml_escape(val)}"')
        lines.append("")
    txt = "\n".join(lines) + "\n"
    CONFIG_FILE.write_text(txt)
    CONFIG_FILE.chmod(0o600)


def _resolve_server(server: dict[str, str]) -> dict[str, str]:
    return {k: _resolve_env(v) if isinstance(v, str) else v for k, v in server.items()}


async def _cu_action(payload: dict) -> dict:
    log.info("CU request: POST %s %s", CU_URL, payload)
    resp = await _cu_client.post(CU_URL, json=payload)
    log.info("CU response: %s", resp.status_code)
    resp.raise_for_status()
    return resp.json()


def is_b64_blob(text: str) -> bool:
    if not text or len(text) < 80:
        return False
    safe = sum(1 for c in text if c.isalnum() or c in "+/=\n")
    return safe > len(text) * 0.85


async def _fetch_models_from_provider(provider: str, endpoint: str, api_key: str) -> list[str]:
    endpoint = endpoint.rstrip("/")
    try:
        if provider == "ollama":
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{endpoint}/api/tags")
                resp.raise_for_status()
                models = [m["name"] for m in resp.json().get("models", [])]
        elif provider == "gemini":
            headers = {"x-goog-api-key": api_key} if api_key else {}
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{endpoint}/v1beta/models", headers=headers)
                resp.raise_for_status()
                models = [m["name"].split("/")[-1] for m in resp.json().get("models", [])]
        else:
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{endpoint}/v1/models", headers=headers)
                resp.raise_for_status()
                models = [m["id"] for m in resp.json().get("data", [])]
        return models
    except Exception as e:
        log.warning("Failed to fetch models from %s/%s: %s", provider, endpoint, e)
        return []
