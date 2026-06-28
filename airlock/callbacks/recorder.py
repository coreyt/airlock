"""DORMANT module-level recorder wiring (0.5.4-MIGRATE-entfathom-wire, pack 2b-i).

Builds the live ``RequestRecorder`` + ``RequestRecorderCallback`` mechanism and
registers the enterprise (always-on) and fathom (async_only) sinks, but leaves it
**dormant**: ``recorder_callback`` is intentionally NOT installed into LiteLLM here
and the existing enterprise/fathom callbacks still fire exactly as today.

Activation (registering ``recorder_callback`` into the LiteLLM callback manager and
removing the old paths) is pack 2b-ii (the cutover). Importing this module must NOT
register anything into LiteLLM and must NOT change any existing callback registration.
"""

from __future__ import annotations

from airlock.callbacks.enterprise_logger import proxy_logger
from airlock.callbacks.fathom_logger import proxy_fathom_logger
from airlock.callbacks.request_event import RequestRecorder, RequestRecorderCallback

request_recorder = RequestRecorder()
request_recorder.register(proxy_logger.record_event, name="enterprise")  # always-on
request_recorder.register(
    proxy_fathom_logger.record_event, name="fathom", async_only=True
)
recorder_callback = RequestRecorderCallback(request_recorder)
# NOTE: recorder_callback is intentionally NOT registered into litellm here — that is
# pack 2b-ii (the cutover). s3/sql/sidechannels sinks register in their own packs.
