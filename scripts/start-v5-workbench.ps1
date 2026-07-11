param(
    [int]$Port = 8000,
    [string]$HostAddress = "127.0.0.1",
    [string]$ExpectedBranch = "integration/v5-chatbot-workbench"
)

$ErrorActionPreference = "Stop"

$ScriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Resolve-Path (Join-Path $ScriptPath "..")
Set-Location $Root

$Branch = (git branch --show-current).Trim()
if (-not $Branch) {
    throw "Refusing to start: runtime worktree is detached. Expected branch $ExpectedBranch."
}
if ($Branch -ne $ExpectedBranch) {
    throw "Refusing to start from branch '$Branch'. Expected '$ExpectedBranch'."
}

$Status = (git status --porcelain)
if ($Status) {
    throw "Refusing to start: runtime worktree has uncommitted changes.`n$Status"
}

$Sha = (git rev-parse --short HEAD).Trim()
$Python = (Get-Command python).Source
$Src = Join-Path $Root "src"
$Frontend = Join-Path $Root "apps\web"

$env:PYTHONPATH = $Src
$ImportInfo = python -c "import sys, importlib.util; print(sys.executable); print(importlib.util.find_spec('fluid_scientist').origin); print(importlib.util.find_spec('fluid_scientist.api.app').origin)"

Write-Host "Fluid Scientist V5 Workbench"
Write-Host "branch=$Branch"
Write-Host "sha=$Sha"
Write-Host "root=$Root"
Write-Host "python=$Python"
Write-Host "frontend=$Frontend"
Write-Host "import-info:"
Write-Host $ImportInfo
Write-Host "build-info=http://$HostAddress`:$Port/api/system/build-info"

python -m uvicorn fluid_scientist.api.app:create_app --factory --host $HostAddress --port $Port
