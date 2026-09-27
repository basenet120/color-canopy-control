# Color Canopy workshop — Windows starter (run through start.bat). Usage: start.bat [sim|allow|setup|status]
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "Installing uv (a small Python manager) - one time only..."
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
Write-Host "Getting the program ready (the first time takes a few minutes)..."
uv sync --frozen --no-dev --extra live --quiet
$cmd = if ($args.Count -gt 0) { $args[0] } else { "run" }
uv run --no-sync python -m conductor.workshop $cmd
