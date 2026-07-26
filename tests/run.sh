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
ran=0
for bin in "${BINS[@]}"; do
  command -v "$bin" >/dev/null 2>&1 || { echo "— skip $bin (not installed)"; continue; }
  ran=$((ran + 1))
  echo "=== $bin ($("$bin" --version 2>/dev/null | head -1)) ==="
  out="$("$bin" --background --python "$TEST" 2>&1 | grep -viE "$NOISE")"
  echo "$out" | grep -E "(FAIL |^=== )"
  echo "$out" | grep -q " 0 FAILED " || { echo "  >>> FAILURES in $bin"; fail=1; }
done

# A run that tested nothing must not look like a clean one: every binary being
# absent used to print skip lines and then ALL GREEN with exit 0, which is
# exactly what a passing run looks like to anyone reading the tail of a log.
if [ "$ran" -eq 0 ]; then
  echo "NOTHING RAN — none of these are installed: ${BINS[*]}"
  exit 1
fi
[ $fail -eq 0 ] && echo "ALL GREEN ($ran Blender)" || echo "SOME FAILED"
exit $fail
