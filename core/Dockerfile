# Серверный слой: FastAPI, роли, дверь к историану.
# Зависимости — точные версии по хешам из requirements.lock, без добора транзитивных.
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.lock \
 && rm /tmp/requirements.lock

# свой uid 10003; домашнего каталога нет, корень в компоновке — только для чтения
RUN groupadd --gid 10003 pcbk-core \
 && useradd --uid 10003 --gid 10003 --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin pcbk-core

WORKDIR /app
COPY pcbk_core /app/pcbk_core

USER 10003:10003
EXPOSE 8000

# 200 только когда все роли здоровы; строка сторожа смотрит /healthz/data
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-m", "pcbk_core.main", "--healthcheck"]

CMD ["python", "-m", "pcbk_core.main"]
