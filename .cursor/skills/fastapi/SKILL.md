---
name: fastapi
description: >-
  FastAPI conventions for this repo and upstream best practices. Use when editing
  app/api routers, Pydantic schemas, SSE/streaming jobs, or dependencies.
---

# FastAPI (apk_updater pointer)

## In this project

- App factory: `app/api/app_factory.py`
- Routers under `app/api/routers/`; shared models in `schemas.py`
- Long jobs: `stream_background_job` in `app/api/sse_job.py` (SSE), not blocking the event loop with heavy work on the request thread without a worker
- State: `request.app.state.api` → `AppState`
- Prefer existing patterns in neighboring routers over inventing new response wrappers

## Prefer

- Request/response models in `schemas.py`
- Thin routers; logic in `app/services/`
- `HTTPException` for client errors; keep messages in Russian for user-facing detail

## Upstream skill (optional deep dive)

A fuller official FastAPI agent skill ships inside the installed package:

`/.venv/Lib/site-packages/fastapi/.agents/skills/fastapi/`

Read `SKILL.md` and `references/streaming.md` / `dependencies.md` there when changing SSE or DI style. Do not copy those reference files into always-on Cursor rules (token cost).

For current library docs at query time, use **Context7** MCP if configured (see `.cursor/mcp.json.example`).
