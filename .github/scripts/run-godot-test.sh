#!/usr/bin/env bash
# Run one headless Godot test script and fail on a non-zero exit, a timeout, or
# any "SCRIPT ERROR" in its output (a GDScript runtime error aborts the current
# function but Godot can still exit 0, B16-1).
# usage: run-godot-test.sh <timeout-seconds> <res:// or project-relative script>
#
# Isolates Godot's user:// directory so overlapping local test runs (for
# example two worktrees on one machine) do not share
# user://simulation_runtime_state.json or user://sparkplug_session.cfg.
# Godot 4.2 on Linux resolves user:// to
# $XDG_DATA_HOME/godot/app_userdata/<project name>/ when use_custom_user_dir
# is off (the default; this project does not set it). Always creates a
# throwaway XDG_DATA_HOME and removes it on exit, even if the caller already
# set one (a desktop session may export the shared ~/.local/share). CI
# runners already start with a clean HOME, but a per-run dir also isolates
# suites in the same job.
set -uo pipefail
limit="$1"
script="$2"
log="$(mktemp "${TMPDIR:-/tmp}/godot-test-log.XXXXXX")" && [ -n "$log" ] || {
  echo "run-godot-test.sh: failed to create a temp log file (TMPDIR=${TMPDIR:-/tmp})" >&2
  exit 1
}
XDG_DATA_HOME="$(mktemp -d "${TMPDIR:-/tmp}/godot-userdata.XXXXXX")" && [ -n "$XDG_DATA_HOME" ] && [ -d "$XDG_DATA_HOME" ] || {
  echo "run-godot-test.sh: failed to create a throwaway XDG_DATA_HOME (TMPDIR=${TMPDIR:-/tmp})" >&2
  rm -f "$log"
  exit 1
}
export XDG_DATA_HOME
cleanup() {
  rm -f "$log"
  rm -rf "$XDG_DATA_HOME"
}
trap cleanup EXIT

# TCL-034: the simulator's built-in timing profile is `realistic` (D-039, the
# demo). Tests run `fast` (holds 2..5 s) unless the caller sets a profile.
export SIMULATOR_TIMING_PROFILE="${SIMULATOR_TIMING_PROFILE:-fast}"
echo "SIMULATOR_TIMING_PROFILE=${SIMULATOR_TIMING_PROFILE}"
echo "XDG_DATA_HOME=${XDG_DATA_HOME}"
timeout "$limit" "$GODOT_BIN" --headless --path . --script "$script" 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}
if grep -q "SCRIPT ERROR" "$log"; then
  echo "::error title=Godot SCRIPT ERROR::${script} printed SCRIPT ERROR (exit ${rc})"
  grep -A3 "SCRIPT ERROR" "$log" >&2
  exit 1
fi
if [ "$rc" -ne 0 ]; then
  echo "::error title=Godot test failed::${script} exited with ${rc}"
  exit "$rc"
fi
