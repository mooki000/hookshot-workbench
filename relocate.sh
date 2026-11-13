#!/usr/bin/env bash
# Rewrite the example home in this checkout only; preserve private state and linked files.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
exec "${HS_PYTHON:-python3}" -I -B "$here/publish.py" --relocate-home "${1:?usage: relocate.sh /home/<you>}"
