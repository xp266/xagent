import os
import sys
import asyncio
import threading
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _PROJECT_ROOT)

load_dotenv()

from src.agent import get_session_manager, run_session_turn, name_session_from_first_message
from src.ai.capabilities import get_model_context_limit
from src.types.events import TokenUsage
from src.utils.config import get_config, update_config as update_config_module
from src.utils.sse import format_sse


sm = get_session_manager()

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    for s in sm.list():
        s.release()


app = FastAPI(title="xAgent Web", lifespan=lifespan)

static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


class ChatInput(BaseModel):
    content: str


class ConfigUpdate(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


class MessagesUpdate(BaseModel):
    messages: list


class ProjectCreate(BaseModel):
    name: str = ""
    path: str = ""


class ProjectRename(BaseModel):
    name: str


@app.get("/api/projects")
async def list_projects():
    sessions = sm.list()
    return JSONResponse({
        "projects": [s.to_dict() for s in sessions],
        "current_id": sm._current_id or "",
    })


@app.post("/api/projects")
async def create_project(body: ProjectCreate):
    s = sm.create(name=body.name, path=body.path or _PROJECT_ROOT)
    return JSONResponse(s.to_dict())


@app.delete("/api/projects/{session_id}")
async def delete_project(session_id: str):
    s = sm.get(session_id)
    if s:
        s.release()
    ok = sm.delete(session_id)
    if not ok:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    return JSONResponse({"ok": True})


@app.put("/api/projects/{session_id}/name")
async def rename_project(session_id: str, body: ProjectRename):
    ok = sm.rename(session_id, body.name)
    if not ok:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    return JSONResponse({"ok": True})


@app.put("/api/projects/switch/{session_id}")
async def switch_project(session_id: str):
    s = sm.get(session_id)
    if not s:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    sm.current = session_id
    return JSONResponse({
        "id": session_id,
        "messages": s.msgs.get_api_messages(),
        "token_usage": s.token_usage.model_dump(),
        "agent_name": s.name,
    })


@app.get("/")
async def index():
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.post("/api/chat/sse")
async def chat_sse(body: ChatInput):
    session = sm.get_or_create_current()
    session_id = session.id

    async def event_stream() -> AsyncGenerator[str, None]:
        loop = asyncio.get_event_loop()
        queue = asyncio.Queue()

        yield format_sse("project-info", {"id": session_id, "name": session.name})

        if session.name == "New Session":
            async def _name_session():
                try:
                    name = await loop.run_in_executor(
                        None, name_session_from_first_message, session, body.content
                    )
                    if name:
                        sm.rename(session_id, name)
                        loop.call_soon_threadsafe(
                            queue.put_nowait, ("project-name", {"id": session_id, "name": name})
                        )
                except Exception:
                    pass

            asyncio.create_task(_name_session())

        def run_in_thread():
            for event in run_session_turn(session, body.content):
                loop.call_soon_threadsafe(queue.put_nowait, ("event", event))
            messages = session.msgs.get_api_messages()
            loop.call_soon_threadsafe(queue.put_nowait, ("done", messages))

        thread = threading.Thread(target=run_in_thread, daemon=True)
        thread.start()

        while True:
            kind, data = await queue.get()

            if kind == "project-name":
                yield format_sse("project-name", data)
                continue
            if kind == "done":
                yield format_sse("done", {"messages": data})
                return
            if kind == "event":
                event = data
                if event.type in ("step-start", "reasoning-start", "reasoning-end", "text-start", "text-end"):
                    yield format_sse(event.type, None)
                elif event.type == "reasoning-delta":
                    yield format_sse("reasoning-delta", event.data)
                elif event.type == "text-delta":
                    yield format_sse("text-delta", event.data)
                elif event.type == "tool-call":
                    yield format_sse("tool-call", {
                        "id": event.data["id"],
                        "name": event.data["name"],
                        "input": event.data["input"],
                    })
                elif event.type == "tool-result":
                    yield format_sse("tool-result", {
                        "id": event.data["id"],
                        "name": event.data["name"],
                        "result": event.data.get("result", ""),
                        "attachments": event.data.get("attachments", []),
                    })
                elif event.type == "tool-error":
                    yield format_sse("tool-error", {
                        "id": event.data["id"],
                        "name": event.data["name"],
                        "error": event.data.get("error", ""),
                    })
                elif event.type == "step-finish":
                    cfg = get_config()
                    context_limit = get_model_context_limit(cfg.model)
                    context_usage_pct = (
                        round((session.token_usage.total_tokens / context_limit) * 100, 1)
                        if context_limit > 0 and session.token_usage.total_tokens > 0
                        else 0
                    )
                    yield format_sse("step-finish", {
                        "finish_reason": event.data.get("finish_reason", ""),
                        "usage": event.data.get("usage", {}),
                        "context_limit": context_limit,
                        "context_usage_pct": context_usage_pct,
                    })
                elif event.type == "provider-error":
                    yield format_sse("provider-error", event.data)
                elif event.type == "finish":
                    yield format_sse("finish", {
                        "finish_reason": event.data.get("finish_reason", ""),
                    })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/config")
async def get_config_route():
    cfg = get_config()
    d = cfg.model_dump()
    if d.get("api_key"):
        k = d["api_key"]
        d["api_key"] = k[:6] + "..." + k[-4:] if len(k) > 12 else "***"
    return JSONResponse(d)


@app.put("/api/config")
async def update_config_route(body: ConfigUpdate):
    kwargs = {}
    if body.base_url is not None:
        kwargs["base_url"] = body.base_url
    if body.model is not None:
        kwargs["model"] = body.model
    if body.api_key is not None:
        kwargs["api_key"] = body.api_key
    update_config_module(**kwargs)
    cfg = get_config()
    for s in sm.list():
        s.release()
    return JSONResponse(cfg.model_dump())


@app.get("/api/messages")
async def get_messages():
    s = sm.current
    return JSONResponse({"messages": s.msgs.get_api_messages() if s else []})


@app.put("/api/messages")
async def update_messages(body: MessagesUpdate):
    s = sm.get_or_create_current()
    s.msgs._messages = body.messages
    return JSONResponse({"messages": s.msgs.get_api_messages()})


@app.post("/api/messages/clear")
async def clear_messages():
    s = sm.current
    if not s:
        return JSONResponse({"messages": []})
    s.token_usage = TokenUsage()
    s.release()
    s.messages = []
    sm.save(s)
    return JSONResponse({"messages": []})


@app.get("/api/status")
async def get_status():
    s = sm.current
    msg_count = 0
    agent_name = ""
    token_usage = TokenUsage()
    if s:
        msg_count = len(s.msgs.get_messages())
        agent_name = s.name
        token_usage = s.token_usage
    cfg = get_config()
    context_limit = get_model_context_limit(cfg.model)
    context_usage_pct = (
        round((token_usage.total_tokens / context_limit) * 100, 1)
        if context_limit > 0 and token_usage.total_tokens > 0
        else 0
    )
    return JSONResponse({
        "model": cfg.model,
        "base_url": cfg.base_url,
        "token_usage": token_usage.model_dump(),
        "context_limit": context_limit,
        "context_usage_pct": context_usage_pct,
        "workdir": _PROJECT_ROOT,
        "message_count": msg_count,
        "agent_name": agent_name,
    })


def run_web(host="0.0.0.0", port=8080, reload=False):
    import uvicorn
    uvicorn.run("src.ui.web.server:app", host=host, port=port, reload=reload)
