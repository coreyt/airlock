"""VLLMBackend — gateway-as-executor adapter for local vLLM batch.

vLLM exposes no async Batch server API (`/v1/files`+`/v1/batches` are 404; only a
synchronous `/v1/chat/completions` and an offline CLI). So this backend does not
*delegate* to a provider job queue like Mistral/AI Studio — Airlock **executes**
the batch itself by streaming the (already scanned + translated) rows at vLLM's
live chat endpoint with bounded concurrency, and owns the lifecycle/status.

Design contract: `dev/plans/prompts/vllm-batch-executor.md` (design-reviewed).
Key points the gateway core forces on us:

- The core unlinks the translated temp file *before* ``create`` runs, so
  ``upload`` **persists** it to a durable idem-keyed path and returns that path.
- ``create`` is fire-and-forget: it spawns the executor task (strong-ref'd in a
  registry so the loop can't GC it and ``cancel`` can signal it) and returns.
- ``poll`` derives status from the executor's own ``.done`` marker; ``fetch``
  reads the executor-written results file. vLLM has no status of its own.
- Resume uses the executor's results-file diff (NOT ``staged_keys``, which only
  populates after the whole batch stages).
- ``list_jobs`` returns ``[]`` (no adoptable provider job); idempotency rests on
  ``store.claim`` + the durable resume diff.

Translation and the executor are pure/seamed (inject ``send_chat``) so the whole
module is unit-testable with no network.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass, field

from airlock.batch.backend import NormalizedStatus, ResultUnavailableError

logger = logging.getLogger("airlock.batch")

# Injectable transport: send one chat-completion request body, get the response
# body (an OpenAI ``chat.completion`` dict). Seamed so tests need no network.
SendChat = Callable[[dict], Awaitable[dict]]

_DEFAULT_TIMEOUT = float(os.getenv("AIRLOCK_VLLM_BATCH_TIMEOUT", "120"))
_DEFAULT_RETRIES = int(os.getenv("AIRLOCK_VLLM_BATCH_RETRIES", "1"))


def _safe_stem(idem: str) -> str:
    """Reject an ``idem`` that would escape ``work_dir`` when used as a filename.

    ``idem`` can come from a caller-supplied ``Idempotency-Key`` header, so it is
    untrusted. Anything with a path separator (``../x``, ``/abs/path``) or a bare
    ``.``/``..`` is refused before it reaches ``os.path.join`` — defense in depth
    behind the middleware's ingress validation.
    """
    if not idem or idem in (".", "..") or os.path.basename(idem) != idem:
        raise ValueError(f"unsafe batch id for path: {idem!r}")
    return idem


# ---------------------------------------------------------------------------
# Translation (pure; no network)
# ---------------------------------------------------------------------------
def openai_line_to_vllm(openai_line: dict, provider_model: str | None) -> dict:
    """Translate one OpenAI batch line to a vLLM execution line.

    Keeps the ``{custom_id, body}`` envelope (drops OpenAI-only ``method``/``url``)
    and **rewrites ``body.model``** to the served model id — vLLM only knows the
    raw served name, not the Airlock alias or litellm ``openai/`` prefix.
    """
    custom_id = openai_line.get("custom_id") or openai_line.get("key")
    body = dict(openai_line.get("body") or {})
    if provider_model:
        body["model"] = provider_model
    return {"custom_id": custom_id, "body": body}


def vllm_result_to_openai(native_line: dict) -> dict:
    """Translate one executor-written native result line to an OpenAI output line.

    The executor writes ``{custom_id, response:{status_code, body}}`` on success
    or ``{custom_id, error:{code,message}}`` on a row that exhausted retries
    (mirrors the Mistral shape). The native body is preserved verbatim in
    ``response.body`` (A4).
    """
    custom_id = native_line.get("custom_id") or native_line.get("key")
    error = native_line.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else str(error)
        code = error.get("code") if isinstance(error, dict) else None
        return {
            "id": f"batch_req_{custom_id}",
            "custom_id": custom_id,
            "response": None,
            "error": {"code": code, "message": message},
        }
    response = native_line.get("response") or {}
    if isinstance(response, dict) and "body" in response:
        status_code = response.get("status_code", 200)
        body = dict(response.get("body") or {})
    else:
        status_code = 200
        body = dict(response)
    body.setdefault("choices", body.get("choices") or [])
    return {
        "id": f"batch_req_{custom_id}",
        "custom_id": custom_id,
        "response": {"status_code": status_code, "request_id": custom_id, "body": body},
        "error": None,
    }


# ---------------------------------------------------------------------------
# Executor (the heart — bounded concurrency, resumable, partial-failure safe)
# ---------------------------------------------------------------------------
_semaphore: asyncio.Semaphore | None = None
# Bound how many row tasks exist at once (memory), independent of GPU concurrency.
_MAX_PENDING = max(8, int(os.getenv("AIRLOCK_VLLM_BATCH_CONCURRENCY", "8")) * 4)


def _get_semaphore() -> asyncio.Semaphore:
    """Process-global concurrency bound (one GPU serves all batches)."""
    global _semaphore
    if _semaphore is None:
        n = max(1, int(os.getenv("AIRLOCK_VLLM_BATCH_CONCURRENCY", "8")))
        _semaphore = asyncio.Semaphore(n)
    return _semaphore


def _iter_jsonl(path: str) -> Iterator[dict]:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue


def _copy_stream(src: str, dst: str) -> None:
    """Stream-copy a file (runs in a worker thread; no event-loop blocking)."""
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        while True:
            chunk = fin.read(1 << 20)
            if not chunk:
                break
            fout.write(chunk)


def _compact_results(results_path: str) -> set[str]:
    """Repair + return the resume diff: the set of already-done ``custom_id``s.

    Rewrites ``results_path`` to contain only well-formed, de-duplicated result
    lines (each newline-terminated). This is the execution-resume diff AND a
    repair: a crash mid-write can leave a truncated final line with no newline;
    without compaction the next append would concatenate onto it, corrupting the
    row permanently. Compaction drops that partial tail so the row re-executes
    cleanly and the file converges.
    """
    if not os.path.exists(results_path):
        return set()
    ids: set[str] = set()
    valid: list[dict] = []
    for line in _iter_jsonl(results_path):
        cid = line.get("custom_id")
        if cid is None or cid in ids:
            continue
        ids.add(cid)
        valid.append(line)
    tmp = f"{results_path}.compact"
    with open(tmp, "w", encoding="utf-8") as f:
        for line in valid:
            f.write(json.dumps(line) + "\n")
    os.replace(tmp, results_path)
    return ids


async def _send_one(
    send_chat: SendChat, custom_id: str, body: dict, *, timeout: float, retries: int
) -> dict:
    """Execute one row with timeout + bounded retry; a hard failure becomes an
    error result line (never fails the whole batch)."""
    last_exc: Exception | None = None
    for _ in range(retries + 1):
        try:
            resp = await asyncio.wait_for(send_chat(body), timeout)
            return {
                "custom_id": custom_id,
                "response": {"status_code": 200, "body": resp},
            }
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001  surface as a per-row error line
            last_exc = exc
    return {
        "custom_id": custom_id,
        "error": {"code": "execution_error", "message": str(last_exc)},
    }


async def execute_batch(
    *,
    idem: str,
    input_path: str,
    results_path: str,
    done_path: str,
    send_chat: SendChat,
    semaphore: asyncio.Semaphore,
    cancel_event: asyncio.Event,
    timeout: float = _DEFAULT_TIMEOUT,
    retries: int = _DEFAULT_RETRIES,
) -> None:
    """Stream the translated input at vLLM, writing one native result line per
    row, resumably and with bounded concurrency. Writes ``done_path`` on full
    completion (so ``poll`` can report ``completed``). Honors ``cancel_event``.
    """
    already = _compact_results(results_path)
    write_lock = asyncio.Lock()
    write_failed = False

    async def run_row(custom_id: str, body: dict) -> None:
        nonlocal write_failed
        if cancel_event.is_set():
            return
        async with semaphore:
            if cancel_event.is_set():
                return
            native = await _send_one(
                send_chat, custom_id, body, timeout=timeout, retries=retries
            )
        async with write_lock:
            try:
                with open(results_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(native) + "\n")
            except OSError:
                # A persistence failure (disk full / perms) must not crash the
                # whole executor or silently complete a partial batch: flag it so
                # the .done marker is withheld and the batch stays in_progress for
                # a later resume.
                write_failed = True
                logger.error(
                    "failed to persist result for %s in batch %s",
                    custom_id,
                    idem,
                    exc_info=True,
                )

    pending: set[asyncio.Task] = set()
    for line in _iter_jsonl(input_path):
        if cancel_event.is_set():
            break
        cid = line.get("custom_id")
        body = line.get("body") or {}
        if cid is None or cid in already:
            continue
        pending.add(asyncio.create_task(run_row(cid, body)))
        if len(pending) >= _MAX_PENDING:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
    if pending:
        await asyncio.gather(*pending)

    if not cancel_event.is_set() and not write_failed:
        with open(done_path, "w", encoding="utf-8") as f:
            f.write("done\n")


# ---------------------------------------------------------------------------
# Job registry (strong refs + cancel signaling; in-process only)
# ---------------------------------------------------------------------------
@dataclass
class _Job:
    task: asyncio.Task
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)


_jobs: dict[str, _Job] = {}


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------
class VLLMBackend:
    """``BatchBackend`` for local vLLM — Airlock is the executor."""

    name = "vllm"

    def __init__(
        self,
        *,
        provider_model: str | None,
        api_base: str | None,
        api_key: str | None = None,
        work_dir: str,
        send_chat: SendChat | None = None,
    ):
        self.provider_model = provider_model
        self.api_base = (api_base or "").rstrip("/")
        self.api_key = api_key
        self.work_dir = work_dir
        self._send_chat = send_chat

    # paths (durable, idem-keyed) ----------------------------------------
    def provider_input_path(self, idem: str) -> str:
        return os.path.join(self.work_dir, f"{_safe_stem(idem)}.provider.jsonl")

    def _results_path(self, idem: str) -> str:
        return os.path.join(self.work_dir, f"{_safe_stem(idem)}.results.jsonl")

    def _done_path(self, idem: str) -> str:
        return os.path.join(self.work_dir, f"{_safe_stem(idem)}.results.done")

    # translation (pure) -------------------------------------------------
    def to_provider_request(self, openai_line: dict) -> dict:
        return openai_line_to_vllm(openai_line, self.provider_model)

    def from_provider_result(self, native_line: dict) -> dict:
        return vllm_result_to_openai(native_line)

    # transport (seamed) -------------------------------------------------
    def _sender(self) -> SendChat:
        if self._send_chat is not None:
            return self._send_chat

        async def send(body: dict) -> dict:
            import httpx  # noqa: PLC0415  lazy

            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.api_base}/chat/completions", json=body, headers=headers
                )
                resp.raise_for_status()
                return resp.json()

        return send

    # provider ops -------------------------------------------------------
    async def upload(self, src: str, display_name: str) -> str:
        """Persist the (translated) input durably; the core unlinks ``src`` next.

        Streams ``src`` to ``{work_dir}/{idem}.provider.jsonl`` so a large upload
        is never rejoined in memory, and returns that durable path as the
        ``file_ref`` ``create`` will execute from.
        """
        dst = self.provider_input_path(display_name)
        # Copy off the event loop: a 2 GB upload would otherwise block it for
        # seconds and starve in-flight executor rows.
        await asyncio.to_thread(_copy_stream, src, dst)
        return dst

    async def create(self, model: str, file_ref: str, display_name: str) -> str:
        """Spawn the executor (fire-and-forget, strong-ref'd) and return the job
        id (the idem). Idempotent: an already-running/partial batch resumes."""
        idem = display_name
        self._spawn(idem, file_ref)
        return idem

    def _spawn(self, idem: str, input_path: str) -> None:
        existing = _jobs.get(idem)
        if existing is not None and not existing.task.done():
            return  # already executing
        cancel_event = asyncio.Event()
        task = asyncio.create_task(
            execute_batch(
                idem=idem,
                input_path=input_path,
                results_path=self._results_path(idem),
                done_path=self._done_path(idem),
                send_chat=self._sender(),
                semaphore=_get_semaphore(),
                cancel_event=cancel_event,
            )
        )
        _jobs[idem] = _Job(task=task, cancel_event=cancel_event)
        task.add_done_callback(lambda _t, k=idem: _jobs.pop(k, None))

    async def poll(self, job_id: str) -> NormalizedStatus:
        if os.path.exists(self._done_path(job_id)):
            return NormalizedStatus(status="completed", raw="done")
        return NormalizedStatus(status="in_progress", raw="executing")

    async def fetch(self, job_id: str) -> Iterable[dict]:
        path = self._results_path(job_id)
        if not os.path.exists(path):
            raise ResultUnavailableError(f"no results for {job_id} (not executed)")
        return list(_iter_jsonl(path))

    async def cancel(self, job_id: str) -> None:
        job = _jobs.get(job_id)
        if job is not None:
            job.cancel_event.set()

    async def list_jobs(self, display_name: str) -> list[str]:
        # No adoptable provider job exists; idempotency is store.claim + resume.
        return []


# ---------------------------------------------------------------------------
# Crash-resume reconciler (in-proxy tasks die on restart)
# ---------------------------------------------------------------------------
async def reconcile_vllm_batches(store, *, backend_factory=None) -> int:
    """Re-spawn the executor for every non-terminal vLLM batch (idempotent).

    The core's reconcile only adopts *provider* jobs; an in-proxy executor task
    dies with the process, so without this a restart strands batches
    ``in_progress`` forever. Resume is safe because ``execute_batch`` skips
    ``custom_id``s already in the results file.
    """
    from airlock.batch import runtime  # noqa: PLC0415

    factory = backend_factory or runtime.backend_for_alias
    rows = store.list_resumable_batches("vllm")
    count = 0
    for row in rows:
        backend = factory(row.get("model"))
        if backend is None or getattr(backend, "name", None) != "vllm":
            continue
        idem = row["idem"]
        file_ref = backend.provider_input_path(idem)
        await backend.create(row.get("model") or "", file_ref, idem)
        count += 1
    if count:
        logger.info("re-spawned %d in-flight vLLM batch executor(s)", count)
    return count
