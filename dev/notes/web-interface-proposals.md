# Airlock Web Interface: Feasibility & Infrastructure Proposals

_Date: 2026-06-28_

## Part 1 — Can the web UI stay identical/similar to the TUI?

**Short answer: Yes — more easily than for most apps, because Airlock's TUI is built on
Textual.** Textual was designed to run in both the terminal and the browser, and the
project's data layer is already cleanly separated from the UI.

### Why this is favorable

The TUI (`airlock/tui/app.py`, `AirlockApp` extends `textual.app.App`) is
**Textual `>=6.2.1`**, with 6 screens (Overview, Guards, Logs, Config, Test, Advisor).
There is a **clean architecture seam**:

- The TUI reads through `airlock.fast.state.StateStore` (a pure, Textual-free interface).
- Control actions already go through an **HTTP admin API** (`airlock/admin/http.py`:
  clear quarantine, reset circuits, clear backoff).
- Canonical state is JSONL logs that any consumer can ingest.

So the backend is *already* web-ready. The only question is how the browser renders the
front end.

### Three degrees of "same-ness" (web research findings)

| Approach | Visual fidelity to TUI | What it is |
|---|---|---|
| **`textual-serve`** (self-hosted) | **Pixel-identical** | Runs the *exact* Textual app as a subprocess; browser renders via **xterm.js** over a websocket. |
| **`textual-web`** (Textualize-hosted relay) | **Pixel-identical** | Same idea, plus "firewall-busting" public URLs via Textualize's relay. |
| **Native web app w/ shared design** | **Similar, not identical** | Separate HTML/CSS frontend mirroring the TUI's layout/color/terminology. |

**Key mechanism:** with textual-serve/web, the app is **not** compiled to run in the
browser. It runs **server-side as a Python subprocess**; xterm.js translates
keyboard/mouse into ANSI escape codes sent over websocket to the app's stdin, and stdout
is streamed back. "You can turn your terminal app into a web application with no
additional code."

**Important limitation:** Textual has **no roadmap for true HTML/DOM rendering** — the
browser experience is always a terminal emulator. It *looks and behaves like a terminal*
(monospace, keyboard-driven), is **not mobile-friendly**, and `textual-serve` ships
**no authentication or multi-user model** of its own (you must supply that layer). Its
only security claim is narrow: the custom protocol "does not expose a shell," so a browser
user can't do anything the app author didn't intend.

---

## Part 2 — Two infrastructure proposals

Both reuse the existing `StateStore` + admin HTTP API; they differ in front-end strategy
and operational surface.

### Proposal A — "Mirror": `textual-serve` wrapper behind an auth proxy

Serve the **existing Textual app verbatim** in the browser.

```
Browser (xterm.js)
  └─ websocket ─► Auth Reverse Proxy (Caddy/nginx + OIDC/SSO, TLS)
                   └─ textual-serve (spawns AirlockApp subprocess per session)
                        └─ reads StateStore / JSONL, calls /airlock/admin/* (loopback)
```

- **Infra:** `textual-serve` (`Server("airlock tui ...").serve()`), fronted by a reverse
  proxy (Caddy or nginx) terminating TLS and enforcing auth — `oauth2-proxy`/SSO or mTLS.
  Single container/VM; scales across CPUs via subprocess-per-session.
- **Effort:** **Days.** Essentially zero UI code — wrap the existing `airlock tui` command.
- **Pros:** Truly identical UX; one codebase to maintain; new TUI features appear on web
  for free; keyboard-power-user friendly.
- **Cons / risks:**
  - **No built-in auth** — must add the proxy layer; this is the #1 security task. The
    admin API is loopback-trusted, so anyone who reaches the served app can take admin
    actions.
  - One Python subprocess per concurrent viewer → memory scales with users; fine for an
    ops team of 5–20, not hundreds.
  - Terminal look only; poor on mobile/tablet.
  - Couples web availability to the Textual app's stability.

**Best when:** the audience is internal/operator users who already like the TUI and want
remote browser access without VPN+SSH.

### Proposal B — "Native": FastAPI backend + lightweight web frontend (mirrored design)

Build a thin web service that consumes the same data layer, with a browser-native UI that
*mirrors* the TUI's information architecture.

```
Browser (HTML/CSS + HTMX or small SPA)
  └─ HTTPS ─► FastAPI service (auth, RBAC, SSE/websocket for live updates)
               ├─ reads StateStore + tail_jsonl()  (shared, Textual-free modules)
               └─ proxies to /airlock/admin/* for control actions
```

- **Infra:** **FastAPI** (already a FastAPI/LiteLLM shop), **server-rendered HTMX + Jinja**
  (recommended) or a small React/Svelte SPA; **Server-Sent Events or websocket** for the
  live Guards/Logs streams; same reverse-proxy/TLS/SSO front. Containerized, horizontally
  scalable (stateless web tier reading shared JSONL/store).
- **Effort:** **Weeks.** Rebuild ~6 screens as web views, but reuse 100% of the backend
  (`StateStore`, `tail_jsonl`, admin API, `api/queries.py`).
- **Pros:** Real web UX — responsive/mobile, multi-user, deep-linkable, proper RBAC/audit;
  stateless and horizontally scalable; decoupled from TUI uptime; web-native niceties
  (CSV export, charts) the terminal can't do.
- **Cons / risks:** Two front ends to keep in visual/behavioral parity (TUI + web); more
  upfront work; "similar," not pixel-identical.

**Best when:** "some customers" means external/enterprise users, multi-tenant access,
mobile dashboards, or anticipated auth/RBAC/audit requirements.

---

## Recommendation

**Stage it:** Ship **Proposal A first** (textual-serve behind an SSO proxy) as a fast win
to satisfy the immediate customer ask and validate demand — days of work, reuses
everything. In parallel/next, invest in **Proposal B** for the durable, multi-user,
customer-facing product. The clean `StateStore`/admin-API seam means **both** can coexist
on the same backend, so A is not throwaway.

Two things to resolve before either:

1. **Security is the gating concern.** The admin API trusts loopback. *Any* web exposure
   must put authn/authz in front of admin actions — non-negotiable for both proposals.
   (Also respects the standing rule never to disturb the live service/port — a web tier
   should read replicas/JSONL, not be co-located with the production proxy port.)
2. **Audience clarity** drives the choice — internal operators (A is enough) vs. external
   customers (B is the real deliverable).

---

## Sources

- textual-serve — https://github.com/Textualize/textual-serve
- textual-web — https://github.com/textualize/textual-web
- Towards Textual Web Applications — https://textual.textualize.io/blog/2024/09/08/towards-textual-web-applications/
- textual-serve on PyPI — https://pypi.org/project/textual-serve/
