"""Strict local inventory for the read-only same-host fleet TUI."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_MAX_TARGETS = 10
_MAX_FILE_BYTES = 64 * 1024
_MAX_FIELD_LENGTH = 256
_TARGET_FIELDS = {"id", "name", "origin", "token_file", "ca_file"}


class FleetProfileError(ValueError):
    """Inventory or local file does not satisfy fleet-v1 policy."""


class _NoDuplicateSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that never silently overwrites a supplied key."""

    def construct_mapping(self, node, deep=False):  # type: ignore[no-untyped-def]
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise FleetProfileError("fleet inventory has an invalid mapping key")
            if key in mapping:
                raise FleetProfileError("fleet inventory contains a duplicate key")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


@dataclass(frozen=True)
class FleetTarget:
    instance_id: str
    display_name: str
    host: str
    port: str
    token: str = field(repr=False)
    ca_pem: str = field(repr=False)


def _read_secure_text(path_value: str | Path, label: str) -> str:
    path = Path(path_value)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise FleetProfileError(f"{label} requires no-follow support")
    try:
        descriptor = os.open(path, os.O_RDONLY | nofollow)
    except OSError as exc:
        raise FleetProfileError(f"{label} must be a readable regular file") from exc
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise FleetProfileError(f"{label} must be a regular file")
            if info.st_mode & 0o077:
                raise FleetProfileError(f"{label} permissions must be owner-only")
            if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
                raise FleetProfileError(f"{label} must be owned by this user")
            if info.st_size > _MAX_FILE_BYTES:
                raise FleetProfileError(f"{label} is too large")
            return handle.read()
    except (OSError, UnicodeError) as exc:
        raise FleetProfileError(f"{label} is unreadable") from exc


def parse_fleet_origin(origin: str) -> tuple[str, str]:
    """Return a strict literal same-host HTTPS origin's host and explicit port."""
    if not isinstance(origin, str) or len(origin) > _MAX_FIELD_LENGTH:
        raise FleetProfileError("invalid target origin")
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError as exc:
        raise FleetProfileError("invalid target origin") from exc
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or host not in _LOOPBACK_HOSTS
        or port is None
        or not 1 <= port <= 65535
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise FleetProfileError("target origin must be exact loopback HTTPS with port")
    return host, str(port)


def _field(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_FIELD_LENGTH:
        raise FleetProfileError(f"invalid target {name}")
    return value


def load_fleet_profile(path: str | Path) -> list[FleetTarget]:
    """Load bounded, owner-only inventory and per-target secret references."""
    raw = _read_secure_text(path, "fleet inventory")
    try:
        document = yaml.load(raw, Loader=_NoDuplicateSafeLoader)
    except (yaml.YAMLError, FleetProfileError) as exc:
        raise FleetProfileError("fleet inventory is invalid YAML") from exc
    if not isinstance(document, dict) or set(document) != {"targets"}:
        raise FleetProfileError("fleet inventory contains unsupported fields")
    targets = document["targets"]
    if not isinstance(targets, list) or not 1 <= len(targets) <= _MAX_TARGETS:
        raise FleetProfileError("fleet inventory must contain 1 through 10 targets")
    loaded: list[FleetTarget] = []
    ids: set[str] = set()
    token_refs: set[str] = set()
    ca_refs: set[str] = set()
    for entry in targets:
        if not isinstance(entry, dict):
            raise FleetProfileError("fleet target must be a mapping")
        if set(entry) != _TARGET_FIELDS:
            raise FleetProfileError("fleet target contains unsupported fields")
        instance_id = _field(entry.get("id"), "id")
        name = _field(entry.get("name"), "name")
        host, port = parse_fleet_origin(_field(entry.get("origin"), "origin"))
        token_ref = _field(entry.get("token_file"), "token_file")
        ca_ref = _field(entry.get("ca_file"), "ca_file")
        if instance_id in ids or token_ref in token_refs or ca_ref in ca_refs:
            raise FleetProfileError("duplicate target id or credential reference")
        token = _read_secure_text(token_ref, "fleet token").strip()
        if not token or not all(
            char.isascii() and (char.isalnum() or char in "-_.") for char in token
        ):
            raise FleetProfileError("fleet token is invalid")
        ca_pem = _read_secure_text(ca_ref, "fleet CA")
        if not ca_pem.strip():
            raise FleetProfileError("fleet CA is empty")
        ids.add(instance_id)
        token_refs.add(token_ref)
        ca_refs.add(ca_ref)
        loaded.append(FleetTarget(instance_id, name, host, port, token, ca_pem))
    return loaded
