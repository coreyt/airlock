"""Tests for the Advisor TUI screen."""


class TestAdvisorPane:
    def test_screen_composes(self):
        """AdvisorPane composes without error."""
        from airlock.tui.screens.advisor import AdvisorPane

        # Just verify the class can be instantiated
        pane = AdvisorPane(id="advisor")
        assert pane is not None

    def test_get_model_choices_with_local(self):
        """Model choices include local tag."""
        from airlock.tui.screens.advisor import _get_model_choices

        models = [
            {
                "model_name": "local-llm",
                "litellm_params": {
                    "model": "openai/llama",
                    "api_base": "http://localhost:8000",
                },
            },
            {"model_name": "claude", "litellm_params": {"model": "anthropic/claude"}},
        ]
        choices = _get_model_choices(models)
        assert choices[0] == ("Auto (local preferred)", "auto")
        assert any("[local]" in label for label, _ in choices)
        assert any("claude" in label for label, _ in choices)

    def test_get_model_choices_empty(self):
        """Empty model list returns just the auto option."""
        from airlock.tui.screens.advisor import _get_model_choices

        choices = _get_model_choices([])
        assert len(choices) == 1
        assert choices[0][1] == "auto"

    def test_app_views_includes_advisor(self):
        """The main app _VIEWS list includes advisor."""
        from airlock.tui.app import _VIEWS

        view_ids = [v[0] for v in _VIEWS]
        assert "advisor" in view_ids

    def test_app_has_advisor_binding(self):
        """The app has a key binding for advisor (key 6)."""
        from airlock.tui.app import AirlockApp

        binding_keys = [
            b[0] if isinstance(b, tuple) else b.key for b in AirlockApp.BINDINGS
        ]
        assert "6" in binding_keys
