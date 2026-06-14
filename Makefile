# Airlock developer tasks
#
# IMPORTANT: `uv sync` does an EXACT prune and will DELETE en_core_web_lg — the
# spaCy model Microsoft Presidio (the default-on PII guard) loads via a bare
# AnalyzerEngine(). The model is a GitHub-wheel package deliberately kept out of
# uv.lock (provisioned by Dockerfile / scripts/setup*.sh), so every plain
# `uv sync` removes it and silently breaks PII redaction on the next request.
#
# Always sync via `make sync` so the model is restored automatically.

SPACY_MODEL := en_core_web_lg

.PHONY: sync ensure-spacy verify test

sync: ## Sync all deps and restore the spaCy model uv prunes
	uv sync --all-extras
	$(MAKE) ensure-spacy

ensure-spacy: ## Re-download the spaCy PII model if a uv sync pruned it (idempotent)
	@uv run python -c "import $(SPACY_MODEL)" >/dev/null 2>&1 \
		&& echo "spaCy model $(SPACY_MODEL) present" \
		|| uv run python -m spacy download $(SPACY_MODEL)

verify: ## Fail fast if the spaCy PII model is missing (CI / preflight gate)
	@uv run python -c "import $(SPACY_MODEL)" >/dev/null 2>&1 \
		|| { echo "ERROR: $(SPACY_MODEL) missing — Presidio PII guard will fail. Run 'make ensure-spacy'."; exit 1; }

test: ## Run the test suite
	uv run python -m pytest -q
