#!/bin/bash
# Автобэкап БД ИРУ
# Запуск через cron: 0 3 * * * /opt/iru/app/deploy/backup_db.sh

BACKUP_DIR="/opt/iru/backups"
DB_PATH="/opt/iru/app/server/iru.db"
DATE=$(date +%Y%m%d_%H%M%S)
KEEP_DAYS=14

mkdir -p "$BACKUP_DIR"

# Используем sqlite3 .backup для консистентного бэкапа (без блокировки)
if [ -f "$DB_PATH" ]; then
    sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/iru_$DATE.db'"
    
    # Сжимаем
    gzip "$BACKUP_DIR/iru_$DATE.db"
    
    echo "[backup] $(date): iru_$DATE.db.gz создан"
    
    # Удаляем бэкапы старше KEEP_DAYS дней
    find "$BACKUP_DIR" -name "iru_*.db.gz" -mtime +$KEEP_DAYS -delete
    
    echo "[backup] Очистка: удалены бэкапы старше $KEEP_DAYS дней"
else
    echo "[backup] ОШИБКА: БД не найдена по пути $DB_PATH"
    exit 1
fi
