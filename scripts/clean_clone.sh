#!/bin/bash
# The clean-clone test (plan task A4): a fresh checkout reproduces the dataset check, the fit, the evaluation and
# the demo.
#
#   scripts/clean_clone.sh [repository url or path]      default: the GitHub repository
#   PYTHON=python3.13 scripts/clean_clone.sh .           the committed state of this checkout, with another Python
#
# It clones into a temporary folder, installs requirements.txt into a new venv, runs the README's commands and fails
# if a test fails or if any committed output differs from what the commands write again. The store step refuses a
# dataset whose SHA-256 differs from datasets/manifest.json, which is the manifest check. Nothing here calls the
# Claude API or needs a key. It needs the network for pip and npm and takes about five minutes. Run it before the
# freeze and before the final prototype.
set -u
REPO=${1:-https://github.com/scalinity/Tracer.git}
PYTHON=${PYTHON:-python3}
DIR=$(mktemp -d "${TMPDIR:-/tmp}/tracer-clean-clone.XXXXXX")
failed=0

step() { echo; echo "=== $1"; }
run() {
  "$@" > "$DIR/last.log" 2>&1 && return 0
  echo "FAILED: $*"; tail -15 "$DIR/last.log"; failed=1
}

step "clone"
git clone -q "$REPO" "$DIR/repo" || exit 1
cd "$DIR/repo" || exit 1
git log --oneline -1

step "install ($($PYTHON --version))"
$PYTHON -m venv .venv && .venv/bin/pip install -q -r requirements.txt || exit 1
.venv/bin/pip list 2>/dev/null | grep -i -E "^(duckdb|numpy|polars|scipy) "

for module in "src.pipeline.store" "src.pipeline.intervals" "src.pipeline.recovery" "src.pipeline.evaluate" \
              "src.pipeline.location_eval" "src.api.export --run test" "src.pipeline.sensitivity" "src.api.qr"; do
  step "python -m $module"
  run .venv/bin/python -m $module
done

step "outputs match the committed ones"
if [ -n "$(git status --short)" ]; then
  echo "DIFFERENT from the committed files:"; git status --short; failed=1
else
  echo "identical"
fi

step "plan data"
run node visuals/plan.check.cjs

step "exhibit tests"
run npm --prefix visuals/exhibit-3d install --no-audit --no-fund
run npm --prefix visuals/exhibit-3d test

step "pytest (the browser tests need the exhibit's node_modules and Playwright's browsers, and say if they skip)"
.venv/bin/pytest -q -p no:cacheprovider -rs 2>&1 | tail -12
[ "${PIPESTATUS[0]}" -eq 0 ] || failed=1

step "result"
if [ "$failed" -eq 0 ]; then
  echo "PASS"; rm -rf "$DIR"
else
  echo "FAIL (the clone is kept at $DIR/repo)"
fi
exit $failed
