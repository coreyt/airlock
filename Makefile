# Airlock developer tasks
#
# IMPORTANT: `uv sync` does an EXACT prune and will DELETE en_core_web_lg — the
# spaCy model Microsoft Presidio (the default-on PII guard) loads via a bare
# AnalyzerEngine(). The model is a GitHub-wheel package deliberately kept out of
# uv.lock (provisioned by Dockerfile / scripts/setup*.sh), so every plain
# `uv sync` removes it and silently breaks PII redaction on the next request.
#
# Always sync via `make sync` so the model is restored automatically.

.PHONY: sync ensure-spacy verify test test-docker

sync: ## Sync all deps and restore the spaCy model uv prunes
	uv sync --locked --all-extras
	$(MAKE) ensure-spacy

ensure-spacy: ## Re-download the spaCy PII model if a uv sync pruned it (idempotent)
	@. scripts/tool-versions.sh; \
	uv run python -c "import $$AIRLOCK_SPACY_MODEL" >/dev/null 2>&1 \
		&& echo "spaCy model $$AIRLOCK_SPACY_MODEL==$$AIRLOCK_SPACY_MODEL_VERSION present" \
		|| uv pip install --python .venv/bin/python "$$AIRLOCK_SPACY_MODEL_URL"

verify: ## Fail fast if the spaCy PII model is missing (CI / preflight gate)
	@. scripts/tool-versions.sh; \
	uv run python -c "import $$AIRLOCK_SPACY_MODEL" >/dev/null 2>&1 \
		|| { echo "ERROR: $$AIRLOCK_SPACY_MODEL missing — Presidio PII guard will fail. Run 'make ensure-spacy'."; exit 1; }

test: ## Run the test suite
	uv run python -m pytest -q -m "not live and not docker"

test-docker: ## Run opt-in Slice 71 disposable Docker topology verification
	@command -v docker >/dev/null || { echo "ERROR: docker is required for make test-docker"; exit 1; }
	@docker info >/dev/null 2>&1 || { echo "ERROR: Docker daemon is unavailable or inaccessible"; exit 1; }
	@command -v openssl >/dev/null || { echo "ERROR: openssl is required for make test-docker"; exit 1; }
	@run_id="$$(date +%s)-$$$$"; revision="$$(git rev-parse HEAD)"; \
	image="$$(docker build --quiet --label org.airlock.slice71.run="$$run_id" --label org.opencontainers.image.revision="$$revision" .)"; \
	printf 'Slice 71 Docker evidence: image=%s run=%s revision=%s\\n' "$$image" "$$run_id" "$$revision"; \
	export SLICE71_IMAGE="$$image" SLICE71_RUN_ID="$$run_id" SLICE71_REVISION="$$revision"; \
	uv run python -m pytest -q -m docker
