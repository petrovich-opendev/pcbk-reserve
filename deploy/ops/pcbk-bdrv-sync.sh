#!/bin/bash
# Учётка историана резерва — копия файла Dify (README «Служба данных», «Учётка историана»). Раз в 10 мин (cron
# пользователя выкладки): файл Dify изменился — копия обновляется и core пересоздаётся (секрет — привязка файла:
# нужен --force-recreate, а не restart). Только чтение файла Dify; Dify не трогается. Журнал — logs/bdrv-sync.log.
set -u
R=/opt/pcbk-reserve
SRC=/opt/dify/scripts/.bdrv.env
LOG=$R/logs/bdrv-sync.log
mkdir -p "$R/logs"
SD=$(grep '^SECRETS_DIR=' "$R/.env" | cut -d= -f2)
[ -n "$SD" ] && [ -d "$SD" ] || { echo "$(date -Is) нет SECRETS_DIR в .env" >> "$LOG"; exit 1; }
[ -r "$SRC" ] || { echo "$(date -Is) файл учётки Dify не читается: $SRC" >> "$LOG"; exit 0; }
cmp -s "$SRC" "$SD/bdrv.env" && exit 0
umask 077
tmp=$(mktemp "$SD/.bdrv.env.XXXXXX") && cat "$SRC" > "$tmp" && chmod 0444 "$tmp" && mv -f "$tmp" "$SD/bdrv.env" || {
  echo "$(date -Is) не удалось обновить копию учётки" >> "$LOG"; rm -f "$tmp"; exit 1; }
echo "$(date -Is) учётка историана у Dify изменилась — копия обновлена, пересоздаю core" >> "$LOG"
cd "$R" && docker compose up -d --no-build --force-recreate core >> "$LOG" 2>&1
