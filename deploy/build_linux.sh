#!/usr/bin/env bash
# build_linux.sh — сборка Linux-бинаря агента и загрузка на сервер.
#
# Запуск из корня репозитория IRU:
#
#   ./deploy/build_linux.sh 3.7
#
# Аргументы:
#   $1              — версия (обязательно, например 3.7)
#
# Переменные окружения:
#   IRU_SERVER        URL сервера (по умолчанию https://irumode.ru)
#   IRU_ADMIN_TOKEN   Админ-токен (обязательно, если не задан --skip-upload)
#   SKIP_UPLOAD=1     Собрать без загрузки
#
# Требования:
#   - python3 >= 3.11
#   - pip (модуль venv)
#   - системные пакеты: build-essential, patchelf (для pyinstaller)
#   - curl
#
# ВАЖНО: собранный бинарь — это Linux ELF (для Linux-тестеров).
# Windows-сборку делай отдельно через build_windows.ps1 на Windows.
# IruAgent.exe на сервере остаётся Windows-версией — Linux-бинарь
# загружается с другим именем: agent-linux.
#
# Серверный endpoint /api/agent/upload принимает только IruAgent.exe,
# поэтому Linux-бинарь кладётся рядом через scp — раздача через
# статику. Для полноценной двойной раздачи нужен доп. endpoint,
# сейчас скрипт ограничивается scp на VPS.

set -euo pipefail

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
    echo "Использование: $0 <версия>   (например: $0 3.7)" >&2
    exit 1
fi

SERVER="${IRU_SERVER:-https://irumode.ru}"
SKIP_UPLOAD="${SKIP_UPLOAD:-0}"
VPS_HOST="${IRU_VPS_HOST:-root@45.150.38.99}"
VPS_UPDATES_DIR="${IRU_VPS_UPDATES_DIR:-/opt/iru/app/server/updates}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_DIR="$REPO_ROOT/agent"
DIST_DIR="$REPO_ROOT/dist"
BUILD_DIR="$REPO_ROOT/build"
VENV_DIR="$REPO_ROOT/.venv-build"

echo "── Сборка agent (Linux) v$VERSION ──"
echo "Репозиторий: $REPO_ROOT"

if [[ ! -f "$AGENT_DIR/agent.py" ]]; then
    echo "Ошибка: не найден $AGENT_DIR/agent.py" >&2
    exit 1
fi

# ── Виртуальное окружение ────────────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
    echo "Создаю venv в $VENV_DIR …"
    python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip >/dev/null
python -m pip install --upgrade pyinstaller websockets httpx >/dev/null

# ── Очистка ──────────────────────────────────────────────────────────────
rm -rf "$DIST_DIR" "$BUILD_DIR"
rm -f "$REPO_ROOT/IruAgent.spec" "$REPO_ROOT/agent.spec"

# ── Сборка ───────────────────────────────────────────────────────────────
cd "$AGENT_DIR"
python -m PyInstaller \
    --onefile \
    --name agent-linux \
    --distpath "$DIST_DIR" \
    --workpath "$BUILD_DIR" \
    --specpath "$REPO_ROOT" \
    --noconfirm \
    --collect-submodules platforms \
    --hidden-import platforms \
    --hidden-import platforms.windows \
    --hidden-import platforms.linux \
    agent.py

BIN_PATH="$DIST_DIR/agent-linux"
if [[ ! -f "$BIN_PATH" ]]; then
    echo "Ошибка: бинарь не собран ($BIN_PATH)" >&2
    exit 1
fi

chmod +x "$BIN_PATH"
SIZE=$(stat -c%s "$BIN_PATH" 2>/dev/null || stat -f%z "$BIN_PATH")
echo "Готово: $BIN_PATH ($SIZE байт)"

# ── Загрузка ─────────────────────────────────────────────────────────────
if [[ "$SKIP_UPLOAD" == "1" ]]; then
    echo "SKIP_UPLOAD=1 — загрузка пропущена."
    exit 0
fi

echo "Загрузка по scp в $VPS_HOST:$VPS_UPDATES_DIR/ …"
scp "$BIN_PATH" "$VPS_HOST:$VPS_UPDATES_DIR/agent-linux"

# Обновим version-linux.json на сервере — простой sidecar-файл для Linux-тестеров.
# Если в будущем добавите отдельный endpoint /api/agent/download-linux,
# он сможет читать этот файл так же как version.json.
VERSION_JSON_LINUX=$(cat <<EOF
{
  "version": "$VERSION",
  "min_version": "3.0",
  "changelog": "",
  "filename": "agent-linux"
}
EOF
)

ssh "$VPS_HOST" "cat > $VPS_UPDATES_DIR/version-linux.json" <<< "$VERSION_JSON_LINUX"

echo ""
echo "✓ agent-linux v$VERSION загружен на VPS."
echo "  Путь на сервере: $VPS_UPDATES_DIR/agent-linux"
echo "  Раздача через: scp пользователям вручную,"
echo "  либо добавь endpoint /api/agent/download-linux в server/main.py по аналогии с Windows-версией."
