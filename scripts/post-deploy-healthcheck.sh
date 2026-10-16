#!/usr/bin/env bash
# post-deploy-healthcheck.sh — Verify the container is healthy after deployment.
# If unhealthy, revert the last merge commit, rebuild, and restart.
#
# Usage: ./scripts/post-deploy-healthcheck.sh [container_name] [wait_seconds]
#   container_name  defaults to "luma"
#   wait_seconds    defaults to 15 (time to wait for container to stabilize)
#
# Exit 0 = healthy, Exit 1 = unhealthy (reverted), Exit 2 = unhealthy (revert failed)

set -uo pipefail

CONTAINER="${1:-luma}"
WAIT="${2:-15}"
REPO_ROOT="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "⏳ Waiting ${WAIT}s for container '${CONTAINER}' to stabilize..."
sleep "$WAIT"

# Check container health status
STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo "missing")

if [ "$STATUS" = "healthy" ]; then
    echo "✅ Container '${CONTAINER}' is healthy."
    exit 0
fi

# Also check if it's running at all
RUNNING=$(docker inspect --format='{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo "false")

if [ "$RUNNING" = "true" ] && [ "$STATUS" = "starting" ]; then
    # Give it more time — healthcheck might still be pending
    echo "⏳ Container is starting, waiting another 15s..."
    sleep 15
    STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo "missing")
    if [ "$STATUS" = "healthy" ]; then
        echo "✅ Container '${CONTAINER}' is healthy (after extended wait)."
        exit 0
    fi
fi

echo "❌ Container '${CONTAINER}' is unhealthy (status: ${STATUS}, running: ${RUNNING})"
echo ""

# Grab recent logs for diagnostics
echo "📋 Recent container logs:"
docker logs --tail 30 "$CONTAINER" 2>&1 || true
echo ""

# Attempt to revert the last commit (assumed to be the merge that broke things)
LAST_COMMIT=$(git rev-parse HEAD)
LAST_MSG=$(git log -1 --format="%s")
echo "🔄 Reverting last commit: ${LAST_COMMIT:0:8} — ${LAST_MSG}"

if git revert --no-edit HEAD; then
    echo "🔨 Rebuilding after revert..."
    if docker compose build "$CONTAINER" && docker compose up -d "$CONTAINER"; then
        echo "⏳ Waiting ${WAIT}s after revert rebuild..."
        sleep "$WAIT"
        REVERT_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo "missing")
        if [ "$REVERT_STATUS" = "healthy" ]; then
            echo "✅ Revert successful — container is healthy again."
            echo "REVERTED_COMMIT=${LAST_COMMIT}"
            exit 1
        else
            echo "⚠️  Container still unhealthy after revert (status: ${REVERT_STATUS})"
            exit 2
        fi
    else
        echo "⚠️  Rebuild after revert failed."
        exit 2
    fi
else
    echo "⚠️  git revert failed — manual intervention needed."
    exit 2
fi
