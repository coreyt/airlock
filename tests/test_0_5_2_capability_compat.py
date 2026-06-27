"""Pack 0.5.2-COMPAT-tests — cross-cutting capability/naming regression lock.

Tests-only. Locks the whole 0.5.2 naming + capability contract against the
**real** ``config.yaml`` and the shipped helpers — the seams BETWEEN the
per-pack units and the old<->new PARITY guarantees.

NO-NETWORK: load ``config.yaml`` once, call the pure helpers, and drive the
``/v1/models`` ASGI seam in-process via ``httpx.ASGITransport``. The proxy is
NEVER started and no provider is ever contacted.

Seven locked areas (design memo §5):
  1. old<->new alias parity (no client breaks)
  2. collision-safety on the real catalog
  3. batch backend parity (old + new alias -> same backend)
  4. capability<->wiring consistency for EVERY entry
  5. the two capability surfaces agree (single source)
  6. /v1/models seam is additive + non-breaking
  7. reference integrity (stale-target guard)
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
import yaml

from airlock.batch.gateway import load_batch_aliases
from airlock.capability import capability_record, endpoints_for
from airlock.fast.model_alias import alias_table
from airlock.fast.router import infer_provider, set_router_config
from airlock.models_seam import ModelsCapabilityMiddleware, _build_capability_map
from airlock.proxy import _prepare_runtime_config

CONFIG_PATH = "config.yaml"


# ---------------------------------------------------------------------------
# Session fixtures — load the real config once and wire the singletons.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def cfg() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def model_list(cfg: dict) -> list[dict]:
    return list(cfg.get("model_list") or [])


@pytest.fixture(scope="module")
def names(model_list: list[dict]) -> set[str]:
    return {e["model_name"] for e in model_list}


@pytest.fixture(scope="module")
def entry_by_name(model_list: list[dict]) -> dict[str, dict]:
    return {e["model_name"]: e for e in model_list}


@pytest.fixture
def wired(cfg: dict):
    """Wire both routing singletons from the real config (catalog-first lookups).

    Function-scoped on purpose: the autouse ``_reset_router_catalog`` conftest
    fixture empties the router catalog before every test, so the wiring must be
    (re)applied per test — AFTER that reset runs — for ``infer_provider`` to use
    the catalog-first path instead of the prefix-only fallback.

    Yield fixture: on teardown it restores BOTH global singletons to their
    pristine state so no catalog/alias-table state leaks into later tests in the
    session — ``set_router_config(None)`` empties the router map (matching the
    conftest baseline) and the alias-table singleton is reset to its freshly
    constructed (unloaded) form.
    """
    set_router_config(cfg)
    alias_table.load_from_config(CONFIG_PATH)
    yield True
    set_router_config(None)
    alias_table._entries = []
    alias_table._exact = {}
    alias_table._provider_body_alias = {}
    alias_table._body_providers = {}
    alias_table._ambiguous_variants = set()
    alias_table._loaded = False


def _underlying(entry_by_name: dict[str, dict], alias: str | None) -> str | None:
    """litellm_params.model for a resolved alias (the served-by deployment)."""
    if alias is None:
        return None
    entry = entry_by_name[alias]
    return (entry.get("litellm_params") or {}).get("model")


# ---------------------------------------------------------------------------
# 1. Old<->new alias PARITY (no client breaks).
# ---------------------------------------------------------------------------
class TestAliasParity:
    @pytest.mark.parametrize(
        ("legacy", "consolidated", "provider"),
        [
            ("gemini-3.5-flash-aistudio", "aistudio/gemini-3.5-flash", "gemini"),
            ("gemini-3.5-flash-vertex", "vertex/gemini-3.5-flash", "vertex_ai"),
            ("mistral-large-batch", "mistral/mistral-large", "mistral"),
            ("qwen36-27b-vllm-batch", "vllm/qwen3.6-27b", "openai"),
            ("claude-haiku", "anthropic/claude-haiku", "anthropic"),
        ],
    )
    def test_legacy_and_consolidated_agree_on_provider(
        self, wired, legacy: str, consolidated: str, provider: str
    ):
        assert infer_provider(consolidated) == provider
        assert infer_provider(legacy) == provider
        assert infer_provider(legacy) == infer_provider(consolidated)

    @pytest.mark.parametrize(
        ("legacy", "consolidated"),
        [
            ("gemini-3.5-flash-aistudio", "aistudio/gemini-3.5-flash"),
            ("gemini-3.5-flash-vertex", "vertex/gemini-3.5-flash"),
            ("mistral-large-batch", "mistral/mistral-large"),
            ("qwen36-27b-vllm-batch", "vllm/qwen3.6-27b"),
            ("claude-haiku", "anthropic/claude-haiku"),
        ],
    )
    def test_legacy_and_consolidated_resolve_to_same_underlying(
        self,
        wired,
        entry_by_name: dict[str, dict],
        legacy: str,
        consolidated: str,
    ):
        # Both alias forms must RESOLVE through alias_table (not just infer a
        # provider) and land on the SAME underlying litellm deployment — that is
        # the real "no client break" guarantee.
        resolved_legacy = alias_table.resolve(legacy)
        resolved_consolidated = alias_table.resolve(consolidated)
        assert resolved_legacy is not None
        assert resolved_consolidated is not None
        under_legacy = _underlying(entry_by_name, resolved_legacy)
        under_consolidated = _underlying(entry_by_name, resolved_consolidated)
        assert under_legacy is not None
        assert under_legacy == under_consolidated

    def test_every_legacy_suffix_twin_resolves_non_none(self, wired, names: set[str]):
        legacy = sorted(
            n for n in names if n.endswith(("-aistudio", "-vertex", "-batch"))
        )
        # Sanity: the real catalog actually carries legacy twins.
        assert legacy, "expected legacy suffix-twin aliases in the real catalog"
        unresolved = [n for n in legacy if alias_table.resolve(n) is None]
        assert unresolved == [], f"legacy aliases resolved to None: {unresolved}"


# ---------------------------------------------------------------------------
# 2. Collision-safety on the REAL catalog.
# ---------------------------------------------------------------------------
class TestCollisionSafety:
    def test_bare_gemini_flash_routes_to_aistudio(
        self, wired, entry_by_name: dict[str, dict]
    ):
        resolved = alias_table.resolve("gemini-3.5-flash")
        assert resolved is not None
        # Bare name must hit the AI-Studio deployment, never vertex_ai/.
        assert _underlying(entry_by_name, resolved).startswith("gemini/")

    @pytest.mark.parametrize(
        "query", ["vertex/gemini-3.5-flash", "vertex_ai/gemini-3.5-flash"]
    )
    def test_vertex_prefixes_route_to_vertex_entry(
        self, wired, entry_by_name: dict[str, dict], query: str
    ):
        resolved = alias_table.resolve(query)
        assert resolved is not None
        assert _underlying(entry_by_name, resolved).startswith("vertex_ai/")

    @pytest.mark.parametrize(
        "query", ["aistudio/gemini-3.5-flash", "gemini/gemini-3.5-flash"]
    )
    def test_aistudio_prefixes_route_to_aistudio_entry(
        self, wired, entry_by_name: dict[str, dict], query: str
    ):
        resolved = alias_table.resolve(query)
        assert resolved is not None
        assert _underlying(entry_by_name, resolved).startswith("gemini/")

    def test_ambiguous_body_without_explicit_alias_is_none(self, wired):
        # gemini-3.1-pro-preview is the litellm body of BOTH a gemini and a
        # vertex_ai deployment, with no explicit alias of that name -> no
        # silent cross-provider pick.
        assert alias_table.resolve("gemini-3.1-pro-preview") is None


# ---------------------------------------------------------------------------
# 3. Batch backend PARITY (old + new alias -> same backend).
# ---------------------------------------------------------------------------
class TestBatchBackendParity:
    @pytest.mark.parametrize(
        ("consolidated", "legacy", "backend"),
        [
            ("aistudio/gemini-3.5-flash", "gemini-3.5-flash-aistudio", "aistudio"),
            ("aistudio/gemini-3.1-pro", "gemini-3.1-pro-aistudio", "aistudio"),
            ("mistral/mistral-large", "mistral-large-batch", "mistral"),
            ("mistral/mistral-small", "mistral-small-batch", "mistral"),
            ("vllm/qwen3.6-27b", "qwen36-27b-vllm-batch", "vllm"),
        ],
    )
    def test_old_and_new_alias_map_to_same_backend(
        self, cfg: dict, consolidated: str, legacy: str, backend: str
    ):
        aliases = load_batch_aliases(cfg)
        assert consolidated in aliases, f"{consolidated} missing from batch aliases"
        assert legacy in aliases, f"{legacy} missing from batch aliases"
        new = aliases[consolidated]
        old = aliases[legacy]
        assert new["backend"] == backend
        assert old["backend"] == backend
        # Same underlying provider model -> identical batch target.
        assert new["provider_model"] == old["provider_model"]

    @pytest.mark.parametrize(
        "sync_only", ["gemini-3.5-flash", "mistral-large", "qwen3.6-27b"]
    )
    def test_bare_sync_entries_are_not_batch_aliases(self, cfg: dict, sync_only: str):
        # No airlock_batch marker -> sync-only -> absent from the gateway map.
        assert sync_only not in load_batch_aliases(cfg)


# ---------------------------------------------------------------------------
# 4. Capability <-> wiring consistency for EVERY entry.
# ---------------------------------------------------------------------------
def _expects_batch(entry: dict) -> bool:
    """The contract: batch iff airlock_batch marker OR vertex_ai/ + regional loc."""
    if entry.get("airlock_batch"):
        return True
    params = entry.get("litellm_params") or {}
    model = params.get("model") or ""
    if model.startswith("vertex_ai/"):
        loc = params.get("vertex_location")
        return bool(loc) and loc.lower() != "global"
    return False


class TestCapabilityWiringConsistency:
    def test_no_entry_over_or_under_claims_batch(self, model_list: list[dict]):
        # Assert the FULL endpoints list for every entry — an entry returning
        # extra/garbage endpoints (e.g. ["chat","embeddings"]) must NOT pass.
        mismatches = []
        for entry in model_list:
            expected = ["chat", "batch"] if _expects_batch(entry) else ["chat"]
            actual = endpoints_for(entry)
            if actual != expected:
                mismatches.append((entry.get("model_name"), actual, expected))
        assert mismatches == [], f"endpoints<->wiring mismatch: {mismatches}"

    def test_vertex_global_entries_are_chat_only(self, model_list: list[dict]):
        checked = 0
        for entry in model_list:
            params = entry.get("litellm_params") or {}
            model = params.get("model") or ""
            loc = params.get("vertex_location")
            if (
                model.startswith("vertex_ai/")
                and isinstance(loc, str)
                and loc.lower() == "global"
                and not entry.get("airlock_batch")
            ):
                checked += 1
                assert endpoints_for(entry) == ["chat"], entry.get("model_name")
        assert checked, "expected vertex-at-global entries in the real catalog"

    def test_every_airlock_batch_entry_is_chat_and_batch(self, model_list: list[dict]):
        checked = 0
        for entry in model_list:
            if entry.get("airlock_batch"):
                checked += 1
                assert endpoints_for(entry) == ["chat", "batch"], entry.get(
                    "model_name"
                )
        assert checked, "expected airlock_batch entries in the real catalog"


# ---------------------------------------------------------------------------
# 5. The two capability surfaces AGREE (single source of truth).
# ---------------------------------------------------------------------------
# Representative entries spanning chat-only, batch, vertex-global, and the
# consolidated/legacy twins.
_REPRESENTATIVE = [
    "claude-haiku",
    "gemini-3.5-flash",
    "aistudio/gemini-3.5-flash",
    "gemini-3.5-flash-aistudio",
    "vertex/gemini-3.5-flash",
    "mistral/mistral-large",
    "mistral-large-batch",
    "vllm/qwen3.6-27b",
    "anthropic/claude-haiku",
]

# Hard-coded ORACLE of full capability records for representative entries.
# This breaks the circularity of using ``capability_record`` to validate the two
# product surfaces that are THEMSELVES built from ``capability_record``: a
# regression INSIDE ``capability_record`` (dropping ``deprecated``, flipping
# ``airlock_provider``, mangling ``underlying``) is now caught because the
# four-way equality includes this independent literal.
_EXPECTED_CAP: dict[str, dict] = {
    # consolidated batch alias: gemini-served, chat+batch, not deprecated
    "aistudio/gemini-3.5-flash": {
        "airlock_provider": "gemini",
        "endpoints": ["chat", "batch"],
        "underlying": "gemini/gemini-3.5-flash",
        "region": None,
        "deprecated": False,
    },
    # vertex-at-global: vertex_ai-served, chat-only (anti-overclaim), region set
    "vertex/gemini-3.5-flash": {
        "airlock_provider": "vertex_ai",
        "endpoints": ["chat"],
        "underlying": "vertex_ai/gemini-3.5-flash",
        "region": "global",
        "deprecated": False,
    },
    # legacy suffix twin: same underlying as the consolidated form, deprecated
    "gemini-3.5-flash-aistudio": {
        "airlock_provider": "gemini",
        "endpoints": ["chat", "batch"],
        "underlying": "gemini/gemini-3.5-flash",
        "region": None,
        "deprecated": True,
    },
    # plain chat-only anthropic entry
    "anthropic/claude-haiku": {
        "airlock_provider": "anthropic",
        "endpoints": ["chat"],
        "underlying": "anthropic/claude-haiku-4-5-20251001",
        "region": None,
        "deprecated": False,
    },
}


class TestSurfacesAgree:
    @pytest.fixture(scope="class")
    def injected_model_info(self, entry_by_name: dict[str, dict]):
        """model_info as _prepare_runtime_config writes it into the runtime YAML."""
        runtime_path, temp_path = _prepare_runtime_config(CONFIG_PATH)
        try:
            with open(runtime_path) as f:
                runtime_cfg = yaml.safe_load(f)
        finally:
            if temp_path is not None and os.path.exists(temp_path):
                os.remove(temp_path)
        return {
            e["model_name"]: (e.get("model_info") or {})
            for e in runtime_cfg.get("model_list") or []
        }

    @pytest.mark.parametrize("name", _REPRESENTATIVE)
    def test_model_info_surface_equals_capability_record(
        self,
        name: str,
        entry_by_name: dict[str, dict],
        injected_model_info: dict[str, dict],
    ):
        # EXACT whole-dict equality — a stale/extra key in the injected model_info
        # must fail, not be silently ignored.
        assert injected_model_info[name] == capability_record(entry_by_name[name])

    @pytest.mark.parametrize("name", _REPRESENTATIVE)
    def test_v1models_surface_equals_capability_record(
        self, name: str, entry_by_name: dict[str, dict]
    ):
        cap_map = _build_capability_map()
        assert cap_map[name] == capability_record(entry_by_name[name])

    @pytest.mark.parametrize("name", sorted(_EXPECTED_CAP))
    def test_four_way_agreement_against_independent_oracle(
        self,
        name: str,
        entry_by_name: dict[str, dict],
        injected_model_info: dict[str, dict],
    ):
        # Both product surfaces, the helper, AND a hand-written literal must all
        # agree — this is what makes the lock non-circular: a regression inside
        # capability_record can no longer hide behind itself.
        cap_map = _build_capability_map()
        injected = injected_model_info[name]
        record = capability_record(entry_by_name[name])
        expected = _EXPECTED_CAP[name]
        assert injected == expected
        assert cap_map[name] == expected
        assert record == expected


# ---------------------------------------------------------------------------
# 6. /v1/models seam is additive + non-breaking (in-process ASGI).
# ---------------------------------------------------------------------------
def _make_models_app(body: dict):
    """Canned ASGI app: serves `body` as JSON on /v1/models, 404 text elsewhere."""

    async def app(scope, receive, send):
        if scope["path"] == "/v1/models":
            raw = json.dumps(body).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(raw)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": raw})
        else:
            payload = b"not found"
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [
                        (b"content-type", b"text/plain"),
                        (b"content-length", str(len(payload)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": payload})

    return app


class TestV1ModelsSeam:
    @pytest.mark.asyncio
    async def test_models_response_is_augmented_with_airlock(
        self, names: set[str], entry_by_name: dict[str, dict]
    ):
        cap_map = _build_capability_map()
        ids = ["claude-haiku", "gemini-3.5-flash", "mistral/mistral-large"]
        body = {
            "object": "list",
            "data": [{"id": mid, "object": "model"} for mid in ids],
        }
        app = ModelsCapabilityMiddleware(_make_models_app(body), cap_map)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            resp = await client.get("/v1/models")

        assert resp.status_code == 200
        data = {m["id"]: m for m in resp.json()["data"]}
        for mid in ids:
            model = data[mid]
            # Standard fields untouched.
            assert model["id"] == mid
            assert model["object"] == "model"
            # Additive airlock object == the single-source capability record.
            assert model["airlock"] == capability_record(entry_by_name[mid])

    @pytest.mark.asyncio
    async def test_non_models_path_passes_through_unchanged(self):
        cap_map = _build_capability_map()
        body = {"object": "list", "data": [{"id": "claude-haiku", "object": "model"}]}
        app = ModelsCapabilityMiddleware(_make_models_app(body), cap_map)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            resp = await client.get("/v1/chat/completions")
        assert resp.status_code == 404
        assert resp.text == "not found"


# ---------------------------------------------------------------------------
# 7. Reference integrity (stale-target guard).
# ---------------------------------------------------------------------------
class TestReferenceIntegrity:
    def test_all_fallback_targets_are_live_models(self, cfg: dict, names: set[str]):
        fallbacks = (cfg.get("router_settings") or {}).get("fallbacks") or []
        dangling = set()
        for mapping in fallbacks:
            for _src, targets in mapping.items():
                for target in targets:
                    if target not in names:
                        dangling.add(target)
        assert dangling == set(), f"fallback targets not in model_list: {dangling}"

    def test_all_cost_tier_members_are_live_models(self, cfg: dict, names: set[str]):
        tiers = cfg.get("cost_tiers") or {}
        dangling = set()
        for _tier, members in tiers.items():
            for member in members:
                if member not in names:
                    dangling.add(member)
        assert dangling == set(), f"cost_tier members not in model_list: {dangling}"
