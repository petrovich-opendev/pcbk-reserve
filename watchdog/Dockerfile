# Сторож резервного стенда: проверки, журнал переходов, страница состояния.
# Только стандартная библиотека Python — зависимостей нет.
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# свой uid 10002; каталог журнала — его, том наследует владельца
RUN groupadd --gid 10002 pcbk-watchdog \
 && useradd --uid 10002 --gid 10002 --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin pcbk-watchdog \
 && install -d -o 10002 -g 10002 -m 0750 /var/lib/pcbk-watchdog

WORKDIR /app
COPY components.json /app/components.json
COPY pcbk_watchdog /app/pcbk_watchdog

USER 10002:10002
EXPOSE 8090

# 200 только при свежем снимке: зависший цикл — unhealthy
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-m", "pcbk_watchdog.main", "--healthcheck"]

CMD ["python", "-m", "pcbk_watchdog.main"]
