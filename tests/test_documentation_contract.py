"""Repository-level contracts for the public documentation information architecture."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
NAV = ROOT / "mkdocs.yml"


def _nav_targets(items: list[object]) -> set[str]:
    targets: set[str] = set()
    for item in items:
        assert isinstance(item, dict)
        for value in item.values():
            if isinstance(value, str):
                targets.add(value)
            else:
                assert isinstance(value, list)
                targets.update(_nav_targets(value))
    return targets


def _local_markdown_targets(document: Path) -> list[Path]:
    targets: list[Path] = []
    for raw_target in re.findall(r"(?<!!)\[[^]]*\]\(([^)]+)\)", document.read_text()):
        target = raw_target.strip("<>").split("#", 1)[0]
        if not target or target.startswith(("/", "http://", "https://", "mailto:")):
            continue
        targets.append((document.parent / target).resolve())
    return targets


def test_mkdocs_navigation_targets_existing_pages() -> None:
    config = yaml.safe_load(NAV.read_text())
    targets = _nav_targets(config["nav"])
    assert targets
    assert all((DOCS / target).is_file() for target in targets)


def test_public_documentation_local_links_resolve() -> None:
    missing = {
        str(target.relative_to(ROOT))
        for document in DOCS.rglob("*.md")
        for target in _local_markdown_targets(document)
        if not target.is_file()
    }
    assert not missing, f"Broken public-documentation links: {sorted(missing)}"


def test_public_docs_home_is_a_navigation_page_not_a_readme_copy() -> None:
    home = (DOCS / "index.md").read_text()
    readme = (ROOT / "README.md").read_text()
    assert home != readme
    assert "Choose your path" in home
    assert "reference/api.md" in home


def test_documentation_entry_points_and_live_api_contract_are_linked() -> None:
    root_readme = (ROOT / "README.md").read_text()
    dev_readme = (ROOT / "dev" / "README.md").read_text()
    api_reference = (DOCS / "reference" / "api.md").read_text()

    assert "docs/index.md" in root_readme
    assert "dev/README.md" in root_readme
    # Pins dev/README.md to the *active* release plan. Update this on each
    # release boundary — the failure is the point: it catches the index going
    # stale, which is how a cold-start reader ends up following a closed-out
    # milestone's instructions.
    assert "plans/0.5.14-todo.md" in dev_readme
    assert "/airlock/docs" in api_reference
    assert "/openapi.json" in api_reference


def test_benchmark_safe_logging_profile_is_documented() -> None:
    configuration = (DOCS / "getting-started" / "configuration.md").read_text()

    assert "## Benchmark-safe FathomDB profile" in configuration
    assert "AIRLOCK_LOG_REDACT_FIELDS=messages,response" in configuration
    assert "AIRLOCK_ENABLE_SQL_LOGGER=0" in configuration
    assert "AIRLOCK_ENABLE_FATHOM_LOGGER=0" in configuration
    assert "AIRLOCK_FATHOM_STORE_MESSAGES=0" in configuration
    assert "AIRLOCK_FATHOM_STORE_RESPONSE_TEXT=0" in configuration


def test_openrouter_recipe_states_explicit_gateway_boundary() -> None:
    configuration = (DOCS / "getting-started" / "configuration.md").read_text()

    assert "## OpenRouter (operator-configured gateway)" in configuration
    assert "openrouter/openai/gpt-4o-mini" in configuration
    assert "os.environ/OPENROUTER_API_KEY" in configuration
    assert "https://openrouter.ai/api/v1" in configuration
    assert "not enabled merely because the key exists" in configuration
    assert (
        "Airlock does not identify or control OpenRouter's downstream provider"
        in " ".join(configuration.split())
    )


def test_deepseek_recipe_states_stable_endpoint_and_tool_boundary() -> None:
    configuration = (DOCS / "getting-started" / "configuration.md").read_text()
    assert "## DeepSeek (operator-configured stable endpoint)" in configuration
    assert "os.environ/DEEPSEEK_API_KEY" in configuration
    assert "https://api.deepseek.com" in configuration
    assert "function** tools only" in configuration
    assert "DeepSeek `user_id`" in configuration


def test_cli_reference_covers_each_supported_top_level_command() -> None:
    reference = (DOCS / "guide" / "cli.md").read_text()
    commands = {
        "config",
        "init",
        "start",
        "status",
        "tui",
        "analyze",
        "advise",
        "post",
        "hooks",
        "dogfood",
        "install-service",
    }
    missing = [
        command for command in commands if f"`airlock {command}`" not in reference
    ]
    assert not missing, f"CLI commands missing from public reference: {missing}"
