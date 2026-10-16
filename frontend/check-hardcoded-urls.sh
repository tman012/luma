#!/bin/bash
# Check built chunks for hardcoded '/api/ URLs in fetch/EventSource/WebSocket calls
# These should use apiUrl() helper instead

set -e

BUILD_DIR="../static/spa/assets"

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

if [ $FOUND_ISSUES -eq 1 ]; then
    echo ""
    echo "Hardcoded URLs found. All fetch/EventSource/WebSocket calls should use apiFetch/apiUrl helpers."
    exit 1
fi

echo "✅ PASS: No hardcoded API URLs found in fetch/EventSource/WebSocket calls"
exit 0
