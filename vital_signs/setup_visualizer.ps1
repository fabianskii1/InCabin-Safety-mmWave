# Create a Python 3.9/3.10 venv for TI Industrial Visualizer (PySide2).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$req = Join-Path $here "requirements-visualizer.txt"
$venv = Join-Path $here ".venv"

$py = $null
foreach ($ver in @("3.10", "3.9", "3.8")) {
    $candidate = & py "-$ver" -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $candidate) {
        $py = $candidate.Trim()
        break
    }
}

if (-not $py) {
    Write-Host "Python 3.9 or 3.10 is required. PySide2 cannot run on 3.13."
    Write-Host "Install 3.10 from https://www.python.org/downloads/ then re-run this script."
    Write-Host "During install, check 'Add python.exe to PATH' and include the py launcher."
    exit 1
}

Write-Host "Using $py"
& $py -m venv $venv
& (Join-Path $venv "Scripts\python.exe") -m pip install --upgrade pip
& (Join-Path $venv "Scripts\python.exe") -m pip install -r $req
Write-Host "Done. Launch with:"
Write-Host "  python $here\run_visualizer.py"
