#!/usr/bin/env bash
# check-worktree-base.sh — guard against the "worktree cut from a stale base" failure.
#
# The failure mode (cost FathomDB two slices before it was codified): you cut a
# worktree at BASE, but `main` moved under it, so the worktree is silently behind and
# its merge re-introduces resolved state or conflicts. This is a cheap merge-base test.
#
# Two modes:
#   ./scripts/check-worktree-base.sh <BASE_REF>   # CUT-TIME guard (run right before
#       `git worktree add … <BASE_REF>`): asserts BASE_REF is `main`'s HEAD or a
#       descendant of it. Exits 1 if BASE_REF is stale (main has commits it lacks).
#   ./scripts/check-worktree-base.sh              # SURVEY mode: lists every active
#       worktree and reports how far its cut-point is behind `main` (informational;
#       a disjoint parallel pack legitimately lags — ff-only before merging if its
#       scope overlaps anything `main` gained since).
#
# Exit: 0 = ok / survey complete; 1 = stale base (cut-time mode) or git error.

set -euo pipefail

MAIN_REF="${MAIN_BRANCH:-main}"
cd "$(git rev-parse --show-toplevel)"

main_head="$(git rev-parse --verify "$MAIN_REF" 2>/dev/null)" || {
    echo "ERROR: cannot resolve '$MAIN_REF' — set MAIN_BRANCH if the trunk has another name." >&2
    exit 1
}

# ---- CUT-TIME guard ---------------------------------------------------------
if [ "$#" -ge 1 ]; then
    base_ref="$1"
    base_sha="$(git rev-parse --verify "$base_ref^{commit}" 2>/dev/null)" || {
        echo "ERROR: cannot resolve base ref '$base_ref'." >&2
        exit 1
    }
    # BASE must be main's HEAD or a descendant ⇒ main must be an ancestor of BASE.
    if git merge-base --is-ancestor "$main_head" "$base_sha"; then
        echo "✓ base $(git rev-parse --short "$base_sha") is current with $MAIN_REF ($(git rev-parse --short "$main_head")) — safe to cut."
        exit 0
    fi
    behind="$(git rev-list --count "$base_sha".."$main_head" 2>/dev/null || echo '?')"
    echo "✗ STALE BASE: '$base_ref' ($(git rev-parse --short "$base_sha")) is NOT current with $MAIN_REF." >&2
    echo "  $MAIN_REF is $behind commit(s) ahead. Cutting here would orphan that work." >&2
    echo "  Fix: cut from $MAIN_REF's HEAD ($(git rev-parse --short "$main_head")), or fast-forward the base first." >&2
    exit 1
fi

# ---- SURVEY mode ------------------------------------------------------------
stale=0
# Parse `git worktree list --porcelain`: blocks of worktree/HEAD/branch.
wt="" ; head=""
while IFS= read -r line; do
    case "$line" in
        worktree\ *) wt="${line#worktree }" ;;
        HEAD\ *)     head="${line#HEAD }" ;;
        branch\ *|detached|"")
            if [ -n "$wt" ] && [ "$wt" != "$(git rev-parse --show-toplevel)" ] && [ -n "$head" ]; then
                cut="$(git merge-base "$main_head" "$head" 2>/dev/null || true)"
                if [ -n "$cut" ] && [ "$cut" != "$main_head" ]; then
                    behind="$(git rev-list --count "$cut".."$main_head" 2>/dev/null || echo '?')"
                    echo "~ $(basename "$wt"): cut-point is $behind commit(s) behind $MAIN_REF — ff-only before merge if scope overlaps recent merges."
                    stale=$((stale + 1))
                else
                    echo "✓ $(basename "$wt"): current with $MAIN_REF."
                fi
            fi
            wt="" ; head="" ;;
    esac
done < <(git worktree list --porcelain; echo "")

[ "$stale" -eq 0 ] && echo "All worktrees current with $MAIN_REF." || true
exit 0
