#!/usr/bin/env bash
# Publish only the explicit live-to-LS map in publish.py; live source files are never edited.
# LS_WB_SOURCE_ROOT selects an isolated fixture/staging tree instead of this PC's live tools.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
"${HS_PYTHON:-python3}" -I -B "$here/publish.py" "$@"
"$here/publish-check.sh"
