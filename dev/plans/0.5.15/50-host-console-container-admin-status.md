# Slice 50 — secure host-console container Admin TUI status

**Status:** complete; independently reviewed and verified on 2026-08-16.

## Scope disposition

Slice 50 supersedes the draft's conditional two-topology proposal with the
ratified host-console-only profile. It is default-off and supports only a TUI
on the host reaching an Airlock container through a `127.0.0.1` published port,
native TLS, an owner-only token file, and a CA-validated capability JWT.

Deferred: IPv6 and peer-container TUI deployment, operational history,
reverse-proxy/mTLS integrations, Docker/CIDR trust, forwarded identity,
configuration and virtual-key CRUD, and all-interface Admin publication.

## Delivered

- Added `admin.remote_tui`, startup validation, a 15-minute JWT ceiling, a
  required `admin:remote_tui` anchor, exact allowed scopes, and a remote
  master-key denial. Existing profile-off behavior is unchanged.
- Added the non-secret `remote_tui_jwt` audit context to successful remote
  mutation records; the actor remains the verified JWT subject.
- Added a separate limited remote TUI and strict `--admin-token-file` /
  `--admin-ca-file` transport. It always uses CA/name-validated HTTPS,
  including `localhost`, and does not reuse the full local dashboard.
- Added a complete opt-in Compose manifest with loopback-only publication and
  read-only TLS mounts. The existing Compose file is untouched.
- Updated requirements, design ratification, configuration reference, and
  Admin/operator runbook including rotation, emergency revocation, and
  rollback.

## TDD and review evidence

- RED: new PDP/profile tests initially failed for master-key admission,
  unrestricted scopes/lifetime, and missing remote-profile validation.
  GREEN: all focused Admin policy tests passed after implementation.
- RED: remote transport tests initially failed to import the new connection
  boundary. GREEN: verified bearer injection, HTTPS/CA verification,
  localhost hostname verification, protected token files, and no server TLS
  environment fallback.
- RED/GREEN: remote CLI tests establish both-file preflight, no proxy start or
  daemon ownership, and dispatch to the isolated remote UI.
- Structural Compose test asserts the only published mapping is host loopback
  and TLS/config mounts are read-only.

## Verification

- Focused Admin, TUI transport/CLI, and Compose tests passed (135 tests);
  separate contract/Compose checks passed (9 tests).
- Ruff check/format, strict MkDocs, Compose render with
  `--no-env-resolution`, `git diff --check`, and `make sync && make verify`
  passed. The standard Compose file has no diff.
- `make test` was running under the verifier at record time; non-PTY attempts
  were truncated by the execution harness before yielding a result. A live
  Docker-daemon topology test requires access to `/var/run/docker.sock`, which
  this sandbox denies; structural Compose and TLS/PDP/client tests cover the
  release contract without daemon access.
