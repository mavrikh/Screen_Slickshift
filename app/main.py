from __future__ import annotations

import asyncio
import logging
import time
from logging.handlers import RotatingFileHandler
from typing import Optional

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import clipboard as clipboard_service
from app.commands import public_macro_list, run_macro
from app.config import LOG_DIR, STATIC_DIR, ensure_directories, get_or_create_pairing_token, get_receive_dir, set_receive_dir, settings
from app.device_identity import get_or_create_device_identity
from app.files import save_upload
from app.discovery import DiscoveredDevice, DiscoveryService, PendingPairRequest, get_local_ip
from app.edge_detector import DetectorState, EdgeDetector, make_detector_config
from app.handoff import make_handoff_layout, make_layout_screen, normalize_edge
from app.handoff_remote import (
    RemoteHandoffBridge,
    RemoteTarget,
    remote_pair,
    remote_request_pair,
    remote_trusted_reconnect,
    remote_cancel_pair_request,
    remote_end_session,
    remote_push_peer_credential,
    warp_remote_cursor,
    warp_remote_cursor_session,
)
from app.input_control import input_control_status, send_text_to_pc, warp_cursor_to_center
from app.llm import generate_text
from app.pairing import PairingCodeBook, PairingSessionBook, TrustedDeviceStore
from app.protocol import parse_message, protocol_capabilities
from app.security import token_is_valid, verify_token, verify_websocket_token
from app.state import lockout_state, trusted_connections_state
from app.transfer_history import TransferHistory
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


class TrustedConnectionsRequest(BaseModel):
    enabled: bool


class SessionPeerCredentialRequest(BaseModel):
    session_id: str
    session_token: str
    peer_device_id: str
    peer_host: str
    peer_port: int = 8765
    peer_shared_secret: str


class RemotePeerCredentialPayload(BaseModel):
    host: str
    port: int = 8765
    session_id: str
    session_token: str
    shared_secret: str


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


class RecordTrustedDeviceRequest(BaseModel):
    device_id: str
    name: str
    permissions: Optional[dict[str, bool]] = None


class TrustReviewRequest(BaseModel):
    keep_trust: bool


class LLMRequest(BaseModel):
    prompt: str
    max_tokens: int = 1000


class ReceiveDirectoryRequest(BaseModel):
    path: str


class HandoffScreenRequest(BaseModel):
    screen_id: str
    device_id: str
    name: str
    rect: dict
    primary: bool = False
    edge_enabled: bool = True


class HandoffLayoutPreviewRequest(BaseModel):
    screens: list[HandoffScreenRequest]
    snap_tolerance_px: int = 24
    min_overlap_px: int = 80


class RemoteHandoffStartRequest(BaseModel):
    host: str
    port: int = 8765
    token: str
    path: str = "/ws/touchpad"


class RemoteHandoffEventRequest(BaseModel):
    type: str
    dx: float = 0
    dy: float = 0
    button: str = "left"
    down: bool = True
    amount: int = 0
    key: str = ""
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    meta: bool = False


class HandoffArmRequest(BaseModel):
    edge: str
    dwell_ms: int = 400
    zone_px: int = 5
    screen_index: int = 0


class RequestPairRequest(BaseModel):
    device_id: str
    name: str


class DiscoveryPairRequest(BaseModel):
    code: str
    device_id: str
    name: str
    permissions: Optional[dict[str, bool]] = None
    remember_device: bool = True
    idle_timeout_seconds: int = 600


class TrustedReconnectRequest(BaseModel):
    device_id: str
    shared_secret: str
    idle_timeout_seconds: int = 600


class HandoffReturnArmRequest(BaseModel):
    return_edge: str
    dwell_ms: int = 400


class RemoteHandoffSessionStartRequest(BaseModel):
    host: str
    port: int = 8765
    path: str = "/ws/touchpad"
    session_id: str
    session_token: str


class RemoteDiscoveryTargetRequest(BaseModel):
    host: str
    port: int = 8765


class CancelPairRequest(BaseModel):
    device_id: str


class RemoteDiscoveryPairPayload(BaseModel):
    host: str
    port: int = 8765
    code: str
    permissions: Optional[dict[str, bool]] = None
    remember_device: bool = True
    idle_timeout_seconds: int = 600


class RemoteDiscoveryReconnectPayload(BaseModel):
    host: str
    port: int = 8765
    shared_secret: str
    idle_timeout_seconds: int = 600


class SessionHandoffArmRequest(SessionRequest):
    edge: str
    dwell_ms: int = 400
    zone_px: int = 5
    screen_index: int = 0


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
    logging.getLogger("websockets.server").setLevel(logging.WARNING)
    logging.getLogger("websockets.client").setLevel(logging.WARNING)
    logging.getLogger("websockets.legacy.server").setLevel(logging.WARNING)
    logging.getLogger("websockets.legacy.client").setLevel(logging.WARNING)
    # Prevent uvicorn's access logger from propagating to our root handler.
    # The --no-access-log flag disables uvicorn's own handler but propagation
    # to basicConfig's StreamHandler still occurs without this.
    logging.getLogger("uvicorn.access").propagate = False


configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name)
pairing_code_book = PairingCodeBook()
pairing_session_book = PairingSessionBook()
transfer_history = TransferHistory()
remote_handoff_bridge = RemoteHandoffBridge()
edge_detector = EdgeDetector()
discovery_service = DiscoveryService()
discovery_code_book = PairingCodeBook(ttl_seconds=45)
_pending_pair_request: Optional[PendingPairRequest] = None
_pending_peer_credentials: dict[str, dict] = {}  # device_id → {shared_secret, host, port}

# Allow all origins: this is a LAN-only tool and security comes from the pairing
# token, not the Origin header. Starlette's CORS middleware blocks WebSocket
# connections with 403 when allow_origins=[] and a browser sends an Origin header
# (always the case when accessing via LAN IP rather than localhost).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins or ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["X-Pairing-Token", "Content-Type"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/app-ui", StaticFiles(directory=STATIC_DIR / "app-ui"), name="app-ui")


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
    return FileResponse(STATIC_DIR / "app-ui" / "index.html")


@app.get("/classic")
async def classic_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/file-transfer")
async def file_transfer_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "file_transfer.html")


@app.get("/handoff")
async def handoff_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "handoff.html")


@app.get("/app")
async def new_app_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "app-ui" / "index.html")



@app.get("/api/status")
async def status() -> dict:
    return {
        "app": settings.app_name,
        "disabled": lockout_state.is_disabled(),
        "auth": "token-required",
        "max_upload_bytes": settings.max_upload_bytes,
        "protocol": protocol_capabilities(),
    }


@app.get("/api/auth/check", dependencies=[Depends(verify_token)])
async def auth_check() -> dict:
    return {"ok": True}


@app.get("/api/input/status", dependencies=[Depends(verify_token)])
async def input_status(check_backend: bool = Query(default=False)) -> dict:
    return input_control_status(check_backend=check_backend)


@app.get("/api/file-transfer/settings", dependencies=[Depends(verify_token)])
async def file_transfer_settings() -> dict:
    return {
        "receive_dir": str(get_receive_dir()),
        "max_upload_bytes": settings.max_upload_bytes,
    }


@app.patch("/api/file-transfer/settings", dependencies=[Depends(verify_token)])
async def update_file_transfer_settings(payload: ReceiveDirectoryRequest) -> dict:
    try:
        receive_dir = set_receive_dir(payload.path)
        logger.info("Updated receive folder.")
        return {
            "ok": True,
            "receive_dir": str(receive_dir),
            "max_upload_bytes": settings.max_upload_bytes,
        }
    except Exception as exc:
        logger.exception("Failed to update receive folder.")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/file-transfer/transfers", dependencies=[Depends(verify_token)])
async def file_transfer_records() -> dict:
    return {
        "transfers": [
            record.public_dict()
            for record in transfer_history.list_records()
        ],
    }


@app.get("/api/file-transfer/targets", dependencies=[Depends(verify_token)])
async def file_transfer_targets() -> dict:
    targets = []
    for trusted_device in TrustedDeviceStore().list_devices():
        target = trusted_device.public_dict()
        file_receive = bool(trusted_device.permissions.get("file_receive"))
        review_required = bool(trusted_device.review_required)
        target["can_receive_files"] = file_receive and not review_required
        if review_required:
            target["receive_blocked_reason"] = "trust review required"
        elif not file_receive:
            target["receive_blocked_reason"] = "file receive disabled"
        else:
            target["receive_blocked_reason"] = ""
        targets.append(target)
    return {"targets": targets}


@app.post("/api/handoff/layout/preview", dependencies=[Depends(verify_token)])
async def handoff_layout_preview(payload: HandoffLayoutPreviewRequest) -> dict:
    screens = []
    for screen in payload.screens:
        layout_screen = make_layout_screen(
            screen_id=screen.screen_id,
            device_id=screen.device_id,
            name=screen.name,
            rect=screen.rect,
            primary=screen.primary,
            edge_enabled=screen.edge_enabled,
        )
        if layout_screen is not None:
            screens.append(layout_screen)

    layout = make_handoff_layout(
        screens,
        snap_tolerance_px=payload.snap_tolerance_px,
        min_overlap_px=payload.min_overlap_px,
    )
    return layout.public_dict()


@app.get("/api/handoff/remote/status", dependencies=[Depends(verify_token)])
async def handoff_remote_status() -> dict:
    return remote_handoff_bridge.status()


@app.post("/api/handoff/remote/start", dependencies=[Depends(verify_token)])
async def handoff_remote_start(payload: RemoteHandoffStartRequest) -> dict:
    try:
        target = RemoteTarget.from_values(payload.host, payload.port, payload.path)
        status = await remote_handoff_bridge.start(target, payload.token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Remote handoff connection failed: %s", exc)
        raise HTTPException(status_code=400, detail="Remote handoff connection failed.") from exc

    if not status.reachable:
        raise HTTPException(status_code=400, detail="Remote receiver is unreachable.")
    if status.disabled:
        raise HTTPException(status_code=409, detail="Remote receiver emergency stop is active.")
    required_events = {"mouse_move", "mouse_button", "scroll", "ping"}
    if status.input_events and not required_events.issubset(set(status.input_events)):
        raise HTTPException(status_code=409, detail="Remote receiver does not advertise mouse control support.")

    return {
        "ok": True,
        "connected": True,
        "target": {"host": target.host, "port": target.port, "path": target.path},
        "remote_status": status.public_dict(),
    }


@app.post("/api/handoff/remote/event", dependencies=[Depends(verify_token)])
async def handoff_remote_event(payload: RemoteHandoffEventRequest) -> dict:
    try:
        return await remote_handoff_bridge.send_event(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Remote handoff event failed: %s", exc)
        raise HTTPException(status_code=400, detail="Remote handoff event failed.") from exc


@app.post("/api/handoff/remote/stop", dependencies=[Depends(verify_token)])
async def handoff_remote_stop() -> dict:
    return await remote_handoff_bridge.stop()


@app.post("/api/handoff/remote/arm-return", dependencies=[Depends(verify_token)])
async def handoff_remote_arm_return(payload: HandoffReturnArmRequest) -> dict:
    try:
        return await remote_handoff_bridge.arm_return_detector(payload.return_edge, payload.dwell_ms)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/handoff/remote/return-state", dependencies=[Depends(verify_token)])
async def handoff_remote_return_state() -> dict:
    try:
        return await remote_handoff_bridge.get_return_state()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/handoff/remote/disarm-return", dependencies=[Depends(verify_token)])
async def handoff_remote_disarm_return() -> dict:
    return await remote_handoff_bridge.disarm_return_detector()


@app.get("/api/handoff/remote/screen-info", dependencies=[Depends(verify_token)])
async def handoff_remote_screen_info() -> dict:
    try:
        return await remote_handoff_bridge.get_remote_screen_info()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/screen/info", dependencies=[Depends(verify_token)])
async def screen_info() -> dict:
    try:
        import pyautogui
        size = await asyncio.to_thread(pyautogui.size)
        pos = await asyncio.to_thread(pyautogui.position)
        return {
            "width": size.width,
            "height": size.height,
            "cursor_x": pos.x,
            "cursor_y": pos.y,
            "error": "",
        }
    except Exception as exc:
        return {"width": None, "height": None, "cursor_x": None, "cursor_y": None, "error": str(exc)}


@app.get("/api/screen/monitors", dependencies=[Depends(verify_token)])
async def screen_monitors() -> dict:
    try:
        from app.edge_detector import get_monitors
        monitors = await asyncio.to_thread(get_monitors)
        return {"monitors": monitors}
    except Exception as exc:
        return {"monitors": [], "error": str(exc)}


@app.post("/api/handoff/arm", dependencies=[Depends(verify_token)])
async def handoff_arm(payload: HandoffArmRequest) -> dict:
    config = make_detector_config(
        edge=payload.edge,
        dwell_ms=payload.dwell_ms,
        zone_px=payload.zone_px,
        screen_index=payload.screen_index,
    )
    if config is None:
        raise HTTPException(status_code=400, detail=f"Invalid edge: {payload.edge!r}")
    await edge_detector.arm(config)
    return edge_detector.public_dict()


@app.post("/api/handoff/disarm", dependencies=[Depends(verify_token)])
async def handoff_disarm() -> dict:
    await edge_detector.disarm()
    return edge_detector.public_dict()


@app.get("/api/handoff/detector/state", dependencies=[Depends(verify_token)])
async def handoff_detector_state() -> dict:
    return edge_detector.public_dict()


@app.post("/api/handoff/warp-cursor", dependencies=[Depends(verify_token)])
async def handoff_warp_cursor() -> dict:
    try:
        x, y = await asyncio.to_thread(warp_cursor_to_center)
        return {"ok": True, "x": x, "y": y}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/handoff/remote/warp-cursor", dependencies=[Depends(verify_token)])
async def handoff_remote_warp_cursor() -> dict:
    try:
        return await remote_handoff_bridge.warp_cursor()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/session/screen/info")
async def session_screen_info(payload: SessionRequest) -> dict:
    if pairing_session_book.verify_session(payload.session_id, payload.session_token) is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    try:
        import pyautogui
        size = await asyncio.to_thread(pyautogui.size)
        return {"width": size.width, "height": size.height, "error": ""}
    except Exception as exc:
        return {"width": None, "height": None, "error": str(exc)}


@app.post("/api/session/handoff/arm")
async def session_handoff_arm(payload: SessionHandoffArmRequest) -> dict:
    verify_session_permission_or_403(payload.session_id, payload.session_token, "mouse")
    config = make_detector_config(edge=payload.edge, dwell_ms=payload.dwell_ms, zone_px=payload.zone_px, screen_index=payload.screen_index)
    if config is None:
        raise HTTPException(status_code=400, detail=f"Invalid edge: {payload.edge!r}")
    await edge_detector.arm(config)
    return edge_detector.public_dict()


@app.post("/api/session/handoff/disarm")
async def session_handoff_disarm(payload: SessionRequest) -> dict:
    if pairing_session_book.verify_session(payload.session_id, payload.session_token) is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    await edge_detector.disarm()
    return edge_detector.public_dict()


@app.post("/api/session/handoff/detector/state")
async def session_handoff_detector_state(payload: SessionRequest) -> dict:
    if pairing_session_book.verify_session(payload.session_id, payload.session_token) is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    return edge_detector.public_dict()


@app.post("/api/session/peer-credential")
async def session_peer_credential(payload: SessionPeerCredentialRequest) -> dict:
    if pairing_session_book.verify_session(payload.session_id, payload.session_token) is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    _pending_peer_credentials[payload.peer_device_id] = {
        "shared_secret": payload.peer_shared_secret,
        "host": payload.peer_host,
        "port": payload.peer_port,
    }
    return {"ok": True}


@app.get("/api/peer-credential/{device_id}", dependencies=[Depends(verify_token)])
async def get_peer_credential(device_id: str) -> dict:
    cred = _pending_peer_credentials.pop(device_id, None)
    if cred is None:
        return {"found": False}
    return {"found": True, **cred}


@app.post("/api/discovery/remote/peer-credential", dependencies=[Depends(verify_token)])
async def discovery_remote_peer_credential(payload: RemotePeerCredentialPayload) -> dict:
    identity = get_or_create_device_identity()
    local_ip = get_local_ip()
    try:
        target = RemoteTarget.from_values(payload.host, payload.port)
        return await asyncio.to_thread(
            remote_push_peer_credential,
            target,
            payload.session_id,
            payload.session_token,
            identity.device_id,
            local_ip,
            8765,
            payload.shared_secret,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/session/end")
async def session_end(payload: SessionRequest) -> dict:
    if pairing_session_book.verify_session(payload.session_id, payload.session_token) is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    pairing_session_book.remove_session(payload.session_id)
    logger.info("Session %s ended by remote client.", payload.session_id)
    return {"ok": True}


@app.post("/api/session/handoff/warp-cursor")
async def session_handoff_warp_cursor(payload: SessionRequest) -> dict:
    if pairing_session_book.verify_session_permission(
        payload.session_id, payload.session_token, "mouse"
    ) is None:
        raise HTTPException(status_code=401, detail="Invalid session or mouse permission required.")
    try:
        x, y = await asyncio.to_thread(warp_cursor_to_center)
        return {"ok": True, "x": x, "y": y}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.delete("/api/file-transfer/transfers", dependencies=[Depends(verify_token)])
async def clear_file_transfer_records() -> dict:
    removed = len(transfer_history.list_records())
    transfer_history.clear()
    logger.info("Cleared recent file-transfer records (%s).", removed)
    return {"ok": True, "removed": removed}


@app.get("/api/device", dependencies=[Depends(verify_token)])
async def device() -> dict:
    identity = get_or_create_device_identity()
    return {"device": identity.to_dict()}


@app.post("/api/trusted-devices/record", dependencies=[Depends(verify_token)])
async def record_trusted_device(payload: RecordTrustedDeviceRequest) -> dict:
    store = TrustedDeviceStore()
    existing = store.get_device(payload.device_id)
    if existing is not None:
        return {"ok": True, "device": existing.public_dict(), "created": False}
    credential = store.trust_device(payload.device_id, payload.name, payload.permissions)
    logger.info("Recorded trusted device %s (%s).", payload.device_id, payload.name)
    return {"ok": True, "device": credential.device.public_dict(), "created": True, "shared_secret": credential.shared_secret}


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


@app.post("/api/discovery/browse/start", dependencies=[Depends(verify_token)])
async def discovery_browse_start() -> dict:
    identity = get_or_create_device_identity()
    await discovery_service.start_browse(device_id=identity.device_id)
    return discovery_service.public_dict()


@app.post("/api/discovery/browse/stop", dependencies=[Depends(verify_token)])
async def discovery_browse_stop() -> dict:
    await discovery_service.stop()
    return discovery_service.public_dict()


@app.post("/api/discovery/advertise", dependencies=[Depends(verify_token)])
async def discovery_advertise() -> dict:
    identity = get_or_create_device_identity()
    await discovery_service.start_advertise(port=8765, device_name=identity.name, device_id=identity.device_id)
    return discovery_service.public_dict()


@app.post("/api/discovery/advertise/stop", dependencies=[Depends(verify_token)])
async def discovery_advertise_stop() -> dict:
    await discovery_service.stop_advertise()
    return discovery_service.public_dict()


@app.post("/api/discovery/stop", dependencies=[Depends(verify_token)])
async def discovery_stop() -> dict:
    await discovery_service.stop()
    return discovery_service.public_dict()


@app.get("/api/discovery/browse", dependencies=[Depends(verify_token)])
async def discovery_browse() -> dict:
    store = TrustedDeviceStore()
    devices = []
    for d in discovery_service.get_discovered():
        entry = d.public_dict()
        entry["trusted"] = store.get_device(d.device_id) is not None
        devices.append(entry)
    return {
        "advertising": discovery_service.advertising,
        "browsing": discovery_service.browsing,
        "trusted_connections_enabled": trusted_connections_state.is_enabled(),
        "devices": devices,
    }


@app.post("/api/discovery/request-pair")
async def discovery_request_pair(payload: RequestPairRequest) -> dict:
    global _pending_pair_request
    if lockout_state.is_disabled():
        raise HTTPException(status_code=503, detail="This device is in emergency lockout.")
    pending = _pending_pair_request
    if pending is not None and not pending.is_expired():
        raise HTTPException(status_code=429, detail="A pairing request is already in progress.")
    pairing_code = discovery_code_book.create_code(guest=False)
    _pending_pair_request = PendingPairRequest(
        requester_id=payload.device_id,
        requester_name=payload.name,
        code=pairing_code.code,
        expires_at=pairing_code.expires_at,
    )
    logger.info("Discovery pairing requested by %s (%s).", payload.name, payload.device_id)
    return {"ok": True, "expires_in": int(pairing_code.expires_at - time.time())}


@app.get("/api/discovery/pending-request", dependencies=[Depends(verify_token)])
async def discovery_pending_request() -> dict:
    global _pending_pair_request
    pending = _pending_pair_request
    if pending is None or pending.is_expired():
        _pending_pair_request = None
        return {"pending": False}
    return {"pending": True, **pending.public_dict()}


@app.post("/api/discovery/dismiss-request", dependencies=[Depends(verify_token)])
async def discovery_dismiss_request() -> dict:
    global _pending_pair_request
    _pending_pair_request = None
    return {"ok": True}


@app.post("/api/discovery/cancel-request")
async def discovery_cancel_request(payload: CancelPairRequest) -> dict:
    global _pending_pair_request
    pending = _pending_pair_request
    if pending is None or pending.is_expired():
        _pending_pair_request = None
        return {"ok": True}
    if pending.requester_id != payload.device_id:
        raise HTTPException(status_code=403, detail="Not authorized to cancel this request.")
    _pending_pair_request = None
    logger.info("Pairing request cancelled by requester %s.", payload.device_id)
    return {"ok": True}


@app.post("/api/discovery/pair")
async def discovery_pair(payload: DiscoveryPairRequest) -> dict:
    global _pending_pair_request
    if lockout_state.is_disabled():
        raise HTTPException(status_code=503, detail="This device is in emergency lockout.")
    consume_result = discovery_code_book.consume_code_result(payload.code)
    if consume_result.rate_limited:
        logger.warning("Discovery pairing: code rate-limited for device %s.", payload.device_id)
        raise HTTPException(status_code=429, detail="Too many invalid attempts. Request a new pairing code.")
    if consume_result.pairing_code is None:
        raise HTTPException(status_code=400, detail="Invalid or expired pairing code.")
    _pending_pair_request = None
    idle_timeout = payload.idle_timeout_seconds if payload.idle_timeout_seconds in (60, 180, 300, 600, 1200, 1800) else 600
    if not payload.remember_device:
        session_grant = pairing_session_book.create_session(
            device_id=payload.device_id,
            guest=True,
            permissions=payload.permissions,
            idle_timeout_seconds=idle_timeout,
        )
        logger.info("Discovery guest session created for %s.", payload.device_id)
        return {
            "ok": True, "trusted": False, "guest": True,
            "session": session_grant.session.public_dict(),
            "session_token": session_grant.session_token,
        }
    credential = TrustedDeviceStore().trust_device(payload.device_id, payload.name, payload.permissions)
    session_grant = pairing_session_book.create_session(
        device_id=credential.device.device_id,
        guest=False,
        permissions=credential.device.permissions,
        idle_timeout_seconds=idle_timeout,
    )
    logger.info("Discovery trusted session created for %s.", payload.device_id)
    return {
        "ok": True, "trusted": True, "guest": False,
        "device": credential.device.public_dict(),
        "shared_secret": credential.shared_secret,
        "session": session_grant.session.public_dict(),
        "session_token": session_grant.session_token,
    }


@app.get("/api/trusted-connections", dependencies=[Depends(verify_token)])
async def get_trusted_connections() -> dict:
    return {"enabled": trusted_connections_state.is_enabled()}


@app.post("/api/trusted-connections", dependencies=[Depends(verify_token)])
async def set_trusted_connections(payload: TrustedConnectionsRequest) -> dict:
    enabled = trusted_connections_state.set_enabled(payload.enabled)
    logger.info("Trusted device connections set to %s.", enabled)
    return {"enabled": enabled}


@app.post("/api/pairing/trusted-reconnect")
async def trusted_reconnect(payload: TrustedReconnectRequest) -> dict:
    if lockout_state.is_disabled():
        raise HTTPException(status_code=503, detail="This device is in emergency lockout.")
    if not trusted_connections_state.is_enabled():
        raise HTTPException(
            status_code=503,
            detail="Trusted device connections are disabled on this device. Enable 'Trusted device connections' on the target device to allow automatic reconnection.",
        )
    device = TrustedDeviceStore().verify_and_mark_seen(payload.device_id, payload.shared_secret)
    if device is None:
        raise HTTPException(status_code=401, detail="Unknown device or invalid credentials.")
    idle_timeout = payload.idle_timeout_seconds if payload.idle_timeout_seconds in (60, 180, 300, 600, 1200, 1800) else 600
    session_grant = pairing_session_book.create_session(
        device_id=device.device_id,
        guest=False,
        permissions=device.permissions,
        idle_timeout_seconds=idle_timeout,
    )
    logger.info("Trusted reconnect accepted for device %s.", payload.device_id)
    return {
        "ok": True,
        "device": device.public_dict(),
        "session": session_grant.session.public_dict(),
        "session_token": session_grant.session_token,
    }


@app.post("/api/handoff/remote/start-session", dependencies=[Depends(verify_token)])
async def handoff_remote_start_session(payload: RemoteHandoffSessionStartRequest) -> dict:
    try:
        target = RemoteTarget.from_values(payload.host, payload.port, payload.path)
        status = await remote_handoff_bridge.start_with_session(target, payload.session_id, payload.session_token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Session-based remote handoff connection failed: %s", exc)
        raise HTTPException(status_code=400, detail="Remote handoff connection failed.") from exc
    if not status.reachable:
        raise HTTPException(status_code=400, detail="Remote receiver is unreachable.")
    if status.disabled:
        raise HTTPException(status_code=409, detail="Remote receiver emergency stop is active.")
    return {
        "ok": True,
        "connected": True,
        "target": {"host": target.host, "port": target.port, "path": target.path},
        "remote_status": status.public_dict(),
    }


@app.post("/api/discovery/remote/request-pair", dependencies=[Depends(verify_token)])
async def discovery_remote_request_pair(payload: RemoteDiscoveryTargetRequest) -> dict:
    identity = get_or_create_device_identity()
    try:
        target = RemoteTarget.from_values(payload.host, payload.port)
        return await asyncio.to_thread(remote_request_pair, target, identity.device_id, identity.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/discovery/remote/pair", dependencies=[Depends(verify_token)])
async def discovery_remote_pair(payload: RemoteDiscoveryPairPayload) -> dict:
    identity = get_or_create_device_identity()
    try:
        target = RemoteTarget.from_values(payload.host, payload.port)
        return await asyncio.to_thread(
            remote_pair,
            target,
            payload.code,
            identity.device_id,
            identity.name,
            payload.permissions,
            payload.remember_device,
            payload.idle_timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/discovery/remote/reconnect", dependencies=[Depends(verify_token)])
async def discovery_remote_reconnect(payload: RemoteDiscoveryReconnectPayload) -> dict:
    identity = get_or_create_device_identity()
    try:
        target = RemoteTarget.from_values(payload.host, payload.port)
        return await asyncio.to_thread(
            remote_trusted_reconnect,
            target,
            identity.device_id,
            payload.shared_secret,
            payload.idle_timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/discovery/remote/cancel-pair", dependencies=[Depends(verify_token)])
async def discovery_remote_cancel_pair(payload: RemoteDiscoveryTargetRequest) -> dict:
    identity = get_or_create_device_identity()
    try:
        target = RemoteTarget.from_values(payload.host, payload.port)
        return await asyncio.to_thread(remote_cancel_pair_request, target, identity.device_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError:
        return {"ok": True}


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
        transfer_history.add_received(result, source="owner")
        logger.info("Saved upload %s (%s bytes).", result["filename"], result["bytes"])
        return {"ok": True, "file": result}
    except Exception as exc:
        transfer_history.add_rejected(
            filename=file.filename or "upload.bin",
            source="owner",
            detail=str(exc),
        )
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
        transfer_history.add_received(result, source="session")
        logger.info("Saved upload from session %s: %s (%s bytes).", session_id, result["filename"], result["bytes"])
        return {"ok": True, "file": result}
    except Exception as exc:
        transfer_history.add_rejected(
            filename=file.filename or "upload.bin",
            source="session",
            detail=str(exc),
        )
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

    auth_message = parse_message(message)
    if not auth_message.supported_version:
        await websocket.close(code=1008, reason="Unsupported protocol version.")
        return

    if auth_message.type != "auth" or not token_is_valid(auth_message.payload.get("token")):
        if auth_message.type != "session_auth":
            await websocket.close(code=1008, reason="Missing or invalid pairing token.")
            return

        session_id = auth_message.payload.get("session_id")
        session_token = auth_message.payload.get("session_token")
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
                session_id, session_token, "mouse",
            ) is not None,
            authorize_keyboard=lambda: pairing_session_book.verify_session_permission(
                session_id, session_token, "keyboard",
            ) is not None,
            mark_session_active=lambda: pairing_session_book.mark_session_active(
                session_id, session_token,
            ),
        )
        return

    await handle_touchpad_socket(websocket, accepted=True)
