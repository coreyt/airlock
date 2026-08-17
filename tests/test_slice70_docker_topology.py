"""Opt-in Docker topology proof for Slice 70's read-only fleet client.

This module is selected solely by ``-m docker``.  It is intentionally a direct
Docker API consumer: it creates only UUID-labelled containers and removes only
the IDs it created after confirming their exact run label.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import jwt
import pytest

from airlock.tui.fleet_client import FleetAdminClient
from airlock.tui.fleet_profile import FleetTarget, load_fleet_profile

pytestmark = pytest.mark.docker

_RUN_LABEL = "org.airlock.slice71.run"
_REVISION_LABEL = "org.opencontainers.image.revision"


def _label_format(label: str) -> str:
    """Return a Docker Go-template using a correctly quoted label key."""
    return f"{{{{index .Config.Labels {json.dumps(label)}}}}}"


def _run(*args: str, timeout: float = 45) -> str:
    """Run Docker/OpenSSL without rendering potentially sensitive output."""
    completed = subprocess.run(
        args,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    if completed.returncode:
        raise AssertionError(
            f"{args[0]} command failed with exit {completed.returncode}"
        )
    return completed.stdout.strip()


def _secure(path: Path, content: str) -> Path:
    path.write_text(content)
    path.chmod(0o600)
    return path


def _certificate_pair(directory: Path, name: str) -> tuple[Path, Path, Path]:
    """Generate one ephemeral CA and 127.0.0.1 leaf, all owner-only."""
    ca_key = directory / f"{name}-ca.key"
    ca_pem = directory / f"{name}-ca.pem"
    leaf_key = directory / f"{name}.key"
    csr = directory / f"{name}.csr"
    leaf_pem = directory / f"{name}.crt"
    ext = directory / f"{name}.ext"
    _run(
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(ca_key),
        "-out",
        str(ca_pem),
        "-subj",
        f"/CN=slice71-{name}-ca",
        "-days",
        "1",
    )
    _run(
        "openssl",
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(leaf_key),
        "-out",
        str(csr),
        "-subj",
        "/CN=127.0.0.1",
    )
    _secure(ext, "subjectAltName = IP:127.0.0.1\n")
    _run(
        "openssl",
        "x509",
        "-req",
        "-in",
        str(csr),
        "-CA",
        str(ca_pem),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-out",
        str(leaf_pem),
        "-days",
        "1",
        "-extfile",
        str(ext),
    )
    for path in (ca_key, ca_pem, leaf_key, csr, leaf_pem):
        path.chmod(0o600)
    return ca_pem, leaf_pem, leaf_key


def _token(secret: str, subject: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "airlock",
            "sub": subject,
            "scope": ["admin:remote_tui", "admin:read"],
            "iat": now,
            "exp": now + 15 * 60,
            "jti": uuid4().hex,
        },
        secret,
        algorithm="HS256",
    )


def _docker_port(container_id: str) -> tuple[str, int]:
    ports = json.loads(
        _run(
            "docker",
            "inspect",
            container_id,
            "--format",
            "{{json .NetworkSettings.Ports}}",
        )
    )
    binding = ports["4000/tcp"]
    assert isinstance(binding, list) and len(binding) == 1
    assert binding[0]["HostIp"] == "127.0.0.1"
    return binding[0]["HostIp"], int(binding[0]["HostPort"])


def _assert_safe_container(container_id: str, run_id: str, revision: str) -> None:
    assert (
        _run(
            "docker",
            "inspect",
            container_id,
            "--format",
            _label_format(_RUN_LABEL),
        )
        == run_id
    )
    assert (
        _run(
            "docker",
            "inspect",
            container_id,
            "--format",
            _label_format(_REVISION_LABEL),
        )
        == revision
    )
    host_config = json.loads(
        _run("docker", "inspect", container_id, "--format", "{{json .HostConfig}}")
    )
    mounts = json.loads(
        _run("docker", "inspect", container_id, "--format", "{{json .Mounts}}")
    )
    assert host_config["NetworkMode"] != "host"
    assert host_config["Privileged"] is False
    assert not any(mount.get("Source") == "/var/run/docker.sock" for mount in mounts)


def _https_request(port: int, ca_pem: str, token: str, method: str, path: str) -> int:
    """Use a direct HTTPS connection; urllib proxy settings cannot influence it."""
    context = ssl.create_default_context(cadata=ca_pem)
    with socket.create_connection(("127.0.0.1", port), timeout=2) as raw:
        with context.wrap_socket(raw, server_hostname="127.0.0.1") as tls:
            tls.settimeout(3)
            request = (
                f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                f"Authorization: Bearer {token}\r\nConnection: close\r\n\r\n"
            ).encode()
            tls.sendall(request)
            response = bytearray()
            while b"\r\n" not in response and len(response) < 8192:
                chunk = tls.recv(min(1024, 8192 - len(response)))
                if not chunk:
                    break
                response.extend(chunk)
            head = bytes(response).split(b"\r\n", 1)[0]
    return int(head.split()[1])


def _wait_livez(port: int, ca_pem: str) -> None:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            if _https_request(port, ca_pem, "", "GET", "/livez") == 200:
                return
        except (OSError, ssl.SSLError, ValueError, IndexError):
            pass
        time.sleep(0.25)
    raise AssertionError(
        "Slice 71 container did not become live before bounded deadline"
    )


def _run_container(
    image: str,
    run_id: str,
    revision: str,
    name: str,
    directory: Path,
    cert: Path,
    key: Path,
    secret: str,
) -> str:
    config = _secure(
        directory / f"{name}.yaml",
        "model_list: []\n"
        "admin:\n"
        "  enabled: true\n"
        "  trust_loopback: false\n"
        "  remote_tui: true\n"
        "  fleet_read_tui: true\n"
        "litellm_settings:\n"
        '  callbacks: ["airlock.callbacks.model_override_headers.proxy_model_override_headers"]\n',
    )
    env_file = _secure(
        directory / f"{name}.env",
        "AIRLOCK_CONFIG=/app/config.yaml\n"
        "AIRLOCK_HOST=0.0.0.0\n"
        "AIRLOCK_PORT=4000\n"
        "AIRLOCK_SSL_CERTFILE=/run/airlock-tls/server.crt\n"
        "AIRLOCK_SSL_KEYFILE=/run/airlock-tls/server.key\n"
        f"AIRLOCK_JWT_SECRET={secret}\n"
        "AIRLOCK_MCP_STARTUP_MODE=off\n"
        "AIRLOCK_STARTUP_MODEL_DISCOVERY=0\n"
        "AIRLOCK_ENABLE_MCP_SERVERS=0\n"
        "AIRLOCK_LOG_DIR=/tmp/airlock-slice71\n",
    )
    return _run(
        "docker",
        "run",
        "--detach",
        "--name",
        name,
        "--label",
        f"{_RUN_LABEL}={run_id}",
        "--label",
        f"{_REVISION_LABEL}={revision}",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--env-file",
        str(env_file),
        "--publish",
        "127.0.0.1::4000",
        "--mount",
        f"type=bind,src={config},dst=/app/config.yaml,readonly",
        "--mount",
        f"type=bind,src={cert},dst=/run/airlock-tls/server.crt,readonly",
        "--mount",
        f"type=bind,src={key},dst=/run/airlock-tls/server.key,readonly",
        image,
    )


def _write_inventory(directory: Path, targets: list[dict[str, str]]) -> Path:
    lines = ["targets:"]
    for target in targets:
        lines.extend(
            [
                f"  - id: {target['id']}",
                f"    name: {target['name']}",
                f"    origin: {target['origin']}",
                f"    token_file: {target['token']}",
                f"    ca_file: {target['ca']}",
            ]
        )
    return _secure(directory / "fleet.yaml", "\n".join(lines) + "\n")


def _fetch_state(target: FleetTarget) -> str:
    """Fetch one deliberately substituted target without retaining worker state."""
    client = FleetAdminClient([target])
    try:
        return client.fetch(target).state
    finally:
        client.close()


def test_slice70_two_container_tls_fleet_topology(
    tmp_path: Path,
):
    """Prove the Slice 70 client against two isolated real TLS containers."""
    if os.geteuid() == 0:
        pytest.fail("Slice 71 must run as a non-root Docker client")
    image = os.environ["SLICE71_IMAGE"]
    run_id = os.environ["SLICE71_RUN_ID"]
    revision = os.environ["SLICE71_REVISION"]
    assert (
        _run(
            "docker",
            "image",
            "inspect",
            image,
            "--format",
            _label_format(_RUN_LABEL),
        )
        == run_id
    )
    assert (
        _run(
            "docker",
            "image",
            "inspect",
            image,
            "--format",
            _label_format(_REVISION_LABEL),
        )
        == revision
    )

    containers: list[str] = []
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    trap_port = listener.getsockname()[1]
    trap_hit = threading.Event()

    def trap() -> None:
        try:
            listener.settimeout(6)
            listener.accept()
            trap_hit.set()
        except OSError:
            pass

    thread = threading.Thread(target=trap, daemon=True)
    thread.start()
    try:
        ca_a, cert_a, key_a = _certificate_pair(tmp_path, "a")
        ca_b, cert_b, key_b = _certificate_pair(tmp_path, "b")
        secret_a, secret_b = uuid4().hex, uuid4().hex
        token_a = _secure(tmp_path / "a.jwt", _token(secret_a, "slice71-a"))
        token_b = _secure(tmp_path / "b.jwt", _token(secret_b, "slice71-b"))
        token_c = _secure(tmp_path / "c.jwt", "abc.def.ghi")
        ca_c = _secure(tmp_path / "c-ca.pem", "unused CA\n")
        name_a, name_b = (f"airlock-s71-{run_id}-a", f"airlock-s71-{run_id}-b")
        containers.append(
            _run_container(
                image, run_id, revision, name_a, tmp_path, cert_a, key_a, secret_a
            )
        )
        containers.append(
            _run_container(
                image, run_id, revision, name_b, tmp_path, cert_b, key_b, secret_b
            )
        )
        for container in containers:
            _assert_safe_container(container, run_id, revision)
        _host_a, port_a = _docker_port(containers[0])
        _host_b, port_b = _docker_port(containers[1])
        assert port_a != port_b
        _wait_livez(port_a, ca_a.read_text())
        _wait_livez(port_b, ca_b.read_text())

        inventory = _write_inventory(
            tmp_path,
            [
                {
                    "id": "a",
                    "name": "A",
                    "origin": f"https://127.0.0.1:{port_a}",
                    "token": str(token_a),
                    "ca": str(ca_a),
                },
                {
                    "id": "b",
                    "name": "B",
                    "origin": f"https://127.0.0.1:{port_b}",
                    "token": str(token_b),
                    "ca": str(ca_b),
                },
                {
                    "id": "trap",
                    "name": "Trap",
                    "origin": f"https://127.0.0.1:{trap_port}",
                    "token": str(token_c),
                    "ca": str(ca_c),
                },
            ],
        )
        # No-follow applies to the inventory itself, not merely its references.
        inventory_link = tmp_path / "fleet-link.yaml"
        inventory_link.symlink_to(inventory)
        with pytest.raises(ValueError):
            load_fleet_profile(inventory_link)

        targets = load_fleet_profile(inventory)
        client = FleetAdminClient(targets)
        try:
            assert [result.state for result in client.refresh(["a", "b"])] == [
                "fresh",
                "fresh",
            ]
            target_a, target_b = targets[:2]
            assert _fetch_state(replace(target_b, token=target_a.token)) == "forbidden"
            assert _fetch_state(replace(target_a, ca_pem=target_b.ca_pem)) == "tls"
            assert (
                _https_request(
                    port_a,
                    target_a.ca_pem,
                    target_a.token,
                    "POST",
                    "/airlock/admin/providers/slice71/clear-quarantine",
                )
                == 403
            )
            assert not trap_hit.wait(0.5)
        finally:
            client.close()
    finally:
        listener.close()
        for container in containers:
            try:
                if (
                    _run(
                        "docker",
                        "inspect",
                        container,
                        "--format",
                        _label_format(_RUN_LABEL),
                    )
                    == run_id
                ):
                    _run("docker", "rm", "--force", container)
            except (AssertionError, subprocess.TimeoutExpired):
                pass
