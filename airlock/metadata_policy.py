"""Rules for values that may cross Airlock metadata boundaries."""

from __future__ import annotations

# Request-scoped secrets must never be copied into telemetry or serialized
# records. Keep this small and explicit: ordinary ``airlock_*`` diagnostics are
# intentionally still observable.
SECRET_METADATA_KEYS = frozenset({"airlock_pii_map"})
