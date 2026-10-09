$PSNativeCommandUseErrorActionPreference = $false
Set-Location -LiteralPath $PSScriptRoot
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$probe = "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"

$cands = @(
    @{ exe = "py"; pre = @("-3") },
    @{ exe = "python"; pre = @() }
)
$py = $null
$pyArgs = @()
$old = $null
foreach ($c in $cands) {
    if (-not (Get-Command $c.exe -CommandType Application -ErrorAction SilentlyContinue)) { continue }
    $pre = $c.pre
    & $c.exe @pre -c "import sys" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { continue }
    & $c.exe @pre -c $probe 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $py = $c.exe
        $pyArgs = $pre
        break
    }
    $old = & $c.exe @pre -c "import platform; print(platform.python_version())" 2>$null | Select-Object -First 1
}
if (-not $py) {
    if ($old) {
        Write-Host "python $old is too old, 3.10 or newer is needed"
    } else {
        Write-Host "python 3.10 or newer was not found, install it from https://www.python.org/downloads/ and tick add python to path"
    }
    exit 1
}

$ok = $false
if (Test-Path -LiteralPath $venvPy) {
    & $venvPy -c $probe 2>$null | Out-Null
    $ok = $LASTEXITCODE -eq 0
}
if (-not $ok) {
    if (Test-Path -LiteralPath ".venv") { Remove-Item -LiteralPath ".venv" -Recurse -Force -ErrorAction SilentlyContinue }
    & $py @pyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPy)) {
        Write-Host "could not create .venv"
        exit 1
    }
}

& $venvPy -c "import filecmp, sys; sys.exit(0 if filecmp.cmp('requirements.txt', '.venv/requirements.txt', shallow=False) else 1)" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    & $venvPy -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "pip install failed"
        exit 1
    }
    Copy-Item -LiteralPath "requirements.txt" -Destination ".venv\requirements.txt" -Force
}

Write-Host "starting Mando"
$env:PYTHONUTF8 = "1"
# a launch that fails (no exe) leaves $LASTEXITCODE stale; preset it so that failure exits 1
$LASTEXITCODE = 1
& $venvPy -m mirror
exit $LASTEXITCODE
