# Запуск в Windows: run.bat [порт]  или  powershell -ExecutionPolicy Bypass -File run.ps1 [порт]
# Создаёт окружение Python (backend\.venv), ставит зависимости и запускает сервер; браузер откроется сам.
# Интерфейс уже собран (frontend\dist). Пересобрать его: run.ps1 -RebuildFrontend (нужен Node.js 20.19+).
param([int]$Port = 0, [switch]$RebuildFrontend)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Venv = Join-Path $Backend ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"

function Fail([string]$Message) {
    Write-Host ""
    Write-Host "ОШИБКА: $Message" -ForegroundColor Red
    exit 1
}

function Test-Python([string[]]$Command) {
    if (-not (Get-Command $Command[0] -ErrorAction SilentlyContinue)) { return $false }
    $exe = $Command[0]
    $rest = @($Command | Select-Object -Skip 1)
    try {
        & $exe @rest -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Test-VenvReady {
    if (-not (Test-Path $VenvPy)) { return $false }
    try {
        & $VenvPy -c "import pip" *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# 1. Окружение Python 3.10+
if (-not (Test-VenvReady)) {
    $candidates = @(@("py", "-3"), @("python"), @("python3"))
    $python = $candidates | Where-Object { Test-Python $_ } | Select-Object -First 1
    if (-not $python) {
        Fail "Нужен Python 3.10 или новее: https://www.python.org/downloads/ (при установке отметьте «Add python.exe to PATH»)."
    }
    if (Test-Path $Venv) { Remove-Item $Venv -Recurse -Force }
    Write-Host "Создаю окружение Python в backend\.venv ..."
    $exe = $python[0]
    $rest = @($python | Select-Object -Skip 1)
    & $exe @rest -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Fail "Не удалось создать окружение Python." }
}

# 2. Зависимости: при первом запуске и после изменения backend\pyproject.toml
$Stamp = Join-Path $Venv ".deps"
$Want = (Get-FileHash (Join-Path $Backend "pyproject.toml") -Algorithm SHA256).Hash
$Have = if (Test-Path $Stamp) { (Get-Content $Stamp -Raw).Trim() } else { "" }
if ($Have -ne $Want) {
    Write-Host "Устанавливаю зависимости Python (при первом запуске 1-2 минуты) ..."
    & $VenvPy -m pip install --disable-pip-version-check --quiet -e $Backend
    if ($LASTEXITCODE -ne 0) { Fail "Не удалось установить зависимости Python (нужен интернет)." }
    Set-Content -Path $Stamp -Value $Want -Encoding ascii
}

# 3. Интерфейс
$Dist = Join-Path $Root "frontend\dist\index.html"
if ($RebuildFrontend -or -not (Test-Path $Dist)) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Fail "Нет собранного интерфейса (frontend\dist) и не найден npm. Установите Node.js 20.19+ и запустите снова."
    }
    Push-Location (Join-Path $Root "frontend")
    try {
        npm ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { Fail "npm ci завершился с ошибкой." }
        npm run build
        if ($LASTEXITCODE -ne 0) { Fail "Сборка интерфейса завершилась с ошибкой." }
    } finally {
        Pop-Location
    }
}

# 4. Сервер
$serveArgs = @("-m", "app.serve")
if ($Port -gt 0) { $serveArgs += @("--port", "$Port") }
Push-Location $Backend
try {
    & $VenvPy @serveArgs
} finally {
    Pop-Location
}
exit $LASTEXITCODE
