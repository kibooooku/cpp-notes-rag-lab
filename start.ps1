$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw '请先按 README 安装 .venv 和项目依赖。'
}
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = '1'
& $pythonPath -m streamlit run src/cpp_notes_rag/app.py --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false
