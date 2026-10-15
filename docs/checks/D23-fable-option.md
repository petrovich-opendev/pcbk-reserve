# Д23 (подготовлено, НЕ выложено): Claude Fable 5.1 как вариант модели без ZDR (16.10.2026)

Просьба владельца (15.10, ночь): «поставь самую продвинутую LLM для рассуждений и глобального ресерча — если
останется время». 15.10 днём владелец выбрал Opus 5.5 с ZDR вместо Fable 5.1, потому что у Fable 5.1 в OpenRouter нет
ни одной точки из списка ZDR. Поэтому здесь только подготовка: включение — после явного «да, без ZDR».

## Что в ветке `d23/fable-option`

- `ModelPolicy.zdr` (по умолчанию `True`); прокси шлёт `provider.zdr` из политики модели, а не всегда `True`.
- Девятая модель белого списка: `anthropic/claude-fable-5.1`, точка `google-vertex/global` (аптайм 100 %), цены
  10 / 50 / запись кэша 12,5 за 1 млн токенов, effort high, `cache_control`, `zdr=False`.
- `student/config/opencode.json` — модель в провайдерах `pcbk` и `pcbk-or`.
- Пока модели нет в `LLM_MODELS` сервера, прокси её не пропускает: выкладка этой ветки сама по себе ничего не меняет.

## Включение (после «да» владельца)

1. Выложить образы этой ветки (core, student) — как ночной выпуск.
2. `.env`: `LLM_MODELS=anthropic/claude-fable-5.1,anthropic/claude-opus-5.5,deepseek/deepseek-v4.1-flash,z-ai/glm-5.3,
   z-ai/glm-5.3-flash`; `LLM_FALLBACK=anthropic/claude-fable-5.1=anthropic/claude-opus-5.5,…` (запасная — Opus с ZDR).
3. Места: `OPENCODE_CONFIG_CONTENT={"model": "pcbk-or/anthropic/claude-fable-5.1"}`; пересоздать места (не во время
   занятия; новый разговор — новая модель).
4. Откат — прежние `.env` и `compose.override.yaml`.

## «Глобальный ресерч» (поиск в интернете) — не подготовлен

На стенде агентам интернет закрыт (AGENTS.md п.6). Веб-поиск OpenRouter (`plugins: web`) отправляет запросы и контекст
во внешний поиск — отдельное решение владельца по данным ПЦБК и правилам стенда.
