# Pelorus API Reference

The primary endpoint is `POST /api/run` for agent execution. Other endpoints are
utility/debug helpers used mainly by the web frontend.

---

## POST /api/run - Execute agent task

**URL:** `POST /api/run`

**Request body** (`application/json`):

| Field | Type | Default | Description |
|---|---|---|---|
| `prompt` | `string` | **required** | Task description sent to the LLM |
| `server_id` | `string\|null` | `null` | Select a named server from `config.toml` |
| `provider` | `string\|null` | `null` | Override: `"ollama"`, `"openai"`, `"gemini"` |
| `endpoint` | `string\|null` | `null` | Override: LLM API endpoint URL |
| `model` | `string\|null` | `null` | Override: model name |
| `api_key` | `string\|null` | `null` | Override: API key |
| `vision` | `bool\|null` | `null` | Override: enable vision (image inputs) |
| `max_steps` | `integer` | `15` | Max agent loop iterations |
| `response_format` | `string` | `"default"` | One of `"minimal"`, `"default"`, `"thoughts"`, `"verbose"` |
| `stream` | `bool` | `false` | When `true`, returns SSE stream |

**Config resolution order:**
1. If `server_id` is provided, that server is loaded from the TOML config
2. Otherwise the server marked `default: true`, or the first server
3. Any explicit `provider` / `endpoint` / `model` / `api_key` / `vision` overrides the loaded config
4. If nothing resolves: `ollama`, `http://localhost:11434`, `gemma4:12b`

### Response formats

When `stream: false`, the response is JSON. The `response_format` controls verbosity:

| Format | `success` | `final_text` | `steps[]` | `steps[].tool_screenshot` | `initial_screenshot` | `final_screenshot` | `steps[].thinking` |
|---|---|---|---|---|---|---|---|
| `minimal` | yes | - | - | - | - | - | - |
| `default` | yes | yes | yes | `null` | - | - | - |
| `thoughts` | yes | yes | yes | omitted | - | - | yes |
| `verbose` | yes | yes | yes | base64 PNG | base64 PNG | base64 PNG | yes |

```bash
# Minimal - returns {"success": true}
curl -s localhost:5100/api/run -H 'Content-Type: application/json' \
  -d '{"prompt":"open kate","response_format":"minimal"}'
```

Failure: `success: false` when the agent exhausted `max_steps` while still making tool calls.

### SSE streaming

When `stream: true`, the response is `text/event-stream`. Events are emitted as the agent works:

```
event: step_start
data: {"iteration": 1}

event: tool_call
data: {"name": "computer", "input": {"action": "desktop_state"}}

event: tool_output
data: {"text": "=== Desktop (4 windows, 1920x1080) ==="}

event: step_end
data: {"iteration": 1, "has_tool_calls": true, "tool_calls_count": 1, "text": "..."}

event: done
data: {"success": true, "final_text": "Kate has been opened."}
```

Full event reference:

| Event | Payload | When |
|---|---|---|
| `step_start` | `{"iteration": n}` | Each agent loop iteration begins |
| `think` | `{"delta": "..."}` | Streaming chain-of-thought token |
| `tool_call` | `{"name": "...", "input": {...}}` | Model invoked a tool |
| `tool_output` | `{"text": "..."}` | Text output from tool execution |
| `tool_error` | `{"text": "..."}` | Error from tool execution |
| `tool_screenshot` | `{"data": "base64..."}` | Screenshot (verbose format only) |
| `step_end` | `{"iteration": n, "has_tool_calls": bool, ...}` | Step completed |
| `screenshot` | `{"which": "initial"\|"final", "data": "base64..."}` | Before/after (verbose only) |
| `done` | `{"success": bool, "final_text": "...", "final_screenshot": "..."}` | Agent run finished |
| `error` | `{"message": "..."}` | An error occurred |

---

## Other endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serve the SPA frontend (`static/index.html`), or `{"status":"pelorus","docs":"/docs"}` |
| `GET` | `/docs` | FastAPI auto-generated Swagger documentation |
| `GET` | `/api/servers` | List configured LLM servers from `config.toml` |
| `POST` | `/api/servers` | Add a new server |
| `GET` | `/api/servers/{id}` | Get one server |
| `PUT` | `/api/servers/{id}` | Update server fields |
| `DELETE` | `/api/servers/{id}` | Delete a server |
| `GET` | `/api/servers/{id}/models` | Fetch available models from a saved server's provider |
| `POST` | `/api/models/fetch` | Fetch models from ad-hoc provider config |
| `GET` | `/api/env` | Return first server's config for frontend pre-population |
| `GET` | `/api/windows` | List KWin windows (debug, KDE only) |
| `GET` | `/api/state` | Return the desktop state text sent to the LLM |
| `WS` | `/ws` | WebSocket endpoint for browser UI (full-duplex agent interaction) |

The server CRUD, model fetch, and WebSocket endpoints are used by the web frontend
and are fully documented via the interactive `/docs` Swagger UI.

---

## Configuration

Servers are stored in `/config/agent/config.toml`. On first run, the file is seeded
from environment variables:

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

**Seeding env vars:**

| Env var | Default |
|---|---|
| `PELORUS_PROVIDER` | `ollama` |
| `PELORUS_ENDPOINT` | `http://localhost:11434` |
| `PELORUS_MODEL` | `gemma4:12b` |
| `PELORUS_API_KEY` | `""` |

**Other env vars:**

| Env var | Default | Purpose |
|---|---|---|---|
| `PELORUS_PORT` | `5100` | HTTP server port |
| `PIXELFLUX_CU` | `5000` | Computer-use backend port |

**Inline override:** instead of pre-configuring a TOML server, you can pass
`provider`, `endpoint`, `model`, and `api_key` directly in the `POST /api/run`
request body. These override any config file values for that single request.

Environment variables in TOML values are resolved via `${VAR_NAME}` syntax.

---

## Vision fallback

Pelorus primarily drives the desktop through AT-SPI accessibility data, giving the
LLM structured text with precise element coordinates. This is far more reliable than
pixel-based approaches, click targets are known exactly, not guessed from images.

Vision mode (`vision: true`) is used as a fallback for applications that lack
accessibility tree support (games, Electron apps without a11y bridges, custom GL
canvases). When enabled, the agent can request per-application screenshots to
interpret non-text interfaces. This is inherently less reliable for coordinate
targeting, even frontier vision models misjudge click positions, but it allows
Pelorus to function in environments where AT-SPI alone is insufficient. Because
the primary loop uses text-based state, even quantized local models can maintain
accurate desktop control.
