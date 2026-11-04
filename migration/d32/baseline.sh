#!/usr/bin/env bash
set -euo pipefail
baseline=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["baseline"])' "$1/wine/wine-scope.json")
git -C "$1/wine/src/wine" cat-file commit "$baseline" > "$2/provenance/WINE-BASELINE-COMMIT"
