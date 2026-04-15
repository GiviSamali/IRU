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

# Конфигурация: reverse proxy на порт 8000
# Для IP-адреса без домена — HTTP mode на порту 80
cat > /etc/caddy/Caddyfile << 'EOF'
:80 {
    reverse_proxy localhost:8000
}
EOF

echo ""
echo "=== Caddy установлен ==="
echo "Текущий конфиг: HTTP на порту 80 → localhost:8000"
echo ""
echo "Если у вас есть домен, замените Caddyfile:"
echo "  nano /etc/caddy/Caddyfile"
echo ""
echo "  ваш-домен.ru {"
echo "      reverse_proxy localhost:8000"
echo "  }"
echo ""

systemctl enable caddy
systemctl restart caddy
systemctl status caddy --no-pager

echo ""
echo "=== Готово! Caddy запущен ==="
