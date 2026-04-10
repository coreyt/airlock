"""``airlock advise`` -- ask the advisor about operational data."""

from __future__ import annotations

import sys

from airlock.advisor.agent import run_advisor
from airlock.advisor.proposals import apply_proposal, parse_action_block


def _print_result(result) -> None:
    """Print an AdvisorResult to stdout."""
    if result.error:
        print(f"Error: {result.error}", file=sys.stderr)
        return

    if not result.is_local:
        print(
            "WARNING: Using remote model '{}'. "
            "Operational data was sent to an external provider.".format(
                result.model_used
            ),
            file=sys.stderr,
        )

    print(result.answer)

    if result.tool_calls_made:
        print(
            f"\n[Tools used: {', '.join(result.tool_calls_made)}]",
            file=sys.stderr,
        )

    for action in result.actions_proposed:
        proposal = parse_action_block(action)
        if proposal:
            print("\n--- Proposed Change ---")
            print(f"Description: {proposal.description}")
            print(f"Risk: {proposal.risk_level}")
            print(f"Requires restart: {proposal.requires_restart}")
            print(f"\nDiff:\n{proposal.diff_preview}")

            try:
                answer = input("Apply this change? [y/N] ")
            except EOFError:
                answer = "n"

            if answer.strip().lower() == "y":
                if proposal.risk_level == "high":
                    try:
                        confirm = input("High-risk change. Type CONFIRM to proceed: ")
                    except EOFError:
                        confirm = ""
                    if confirm.strip() != "CONFIRM":
                        print("Aborted.")
                        continue
                backup = apply_proposal(proposal)
                print(f"Applied. Backup saved to: {backup}")
            else:
                print("Skipped.")


def _interactive_loop(args) -> None:
    """Run an interactive advisor REPL."""
    print("Airlock Advisor (interactive mode). Type 'quit' to exit.\n")
    while True:
        try:
            question = input("advisor> ")
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        question = question.strip()
        if not question:
            continue
        if question.lower() in ("quit", "exit", "q"):
            break

        result = run_advisor(
            question,
            model=args.model,
            local_only=args.local_only,
        )
        _print_result(result)
        print()


def run(args) -> None:
    """Entry point for ``airlock advise``."""
    if args.interactive:
        _interactive_loop(args)
        return

    if not args.question:
        print("Usage: airlock advise 'your question here'", file=sys.stderr)
        print("       airlock advise --interactive", file=sys.stderr)
        sys.exit(1)

    result = run_advisor(
        args.question,
        model=args.model,
        local_only=args.local_only,
    )
    _print_result(result)
    if result.error:
        sys.exit(1)
