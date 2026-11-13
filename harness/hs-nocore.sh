#!/bin/bash
# hs-nocore.sh <command> [args...] : runs the command with the core-dump limit at one byte, which every child inherits.
# The game's game-free checks crash fixtures on purpose (tests/test_linux_driver_fault_guard.py: guard, SIGSEGV;
# tests/test_recorder_sync.py: recorder_sync_test.exe broken12 and broken13, SIGSEGV and SIGABRT): three core dumps a run
# of tests/run_all.py, each one a "Process crashed" notification on the user's desktop (107 in the night of 2026-11-10).
# With the limit at exactly 1 the kernel does not hand the crash to systemd-coredump at all ("RLIMIT_CORE is set to 1,
# aborting core"): no core file, no journal entry, no notification; the program still dies by its signal, so a test that
# expects the crash still sees it (checked 2026-11-11: exit 139, no entry). bash's ulimit cannot set 1 byte (its unit is
# 1024), hence Python. Not for game runs: a real crash of the game should leave its core.
exec python3 -c '
import os, resource, sys
resource.setrlimit(resource.RLIMIT_CORE, (1, 1))
os.execvp(sys.argv[1], sys.argv[1:])
' "$@"
