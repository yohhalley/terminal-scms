#!/usr/bin/env bash
# Run one headless Godot test script and fail on a non-zero exit, a timeout, or
# any "SCRIPT ERROR" in its output (a GDScript runtime error aborts the current
# function but Godot can still exit 0, B16-1).
# usage: run-godot-test.sh <timeout-seconds> <res:// or project-relative script>
set -uo pipefail
limit="$1"
script="$2"
log="$(mktemp)"
timeout "$limit" "$GODOT_BIN" --headless --path . --script "$script" 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}
if grep -q "SCRIPT ERROR" "$log"; then
  echo "::error title=Godot SCRIPT ERROR::${script} printed SCRIPT ERROR (exit ${rc})"
  grep -A3 "SCRIPT ERROR" "$log" >&2
  rm -f "$log"
  exit 1
fi
rm -f "$log"
if [ "$rc" -ne 0 ]; then
  echo "::error title=Godot test failed::${script} exited with ${rc}"
  exit "$rc"
fi
