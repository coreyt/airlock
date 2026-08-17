"""RED contracts for the bounded same-host Slice 70 fleet client."""

from __future__ import annotations

import socket
import time

import pytest

from airlock.tui.fleet_profile import (
    FleetProfileError,
    FleetTarget,
    load_fleet_profile,
    parse_fleet_origin,
)


def _secure(path, text):
    path.write_text(text)
    path.chmod(0o600)
    return path


def _profile(tmp_path, targets):
    return _secure(tmp_path / "fleet.yaml", "targets:\n" + "\n".join(targets))


def test_profile_loads_only_distinct_secure_loopback_targets(tmp_path):
    ca1 = _secure(tmp_path / "one-ca.pem", "CA one")
    token1 = _secure(tmp_path / "one.jwt", "abc.def.ghi")
    ca2 = _secure(tmp_path / "two-ca.pem", "CA two")
    token2 = _secure(tmp_path / "two.jwt", "jkl.mno.pqr")
    profile = _profile(
        tmp_path,
        [
            "  - id: one\n    name: One\n    origin: https://localhost:4101\n"
            f"    ca_file: {ca1}\n    token_file: {token1}",
            "  - id: two\n    name: Two\n    origin: https://127.0.0.1:4102\n"
            f"    ca_file: {ca2}\n    token_file: {token2}",
        ],
    )

    targets = load_fleet_profile(profile)

    assert [target.instance_id for target in targets] == ["one", "two"]
    assert targets[0].host == "localhost"
    assert targets[0].token == "abc.def.ghi"
    assert targets[0].ca_pem == "CA one"


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:4000",
        "https://10.0.0.1:4000",
        "https://localhost",
        "https://localhost:4000/path",
        "https://user@localhost:4000",
        "https://localhost:4000?x=1",
    ],
)
def test_origin_rejects_nonexact_loopback_https(origin):
    with pytest.raises(FleetProfileError):
        parse_fleet_origin(origin)


def test_profile_rejects_duplicate_secret_references_and_insecure_inventory(tmp_path):
    ca = _secure(tmp_path / "ca.pem", "CA")
    token = _secure(tmp_path / "token.jwt", "abc.def.ghi")
    profile = _profile(
        tmp_path,
        [
            "  - id: one\n    name: One\n    origin: https://localhost:4101\n"
            f"    ca_file: {ca}\n    token_file: {token}",
            "  - id: two\n    name: Two\n    origin: https://localhost:4102\n"
            f"    ca_file: {ca}\n    token_file: {token}",
        ],
    )
    with pytest.raises(FleetProfileError, match="duplicate"):
        load_fleet_profile(profile)
    profile.chmod(0o644)
    with pytest.raises(FleetProfileError, match="permissions"):
        load_fleet_profile(profile)


def test_profile_rejects_inventory_symlink_before_reading_target_references(tmp_path):
    profile = _secure(tmp_path / "fleet.yaml", "targets: []\n")
    linked = tmp_path / "fleet-link.yaml"
    linked.symlink_to(profile)

    with pytest.raises(FleetProfileError, match="regular file"):
        load_fleet_profile(linked)


def test_profile_rejects_embedded_or_unsupported_values(tmp_path):
    ca = _secure(tmp_path / "ca.pem", "CA")
    token = _secure(tmp_path / "token.jwt", "abc.def.ghi")
    profile = _profile(
        tmp_path,
        [
            "  - id: one\n    name: One\n    origin: https://localhost:4101\n"
            f"    ca_file: {ca}\n    token_file: {token}\n    token: must-not-be-here"
        ],
    )
    with pytest.raises(FleetProfileError, match="unsupported"):
        load_fleet_profile(profile)


@pytest.mark.parametrize(
    "extra",
    [
        "provider_config: never",
        "token_file: /duplicate.jwt",
    ],
)
def test_profile_rejects_unknown_root_and_duplicate_yaml_keys(tmp_path, extra):
    ca = _secure(tmp_path / "ca.pem", "CA")
    token = _secure(tmp_path / "token.jwt", "abc.def.ghi")
    profile = _secure(
        tmp_path / "fleet.yaml",
        "targets:\n"
        "  - id: one\n    name: One\n    origin: https://localhost:4101\n"
        f"    ca_file: {ca}\n    token_file: {token}\n    {extra}\n",
    )
    with pytest.raises(FleetProfileError):
        load_fleet_profile(profile)

    profile.write_text("targets: []\nprovider_config: never\n")
    with pytest.raises(FleetProfileError, match="unsupported"):
        load_fleet_profile(profile)


def test_fleet_client_rejects_rebinding_and_peer_mismatch(monkeypatch):
    from airlock.tui.fleet_client import FleetAdminClient, FleetTransportError

    target = FleetTarget("one", "One", "localhost", "4101", "jwt", "CA")
    client = FleetAdminClient(
        [target],
        resolver=lambda _host, _port: [
            (socket.AF_INET, ("127.0.0.1", 4101)),
            (socket.AF_INET, ("10.0.0.1", 4101)),
        ],
    )
    with pytest.raises(FleetTransportError, match="loopback"):
        client._request(target)


def test_fleet_client_rejects_a_connected_peer_other_than_vetted_address(monkeypatch):
    from airlock.tui.fleet_client import FleetAdminClient, FleetTransportError

    class FakeSocket:
        def settimeout(self, _timeout):
            pass

        def getpeername(self):
            return ("127.0.0.2", 4101)

        def close(self):
            pass

    target = FleetTarget("one", "One", "localhost", "4101", "jwt", "CA")
    client = FleetAdminClient(
        [target], resolver=lambda _host, _port: [(socket.AF_INET, ("127.0.0.1", 4101))]
    )

    class FakeContext:
        check_hostname = True

    monkeypatch.setattr(
        "airlock.tui.fleet_client.ssl.create_default_context",
        lambda **_kwargs: FakeContext(),
    )
    monkeypatch.setattr(client, "_open_tls", lambda *_args: FakeSocket())

    with pytest.raises(FleetTransportError, match="loopback"):
        client._request(target)


def test_fleet_client_limits_selection_and_isolates_target_failure():
    from airlock.tui.fleet_client import FleetAdminClient

    targets = [
        FleetTarget(str(i), str(i), "127.0.0.1", str(4100 + i), "jwt", "CA")
        for i in range(11)
    ]
    client = FleetAdminClient(targets)
    with pytest.raises(ValueError, match="at most 10"):
        client.refresh([str(i) for i in range(11)])
    with pytest.raises(ValueError, match="not configured"):
        client.refresh(["missing"])


def test_fleet_client_bounds_a_slow_resolver_and_never_renders_loopback():
    from airlock.tui.fleet_client import FleetAdminClient

    target = FleetTarget("one", "One", "localhost", "4101", "jwt", "CA")

    def slow_resolver(_host, _port):
        time.sleep(10)
        return [(socket.AF_INET, ("127.0.0.1", 4101))]

    client = FleetAdminClient([target], resolver=slow_resolver)
    started = time.monotonic()
    result = client.fetch(target)
    assert time.monotonic() - started < 5.5
    assert result.state == "unavailable"


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ((401, {}), "auth"),
        ((403, {}), "forbidden"),
        ((302, {}), "unavailable"),
    ],
)
def test_fleet_client_classifies_only_permitted_result_states(
    monkeypatch, outcome, expected
):
    from airlock.tui.fleet_client import FleetAdminClient

    target = FleetTarget("one", "One", "localhost", "4101", "jwt", "CA")
    client = FleetAdminClient([target])
    monkeypatch.setattr(client, "_request", lambda _target: outcome)
    assert client.fetch(target).state == expected


def test_fleet_client_caps_response_before_json_parsing():
    from airlock.tui.fleet_client import (
        _MAX_BODY_BYTES,
        FleetAdminClient,
        FleetTransportError,
    )

    class ResponseSocket:
        def __init__(self):
            self._chunks = [
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n",
                b"x" * (_MAX_BODY_BYTES + 1),
            ]

        def settimeout(self, _timeout):
            pass

        def recv(self, _size):
            return self._chunks.pop(0) if self._chunks else b""

    with pytest.raises(FleetTransportError, match="unavailable"):
        FleetAdminClient([])._read_response(ResponseSocket(), time.monotonic() + 1)


def test_fleet_target_repr_and_fleet_ui_are_secret_blind_and_manual():
    import inspect

    from airlock.tui import fleet_app

    target = FleetTarget("one", "One", "localhost", "4101", "very-secret", "CA")
    assert "very-secret" not in repr(target)
    assert "set_interval" not in inspect.getsource(fleet_app.FleetAdminApp)


def test_fleet_ipv6_origin_uses_a_bracketed_http_host_header():
    from airlock.tui.fleet_client import _host_header

    assert _host_header("::1", "4101") == "[::1]:4101"


def test_fleet_resolver_pool_is_bounded_and_tls_handshake_uses_deadline(monkeypatch):
    from airlock.tui.fleet_client import FleetAdminClient

    class RawSocket:
        def __init__(self):
            self.timeouts = []

        def settimeout(self, timeout):
            self.timeouts.append(timeout)

        def connect(self, _address):
            pass

        def close(self):
            pass

    class TlsSocket:
        def settimeout(self, _timeout):
            pass

    class Context:
        def wrap_socket(self, _raw, **_kwargs):
            return TlsSocket()

    client = FleetAdminClient([])
    assert client._resolver_pool._max_workers == 4
    raw = RawSocket()
    moments = iter((0.0, 1.5, 2.0))
    monkeypatch.setattr("airlock.tui.fleet_client.socket.socket", lambda *_args: raw)
    monkeypatch.setattr(
        "airlock.tui.fleet_client.time.monotonic", lambda: next(moments)
    )

    client._open_tls(socket.AF_INET, ("127.0.0.1", 4101), Context(), "localhost", 5.0)

    assert raw.timeouts == [2.0, 3.5]
    client.close()
