#!/usr/bin/env bash
# One-command setup for the Desktop PC (Linux / macOS).
# Creates a virtual environment, installs all dependencies, and verifies the install.
set -euo pipefail

VENV_DIR="venv"
PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=9

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

echo ""
echo "======================================"
echo "  WiFi CSI Detector — PC Setup"
echo "======================================"
echo ""

# ---------------------------------------------------------------------------
# 1. Check Python version
# ---------------------------------------------------------------------------
PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if (( major > PYTHON_MIN_MAJOR || (major == PYTHON_MIN_MAJOR && minor >= PYTHON_MIN_MINOR) )); then
            PYTHON="$candidate"
            ok "Python $ver found ($candidate)"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    fail "Python $PYTHON_MIN_MAJOR.$PYTHON_MIN_MINOR+ not found. Install from https://www.python.org/"
fi

# ---------------------------------------------------------------------------
# 2. Create virtual environment
# ---------------------------------------------------------------------------
if [[ -d "$VENV_DIR" ]]; then
    warn "Virtual environment already exists at $VENV_DIR — skipping creation"
else
    echo "Creating virtual environment in $VENV_DIR/ ..."
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Virtual environment created"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# ---------------------------------------------------------------------------
# 3. Upgrade pip
# ---------------------------------------------------------------------------
echo "Upgrading pip..."
"$VENV_PIP" install --quiet --upgrade pip
ok "pip upgraded"

# ---------------------------------------------------------------------------
# 4. Install core dependencies
# ---------------------------------------------------------------------------
echo "Installing core dependencies (setuptools, numpy, PyYAML, matplotlib)..."
"$VENV_PIP" install --quiet --upgrade \
    "setuptools>=65.0" \
    "numpy>=1.24.0" \
    "PyYAML>=6.0" \
    "matplotlib>=3.7.0"
ok "Core dependencies installed"

# ---------------------------------------------------------------------------
# 5. Install optional but strongly recommended dependencies
# ---------------------------------------------------------------------------
echo "Installing optional dependencies (scipy, flask, colorlog)..."
"$VENV_PIP" install --quiet \
    "scipy>=1.11.0" \
    "flask>=2.3.0" \
    "colorlog>=6.7.0" \
    || warn "Some optional packages failed — system will fall back to pure-numpy implementations"
ok "Optional dependencies installed"

# ---------------------------------------------------------------------------
# 6. Install project in editable mode
# ---------------------------------------------------------------------------
echo "Installing project package (editable mode)..."
"$VENV_PIP" install --quiet -e ".[full]"
ok "Project installed (python -m pc.main will now work)"

# ---------------------------------------------------------------------------
# 7. Verify imports
# ---------------------------------------------------------------------------
echo "Verifying imports..."
"$VENV_PYTHON" - <<'PYCHECK'
import sys
errors = []

def check(module, pkg_name=None):
    try:
        __import__(module)
    except ImportError:
        errors.append(pkg_name or module)

check("numpy")
check("yaml", "PyYAML")
check("matplotlib")

try:
    import scipy
except ImportError:
    print("  [WARN] scipy not available — DSP filters will use pure-numpy fallbacks")

try:
    import flask
except ImportError:
    print("  [WARN] flask not available — web UI will not work (matplotlib UI still works)")

# Core project imports
try:
    from pc.common.types import CSIFrame, DSPFeatures, MotionEvent
    from pc.common.config import load_config
    from pc.ingestion.parser import parse_packet, build_packet
    from pc.dsp.pipeline import DSPPipeline
    from pc.detection.pipeline import DetectionPipeline
except Exception as e:
    errors.append(f"project import failed: {e}")

if errors:
    print(f"  MISSING: {', '.join(errors)}", file=sys.stderr)
    sys.exit(1)

print("  All imports OK")
PYCHECK

ok "Import verification passed"

# ---------------------------------------------------------------------------
# 8. Done
# ---------------------------------------------------------------------------
echo ""
echo "======================================"
echo "  Setup complete!"
echo "======================================"
echo ""
echo "  Activate the environment before running:"
echo ""
echo "    source venv/bin/activate"
echo ""
echo "  Then start the system:"
echo ""
echo "    python -m pc.main                  # matplotlib dashboard"
echo "    python -m pc.main --web-ui         # web dashboard"
echo "    python -m pc.main --no-viz         # headless / logs only"
echo ""
echo "  Run tests:"
echo ""
echo "    python -m unittest discover -s tests/unit -p 'test_*.py' -v"
echo ""
