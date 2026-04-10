#!/usr/bin/env bash
# Install git hooks for this repo.
#
# Usage: bash scripts/install-hooks.sh
#
# Idempotent — safe to re-run after pulling new hook updates.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"
SCRIPTS_DIR="$REPO_ROOT/scripts"

mkdir -p "$HOOKS_DIR"

for hook in pre-commit; do
    src="$SCRIPTS_DIR/$hook"
    dst="$HOOKS_DIR/$hook"
    if [ ! -f "$src" ]; then
        echo "skip: $src not found"
        continue
    fi
    cp "$src" "$dst"
    chmod +x "$dst"
    echo "installed: $dst"
done

echo ""
echo "Hooks installed. Test with: git commit --allow-empty -m 'test hook'"
