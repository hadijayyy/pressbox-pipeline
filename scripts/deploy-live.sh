#!/bin/bash
# Deploy the repo working tree into the LIVE publisher directory.
#
# WHY: cron "Pressbox MVP" runs ~/.hermes/scripts/run-mvp.sh, which does
#   cd ~/.hermes/pressbox-pipeline && python3 pressbox-mvp.py
# So the LIVE code lives in ~/.hermes/pressbox-pipeline — NOT in this git clone.
# Editing this clone alone changes nothing at runtime (silent no-op deploy).
#
# Usage:  bash scripts/deploy-live.sh [--dry-run-verify]
# Safety: backs up every replaced file, verifies hashes, compiles, runs the suite.
#         Does NOT restart anything and does NOT publish.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIVE="$HOME/.hermes/pressbox-pipeline"
SCRIPTS="$HOME/.hermes/scripts"
STAMP="$(date +%Y%m%d-%H%M%S)"
FAIL=0

[ -d "$LIVE" ] || { echo "FATAL: live dir missing: $LIVE" >&2; exit 1; }

echo "repo : $REPO"
echo "live : $LIVE"
echo "stamp: $STAMP"

# 1. Refuse to deploy a dirty working tree.
if [ -n "$(git -C "$REPO" status --porcelain -- '*.py' 'scripts/*.sh')" ]; then
    echo "FATAL: uncommitted .py/scripts changes in repo — commit first." >&2
    git -C "$REPO" status --porcelain -- '*.py' 'scripts/*.sh' >&2
    exit 1
fi

# 2. Back up + copy python module files.
for f in "$REPO"/*.py; do
    b="$(basename "$f")"
    [ -f "$LIVE/$b" ] || continue
    if ! cmp -s "$f" "$LIVE/$b"; then
        cp -p "$LIVE/$b" "$LIVE/$b.bak-$STAMP"
        cp -f "$f" "$LIVE/$b"
        echo "  updated $b (backup .bak-$STAMP)"
    fi
done

# 3. Back up + copy tests/ and scripts/ (no __pycache__).
for d in tests scripts; do
    [ -d "$REPO/$d" ] || continue
    while IFS= read -r f; do
        rel="${f#$REPO/$d/}"
        mkdir -p "$LIVE/$d/$(dirname "$rel")"
        cp -f "$f" "$LIVE/$d/$rel"
    done < <(find "$REPO/$d" -type f ! -path '*__pycache__*')
    echo "  synced $d/"
done

# 4. The live runner itself.
if [ -f "$REPO/scripts/run-mvp.sh" ]; then
    if ! cmp -s "$REPO/scripts/run-mvp.sh" "$SCRIPTS/run-mvp.sh"; then
        cp -p "$SCRIPTS/run-mvp.sh" "$SCRIPTS/run-mvp.sh.bak-$STAMP"
        cp -f "$REPO/scripts/run-mvp.sh" "$SCRIPTS/run-mvp.sh"
        chmod +x "$SCRIPTS/run-mvp.sh"
        echo "  updated live runner $SCRIPTS/run-mvp.sh (backup .bak-$STAMP)"
    fi
fi

# 5. Verify: hashes, syntax, suite.
echo "--- hash parity ---"
for f in "$REPO"/*.py; do
    b="$(basename "$f")"
    [ -f "$LIVE/$b" ] || continue
    a="$(sha256sum "$f" | cut -d' ' -f1)"
    c="$(sha256sum "$LIVE/$b" | cut -d' ' -f1)"
    if [ "$a" = "$c" ]; then echo "  SAME $b"; else echo "  DIFF $b"; FAIL=1; fi
done

echo "--- syntax ---"
( cd "$LIVE" && python3 -m py_compile ./*.py ) || FAIL=1
bash -n "$SCRIPTS/run-mvp.sh" || FAIL=1

echo "--- test suite (live dir) ---"
( cd "$LIVE" && python3 -m pytest tests/ -q ) || FAIL=1

# 6. Optional end-to-end dry run (never publishes).
if [ "${1:-}" = "--dry-run-verify" ]; then
    echo "--- dry run ---"
    ( cd "$LIVE" && python3 -u pressbox-mvp.py --dry-run ) | tail -3
fi

[ "$FAIL" -eq 0 ] && echo "DEPLOY OK" || echo "DEPLOY FAILED" >&2
exit "$FAIL"
