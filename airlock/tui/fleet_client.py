"""Bounded direct transport for Slice 70's read-only loopback fleet view."""

from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from airlock.tui.fleet_profile import FleetTarget

_MAX_SELECTED = 10
_MAX_CONCURRENCY = 4
_CONNECT_TIMEOUT = 2.0
_TOTAL_TIMEOUT = 5.0
_MAX_BODY_BYTES = 64 * 1024


class FleetTransportError(RuntimeError):
    """Secret-blind, classified fleet transport failure."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class FleetResult:
    instance_id: str
    display_name: str
    state: str
    observed_at: str | None = None
    provider_count: int | None = None


Resolver = Callable[[str, int], list[tuple[int, tuple]]]


def _host_header(host: str, port: str) -> str:
    """Format the authority without making an IPv6 origin ambiguous."""
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _resolve_loopback(host: str, port: int) -> list[tuple[int, tuple]]:
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise FleetTransportError("unavailable") from exc
    vetted = [(family, sockaddr) for family, _type, _proto, _canon, sockaddr in answers]
    if not vetted or any(
        not ipaddress.ip_address(sockaddr[0].split("%", 1)[0]).is_loopback
        for _family, sockaddr in vetted
    ):
        raise FleetTransportError("loopback")
    return vetted


class FleetAdminClient:
    """Direct, non-proxy, non-redirecting Admin reader with bounded fan-out."""

    def __init__(
        self, targets: list[FleetTarget], *, resolver: Resolver = _resolve_loopback
    ):
        self._targets = {target.instance_id: target for target in targets}
        self._resolver = resolver
        self._last_success: dict[str, FleetResult] = {}
        self._resolver_pool = ThreadPoolExecutor(
            max_workers=_MAX_CONCURRENCY, thread_name_prefix="airlock-fleet-resolve"
        )

    def close(self) -> None:
        """Cancel queued lookups when the UI exits."""
        self._resolver_pool.shutdown(wait=False, cancel_futures=True)

    def _read_response(
        self, tls_socket: ssl.SSLSocket, deadline: float
    ) -> tuple[int, dict]:
        raw = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FleetTransportError("unavailable")
            tls_socket.settimeout(remaining)
            chunk = tls_socket.recv(4096)
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > _MAX_BODY_BYTES + 16 * 1024:
                raise FleetTransportError("unavailable")
        head, marker, body = bytes(raw).partition(b"\r\n\r\n")
        if not marker or len(body) > _MAX_BODY_BYTES:
            raise FleetTransportError("unavailable")
        lines = head.split(b"\r\n")
        try:
            status = int(lines[0].split()[1])
        except (IndexError, ValueError):
            raise FleetTransportError("unavailable") from None
        if status != 200:
            return status, {}
        try:
            payload = json.loads(body or b"{}")
        except (TypeError, ValueError):
            raise FleetTransportError("unavailable") from None
        if not isinstance(payload, dict):
            raise FleetTransportError("unavailable")
        return status, payload

    @staticmethod
    def _open_tls(
        family: int,
        address: tuple,
        context: ssl.SSLContext,
        hostname: str,
        deadline: float,
    ) -> ssl.SSLSocket:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FleetTransportError("unavailable")
        raw_socket = socket.socket(family, socket.SOCK_STREAM)
        raw_socket.settimeout(min(_CONNECT_TIMEOUT, remaining))
        raw_socket.connect(address)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raw_socket.close()
            raise FleetTransportError("unavailable")
        raw_socket.settimeout(remaining)
        tls_socket = context.wrap_socket(raw_socket, server_hostname=hostname)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            tls_socket.close()
            raise FleetTransportError("unavailable")
        tls_socket.settimeout(remaining)
        return tls_socket

    def _resolve_with_deadline(
        self, host: str, port: int, deadline: float
    ) -> list[tuple[int, tuple]]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FleetTransportError("unavailable")
        future = self._resolver_pool.submit(self._resolver, host, port)
        try:
            return future.result(timeout=remaining)
        except TimeoutError as exc:
            future.cancel()
            raise FleetTransportError("unavailable") from exc

    def _request(self, target: FleetTarget) -> tuple[int, dict]:
        deadline = time.monotonic() + _TOTAL_TIMEOUT
        answers = self._resolve_with_deadline(target.host, int(target.port), deadline)
        if not answers or any(
            not ipaddress.ip_address(sockaddr[0].split("%", 1)[0]).is_loopback
            for _family, sockaddr in answers
        ):
            raise FleetTransportError("loopback")
        try:
            context = ssl.create_default_context(cadata=target.ca_pem)
            context.check_hostname = True
            last_error: OSError | None = None
            for family, address in answers:
                try:
                    tls_socket = self._open_tls(
                        family, address, context, target.host, deadline
                    )
                except ssl.SSLError:
                    raise
                except OSError as exc:
                    last_error = exc
                    continue
                peer = tls_socket.getpeername()[0].split("%", 1)[0]
                if (
                    peer != address[0].split("%", 1)[0]
                    or not ipaddress.ip_address(peer).is_loopback
                ):
                    tls_socket.close()
                    raise FleetTransportError("loopback")
                request = (
                    f"GET /airlock/admin/providers HTTP/1.1\r\n"
                    f"Host: {_host_header(target.host, target.port)}\r\n"
                    f"Authorization: Bearer {target.token}\r\n"
                    "Accept: application/json\r\nConnection: close\r\n\r\n"
                ).encode()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    tls_socket.close()
                    raise FleetTransportError("unavailable")
                tls_socket.settimeout(remaining)
                tls_socket.sendall(request)
                try:
                    return self._read_response(tls_socket, deadline)
                finally:
                    tls_socket.close()
            if last_error is not None:
                raise last_error
            raise FleetTransportError("unavailable")
        except FleetTransportError:
            raise
        except ssl.SSLError as exc:
            raise FleetTransportError("tls") from exc
        except (OSError, ValueError) as exc:
            raise FleetTransportError("unavailable") from exc

    def fetch(self, target: FleetTarget) -> FleetResult:
        try:
            status, payload = self._request(target)
        except FleetTransportError as exc:
            previous = self._last_success.get(target.instance_id)
            if previous is not None:
                return FleetResult(
                    target.instance_id,
                    target.display_name,
                    "stale",
                    previous.observed_at,
                    previous.provider_count,
                )
            state = exc.category if exc.category == "tls" else "unavailable"
            return FleetResult(target.instance_id, target.display_name, state)
        if status == 401:
            return FleetResult(target.instance_id, target.display_name, "auth")
        if status == 403:
            return FleetResult(target.instance_id, target.display_name, "forbidden")
        if status != 200 or not isinstance(payload.get("providers"), dict):
            return FleetResult(target.instance_id, target.display_name, "unavailable")
        result = FleetResult(
            target.instance_id,
            target.display_name,
            "fresh",
            datetime.now(UTC).isoformat(),
            len(payload["providers"]),
        )
        self._last_success[target.instance_id] = result
        return result

    def refresh(self, selected_ids: list[str]) -> list[FleetResult]:
        if not 1 <= len(selected_ids) <= _MAX_SELECTED:
            raise ValueError("select 1 through at most 10 targets")
        if len(set(selected_ids)) != len(selected_ids):
            raise ValueError("target selection contains duplicates")
        try:
            selected = [self._targets[instance_id] for instance_id in selected_ids]
        except KeyError as exc:
            raise ValueError("selected target is not configured") from exc
        results: dict[str, FleetResult] = {}
        with ThreadPoolExecutor(
            max_workers=min(_MAX_CONCURRENCY, len(selected))
        ) as pool:
            pending = {
                pool.submit(self.fetch, target): target.instance_id
                for target in selected
            }
            for future in as_completed(pending):
                instance_id = pending[future]
                try:
                    results[instance_id] = future.result()
                except Exception:  # pragma: no cover - defense at isolated boundary
                    target = self._targets[instance_id]
                    results[instance_id] = FleetResult(
                        instance_id, target.display_name, "unavailable"
                    )
        return [results[instance_id] for instance_id in selected_ids]
