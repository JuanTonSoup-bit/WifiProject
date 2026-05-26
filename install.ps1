# WiFi CSI Detector - Windows PC Setup
# Run from the repo root in PowerShell.
# If you get a script execution error, run first:
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

param(
    [string]$VenvDir = "venv",
    [switch]$SkipOptional
)

$ErrorActionPreference = "Stop"

function Write-OK   { param($msg) Write-Host "[OK]  $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Fail { param($msg) Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 1 }
function Write-Step { param($msg) Write-Host "`n$msg..." -ForegroundColor Cyan }

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  WiFi CSI Detector - PC Setup"        -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Find Python 3.9+
# ---------------------------------------------------------------------------
Write-Step "Checking Python version"

$PythonExe = $null
foreach ($candidate in @("python", "python3", "py")) {
    try {
        $ver = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>$null
        if ($ver -match "^(\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 9)) {
                $PythonExe = $candidate
                Write-OK "Python $ver found ($candidate)"
                break
            }
        }
    } catch { }
}

if (-not $PythonExe) {
    Write-Fail "Python 3.9+ not found. Download from https://www.python.org/downloads/"
}

# ---------------------------------------------------------------------------
# 2. Create virtual environment
# ---------------------------------------------------------------------------
Write-Step "Setting up virtual environment"

if (Test-Path $VenvDir) {
    Write-Warn "Virtual environment already exists at $VenvDir - skipping creation"
} else {
    & $PythonExe -m venv $VenvDir
    Write-OK "Virtual environment created at $VenvDir\"
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip    = Join-Path $VenvDir "Scripts\pip.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Fail "Virtual environment creation failed — $VenvPython not found"
}

# ---------------------------------------------------------------------------
# 3. Upgrade pip
# ---------------------------------------------------------------------------
Write-Step "Upgrading pip"
& $VenvPip install --quiet --upgrade pip
Write-OK "pip upgraded"

# ---------------------------------------------------------------------------
# 4. Install core dependencies
# ---------------------------------------------------------------------------
Write-Step "Installing core dependencies (setuptools, numpy, PyYAML, matplotlib)"
& $VenvPip install --quiet --upgrade "setuptools>=65.0" "numpy>=1.24.0" "PyYAML>=6.0" "matplotlib>=3.7.0"
Write-OK "Core dependencies installed"

# ---------------------------------------------------------------------------
# 5. Install optional dependencies
# ---------------------------------------------------------------------------
if (-not $SkipOptional) {
    Write-Step "Installing optional dependencies (scipy, flask, colorlog)"
    try {
        & $VenvPip install --quiet "scipy>=1.11.0" "flask>=2.3.0" "colorlog>=6.7.0"
        Write-OK "Optional dependencies installed"
    } catch {
        Write-Warn "Some optional packages failed to install - system will use fallbacks"
    }
}

# ---------------------------------------------------------------------------
# 6. Install project in editable mode
# ---------------------------------------------------------------------------
Write-Step "Installing project package"
& $VenvPip install --quiet -e ".[full]"
Write-OK "Project installed (python -m pc.main will now work)"

# ---------------------------------------------------------------------------
# 7. Verify imports
# ---------------------------------------------------------------------------
Write-Step "Verifying all imports"

$verifyScript = @'
import sys
errors = []

required = {"numpy": "numpy", "yaml": "PyYAML", "matplotlib": "matplotlib"}
for module, pkg in required.items():
    try:
        __import__(module)
    except ImportError:
        errors.append(pkg)

optional = {"scipy": "scipy", "flask": "flask", "colorlog": "colorlog"}
for module, pkg in optional.items():
    try:
        __import__(module)
    except ImportError:
        print(f"  [WARN] {pkg} not available (optional)")

try:
    from pc.common.types import CSIFrame, DSPFeatures, MotionEvent
    from pc.common.config import load_config
    from pc.ingestion.parser import parse_packet, build_packet
    from pc.dsp.pipeline import DSPPipeline
    from pc.detection.pipeline import DetectionPipeline
except Exception as e:
    errors.append(f"project: {e}")

if errors:
    print(f"MISSING PACKAGES: {', '.join(errors)}", file=sys.stderr)
    sys.exit(1)

print("  All required imports OK")
'@

$verifyScript | & $VenvPython -
Write-OK "Import verification passed"

# ---------------------------------------------------------------------------
# 8. Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "======================================" -ForegroundColor Green
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Activate the environment before running:"
Write-Host ""
Write-Host "    venv\Scripts\activate" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Then start the system:"
Write-Host ""
Write-Host "    python -m pc.main                  # matplotlib dashboard" -ForegroundColor Yellow
Write-Host "    python -m pc.main --web-ui         # web dashboard (http://localhost:5503)" -ForegroundColor Yellow
Write-Host "    python -m pc.main --no-viz         # headless / logs only" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Run tests:"
Write-Host ""
Write-Host "    python -m unittest discover -s tests/unit -p 'test_*.py' -v" -ForegroundColor Yellow
Write-Host ""
