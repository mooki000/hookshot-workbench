#!/usr/bin/env bash
# Read-only: heads, reviews, failed test names, same failures on main.
set -euo pipefail
here=$(dirname "$(readlink -f "$0")")
python=${HS_PYTHON:-/home/mooki/Code/hookshot-wt/deps/python/bin/python3}
exec "$python" -I -B "$here/hs_pr_ci.py" "$@"
