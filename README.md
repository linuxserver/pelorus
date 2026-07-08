# Pelorus

AI navigator for Selkies-powered Linux desktops. Pelorus runs a FastAPI server that gives an LLM agent (Ollama, OpenAI-compatible, or Gemini) control over mouse, keyboard, screenshot, and window management via the Pixelflux computer-use backend, a Linux accessibility tree (AT-SPI), and optional KWin D-Bus integration.

## How it works

Pelorus sits between an LLM and your desktop as an agent loop:

1. **State capture** - reads the desktop accessibility tree (AT-SPI) to list icons, taskbar buttons, open windows, and the start menu. On KDE it also queries KWin D-Bus for accurate window geometry.
2. **Model inference** - sends the captured state + system prompt + your task to an LLM. The model responds with text and/or tool calls (`mouse_move`, `click`, `key`, `type`, `scroll`, `screenshot`, `explore_window`, `desktop_state`, etc.).
3. **Tool execution** - tool calls are dispatched to the Pixelflux computer-use backend for input actions, or handled locally for introspection (desktop state, window tree exploration, window close).
4. **Repeat** - results feed back to the LLM until the task completes or `max_steps` is reached.

AT-SPI works on any Linux desktop (GNOME, KDE, etc.). KWin D-Bus integration is specific to KDE and enhances window geometry accuracy, Pelorus works without it (with reduced coordinate precision for window interiors).

### Vision vs. text-based control

Unlike systems that pipe full screenshots into a vision model, Pelorus primarily works by reading the desktop accessibility tree (AT-SPI), giving the LLM structured, precise coordinates for every clickable element. This makes it far more reliable than vision-based approaches, which struggle with click targeting even on frontier models.

Vision mode is available (set `vision: true` on a server) and used as a fallback for applications that lack AT-SPI support, such as games, Electron apps without accessibility bridges, or custom OpenGL canvases. When vision is enabled, the agent can request per-application screenshots to interpret non-text interfaces. This is inherently less reliable for precise coordinate targeting, and text-based control via AT-SPI will always be preferred when available. The practical benefit is that even a quantized local model can control a desktop session with high accuracy, since it relies on structured data rather than pixel interpretation.

## Quick Start

```bash
pelorus
# listens on 0.0.0.0:5100  (or $PELORUS_PORT)
```

On first run a config file is created at `/config/agent/config.toml` seeded from environment variables.

Open `http://localhost:5100` in a browser for the chat UI, or use the REST API (see [API.md](API.md)).

## Configuration

Servers are stored in `/config/agent/config.toml`:

```toml
[[servers]]
id = "svr_abc123def456"
name = "Default Server"
provider = "ollama"
endpoint = "http://localhost:11434"
model = "gemma4:12b"
api_key = ""
vision = false
default = true
```

Environment variables in TOML values are resolved via `${VAR_NAME}` syntax.

| Env var | Default | Purpose |
|---|---|---|
| `PELORUS_PORT` | `5100` | HTTP server port |
| `PIXELFLUX_CU` | `5000` | Computer-use backend port |
| `PELORUS_PROVIDER` | `ollama` | Provider for config seed |
| `PELORUS_ENDPOINT` | `http://localhost:11434` | Endpoint for config seed |
| `PELORUS_MODEL` | `gemma4:12b` | Model for config seed |
| `PELORUS_API_KEY` | `""` | API key for config seed |
| `PELORUS_ENVIRONMENT` | `KDE Plasma Desktop (Linux)` | Desktop environment label shown in `desktop_state` header; override for other DEs (e.g. `GNOME (Linux)`, `Sway (Linux)`) |

## Web Interface

The SPA frontend (vanilla JS, served at `/`) provides a full chat experience with session tabs, streaming output, server management, and settings, all communicating via WebSocket. Server selection and client settings are persisted to `localStorage`.

## API Reference

See [API.md](API.md) for the complete REST API reference. The primary endpoint is `POST /api/run`, which accepts a task prompt and optional LLM configuration (provider, endpoint, model, api_key), either referencing a pre-configured server from `config.toml` or specifying credentials inline.

Interactive OpenAPI documentation is available at `http://localhost:5100/docs` when the server is running.
