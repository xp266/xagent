import os
import sys
import json
import asyncio
import threading
from datetime import datetime
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _PROJECT_ROOT)

load_dotenv()

from src.ai import OpenAIProvider, detect_capabilities
from src.agent import MessageManager, SessionStore, generate_name, agent_stream
from src.agent.projects import ProjectManager
from src.tools import ToolRegistry
from src.types.events import LLMResponse, StreamEvent
from src.utils import load_prompt


# ── Global config ──────────────────────────────────────────
_config = {
    "base_url": os.getenv("BASE_URL", "https://opencode.ai/zen/v1"),
    "model": os.getenv("MODEL_NAME", "mimo-v2.5-free"),
    "api_key": os.getenv("API_KEY", ""),
}

_pm = ProjectManager()


class ProjectRuntime:
    def __init__(self):
        self.provider: OpenAIProvider | None = None
        self.registry: ToolRegistry | None = None
        self.msgs: MessageManager | None = None
        self.token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

_runtimes: dict[str, ProjectRuntime] = {}


def _init_runtime(project):
    r = ProjectRuntime()
    capabilities = detect_capabilities(_config["model"])
    r.provider = OpenAIProvider(
        model=_config["model"],
        base_url=_config["base_url"],
        api_key=_config["api_key"],
        capabilities=capabilities,
    )
    r.registry = ToolRegistry()
    r.registry.load_local(os.path.join(_PROJECT_ROOT, "src", "tools"))
    store = SessionStore(sessions_dir=project.sessions_dir)
    r.msgs = MessageManager(load_prompt("default"), session=store)
    r.msgs.set_reasoning_fields(r.provider.reasoning_fields)
    return r


def _get_runtime(project_id: str) -> ProjectRuntime | None:
    if project_id not in _runtimes:
        p = _pm.get(project_id)
        if not p:
            return None
        r = _init_runtime(p)
        _runtimes[project_id] = r
    return _runtimes[project_id]


def _ensure_project_or_none():
    p = _pm.current
    if not p and _pm.list():
        p = _pm.list()[0]
        _pm.current = p.id
    return p


def _sse_json(event_type: str, data) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── FastAPI app ────────────────────────────────────────────
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    for r in _runtimes.values():
        if r.registry:
            r.registry.cleanup()


app = FastAPI(title="LingCode Web", lifespan=lifespan)

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


# ── Project API ────────────────────────────────────────────
@app.get("/api/projects")
async def list_projects():
    projects = []
    for p in _pm.list():
        projects.append(p.to_dict())
    return JSONResponse({"projects": projects, "current_id": _pm._current_id or ""})


@app.post("/api/projects")
async def create_project(body: ProjectCreate):
    name = body.name or datetime.now().strftime("%m%d-%H%M%S")
    p = _pm.create(name=name, path=body.path or _PROJECT_ROOT)
    _get_runtime(p.id)
    return JSONResponse(p.to_dict())


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    if project_id in _runtimes:
        r = _runtimes.pop(project_id)
        if r.registry:
            r.registry.cleanup()
    ok = _pm.delete(project_id)
    if not ok:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    return JSONResponse({"ok": True})


@app.put("/api/projects/{project_id}/name")
async def rename_project(project_id: str, body: ProjectRename):
    ok = _pm.rename(project_id, body.name)
    if not ok:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    return JSONResponse({"ok": True})


@app.put("/api/projects/switch/{project_id}")
async def switch_project(project_id: str):
    rt = _get_runtime(project_id)
    if not rt:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    _pm.current = project_id
    return JSONResponse({
        "id": project_id,
        "messages": rt.msgs.get_api_messages(),
        "token_usage": rt.token_usage,
        "agent_name": rt.msgs.session.agent_name,
    })


# ── Chat API ───────────────────────────────────────────────
@app.get("/")
async def index():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.post("/api/chat/sse")
async def chat_sse(body: ChatInput):
    p = _ensure_project_or_none()
    if not p:
        ts = datetime.now().strftime("%m%d-%H%M%S")
        p = _pm.create(name=ts, path=_PROJECT_ROOT)

    rt = _get_runtime(p.id)
    if not rt or not rt.msgs or not rt.provider or not rt.registry:
        return JSONResponse({"error": "Session not initialized"}, status_code=500)

    rt.msgs.add_user(body.content)
    project_id = p.id

    if not rt.msgs.session.agent_name:
        rt.msgs.session.agent_name = "New Session"

    async def event_stream() -> AsyncGenerator[str, None]:
        loop = asyncio.get_event_loop()
        queue = asyncio.Queue()

        yield _sse_json("project-info", {"id": project_id, "name": p.name})

        if not rt.msgs.session.agent_name or rt.msgs.session.agent_name == "New Session":
            async def _name_session():
                try:
                    name = await loop.run_in_executor(None, generate_name, rt.provider, body.content)
                    rt.msgs.session.agent_name = name
                    _pm.rename(project_id, name)
                    loop.call_soon_threadsafe(queue.put_nowait, ("project-name", {"id": project_id, "name": name}))
                except Exception:
                    pass

            naming_task = asyncio.create_task(_name_session())

        def run_cycle():
            while True:
                response = LLMResponse()
                tool_calls_pending = []
                tool_results = []

                try:
                    stream = agent_stream(
                        rt.provider,
                        rt.msgs.get_api_messages(),
                        rt.registry.schemas() if rt.registry.schemas() else None,
                        rt.registry,
                    )

                    for event in stream:
                        if event.type == "step-start":
                            response = LLMResponse()
                            tool_calls_pending = []
                            tool_results = []
                        elif event.type == "reasoning-delta":
                            response.reasoning += event.data
                        elif event.type == "text-delta":
                            response.content += event.data
                        elif event.type == "tool-call":
                            tool_calls_pending.append(event.data)
                        elif event.type in ("tool-result", "tool-error"):
                            tool_results.append(event.data)
                        elif event.type == "step-finish":
                            response.finish_reason = event.data.get("finish_reason", "")
                            usage = event.data.get("usage", {})
                            if usage:
                                rt.token_usage = usage

                        loop.call_soon_threadsafe(queue.put_nowait, ("event", event))

                except Exception as e:
                    loop.call_soon_threadsafe(queue.put_nowait, ("error", str(e)))
                    return

                if not response.content and response.reasoning:
                    response.content = response.reasoning
                    response.reasoning = ""

                if tool_calls_pending and response.content:
                    if response.reasoning:
                        response.content = ""
                    else:
                        response.reasoning = response.content
                        response.content = ""

                for tc in tool_calls_pending:
                    response.tool_calls.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["input"]) if isinstance(tc["input"], dict) else str(tc["input"]),
                        },
                    })

                rt.msgs.add_assistant(response, rt.provider.reasoning_fields[0])

                for tr in tool_results:
                    rt.msgs.add_tool(
                        tr["id"],
                        tr.get("result", tr.get("error", "")),
                        tr.get("attachments"),
                    )

                if response.finish_reason != "tool_calls":
                    break

            rt.msgs.save()
            messages_snapshot = [m.to_api() if not isinstance(m, dict) else m for m in rt.msgs.get_messages()]
            loop.call_soon_threadsafe(queue.put_nowait, ("done-with-msgs", messages_snapshot))

        thread = threading.Thread(target=run_cycle, daemon=True)
        thread.start()

        while True:
            kind, data = await queue.get()
            if kind == "project-name":
                yield _sse_json("project-name", data)
                continue
            if kind == "done-with-msgs":
                break
            if kind == "error":
                yield _sse_json("provider-error", {"error": data, "code": 0})
                yield _sse_json("done", {"messages": rt.msgs.get_api_messages()})
                return
            if kind == "event":
                event = data
                if event.type in ("step-start", "reasoning-start", "reasoning-end", "text-start", "text-end"):
                    yield _sse_json(event.type, None)
                elif event.type == "reasoning-delta":
                    yield _sse_json("reasoning-delta", event.data)
                elif event.type == "text-delta":
                    yield _sse_json("text-delta", event.data)
                elif event.type == "tool-call":
                    yield _sse_json("tool-call", {
                        "id": event.data["id"],
                        "name": event.data["name"],
                        "input": event.data["input"],
                    })
                elif event.type == "tool-result":
                    yield _sse_json("tool-result", {
                        "id": event.data["id"],
                        "name": event.data["name"],
                        "result": event.data.get("result", ""),
                        "attachments": event.data.get("attachments", []),
                    })
                elif event.type == "tool-error":
                    yield _sse_json("tool-error", {
                        "id": event.data["id"],
                        "name": event.data["name"],
                        "error": event.data.get("error", ""),
                    })
                elif event.type == "step-finish":
                    yield _sse_json("step-finish", {
                        "finish_reason": event.data.get("finish_reason", ""),
                        "usage": event.data.get("usage", {}),
                    })
                elif event.type == "provider-error":
                    yield _sse_json("provider-error", event.data)
                elif event.type == "finish":
                    yield _sse_json("finish", {
                        "finish_reason": event.data.get("finish_reason", ""),
                    })

        yield _sse_json("done", {"messages": rt.msgs.get_api_messages()})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Config API ─────────────────────────────────────────────
@app.get("/api/config")
async def get_config():
    cfg = dict(_config)
    if cfg.get("api_key"):
        k = cfg["api_key"]
        cfg["api_key"] = k[:6] + "..." + k[-4:] if len(k) > 12 else "***"
    return JSONResponse(cfg)


@app.put("/api/config")
async def update_config(body: ConfigUpdate):
    if body.base_url is not None:
        _config["base_url"] = body.base_url
    if body.model is not None:
        _config["model"] = body.model
    if body.api_key is not None:
        _config["api_key"] = body.api_key
    capabilities = detect_capabilities(_config["model"])
    for rid, rt in _runtimes.items():
        rt.provider = OpenAIProvider(
            model=_config["model"],
            base_url=_config["base_url"],
            api_key=_config["api_key"],
            capabilities=capabilities,
        )
    return JSONResponse(_config)


# ── Messages API ───────────────────────────────────────────
@app.get("/api/messages")
async def get_messages():
    p = _ensure_project_or_none()
    if not p:
        return JSONResponse({"messages": []})
    rt = _get_runtime(p.id)
    return JSONResponse({"messages": rt.msgs.get_api_messages() if rt.msgs else []})


@app.put("/api/messages")
async def update_messages(body: MessagesUpdate):
    p = _ensure_project_or_none()
    if not p:
        return JSONResponse({"error": "No project"}, status_code=400)
    rt = _get_runtime(p.id)
    if not rt.msgs:
        return JSONResponse({"error": "Session not initialized"}, status_code=500)
    rt.msgs._messages = body.messages
    return JSONResponse({"messages": rt.msgs.get_api_messages()})


@app.post("/api/messages/clear")
async def clear_messages():
    p = _ensure_project_or_none()
    if not p:
        return JSONResponse({"messages": []})
    rt = _get_runtime(p.id)
    rt.msgs = MessageManager(load_prompt("default"), session=SessionStore(sessions_dir=p.sessions_dir))
    rt.msgs.set_reasoning_fields(rt.provider.reasoning_fields)
    rt.token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return JSONResponse({"messages": rt.msgs.get_api_messages()})


# ── Status API ─────────────────────────────────────────────
@app.get("/api/status")
async def get_status():
    p = _ensure_project_or_none()
    msgs = []
    agent_name = ""
    token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if p:
        rt = _get_runtime(p.id)
        if rt:
            msgs = rt.msgs.get_messages() if rt.msgs else []
            agent_name = rt.msgs.session.agent_name if rt.msgs else ""
            token_usage = rt.token_usage
    return JSONResponse({
        "model": _config["model"],
        "base_url": _config["base_url"],
        "token_usage": token_usage,
        "workdir": _PROJECT_ROOT,
        "message_count": len(msgs),
        "agent_name": agent_name,
    })


def run_web(host="0.0.0.0", port=8080, reload=False):
    import uvicorn
    uvicorn.run("src.ui.web.server:app", host=host, port=port, reload=reload)
