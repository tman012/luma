#!/bin/bash
# Check built chunks (and the TypeScript sources) for root-relative URLs that
# ignore the URL base: fetch/EventSource/WebSocket calls, /api/img image URLs
# and src literals. These should use the apiUrl()/assetUrl()/withBase() helpers.
# The request page (static/request/*.js) is checked too: fetch() and asset
# URLs there must go through its url() helper.
# Usage: check-hardcoded-urls.sh [build-dir] [src-dir] [portal-dir]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${1:-$SCRIPT_DIR/dist/assets}"
SRC_DIR="${2:-$SCRIPT_DIR/src}"
PORTAL_DIR="${3:-$SCRIPT_DIR/../static/request}"

if [ ! -d "$BUILD_DIR" ]; then
    echo "❌ Build directory not found: $BUILD_DIR"
    echo "Run 'npm run build' first"
    exit 1
fi

echo "Checking built chunks for hardcoded API URLs..."

FOUND_ISSUES=0

# Check for fetch("/api pattern
if grep -rE 'fetch\(["`'"'"']\/api' "$BUILD_DIR" > /dev/null 2>&1; then
    echo "❌ FAIL: Found fetch('/api...' pattern"
    FOUND_ISSUES=1
fi

# Check for EventSource("/api pattern
if grep -rE 'EventSource\(["`'"'"']\/api' "$BUILD_DIR" > /dev/null 2>&1; then
    echo "❌ FAIL: Found new EventSource('/api...' pattern"
    FOUND_ISSUES=1
fi

# Check for WebSocket("/api or WebSocket("ws
if grep -rE 'WebSocket\(["`'"'"'](ws|\/api)' "$BUILD_DIR" > /dev/null 2>&1; then
    echo "❌ FAIL: Found new WebSocket('/api...' or WebSocket('ws...' pattern"
    FOUND_ISSUES=1
fi

# Check for image proxy URLs not wrapped in apiUrl()/withBase() (built output:
# the wrapped form is always a call argument, so it follows an opening paren)
if grep -rE '(^|[^(])["`'"'"']\/api\/img' "$BUILD_DIR" > /dev/null 2>&1; then
    echo "❌ FAIL: Found unwrapped '/api/img...' URL"
    grep -rhoE '.{0,40}(^|[^(])["`'"'"']\/api\/img.{0,30}' "$BUILD_DIR" | head -5
    FOUND_ISSUES=1
fi

# Check for root-relative src literals (JSX src="/..." compiles to src:"/...")
if grep -rE 'src:["`'"'"']\/(api|static|assets)\/' "$BUILD_DIR" > /dev/null 2>&1; then
    echo "❌ FAIL: Found root-relative src literal"
    grep -rhoE '.{0,40}src:["`'"'"']\/(api|static|assets)\/.{0,30}' "$BUILD_DIR" | head -5
    FOUND_ISSUES=1
fi

if [ -n "$SRC_DIR" ] && [ -d "$SRC_DIR" ]; then
    # Same rules on the TypeScript sources, which keep line numbers
    if grep -rnE '(^|[^(])["`'"'"']\/api\/img' "$SRC_DIR" | grep -vE 'startsWith\(' ; then
        echo "❌ FAIL: Unwrapped '/api/img...' URL in source"
        FOUND_ISSUES=1
    fi
    if grep -rnE 'src=\{?["`'"'"']\/(api|static|assets)\/' "$SRC_DIR"; then
        echo "❌ FAIL: Root-relative src= literal in source"
        FOUND_ISSUES=1
    fi
fi

if [ -n "$PORTAL_DIR" ] && [ -d "$PORTAL_DIR" ]; then
    # API paths are passed to api(), which adds the base; fetch() and asset
    # paths must be wrapped in url().
    if grep -nE 'fetch\(\s*["`'"'"']\/|(^|[^(])["`'"'"']\/(static|assets)\/' "$PORTAL_DIR"/*.js; then
        echo "❌ FAIL: Root-relative URL in the request page not wrapped in url()"
        FOUND_ISSUES=1
    fi
fi

if [ $FOUND_ISSUES -eq 1 ]; then
    echo ""
    echo "Hardcoded URLs found. Use the apiFetch/apiUrl/assetUrl/withBase helpers."
    exit 1
fi

echo "✅ PASS: No hardcoded API, image or src URLs found"
exit 0
