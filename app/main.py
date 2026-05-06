from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from typing import Optional

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import clipboard as clipboard_service
from app.commands import public_macro_list, run_macro
from app.config import LOG_DIR, STATIC_DIR, ensure_directories, get_or_create_pairing_token, settings
from app.device_identity import get_or_create_device_identity
from app.files import save_upload
from app.input_control import send_text_to_pc
from app.llm import generate_text
from app.pairing import PairingCodeBook, PairingSessionBook, TrustedDeviceStore
from app.security import token_is_valid, verify_token, verify_websocket_token
from app.state import lockout_state
from app.websocket import handle_touchpad_socket


class TextRequest(BaseModel):
    text: str


class MacroRequest(BaseModel):
    id: str


class ClipboardRequest(BaseModel):
    text: str


class SessionRequest(BaseModel):
    session_id: str
    session_token: str


class SessionTextRequest(SessionRequest):
    text: str


class SessionMacroRequest(SessionRequest):
    id: str


class LockoutRequest(BaseModel):
    disabled: bool


class PairingCodeRequest(BaseModel):
    guest: bool = False


class ConsumePairingCodeRequest(BaseModel):
    code: str
    device_id: str
    name: str
    remember_device: bool = True
    idle_timeout_seconds: int = 600


class TrustedDevicePermissionsRequest(BaseModel):
    permissions: dict[str, bool]


class TrustReviewRequest(BaseModel):
    keep_trust: bool


class LLMRequest(BaseModel):
    prompt: str
    max_tokens: int = 1000


def configure_logging() -> None:
    ensure_directories()
    log_file = LOG_DIR / "server.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"),
        ],
    )


configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name)
pairing_code_book = PairingCodeBook()
pairing_session_book = PairingSessionBook()

# Safe default: the frontend is served by this same app, so no cross-origin browser
# access is needed. If you later split the frontend onto another host, add only that
# exact origin here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["X-Pairing-Token", "Content-Type"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def verify_session_permission_or_403(
    session_id: str,
    session_token: str,
    permission: str,
):
    session = pairing_session_book.verify_session_permission(
        session_id,
        session_token,
        permission,
    )
    if session is None:
        raise HTTPException(
            status_code=403,
            detail="Missing, expired, or unauthorized session.",
        )
    return session


def mark_session_active(session_id: str, session_token: str) -> None:
    pairing_session_book.mark_session_active(session_id, session_token)


@app.on_event("startup")
async def startup() -> None:
    ensure_directories()
    token = get_or_create_pairing_token()
    logger.info("Screen Slickshift started.")
    logger.info("Pairing token loaded; shown in local console only.")
    print("")
    print("============================================================")
    print("Screen Slickshift is starting.")
    print(f"Pairing token: {token}")
    print("Keep this token private. Anyone with it can control this PC.")
    print("============================================================")
    print("")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def status() -> dict:
    return {
        "app": settings.app_name,
        "disabled": lockout_state.is_disabled(),
        "auth": "token-required",
    }


@app.get("/api/auth/check", dependencies=[Depends(verify_token)])
async def auth_check() -> dict:
    return {"ok": True}


@app.get("/api/device", dependencies=[Depends(verify_token)])
async def device() -> dict:
    identity = get_or_create_device_identity()
    return {"device": identity.to_dict()}


@app.get("/api/trusted-devices", dependencies=[Depends(verify_token)])
async def trusted_devices() -> dict:
    devices = [
        trusted_device.public_dict()
        for trusted_device in TrustedDeviceStore().list_devices()
    ]
    return {"devices": devices}


@app.delete("/api/trusted-devices/{device_id}", dependencies=[Depends(verify_token)])
async def remove_trusted_device(device_id: str) -> dict:
    removed = TrustedDeviceStore().remove_device(device_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Trusted device not found.")

    revoked_sessions = pairing_session_book.remove_sessions_for_device(device_id)
    logger.info("Removed trusted device %s.", device_id)
    if revoked_sessions:
        logger.info("Revoked sessions for removed trusted device %s (%s).", device_id, revoked_sessions)
    return {"ok": True, "device_id": device_id, "revoked_sessions": revoked_sessions}


@app.patch("/api/trusted-devices/{device_id}/permissions", dependencies=[Depends(verify_token)])
async def update_trusted_device_permissions(
    device_id: str,
    payload: TrustedDevicePermissionsRequest,
) -> dict:
    trusted_device = TrustedDeviceStore().update_permissions(device_id, payload.permissions)
    if trusted_device is None:
        raise HTTPException(status_code=404, detail="Trusted device not found.")

    revoked_sessions = pairing_session_book.remove_sessions_for_device(device_id)
    logger.info("Updated trusted device permissions for %s.", device_id)
    if revoked_sessions:
        logger.info("Revoked sessions after permission update for %s (%s).", device_id, revoked_sessions)
    return {
        "ok": True,
        "device": trusted_device.public_dict(),
        "revoked_sessions": revoked_sessions,
    }


@app.post("/api/trusted-devices/{device_id}/trust-review", dependencies=[Depends(verify_token)])
async def resolve_trusted_device_review(device_id: str, payload: TrustReviewRequest) -> dict:
    store = TrustedDeviceStore()
    if store.get_device(device_id) is None:
        raise HTTPException(status_code=404, detail="Trusted device not found.")

    revoked_sessions = pairing_session_book.remove_sessions_for_device(device_id)
    trusted_device = store.resolve_review(device_id, keep_trust=payload.keep_trust)
    if trusted_device is None:
        logger.warning("Trust review removed trusted device %s.", device_id)
        return {
            "ok": True,
            "kept": False,
            "device_id": device_id,
            "revoked_sessions": revoked_sessions,
        }

    logger.info("Trust review kept trusted device %s.", device_id)
    return {
        "ok": True,
        "kept": True,
        "device": trusted_device.public_dict(),
        "revoked_sessions": revoked_sessions,
    }


@app.post("/api/pairing-code", dependencies=[Depends(verify_token)])
async def create_pairing_code(payload: PairingCodeRequest) -> dict:
    pairing_code = pairing_code_book.create_code(guest=payload.guest)
    logger.info("Created temporary pairing code for guest=%s.", payload.guest)
    return {
        "code": pairing_code.code,
        "expires_at": pairing_code.expires_at,
        "guest": pairing_code.guest,
    }


@app.post("/api/pairing-code/consume", dependencies=[Depends(verify_token)])
async def consume_pairing_code(payload: ConsumePairingCodeRequest) -> dict:
    consume_result = pairing_code_book.consume_code_result(payload.code)
    if consume_result.rate_limited:
        logger.warning("Rejected pairing code attempt; new code required.")
        raise HTTPException(
            status_code=429,
            detail="Too many invalid pairing code attempts. Generate a new pairing code.",
        )

    pairing_code = consume_result.pairing_code
    if pairing_code is None:
        raise HTTPException(status_code=400, detail="Invalid or expired pairing code.")

    if pairing_code.guest or not payload.remember_device:
        session_grant = pairing_session_book.create_session(
            device_id=payload.device_id,
            guest=True,
            idle_timeout_seconds=payload.idle_timeout_seconds,
        )
        logger.info("Accepted temporary guest pairing for device %s.", payload.device_id)
        return {
            "ok": True,
            "trusted": False,
            "guest": True,
            "device_id": payload.device_id,
            "session": session_grant.session.public_dict(),
            "session_token": session_grant.session_token,
        }

    credential = TrustedDeviceStore().trust_device(payload.device_id, payload.name)
    session_grant = pairing_session_book.create_session(
        device_id=credential.device.device_id,
        guest=False,
        permissions=credential.device.permissions,
        idle_timeout_seconds=payload.idle_timeout_seconds,
    )
    logger.info("Trusted paired device %s.", payload.device_id)
    return {
        "ok": True,
        "trusted": True,
        "guest": False,
        "device": credential.device.public_dict(),
        "shared_secret": credential.shared_secret,
        "session": session_grant.session.public_dict(),
        "session_token": session_grant.session_token,
    }


@app.get("/api/pairing-sessions", dependencies=[Depends(verify_token)])
async def pairing_sessions() -> dict:
    sessions = [
        session.public_dict()
        for session in pairing_session_book.list_sessions()
    ]
    return {"sessions": sessions}


@app.delete("/api/pairing-sessions/{session_id}", dependencies=[Depends(verify_token)])
async def remove_pairing_session(session_id: str) -> dict:
    removed = pairing_session_book.remove_session(session_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Pairing session not found.")

    logger.info("Removed pairing session %s.", session_id)
    return {"ok": True, "session_id": session_id}


@app.delete("/api/pairing-sessions", dependencies=[Depends(verify_token)])
async def remove_all_pairing_sessions() -> dict:
    removed_count = pairing_session_book.remove_all_sessions()
    logger.warning("Removed all pairing sessions (%s).", removed_count)
    return {"ok": True, "removed": removed_count}


@app.get("/api/macros", dependencies=[Depends(verify_token)])
async def macros() -> dict:
    return {"macros": public_macro_list()}


@app.post("/api/generate-text", dependencies=[Depends(verify_token)])
async def generate_text_api(payload: LLMRequest) -> dict:
    try:
        generated = generate_text(payload.prompt, payload.max_tokens)
        logger.info("Generated text with LLM (%s chars).", len(generated))
        return {"ok": True, "generated": generated}
    except Exception as exc:
        logger.exception("Failed to generate text.")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/send-text", dependencies=[Depends(verify_token)])
async def send_text(payload: TextRequest) -> dict:
    try:
        send_text_to_pc(payload.text)
        logger.info("Sent text to PC (%s chars).", len(payload.text))
        return {"ok": True}
    except Exception as exc:
        logger.exception("Failed to send text.")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/session/send-text")
async def session_send_text(session_id: str = Body(...), session_token: str = Body(...), text: str = Body(...)) -> dict:
    verify_session_permission_or_403(
        session_id,
        session_token,
        "keyboard",
    )
    try:
        send_text_to_pc(text)
        mark_session_active(session_id, session_token)
        logger.info("Sent text to PC from session %s (%s chars).", session_id, len(text))
        return {"ok": True}
    except Exception as exc:
        logger.exception("Failed to send text from session.")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/macro", dependencies=[Depends(verify_token)])
async def macro(payload: MacroRequest) -> dict:
    try:
        result = run_macro(payload.id)
        return {"ok": True, "macro": result}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to run macro.")
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.post("/api/session/macro")
async def session_macro(session_id: str = Body(...), session_token: str = Body(...), id: str = Body(...)) -> dict:
    verify_session_permission_or_403(
        session_id,
        session_token,
        "macros",
    )
    try:
        result = run_macro(id)
        mark_session_active(session_id, session_token)
        logger.info("Ran macro from session %s: %s.", session_id, id)
        return {"ok": True, "macro": result}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to run macro from session.")
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.get("/api/clipboard", dependencies=[Depends(verify_token)])
async def get_clipboard() -> dict:
    try:
        return {"text": clipboard_service.get_clipboard_text()}
    except Exception as exc:
        logger.exception("Failed to get clipboard.")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/session/clipboard/read")
async def session_get_clipboard(session_id: str = Body(...), session_token: str = Body(...)) -> dict:
    verify_session_permission_or_403(
        session_id,
        session_token,
        "clipboard_read",
    )
    try:
        text = clipboard_service.get_clipboard_text()
        mark_session_active(session_id, session_token)
        logger.info("Clipboard read from session %s (%s chars).", session_id, len(text))
        return {"text": text}
    except Exception as exc:
        logger.exception("Failed to get clipboard from session.")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/clipboard", dependencies=[Depends(verify_token)])
async def set_clipboard(payload: ClipboardRequest) -> dict:
    try:
        clipboard_service.set_clipboard_text(payload.text)
        logger.info("Clipboard updated from web UI (%s chars).", len(payload.text))
        return {"ok": True}
    except Exception as exc:
        logger.exception("Failed to set clipboard.")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/session/clipboard/write")
async def session_set_clipboard(session_id: str = Body(...), session_token: str = Body(...), text: str = Body(...)) -> dict:
    verify_session_permission_or_403(
        session_id,
        session_token,
        "clipboard_write",
    )
    try:
        clipboard_service.set_clipboard_text(text)
        mark_session_active(session_id, session_token)
        logger.info("Clipboard updated from session %s (%s chars).", session_id, len(text))
        return {"ok": True}
    except Exception as exc:
        logger.exception("Failed to set clipboard from session.")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/upload", dependencies=[Depends(verify_token)])
async def upload(file: UploadFile = File(...)) -> dict:
    try:
        result = await save_upload(file)
        logger.info("Saved upload %s (%s bytes).", result["filename"], result["bytes"])
        return {"ok": True, "file": result}
    except Exception as exc:
        logger.exception("Failed to save upload.")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/session/upload")
async def session_upload(
    session_id: str = Form(...),
    session_token: str = Form(...),
    file: UploadFile = File(...),
) -> dict:
    verify_session_permission_or_403(
        session_id,
        session_token,
        "file_receive",
    )
    try:
        result = await save_upload(file)
        mark_session_active(session_id, session_token)
        logger.info("Saved upload from session %s: %s (%s bytes).", session_id, result["filename"], result["bytes"])
        return {"ok": True, "file": result}
    except Exception as exc:
        logger.exception("Failed to save upload from session.")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/lockout", dependencies=[Depends(verify_token)])
async def set_lockout(payload: LockoutRequest) -> dict:
    disabled = lockout_state.set_disabled(payload.disabled)
    revoked_sessions = 0
    marked_review_required = 0
    if disabled:
        trusted_session_device_ids = {
            session.device_id
            for session in pairing_session_book.list_sessions()
            if not session.guest
        }
        marked_review_required = TrustedDeviceStore().mark_review_required(trusted_session_device_ids)
        revoked_sessions = pairing_session_book.remove_all_sessions()
    logger.warning("Emergency lockout set to %s.", disabled)
    if revoked_sessions:
        logger.warning("Emergency lockout revoked pairing sessions (%s).", revoked_sessions)
    if marked_review_required:
        logger.warning("Emergency lockout marked trusted devices for review (%s).", marked_review_required)
    return {
        "ok": True,
        "disabled": disabled,
        "revoked_sessions": revoked_sessions,
        "marked_review_required": marked_review_required,
    }


@app.websocket("/ws/touchpad")
async def touchpad(websocket: WebSocket, token: Optional[str] = Query(default=None)) -> None:
    if token is not None:
        if not await verify_websocket_token(websocket, token):
            return
        await handle_touchpad_socket(websocket)
        return

    await websocket.accept()
    try:
        message = await websocket.receive_json()
    except Exception:
        await websocket.close(code=1008, reason="Missing or invalid pairing token.")
        return

    if message.get("type") != "auth" or not token_is_valid(message.get("token")):
        if message.get("type") != "session_auth":
            await websocket.close(code=1008, reason="Missing or invalid pairing token.")
            return

        session_id = message.get("session_id")
        session_token = message.get("session_token")
        if not isinstance(session_id, str) or not isinstance(session_token, str):
            await websocket.close(code=1008, reason="Missing or invalid session credentials.")
            return
        if pairing_session_book.verify_session(session_id, session_token) is None:
            await websocket.close(code=1008, reason="Missing or invalid session credentials.")
            return

        await handle_touchpad_socket(
            websocket,
            accepted=True,
            authorize_mouse=lambda: pairing_session_book.verify_session_permission(
                session_id,
                session_token,
                "mouse",
            )
            is not None,
            mark_session_active=lambda: pairing_session_book.mark_session_active(
                session_id,
                session_token,
            ),
        )
        return

    await handle_touchpad_socket(websocket, accepted=True)
