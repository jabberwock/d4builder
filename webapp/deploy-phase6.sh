#!/usr/bin/env bash
# Phase 6 deployment script — builds and serves the production bundle
# The Cloudflare tunnel (planet-faces-completing-accommodation.trycloudflare.com)
# must be running and pointed at localhost:3000.
#
# Usage:
#   ./deploy-phase6.sh          — build + serve (default)
#   ./deploy-phase6.sh --serve  — skip build, serve existing dist/
#   ./deploy-phase6.sh --build  — build only, do not serve

set -euo pipefail

WEBAPP_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$WEBAPP_DIR/dist"
PORT=3000

BUILD=true
SERVE=true

for arg in "$@"; do
  case "$arg" in
    --serve) BUILD=false ;;
    --build) SERVE=false ;;
  esac
done

cd "$WEBAPP_DIR"

if $BUILD; then
  echo "==> Building production bundle..."
  npm run build
  echo "==> Build complete. Output: $DIST_DIR"
  echo "    Social previews: $(ls "$DIST_DIR/social-previews/"*.png 2>/dev/null | wc -l | tr -d ' ') PNGs"
fi

if $SERVE; then
  echo "==> Serving dist/ on port $PORT..."
  echo "    Tunnel URL: https://planet-faces-completing-accommodation.trycloudflare.com"
  echo "    Press Ctrl+C to stop."
  # Kill any existing process on port 3000
  lsof -ti :"$PORT" | xargs kill -9 2>/dev/null || true
  npx serve "$DIST_DIR" --listen "$PORT" --single
fi
