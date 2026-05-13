from __future__ import annotations

import asyncio
import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf


logger = logging.getLogger(__name__)

SERVICE_TYPE = "_slickshift._tcp.local."
PAIR_REQUEST_TTL = 45.0


@dataclass(frozen=True)
class DiscoveredDevice:
    name: str
    device_id: str
    host: str
    port: int

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "device_id": self.device_id,
            "host": self.host,
            "port": self.port,
        }


@dataclass
class PendingPairRequest:
    requester_id: str
    requester_name: str
    code: str
    expires_at: float

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def seconds_remaining(self) -> int:
        return max(0, int(self.expires_at - time.time()))

    def public_dict(self) -> dict[str, Any]:
        return {
            "requester_id": self.requester_id,
            "requester_name": self.requester_name,
            "code": self.code,
            "expires_at": self.expires_at,
            "seconds_remaining": self.seconds_remaining(),
        }


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0)
            sock.connect(("10.254.254.254", 1))
            return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"


class DiscoveryService:
    def __init__(self) -> None:
        self._zc: Optional[AsyncZeroconf] = None
        self._info: Optional[AsyncServiceInfo] = None
        self._browser: Optional[AsyncServiceBrowser] = None
        self._found: dict[str, DiscoveredDevice] = {}
        self._advertising = False
        self._browsing = False
        self._own_device_id: Optional[str] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def advertising(self) -> bool:
        return self._advertising

    @property
    def browsing(self) -> bool:
        return self._browsing

    def get_discovered(self) -> list[DiscoveredDevice]:
        return list(self._found.values())

    def public_dict(self) -> dict[str, Any]:
        return {
            "advertising": self._advertising,
            "browsing": self._browsing,
            "discovered": [d.public_dict() for d in self.get_discovered()],
        }

    async def _ensure_zc(self) -> None:
        if self._zc is None:
            self._loop = asyncio.get_event_loop()
            self._zc = AsyncZeroconf()

    async def _ensure_browser(self) -> None:
        await self._ensure_zc()
        if self._browser is None:
            self._browser = AsyncServiceBrowser(
                self._zc.zeroconf,
                SERVICE_TYPE,
                handlers=[self._on_service_state_change],
            )

    async def start_browse(self, device_id: str) -> None:
        if self._browsing:
            return
        self._own_device_id = device_id
        await self._ensure_browser()
        self._browsing = True
        logger.info("Discovery: browsing started")

    async def start_advertise(self, port: int, device_name: str, device_id: str) -> None:
        if self._advertising:
            return
        self._own_device_id = device_id
        await self._ensure_zc()  # advertise needs zeroconf but not the service browser
        local_ip = get_local_ip()
        self._info = AsyncServiceInfo(
            SERVICE_TYPE,
            f"{device_name}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(local_ip)],
            port=port,
            properties={
                b"device_id": device_id.encode("utf-8"),
                b"name": device_name.encode("utf-8"),
            },
        )
        await self._zc.async_register_service(self._info)
        self._advertising = True
        logger.info(
            "Discovery: advertising %s (%s) at %s:%d",
            device_name, device_id, local_ip, port,
        )

    async def stop_advertise(self) -> None:
        if not self._advertising or self._zc is None:
            return
        if self._info is not None:
            try:
                await self._zc.async_unregister_service(self._info)
            except Exception as exc:
                logger.debug("Discovery: unregister error: %s", exc)
            self._info = None
        self._advertising = False
        logger.info("Discovery: advertising stopped, browsing continues")

    async def start(self, port: int, device_name: str, device_id: str) -> None:
        await self.start_browse(device_id)
        await self.start_advertise(port, device_name, device_id)

    async def stop(self) -> None:
        if self._zc is None:
            return
        if self._info is not None:
            try:
                await self._zc.async_unregister_service(self._info)
            except Exception as exc:
                logger.debug("Discovery: unregister error: %s", exc)
        try:
            await self._zc.async_close()
        except Exception as exc:
            logger.debug("Discovery: close error: %s", exc)
        self._zc = None
        self._info = None
        self._browser = None
        self._advertising = False
        self._browsing = False
        self._found.clear()
        logger.info("Discovery stopped.")

    def _on_service_state_change(
        self,
        zeroconf: Any,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        if state_change is ServiceStateChange.Removed:
            self._found.pop(name, None)
            logger.debug("Discovery: removed %s", name)
            return
        if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
            if self._loop is not None:
                self._loop.call_soon_threadsafe(
                    lambda n=name: self._loop.create_task(  # type: ignore[union-attr]
                        self._resolve_service(zeroconf, service_type, n)
                    )
                )

    async def _resolve_service(self, zeroconf: Any, service_type: str, name: str) -> None:
        info = AsyncServiceInfo(service_type, name)
        try:
            await info.async_request(zeroconf, 3000)
        except Exception as exc:
            logger.debug("Discovery: resolve failed for %s: %s", name, exc)
            return

        if not info.addresses:
            return

        try:
            host = socket.inet_ntoa(info.addresses[0])
            port = info.port or 8765
            raw_props = info.properties or {}
            props = {
                k.decode("utf-8", errors="replace"): v.decode("utf-8", errors="replace")
                for k, v in raw_props.items()
            }
            device_id = props.get("device_id", "")
            device_name = props.get("name", name.split(".")[0])
        except Exception as exc:
            logger.debug("Discovery: parse error for %s: %s", name, exc)
            return

        if device_id == self._own_device_id:
            return

        device = DiscoveredDevice(
            name=device_name,
            device_id=device_id,
            host=host,
            port=port,
        )
        self._found[name] = device
        logger.info("Discovery: found %s at %s:%d", device_name, host, port)
