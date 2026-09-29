# Рабочее место: OpenCode 1.18.33 и ripgrep 15.1.0 по sha256, uid 10001, без apt.
# Контекст сборки — student/. Строки «# syntax=» нет: встроенный фронтенд знает ADD --checksum,
# а строка тянула бы незакреплённый образ.
FROM ubuntu:22.04@sha256:b8b6ee6aa931ecd9d0d952abc34dc0e5f7c6a30c6bb71b079fe399fde0329c02 AS fetch
ADD --checksum=sha256:e546123213ae47909a4268692aa4b94950d011afe9cac9938753a2194f1c16d5 \
    https://github.com/anomalyco/opencode/releases/download/v1.18.33/opencode-linux-x64.tar.gz /dl/
ADD --checksum=sha256:1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599 \
    https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/ripgrep-15.1.0-x86_64-unknown-linux-musl.tar.gz /dl/
RUN mkdir /out \
 && tar -xzf /dl/opencode-linux-x64.tar.gz --no-same-owner -C /out opencode \
 && tar -xzf /dl/ripgrep-15.1.0-x86_64-unknown-linux-musl.tar.gz --no-same-owner -C /out \
      --strip-components=1 ripgrep-15.1.0-x86_64-unknown-linux-musl/rg

FROM ubuntu:22.04@sha256:b8b6ee6aa931ecd9d0d952abc34dc0e5f7c6a30c6bb71b079fe399fde0329c02
# пользователь места; HOME и конфигурация — root, только чтение; тома — его, 0700
RUN groupadd -g 10001 pcbk \
 && useradd -u 10001 -g 10001 -M -d /home/pcbk -s /usr/sbin/nologin pcbk \
 && install -d -o root -g root -m 0755 /home/pcbk /etc/pcbk-opencode /etc/pcbk-opencode/opencode \
      /etc/pcbk-opencode/opencode/agents /run/secrets \
 && install -d -o 10001 -g 10001 -m 0700 /var/lib/opencode /work
COPY --from=fetch --chown=0:0 --chmod=0755 /out/opencode /out/rg /usr/local/bin/
COPY --chown=0:0 --chmod=0755 pcbk-entrypoint pcbk-health /usr/local/bin/
# конфигурация вместе с пустым .gitignore: без него GET /agent → 500
COPY --chown=0:0 config/ /etc/pcbk-opencode/opencode/
# права не зависят от umask рабочей копии: каталоги 0755, файлы 0644
RUN find /etc/pcbk-opencode -type d -exec chmod 0755 {} + \
 && find /etc/pcbk-opencode -type f -exec chmod 0644 {} +

ENV HOME=/home/pcbk \
    XDG_CONFIG_HOME=/etc/pcbk-opencode \
    XDG_DATA_HOME=/var/lib/opencode/data \
    XDG_CACHE_HOME=/var/lib/opencode/cache \
    XDG_STATE_HOME=/var/lib/opencode/state \
    TMPDIR=/tmp \
    OPENCODE_DISABLE_MODELS_FETCH=1 \
    OPENCODE_DISABLE_AUTOUPDATE=1 \
    OPENCODE_DISABLE_SHARE=1 \
    OPENCODE_DISABLE_LSP_DOWNLOAD=1 \
    OPENCODE_DISABLE_PROJECT_CONFIG=1 \
    OPENCODE_DISABLE_DEFAULT_PLUGINS=1 \
    OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER=true \
    BUN_RUNTIME_TRANSPILER_CACHE_PATH=0 \
    OPENCODE_SERVER_USERNAME=opencode

USER 10001:10001
WORKDIR /work
EXPOSE 4096

# ручка экземпляра GET /agent: /global/health отвечает 200 и сломанному экземпляру
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --start-interval=2s --retries=3 CMD ["/usr/local/bin/pcbk-health"]

# пароль — из файла /run/secrets/opencode-pw; без него — код 78
ENTRYPOINT ["/usr/local/bin/pcbk-entrypoint"]
