# Серверный слой: FastAPI, роли, дверь к историану.
# Зависимости — точные версии по хешам из requirements.lock, без добора транзитивных.
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.lock \
 && rm /tmp/requirements.lock

# свой uid 10003; домашнего каталога нет, корень в компоновке — только для чтения.
# Каталог журнала событий (core.db) — его: новый именованный том pcbk-core-data наследует
# владельца каталога образа, без каталога том был бы root:root 0755 и core.db не создался бы
RUN groupadd --gid 10003 pcbk-core \
 && useradd --uid 10003 --gid 10003 --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin pcbk-core \
 && install -d -o 10003 -g 10003 -m 0750 /var/lib/pcbk-core

WORKDIR /app
COPY pcbk_core /app/pcbk_core

USER 10003:10003
EXPOSE 8000

# живость процесса: /healthz/live отвечает 200, пока жив uvicorn; здоровье ролей видит сторож (/healthz/data, /health/llm)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-m", "pcbk_core.main", "--healthcheck"]

CMD ["python", "-m", "pcbk_core.main"]
