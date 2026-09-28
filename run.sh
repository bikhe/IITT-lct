#!/usr/bin/env bash
# Запуск в Linux и macOS: ./run.sh [порт]
# Создаёт окружение Python (backend/.venv), ставит зависимости и запускает сервер.
# Интерфейс уже собран (frontend/dist). Пересобрать его: REBUILD_FRONTEND=1 ./run.sh (нужен Node.js 20.19+).
set -euo pipefail
cd "$(dirname "$0")"
PORT_ARG="${1:-${PORT:-}}"
VENV=backend/.venv
export PYTHONIOENCODING=utf-8

fail() {
  printf '\nОШИБКА: %s\n' "$1" >&2
  exit 1
}

venv_python() {
  if [ -x "$VENV/bin/python" ]; then echo "$VENV/bin/python"; else echo "$VENV/Scripts/python.exe"; fi  # Scripts — Git Bash
}

# 1. Окружение Python 3.10+
if ! "$(venv_python)" -c 'import pip' >/dev/null 2>&1; then
  PY=""
  for cand in python3 python py; do
    if command -v "$cand" >/dev/null 2>&1 &&
      "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      PY="$cand"
      break
    fi
  done
  [ -n "$PY" ] || fail "нужен Python 3.10 или новее (https://www.python.org/downloads/)."
  rm -rf "$VENV"
  echo "Создаю окружение Python в $VENV ..."
  if ! "$PY" -m venv "$VENV"; then
    rm -rf "$VENV"
    fail "не удалось создать окружение Python. В Debian и Ubuntu установите пакет: sudo apt install python3-venv"
  fi
fi
VPY="$(venv_python)"

# 2. Зависимости: при первом запуске и после изменения backend/pyproject.toml
STAMP="$VENV/.deps"
WANT="$("$VPY" -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest().upper())' backend/pyproject.toml)"
HAVE=""
[ -f "$STAMP" ] && HAVE="$(tr -d '\r\n' <"$STAMP")"
if [ "$HAVE" != "$WANT" ]; then
  echo "Устанавливаю зависимости Python (при первом запуске 1–2 минуты) ..."
  "$VPY" -m pip install --disable-pip-version-check --quiet -e ./backend ||
    fail "не удалось установить зависимости Python (нужен интернет)."
  printf '%s\n' "$WANT" >"$STAMP"
fi

# 3. Интерфейс
if [ ! -f frontend/dist/index.html ] || [ "${REBUILD_FRONTEND:-0}" = "1" ]; then
  command -v npm >/dev/null 2>&1 ||
    fail "нет собранного интерфейса (frontend/dist) и не найден npm. Установите Node.js 20.19+ и запустите снова."
  (cd frontend && npm ci --no-audit --no-fund && npm run build) || fail "сборка интерфейса завершилась с ошибкой."
fi

# 4. Сервер
cd backend
exec "../$VPY" -m app.serve ${PORT_ARG:+--port "$PORT_ARG"}
