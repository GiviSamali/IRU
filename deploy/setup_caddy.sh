#!/bin/bash
# ИРУ v3.4 — Установка Caddy (reverse proxy + auto HTTPS)
# Запускать от root на VPS

set -e

echo "=== Установка Caddy ==="

# Установить Caddy
apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update
apt install -y caddy

# Конфигурация: reverse proxy с автоматическим HTTPS
cat > /etc/caddy/Caddyfile << 'EOF'
irumode.ru, www.irumode.ru {
    reverse_proxy localhost:8000
}
EOF

echo ""
echo "=== Caddy установлен ==="
echo "Домен: irumode.ru (HTTPS автоматически через Let's Encrypt)"
echo ""

systemctl enable caddy
systemctl restart caddy
systemctl status caddy --no-pager

echo ""
echo "=== Готово! Caddy запущен ==="
