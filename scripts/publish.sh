#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 scripts/export_usage.py "$@"

echo
cat <<'EOF'
Export complete.

Next steps:
1. Commit the updated site/ files you want to publish
2. Push to your GitHub Pages repo/branch
3. Open the Pages URL on your phone

Note:
- data/private/ stays local
- site/data/summary.json is sanitized for publishing
EOF
