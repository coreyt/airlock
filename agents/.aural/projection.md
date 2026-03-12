# System Check
All 6 checks passed.

# Project Status
_Last updated: 2026-03-02T03:09:52.715556+00:00_

## Constraints
_None logged._

## Aural: What to Log

You MUST log Smart Events when you recognize these moments. Without them, the next session has no memory of your decisions, constraints, or rejected approaches.

| Moment | Command |
|--------|---------|
| Quick one-liner (auto-classified) | `aural note "I chose X over Y because Z"` |
| You choose between approaches | `aural log decision --decision '...' --rationale '...' --rejected '...'` |
| You discover a rule that must be followed | `aural log constraint --rule '...' --scope '...' --reason '...' --severity hard` |
| You need human input to proceed | `aural log blocked --question '...' --context '...'` |
| You try something and it fails | `aural log rejection --approach '...' --reason '...' --context '...'` |
| You start a distinct unit of work | `aural log task-start --description '...'` |
| You finish a unit of work | `aural log task-done --task-id '...' --description '...'` |
| You finish exploring how a subsystem works | `aural log knowledge --topic '...' --summary '...' --files '...'` |
| Context window is filling up (you'll be warned) | `aural log checkpoint --summary '...' --next-steps '...' --files '...'` |
| You are about to enter plan mode | `aural plan-check` (also fires automatically via PreToolUse hook) |
| Session ends with no Smart Events | `aural extract-smart-events` (runs automatically via Stop hook) |

**Plan mode**: Planning is where key decisions happen. When you finalize a plan, log each significant decision (chosen approach + rejected alternatives). Constraints and rejections discovered during exploration should be logged as they arise — the plan file is ephemeral, Smart Events persist.

To verify events were stored: `aural reduce` (prints current state as JSON). MCP equivalents (`aural_log_decision`, etc.) also work if available.

Without these, the next session sees only file edits and bash commands — no decisions, no constraints, no context.

## Blocked (waiting on human)
_None logged._

## Do Not Retry
_None logged._

## Decisions
_None logged._

## Recent Activity
_None logged._

## Completed
_None logged._
