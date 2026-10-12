#!/bin/bash
# Самолечение (с 12.10): Docker перезапускает упавший контейнер (restart: unless-stopped), но не «нездоровый» живой.
# Раз в 2 мин (cron пользователя выкладки): pcbk-core, pcbk-edge, pcbk-watchdog в состоянии unhealthy 3 проверки
# подряд (≈ 6 мин) — docker restart, не чаще раза в 30 мин на контейнер. Остановленные администратором (exited) — не
# трогаются. Места — забота шлюза. Журнал — logs/autoheal.log, счётчики — logs/autoheal/.
set -u
R=/opt/pcbk-reserve
ST=$R/logs/autoheal
LOG=$R/logs/autoheal.log
mkdir -p "$ST"
now=$(date +%s)
for c in pcbk-core pcbk-edge pcbk-watchdog; do
  h=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$c" 2>/dev/null || echo missing)
  if [ "$h" != unhealthy ]; then echo 0 > "$ST/$c.count"; continue; fi
  n=$(( $(cat "$ST/$c.count" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$ST/$c.count"
  last=$(cat "$ST/$c.last" 2>/dev/null || echo 0)
  if [ "$n" -ge 3 ] && [ $((now - last)) -ge 1800 ]; then
    echo "$(date -Is) $c: unhealthy $n проверок подряд — docker restart" >> "$LOG"
    docker restart -t 30 "$c" >> "$LOG" 2>&1 && echo "$now" > "$ST/$c.last" && echo 0 > "$ST/$c.count"
  fi
done
