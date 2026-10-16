#!/usr/bin/env bash
# validate-python.sh — Compile-check all modified .py files before committing.
# Usage: ./scripts/validate-python.sh [base_ref]
#   base_ref defaults to HEAD (i.e. staged/unstaged changes).
#   Pass "master" to compare the current branch against master.
#
# Exit 0 = all clean, Exit 1 = syntax errors found.

set -euo pipefail

BASE_REF="${1:-HEAD}"
REPO_ROOT="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Collect modified .py files
if [ "$BASE_REF" = "HEAD" ]; then
    # Check both staged and unstaged changes
    FILES=$(git diff --name-only --diff-filter=ACMR HEAD -- '*.py' 2>/dev/null || true)
    STAGED=$(git diff --cached --name-only --diff-filter=ACMR -- '*.py' 2>/dev/null || true)
    FILES=$(echo -e "${FILES}\n${STAGED}" | sort -u | grep -v '^$' || true)
else
    FILES=$(git diff --name-only --diff-filter=ACMR "$BASE_REF" -- '*.py' 2>/dev/null || true)
fi

if [ -z "$FILES" ]; then
    echo "✅ No modified Python files to check."
    exit 0
fi

ERRORS=0
echo "🐍 Checking $(echo "$FILES" | wc -l | tr -d ' ') Python file(s)..."

while IFS= read -r f; do
    [ -z "$f" ] && continue
    if ! python3 -m py_compile "$f" 2>&1; then
        ERRORS=$((ERRORS + 1))
    fi
done <<< "$FILES"

if [ "$ERRORS" -gt 0 ]; then
    echo "❌ $ERRORS file(s) had syntax errors."
    exit 1
else
    echo "✅ All Python files compiled cleanly."
    exit 0
fi
