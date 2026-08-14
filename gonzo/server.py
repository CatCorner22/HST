"""FastAPI backend: SSE chat plus the three non-streaming modes."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gonzo.client import CredentialsError, GonzoClient, RefusalError
from gonzo.config import WEB_DIR
from gonzo.modes.chat import ChatSession
from gonzo.modes.compose import Composer
from gonzo.modes.critique import Critic
from gonzo.modes.transfer import Transferrer
from gonzo.style.spec import load_spec

log = logging.getLogger(__name__)

app = FastAPI(title="Gonzo Style Engine", version="0.1.0")

# In-process session store. Fine for a local single-user tool; a deployed
# instance would want something with eviction and a shared backend.
_SESSIONS: dict[str, ChatSession] = {}


def _client() -> GonzoClient:
    return GonzoClient()


def _session(session_id: str | None) -> tuple[str, ChatSession]:
    if session_id and session_id in _SESSIONS:
        return session_id, _SESSIONS[session_id]
    new_id = session_id or uuid.uuid4().hex
    _SESSIONS[new_id] = ChatSession(client=_client(), spec=load_spec())
    return new_id, _SESSIONS[new_id]


# --- request models --------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = None


class ComposeRequest(BaseModel):
    assignment: str = Field(min_length=1)
    seed: int | None = None
    judge: bool = True
    revise: bool = True


class TransferRequest(BaseModel):
    text: str = Field(min_length=1)
    seed: int | None = None


class ScoreRequest(BaseModel):
    text: str = Field(min_length=1)
    judge: bool = True
    assignment: str | None = None


# --- routes ----------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    spec = load_spec()
    return {
        "status": "ok",
        "bands": spec.band_names,
        "templates": [t["name"] for t in spec.templates],
        "sessions": len(_SESSIONS),
    }


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/api/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    """Stream a reply as Server-Sent Events."""
    session_id, session = _session(req.session_id)

    def generate():
        yield _sse("session", {"session_id": session_id})
        try:
            for chunk in session.stream(req.message):
                yield _sse("delta", {"text": chunk})
        except RefusalError as exc:
            yield _sse("error", {"message": str(exc), "kind": "refusal"})
            return
        except CredentialsError as exc:
            yield _sse("error", {"message": str(exc), "kind": "credentials"})
            return
        except Exception as exc:  # surface failures to the UI, don't hang it
            log.exception("chat stream failed")
            yield _sse("error", {"message": str(exc), "kind": "error"})
            return

        directive = session.last_directive
        yield _sse("done", {"directive": directive.to_dict() if directive else None})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/compose")
def compose(req: ComposeRequest) -> dict[str, Any]:
    composer = Composer(client=_client(), use_judge=req.judge)
    try:
        draft = composer.compose(req.assignment, seed=req.seed, revise=req.revise)
    except CredentialsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RefusalError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "text": draft.text,
        "revision": draft.revision,
        "directive": draft.directive.to_dict(),
        "report": draft.report.to_dict(),
    }


@app.post("/api/transfer")
def transfer(req: TransferRequest) -> dict[str, Any]:
    try:
        result = Transferrer(client=_client()).transfer(req.text, seed=req.seed)
    except CredentialsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RefusalError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "text": result.text,
        "report": result.report.to_dict(),
        "dropped_facts": result.dropped_facts,
        "facts_preserved": result.facts_preserved,
    }


@app.post("/api/score")
def score(req: ScoreRequest) -> dict[str, Any]:
    critic = Critic(client=_client() if req.judge else None)
    try:
        report = critic.critique(req.text, use_judge=req.judge, assignment=req.assignment)
    except CredentialsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RefusalError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return report.to_dict()


@app.delete("/api/session/{session_id}")
def clear_session(session_id: str) -> dict[str, str]:
    _SESSIONS.pop(session_id, None)
    return {"status": "cleared"}


# --- static UI -------------------------------------------------------------

if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")
