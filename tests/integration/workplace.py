"""Константы рабочего места для тестов: образ, окружение, точки монтирования, метки."""

IMAGE = "pcbk-reserve/student:d26"
# модели места — ровно список прокси по порядку select_models("") (core/pcbk_core/llm/models.py)
MODELS = ["z-ai/glm-5.3", "deepseek/deepseek-v4.1-flash", "google/gemini-3.8-flash", "openai/gpt-6-sol",
          "z-ai/glm-5.3-flash", "openai/gpt-6-luna", "anthropic/claude-sonnet-5.5", "anthropic/claude-opus-5.5",
          "anthropic/claude-fable-5.1"]
# те же имена через @openrouter/ai-sdk-provider: reasoning_details с подписями (Д4б-R20)
OR_MODELS = ["google/gemini-3.8-flash", "anthropic/claude-sonnet-5.5", "anthropic/claude-opus-5.5",
          "anthropic/claude-fable-5.1"]
# начало описания каждой заглушки bash/edit/write/apply_patch
STUB_PREFIX = "Отключено на учебном стенде"
# окружение образа места — ровно эти переменные сверх PATH базового образа
IMAGE_ENV = {
    "HOME": "/home/pcbk", "XDG_CONFIG_HOME": "/etc/pcbk-opencode",
    "XDG_DATA_HOME": "/var/lib/opencode/data", "XDG_CACHE_HOME": "/var/lib/opencode/cache",
    "XDG_STATE_HOME": "/var/lib/opencode/state", "TMPDIR": "/tmp",
    "OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_SHARE": "1", "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    "OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER": "true", "BUN_RUNTIME_TRANSPILER_CACHE_PATH": "0",
    "OPENCODE_SERVER_USERNAME": "opencode",
}
WORKPLACE_ENV = IMAGE_ENV | {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
# точки монтирования места в компоновке (задача 3 Д2, токен данных — Д4б): секреты, агенты, два тома
MOUNTS = {"/run/secrets/opencode-pw", "/run/secrets/llm-token", "/run/secrets/data-token",
          "/etc/pcbk-opencode/opencode/agents", "/var/lib/opencode", "/work"}
# метка одноразовых контейнеров тестов: уборка снимает только их
ONESHOT_LABEL = "pcbk-test.oneshot"
