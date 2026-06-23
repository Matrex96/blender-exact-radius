#!/usr/bin/env bash
# Run the Exact Radius test suite headless across every locally installed
# Blender. Uses an absolute script path (some Blender builds resolve a relative
# --python path against $HOME, not the working dir). Exits non-zero on any fail.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST="$HERE/test_exact_radius.py"

# Pass binary names as args, or default to the usual three.
BINS=("$@")
[ ${#BINS[@]} -eq 0 ] && BINS=(blender-4.5 blender blender-alpha)

NOISE='Modifier_List|PrecisionBolts|bpy_types|MCP|preferences.json|Read prefs|found bundled'
fail=0
for bin in "${BINS[@]}"; do
  command -v "$bin" >/dev/null 2>&1 || { echo "— skip $bin (not installed)"; continue; }
  echo "=== $bin ($("$bin" --version 2>/dev/null | head -1)) ==="
  out="$("$bin" --background --python "$TEST" 2>&1 | grep -viE "$NOISE")"
  echo "$out" | grep -E "(FAIL |^=== [0-9]+/)"
  echo "$out" | grep -q " 0 FAILED " || { echo "  >>> FAILURES in $bin"; fail=1; }
done
[ $fail -eq 0 ] && echo "ALL GREEN" || echo "SOME FAILED"
exit $fail
