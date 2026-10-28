$ErrorActionPreference = "Stop"
$appDir = $PSScriptRoot
$projectDir = Split-Path -Parent $appDir
$projectPython = Join-Path $projectDir ".venv\Scripts\python.exe"
$appPython = Join-Path $appDir ".venv\Scripts\python.exe"

function Test-LabelerDependencies($pythonPath) {
    if (-not (Test-Path $pythonPath)) { return $false }
    & $pythonPath -c "import PIL, pypdfium2" *> $null
    return $LASTEXITCODE -eq 0
}

if (Test-LabelerDependencies $projectPython) {
    $pythonPath = $projectPython
} elseif (Test-LabelerDependencies $appPython) {
    $pythonPath = $appPython
} else {
    if (-not (Test-Path $appPython)) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3.11 -m venv (Join-Path $appDir ".venv")
        } else {
            & python -m venv (Join-Path $appDir ".venv")
        }
        if ($LASTEXITCODE -ne 0) { throw "Không tạo được môi trường Python. Cài Python 3.11 rồi chạy lại." }
    }
    $pythonPath = $appPython
    & $pythonPath -m pip install -r (Join-Path $appDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Không cài được Pillow/PyPDFium2. Kiểm tra kết nối mạng rồi chạy lại." }
}

Write-Host "Đang chạy OCR Labeler trên localhost. Nhấn Ctrl+C để dừng." -ForegroundColor Cyan
& $pythonPath -X utf8 (Join-Path $appDir "app.py")
