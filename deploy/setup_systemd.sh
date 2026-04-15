#!/bin/bash
# ИРУ v3.4 — Настройка systemd-сервиса
# Запускать от root на VPS

set -e

echo "=== Настройка systemd для ИРУ ==="

# Копировать service-файл
cp /opt/iru/app/deploy/iru.service /etc/systemd/system/iru.service

# Перезагрузить systemd
systemctl daemon-reload

# Включить автозапуск
systemctl enable iru

# Запустить/перезапустить
systemctl restart iru

echo ""
systemctl status iru --no-pager

echo ""
echo "=== Готово! ==="
echo "Команды управления:"
echo "  systemctl status iru   — статус"
echo "  systemctl restart iru  — перезапуск"
echo "  systemctl stop iru     — остановка"
echo "  journalctl -u iru -f   — логи в реальном времени"
