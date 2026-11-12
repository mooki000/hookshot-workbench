#!/usr/bin/env bash
# Compile the branch's C/C++ fixtures for Windows using their own test drivers.
# Syntax only, plus the explicitly labelled empty-environment assertion probe.
set -euo pipefail
here=$(dirname "$(readlink -f "$0")")
python=${HS_PYTHON:-/home/mooki/Code/hookshot-wt/deps/python/bin/python3}
export RX_CPM=${RX_CPM:-/home/mooki/Code/hookshot-wt/deps/cpm}
exec "$python" -I -B "$here/hs_win_fixture_check.py" "$@"
