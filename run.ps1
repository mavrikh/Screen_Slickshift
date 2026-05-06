param(
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    Write-Host "Creating Python virtual environment..."
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3 -m venv .venv
    }
    else {
        python -m venv .venv
    }
}

Write-Host "Activating virtual environment..."
. .\.venv\Scripts\Activate.ps1

Write-Host "Installing/updating dependencies..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

Write-Host ""
Write-Host "Starting Screen Slickshift on http://$HostAddress`:$Port"
Write-Host "Use Ctrl+C to stop the server."
Write-Host ""

python -m uvicorn app.main:app --host $HostAddress --port $Port --no-access-log
