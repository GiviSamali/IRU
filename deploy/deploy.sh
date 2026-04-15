#!/bin/bash
# ИРУ v3.4 — Полный деплой на VPS
# Запускать от root на VPS: bash /opt/iru/app/deploy/deploy.sh

set -e

APP_DIR="/opt/iru/app"
VENV="/opt/iru/venv"

echo "=== ИРУ v3.4 — Деплой ==="

# 1. Установить зависимости
echo "[1/4] Установка зависимостей..."
source $VENV/bin/activate
pip install -q -r $APP_DIR/requirements.txt

# 2. Настроить systemd
echo "[2/4] Настройка systemd..."
cp $APP_DIR/deploy/iru.service /etc/systemd/system/iru.service
systemctl daemon-reload
systemctl enable iru

# 3. Перезапустить сервер
echo "[3/4] Перезапуск сервера..."
systemctl restart iru
sleep 2

# 4. Проверка
echo "[4/4] Проверка..."
if systemctl is-active --quiet iru; then
    echo "✅ ИРУ запущен и работает"
    echo "   http://$(hostname -I | awk '{print $1}'):8000"
else
    echo "❌ Ошибка запуска! Проверьте логи:"
    echo "   journalctl -u iru -n 20"
    exit 1
fi

echo ""
echo "=== Деплой завершён ==="
