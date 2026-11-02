#!/usr/bin/env bash
# Import a Godot 4.2 project headless and fail unless the import is complete.
#
# Usage: godot-import.sh [project dir]   (default: current directory)
# Env:   GODOT_BIN                  Godot binary (default: godot)
#        GODOT_IMPORT_PASSES        max editor passes (default: 6)
#        GODOT_IMPORT_PASS_TIMEOUT  seconds per pass (default: 300)
#        GODOT_IMPORT_QUIT_AFTER    editor iterations per pass (default: 3000)
#
# Godot 4.2 has no --import flag, and `--editor --quit` exits before the first
# filesystem scan, so it imports nothing and still exits 0. This runs the
# headless editor with --quit-after until every file named in a *.import is
# present under .godot/imported/, and fails with the missing list otherwise.
# Method from Moon Unit's TCL-007 Dockerfile.headless (PR #32), shared here so
# CI and the images import the same way.
#
# Run it as the user that owns the project directory: Godot can't write .godot/
# into a directory it doesn't own (e.g. a root-owned /app), and then imports
# nothing without an error.
set -euo pipefail

project_dir="${1:-.}"
godot_bin="${GODOT_BIN:-godot}"
passes="${GODOT_IMPORT_PASSES:-6}"
pass_timeout="${GODOT_IMPORT_PASS_TIMEOUT:-300}"
quit_after="${GODOT_IMPORT_QUIT_AFTER:-3000}"

cd "${project_dir}"
if [ ! -f project.godot ]; then
  echo "godot-import: no project.godot in $(pwd)" >&2
  exit 1
fi
if [ ! -w . ] || { [ -e .godot ] && [ ! -w .godot ]; }; then
  echo "godot-import: $(pwd) (or its .godot) is not writable by $(id -un); run the import as the project owner" >&2
  ls -ld . .godot 2>/dev/null >&2 || true
  exit 1
fi

# Editor settings and caches go to a throwaway HOME, not the caller's.
import_home="$(mktemp -d)"
log="${import_home}/godot-import.log"
trap 'rm -rf "${import_home}"' EXIT

missing_targets() {
  find . -name '*.import' -not -path './.godot/*' -print0 \
    | xargs -0 -r grep -ho 'res://\.godot/imported/[^"]*' \
    | sort -u | sed 's#^res://#./#' \
    | while read -r f; do [ -e "$f" ] || echo "$f"; done
}

total="$(find . -name '*.import' -not -path './.godot/*' -print0 \
  | xargs -0 -r grep -ho 'res://\.godot/imported/[^"]*' | sort -u | wc -l)"
missing="${total}"
for pass in $(seq 1 "${passes}"); do
  if ! HOME="${import_home}" timeout "${pass_timeout}" "${godot_bin}" --headless --editor \
      --path . --quit-after "${quit_after}" > "${log}" 2>&1; then
    echo "godot-import: editor pass ${pass} failed or timed out (${pass_timeout}s)" >&2
    tail -n 50 "${log}" >&2
    exit 1
  fi
  missing="$(missing_targets | wc -l)"
  echo "godot import pass ${pass}: ${missing} of ${total} imported file(s) missing"
  [ "${missing}" -eq 0 ] && break
done

if [ "${missing}" -ne 0 ]; then
  echo "godot-import: ${missing} imported file(s) still missing after ${passes} passes:" >&2
  missing_targets >&2
  tail -n 50 "${log}" >&2
  exit 1
fi
if [ ! -s .godot/global_script_class_cache.cfg ]; then
  echo "godot-import: .godot/global_script_class_cache.cfg is missing or empty" >&2
  tail -n 50 "${log}" >&2
  exit 1
fi
echo "godot-import: all ${total} imported files present"
