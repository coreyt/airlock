"""UserPromptSubmit hook — screen prompts against blocked keywords."""

from __future__ import annotations

from airlock.hooks._common import block, get_blocked_keywords, proceed, read_hook_input


def main() -> None:
    keywords = get_blocked_keywords()
    if not keywords:
        proceed()

    data = read_hook_input()
    prompt = data.get("prompt", "").lower()

    for kw in keywords:
        if kw in prompt:
            block(
                "This prompt contains restricted content and has been "
                "blocked by Airlock. Please remove any references to "
                "restricted terms and try again."
            )

    proceed()


if __name__ == "__main__":
    main()
