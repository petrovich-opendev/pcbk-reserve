# Подбор навыков для брейншторма и следующих этапов

29.09.2026 · исследование агента на Opus 5.5, только чтение и поиск, ничего не
установлено · провенанс: **[Д]** — документация или репозиторий по ссылке,
**[?]** — не подтверждено.

## Что установлено на самом деле

* Включён только плагин superpowers **6.1.1** от 14.07 (`installed_plugins.json`,
  `enabledPlugins`). Актуальная версия — **6.4.2** от 25.09.
* Синхронизированные наборы engineering, design, data, productivity и навыки из
  `skills/synced` активны.
* Маркетплейс `claude-plugins-official` лежит локальным снимком от 09.09. **Ни
  один плагин из него не установлен**: frontend-design, mcp-server-dev,
  playground, plugin-dev, claude-security, security-guidance, playwright.

## Карта: этап — навык — зачем

| Этап | Навык | Статус | Зачем здесь |
|---|---|---|---|
| Брейншторм | superpowers:brainstorming | установлен | Требует разрезать задачу на подпроекты: веб и серверный слой / экземпляры OpenCode / служба данных / мнемосхемы. Есть визуальный помощник для эскизов мнемосхем. |
| Брейншторм | manufacturing-ot-architect | установлен | Граница «веб и модель ↔ историан». Его красный флаг «прямые сессии L4/5 в OT» — это наши Н3 и неуспех 1. |
| Брейншторм | engineering:architecture, engineering:system-design | установлены | Записи о решениях по развилкам: изоляция OpenCode (контейнер или микро-ВМ), стек фронтенда, доставка живых значений (опрос раз в 5 с или SSE). Оба тонкие, по сути шаблоны. |
| Брейншторм, проверка | design:user-research | установлен | Сценарий проверки М1 и успеха 1 «после одного показа». |
| План | superpowers:writing-plans | установлен, нужна 6.4.2 | В 6.4.2 исправлено: Opus 5.5 начинал писать весь проект вместо плана; добавлен раздел Review Focus. |
| План, проверка | data:validate-data | установлен | Сверка «агент = калькулятор» (успех 2, неуспех 4, Н2): среднее по времени или арифметическое, границы смены, часовой пояс. |
| Реализация | superpowers: subagent-driven-development, TDD, systematic-debugging, using-git-worktrees | установлены | Основной цикл реализации. |
| Реализация | skill-creator, superpowers:writing-skills | установлены | Свои навыки с циклом проверки. OpenCode читает тот же формат SKILL.md — навыки годятся и для агентов студентов. |
| Реализация | dataviz | установлен | Тренды, плитки значений, пометка «устарело» (Н1). |
| Реализация | design:ux-copy | установлен | Русские тексты: плашка статуса стенда, предупреждение о просмотре преподавателем, «устарело, последнее значение в …». |
| Реализация | mcp-builder ([anthropics/skills](https://github.com/anthropics/skills), Apache-2.0, изменён 20.04.2026) [Д] | внешний | Только если служба данных отдаётся агентам как MCP-сервер. Python FastMCP, генерация вопросов для оценки. |
| Реализация | [docker/skills](https://github.com/docker/skills) (Apache-2.0, v0.3.0, 29.09.2026) [Д] | внешний | Dockerfile без root, hardening, compose, защита от разрушительных команд (рядом Dify в Docker). Справка по `sbx`: микро-ВМ со встроенным opencode; требует Ubuntu 24.04+ и KVM, на ВМ — вложенная виртуализация [?]. |
| Реализация | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) (MIT, 25.09.2026): observability-and-instrumentation, security-and-hardening, api-and-interface-design [Д] | внешний | «Наблюдаемость раньше функций»: молчание службы данных, 402 от OpenRouter, бюджет $50. API серверного слоя. |
| Проверка | superpowers: verification-before-completion, requesting-code-review; engineering:code-review | установлены | Ревью с фокусом на доступ к чужим данным (неуспех 3, Н4). |
| Проверка | [microsoft/playwright-cli](https://github.com/microsoft/playwright-cli) (Apache-2.0, v0.1.22, 28.09.2026) [Д] | внешний | Сквозные тесты: вход, изоляция студентов, перетаскивание в редакторе, «устарело» через 15 с. Экономнее по токенам, чем Playwright MCP. |
| Проверка | claude-security (официальный маркетплейс, v0.11.0, 25.09.2026) [Д] | внешний | Модель угроз и скан с перепроверкой находок: побег из контейнера, обход белого списка, доступ к чужому — неуспех 1 и 3, Н3, Н4. |
| Выкладка | engineering:deploy-checklist, engineering:documentation | установлены | Выкладка рядом с Dify с условиями отката; инструкция восстановления. |

## Готовых навыков нет

* **AVEVA InSQL / историан** — ни навыка, ни MCP-сервера. Предложение: свой
  навык `insql-retrieval` через skill-creator.
* **HMI по ISA-101** — только [aott33/claude-ignition-skills](https://github.com/aott33/claude-ignition-skills)
  (4 звезды, без лицензии — все права защищены, привязан к Ignition): только
  как справка. Предложение: свой навык `hmi-isa101`.

## OpenCode: агент-конструктор и навыки для агентов студентов

* **Формат** [Д]: агенты — markdown с frontmatter (`description`, `mode`,
  `model`, `permission`, `steps`, `hidden`) в `.opencode/agents/` или
  `~/.config/opencode/agents/`; навыки — SKILL.md в `.opencode/skills`,
  `.claude/skills` или `.agents/skills`, доступ через `permission.skill`,
  отключение — `tools: {skill: false}` ([агенты](https://opencode.ai/docs/agents/),
  [навыки](https://opencode.ai/docs/skills/)).
* **Генератор агентов встроен** [Д]: `opencode agent create`, промпт
  [`packages/opencode/src/agent/generate.txt`](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/agent/generate.txt)
  (MIT), вызов `Agent.generate`; неинтерактивный режим с флагами
  `--path --description --mode --permissions --model` — можно звать в
  контейнере студента.
  * минус: промпт заточен под агентов для кода — для технологов нужна своя
    редакция;
  * минус: HTTP-ручки для генерации нет, сервер отдаёт только `agent.list`;
  * вторая справка — `plugin-dev/skills/agent-development/references/agent-creation-system-prompt.md`
    в локальном маркетплейсе.
* **Риск версий** [Д]: параллельно живут V1 (v1.18.33 от 28.09) и V2 (2.0.x),
  формат `permission` у них разный, ошибки
  [#49333](https://github.com/anomalyco/opencode/issues/49333),
  [#51262](https://github.com/anomalyco/opencode/issues/51262). Версию закрепить
  до того, как конструктор начнёт писать файлы агентов.
* **Готовые навыки сообщества не подходят**:
  [NjengaFelix/opencode-agents](https://github.com/NjengaFelix/opencode-agents)
  (1 звезда, без LICENSE, про разработку),
  [osmontero/opencode-skills](https://github.com/osmontero/opencode-skills)
  (MIT, переупаковка anthropics/skills и superpowers),
  [joshuadavidthomas/opencode-agent-skills](https://github.com/joshuadavidthomas/opencode-agent-skills)
  (MIT, загрузчик навыков — OpenCode теперь умеет сам).
* **Не навык, но на брейншторм** («сперва искать готовое»):
  [Th0rgal/openagent (sandboxed.sh)](https://github.com/Th0rgal/openagent) —
  панель управления OpenCode с изолированными рабочими пространствами на
  пользователя; лицензия [?].

## Рекомендовано установить (не выполнено, решение владельца)

1. **Обновить superpowers до 6.4.2** — исправление writing-plans под Opus 5.5.
   `claude plugin marketplace update superpowers-marketplace && claude plugin update superpowers@superpowers-marketplace`;
   после — проверить `installed_plugins.json` (в манифесте маркетплейса 6.3.0,
   источник без закреплённой версии).
2. **claude-security** — проверка неуспеха 1 и 3, Н3, Н4.
   `claude plugin install claude-security@claude-plugins-official`; сканы
   тратят много токенов.
3. **Навык Playwright CLI в проект** —
   `npm install -g @playwright/cli@latest && playwright-cli install --skills`;
   версия 0.1.x, браузеры тяжёлые, на сервере гонять вне занятий.
4. **docker-skills в проект** —
   `claude plugin marketplace add docker/skills && claude plugin install docker-skills@docker -s project`;
   оставить 4 из 11: build-strategies, compose-patterns, destructive-guardrails,
   sandboxes-lifecycle.
5. **Три навыка addyosmani скопировать в проект** (не плагином — он ставит хуки
   и дублирует superpowers): observability-and-instrumentation,
   security-and-hardening, api-and-interface-design.

## Не нужно

* **frontend-design** — «смелый нешаблонный» визуал противоречит ISA-101 (серый
  — норма, цвет только для отклонения); разве что вход и чат.
* **security-guidance** — LLM-вызов на каждую правку, ставит Agent SDK через
  pip; claude-security покрывает то же разовыми сканами.
* **mcp-server-dev** — под коннекторы Claude; если MCP — лучше mcp-builder.
* playground, enterprise-architect (TOGAF — перебор для 10 пользователей),
  data:sql-queries (нет T-SQL и InSQL), engineering:testing-strategy (общие
  слова; TDD сильнее), vercel react-best-practices (под Next.js и Vercel),
  modern-web-guidance (шум), Playwright MCP (без закреплённой версии,
  прожорлив), mattpocock-skills (повторяет brainstorming).
* **superpowers внутри OpenCode студентов** — процесс для разработчиков,
  технологам помешает.
* **Trail of Bits skills** (CC-BY-SA-4.0) — про смарт-контракты, C и Rust.
