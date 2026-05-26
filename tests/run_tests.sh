#!/usr/bin/env bash
# Run all tests and report results.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

PASS=0
FAIL=0

run_suite() {
    local name="$1"
    local module="$2"
    echo ""
    echo "--- $name ---"
    if python3 -m pytest "$module" -v --tb=short 2>/dev/null || \
       python3 -m unittest discover -s "$(dirname "$module")" -p "$(basename "$module")" -v; then
        echo "[PASS] $name"
        ((PASS++))
    else
        echo "[FAIL] $name"
        ((FAIL++))
    fi
}

run_suite "Parser unit tests"     "tests/unit/test_parser.py"
run_suite "DSP unit tests"        "tests/unit/test_dsp.py"
run_suite "State machine tests"   "pc/detection/tests/test_state_machine.py"
run_suite "Integration tests"     "tests/integration/test_pipeline.py"

echo ""
echo "=============================="
echo "  Results: $PASS passed, $FAIL failed"
echo "=============================="

[[ $FAIL -eq 0 ]]
