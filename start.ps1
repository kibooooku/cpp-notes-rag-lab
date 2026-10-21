param(
    [switch]$CheckOnly,
    [switch]$NoBrowser,
    [ValidateRange(1024, 65535)][int]$Port = 8501
)

# Keep this wrapper ASCII-only for Windows PowerShell 5.1 compatibility.
$ErrorActionPreference = 'Stop'
try {
    $projectRoot = $PSScriptRoot
    $pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw 'Missing .venv. Complete the first-time installation in README.md, then double-click start.bat again.'
    }
    Set-Location -LiteralPath $projectRoot
    $env:PYTHONUTF8 = '1'
    $env:CPP_RAG_ROOT = $projectRoot
    $sourcePath = Join-Path $projectRoot 'src'
    $env:PYTHONPATH = $sourcePath + [IO.Path]::PathSeparator + $env:PYTHONPATH
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding

    Write-Host '[CppNotes] Checking local environment and indexes...'
    $prepareArgs = @('-X', 'utf8', (Join-Path $projectRoot 'scripts/prepare_launch.py'), '--port', "$Port")
    if ($CheckOnly) { $prepareArgs += '--skip-port-check' }
    & $pythonPath @prepareArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if ($CheckOnly) { exit 0 }

    Write-Host "Opening CppNotes at http://127.0.0.1:$Port"
    Write-Host 'Keep this window open. Press Ctrl+C or close it to stop the server.'
    $headless = if ($NoBrowser) { 'true' } else { 'false' }
    $streamlitArgs = @(
        '-X', 'utf8', '-m', 'streamlit', 'run', (Join-Path $sourcePath 'cpp_notes_rag/app.py'),
        '--server.address', '127.0.0.1', '--server.port', "$Port",
        '--browser.serverAddress', '127.0.0.1', '--browser.serverPort', "$Port",
        '--server.headless', $headless, '--server.showEmailPrompt', 'false',
        '--browser.gatherUsageStats', 'false'
    )
    & $pythonPath @streamlitArgs
    exit $LASTEXITCODE
} catch {
    Write-Host ("[ERROR] " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
