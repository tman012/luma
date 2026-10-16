#!/bin/bash
# Script to migrate hardcoded '/api' calls to use apiUrl() helper

# Find all TypeScript/TSX files with '/api' references
echo "Finding files with hardcoded '/api' references..."
cd /workspace/frontend/src
grep -r "'/api" --include="*.ts" --include="*.tsx" . | cut -d: -f1 | sort -u > /tmp/files_to_migrate.txt

echo "Files to migrate:"
cat /tmp/files_to_migrate.txt

echo ""
echo "Total files: $(wc -l < /tmp/files_to_migrate.txt)"
