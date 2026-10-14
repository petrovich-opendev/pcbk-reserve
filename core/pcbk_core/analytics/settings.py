"""Настройки подсистемы аналитики КДМ-1 (Task 0; план §Task 0; мастер-промпт §4, §9, §10).

Отдельный класс — тот же приём, что `GatewaySettings`/`LlmSettings` в `core/pcbk_core/settings.py`: ошибка в
ANALYTICS_* не роняет `main()` и прочие роли, причину покажет `service.py` (Task 4) как `config_error`; процесс
живёт. Конструктор ничего не читает с диска — чтение корпуса и состояния делают `knowledge.py`/`store.py`.

`Settings.from_env` общего слоя (`core/pcbk_core/settings.py`) рассчитан на поля-строки и положительные числа
(тип поля по умолчанию решает разбор) — у него нет булевых полей, и бездумное добавление туда `ANALYTICS_ENABLED`
дало бы `bool("false") is True`. Здесь разбор полей — по отдельности, явным словарём проверок (приём
`GatewaySettings._parse`/`_GW_FIELD_CHECKS`), а не общим циклом по типу умолчания.

Политика передачи в LLM (`ANALYTICS_LLM_EXPORT_POLICY`) — умолчание `public_only` (мастер-промпт §9: «до
подтверждённой политики передачи закрытого содержания исходящий запрос содержит только публичный контекст»).
Значение здесь — только настройка; собирает исходящий пакет и действительно ограничивает его `llm_adapter.py`
(Task 5) — settings.py сам по сети и в LLM ничего не отправляет.

Итоговая правка F6 (решение владельца, 14.10.2026, дословно): «4. Разрешен полный контент в тч внутренний и
данные с БДРВ»; повторно, тем же днём: «Разрашею передачу полного контента в модель!» — владелец (заказчик и
администратор стенда ПЦБК) дважды явно разрешил `approved_subset` (полный контент пакета, включая внутренний
и данные БДРВ, если они есть в пакете, в исходящем запросе к модели); модель остаётся существующей LLM-ролью
приложения через OpenRouter — учёт, бюджет и журнал не меняются этой правкой. `approved_subset` из варианта,
прежде отклонявшегося явной ошибкой настройки (H-40), становится обычным допустимым значением, как
`public_only`/`none` — см. докстринг `LLM_EXPORT_POLICIES` ниже.
"""
from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields

# выключатель: допустимые записи без учёта регистра и краевых пробелов
_BOOL_TRUE = frozenset({"1", "true", "on", "yes"})
_BOOL_FALSE = frozenset({"0", "false", "off", "no"})

# политика передачи закрытого контекста в LLM (контракт `preanalysis.schema.json#llm_export_policy`,
# `models.LlmExportPolicy` — та модель НЕ правится этой правкой, она и раньше несла все три значения, это
# контракт с LLM-путём, не с настройкой): этой НАСТРОЙКОЙ сегодня включаемы ВСЕ ТРИ —
# public_only — только публичный контекст и абстрактное намерение (умолчание, мастер-промпт §9);
# none — разбор без LLM (справка), оплачиваемый путь недоступен (`service.py` — `capabilities.llm_available=
# False`, F4a/контроллер);
# approved_subset — РЕШЕНИЕ ВЛАДЕЛЬЦА (итоговая правка F6, 14.10.2026, дословно): «4. Разрешен полный контент
# в тч внутренний и данные с БДРВ»; повторно, тем же днём: «Разрашею передачу полного контента в модель!» —
# владелец (заказчик и администратор стенда ПЦБК) дважды явно разрешил полный контент, включая внутренний и
# данные с БДРВ, в исходящем запросе к модели. Этой настройкой включается подстановка полного содержания
# пакета преданализа (исходный вопрос технолога и тексты карточек пакета — `llm_adapter.build_raw_body`,
# Task 5/F6); сама настройка по сети и в LLM ничего не отправляет и не читает БДРВ — это делает (и
# действительно ограничивает) `llm_adapter.py`, `service.py` лишь подставляет значение этой настройки в
# `Preanalysis.llm_export_policy` перед вызовом адаптера (контракт `Preanalysis` допускал все три значения и
# раньше — это не правка контракта). Ответ модели по-прежнему ТОЛЬКО выбор id по `llm-selection.schema.json` —
# политика расширяет то, что модель ВИДИТ в запросе, не то, что она имеет право вернуть клиенту; сырой текст
# модели клиенту не выводится ни при одном значении (`llm_adapter.py`).
LLM_EXPORT_POLICIES = frozenset({"public_only", "approved_subset", "none"})
DEFAULT_LLM_EXPORT_POLICY = "public_only"

# минимальный срок одного задания: меньше — асинхронный путь (queued→…→completed) не успел бы ни разу опросить
# стадию до истечения (симметрично MIN_CATALOG_DEADLINE_S службы данных)
MIN_JOB_TIMEOUT_S = 5.0


def _parse_bool(name: str, raw: str) -> bool:
    """Строгий разбор выключателя: допустимо 1/true/on/yes или 0/false/off/no (без учёта регистра); иначе —
    `ValueError` с именем поля, без самого значения (текст идёт в config_error и на страницу состояния)."""
    low = raw.strip().lower()
    if low in _BOOL_TRUE:
        return True
    if low in _BOOL_FALSE:
        return False
    raise ValueError(f"{name}: допустимо 1/true/on/yes или 0/false/off/no")


def _parse_abs_path(name: str, raw: str) -> str:
    """Путь — непустая строка, начинающаяся с «/» (как LESSONS_ROOT/AGENTS_ROOT общего слоя); иначе ValueError."""
    if not raw.startswith("/"):
        raise ValueError(f"{name}: нужен абсолютный путь")
    return raw


def _parse_positive_float(name: str, raw: str, *, minimum: float = 0.0) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name}: не число") from None
    if not (math.isfinite(value) and value > 0 and value >= minimum):
        need = "больше нуля" if minimum <= 0 else f"не меньше {minimum:g}"
        raise ValueError(f"{name}: нужно число {need}")
    return value


def _parse_llm_models(raw: str) -> str:
    """ANALYTICS_LLM_MODELS: пусто — без ограничения сверх того, что уже выбрала активная роль LlmRole (её
    `LLM_MODELS`); иначе — подмножество имён кандидатов `llm/models.py` через запятую, проверенное тем же
    разбором, что `LlmSettings.LLM_MODELS` (`select_models`). Пересечение с реально доступными моделями роли —
    во время вызова (`llm_adapter.py`, Task 5): здесь модуль `llm` не знает о загруженном ключе и бюджете."""
    from ..llm.models import select_models

    if raw.strip() == "":
        return raw
    try:
        select_models(raw)
    except ValueError as exc:
        raise ValueError(f"ANALYTICS_LLM_MODELS: {str(exc).split(': ', 1)[-1]}") from None
    return raw


def _parse_llm_export_policy(raw: str) -> str:
    """Строгий разбор с нормализацией регистра/пробелов (приём — правка контроллера F4b, раунд 1, сохранён в
    F6): `strip().lower()` до сравнения — « Approved_Subset»/«APPROVED_SUBSET »/«approved_subset» узнаются как
    одно и то же допустимое значение (решение владельца 14.10.2026, см. докстринг `LLM_EXPORT_POLICIES` выше),
    настоящая опечатка (например, «aproved_subset», «public» без «_only») — явная `ValueError` со списком
    допустимых значений, не тихий откат на умолчание (откат на умолчание — дело `AnalyticsSettings.
    valid_from_env`, не этой функции)."""
    normalized = raw.strip().lower()
    if normalized not in LLM_EXPORT_POLICIES:
        raise ValueError("ANALYTICS_LLM_EXPORT_POLICY: допустимо " +
                         " или ".join(sorted(LLM_EXPORT_POLICIES)))
    return normalized


ANALYTICS_REVIEWERS_MODES = frozenset({"grants", "all_students"})
DEFAULT_ANALYTICS_REVIEWERS = "grants"


def _parse_reviewers_mode(raw: str) -> str:
    """ANALYTICS_REVIEWERS (решение владельца, 14.10.2026, F5-brief): `grants` (умолчание — прежнее поведение,
    право только по явному назначению) или `all_students` (любой вошедший `student` решает любую заявку
    области КДМ-1 без назначения, в одно действие — `reviews.py` несёт саму политику). Строгий разбор —
    опечатка («All_Students», «studentS» и т.п.) — явная ошибка настройки, не тихое падение в `grants`
    (приём прочих полей этого модуля: `_parse_llm_export_policy`/`_parse_bool`)."""
    if raw not in ANALYTICS_REVIEWERS_MODES:
        raise ValueError("ANALYTICS_REVIEWERS: допустимо " + " или ".join(sorted(ANALYTICS_REVIEWERS_MODES)))
    return raw


def _parse_teacher_payer(raw: str) -> str:
    """ANALYTICS_TEACHER_PAYER (Task 5, мастер-промпт §6): произвольная непустая строка — владелец явно
    подтвердил, что у преподавателя есть платящий за него разбор (какой именно `caller`/бюджет это отражает —
    решает не эта настройка, а `identity.caller`, уже зафиксированный `gateway/analytics_api.py::TEACHER_
    CALLER`: эта строка — только флаг «настроено», не имя вызывающего). Пустая строка (умолчание) — платный
    путь для преподавателя остаётся недоступен (`llm_adapter.request_selection`, `teacher_payer=""`): «без
    явно настроенного плательщика преподавателя платный разбор недоступен, а справка остаётся»."""
    return raw.strip()


# проверка каждого поля по отдельности (приём GatewaySettings._parse): значение из env → значение поля или
# ValueError; поле без записи — обычная непустая строка (пока не нужно строже)
_FIELD_CHECKS: dict[str, Callable[[str], object]] = {
    "ANALYTICS_ENABLED": lambda raw: _parse_bool("ANALYTICS_ENABLED", raw),
    "ANALYTICS_CORPUS_DIR": lambda raw: _parse_abs_path("ANALYTICS_CORPUS_DIR", raw),
    "ANALYTICS_STATE_PATH": lambda raw: _parse_abs_path("ANALYTICS_STATE_PATH", raw),
    "ANALYTICS_LLM_MODELS": _parse_llm_models,
    "ANALYTICS_LLM_EXPORT_POLICY": _parse_llm_export_policy,
    "ANALYTICS_REVIEWERS": _parse_reviewers_mode,
    "ANALYTICS_TEACHER_PAYER": _parse_teacher_payer,
    "ANALYTICS_HISTORY_RETENTION_DAYS": lambda raw: _parse_positive_float(
        "ANALYTICS_HISTORY_RETENTION_DAYS", raw, minimum=1.0),
    "ANALYTICS_EVIDENCE_RETENTION_DAYS": lambda raw: _parse_positive_float(
        "ANALYTICS_EVIDENCE_RETENTION_DAYS", raw, minimum=1.0),
    "ANALYTICS_JOB_TIMEOUT_S": lambda raw: _parse_positive_float(
        "ANALYTICS_JOB_TIMEOUT_S", raw, minimum=MIN_JOB_TIMEOUT_S),
}


@dataclass(frozen=True)
class AnalyticsSettings:
    """Настройки аналитики КДМ-1 (мастер-промпт §4): все поля — несекретные, секретов у этой подсистемы нет
    (ключ LLM и токены вызывающих остаются у ролей llm/gateway, Task 5 их не копирует).

    ANALYTICS_ENABLED — выключатель (Global Constraints): по умолчанию выключена — кусок кода не трогает
    старый вход/чат/калькуляторы/уроки, пока владелец явно не включит. ANALYTICS_CORPUS_DIR — каталог с
    версиями корпуса, смонтированный хосту только на чтение (как `LESSONS_ROOT`; мастер-промпт §3: `/srv/analytics`
    тома). ANALYTICS_STATE_PATH — файл состояния диалогов/заданий аналитики, отдельный от `core.db`
    (мастер-промпт §4: «отдельный файл состояния аналитики в прежнем томе»). ANALYTICS_LLM_MODELS — пусто
    (умолчание) или подмножество имён кандидатов `llm/models.py`, которым разрешено отвечать на аналитические
    задания (дополнительное ограничение сверх `LlmSettings.LLM_MODELS` активной роли, не замена ему).
    ANALYTICS_LLM_EXPORT_POLICY — что уходит в исходящий запрос LLM (§9); умолчание `public_only`;
    `approved_subset` — решение владельца (F6, 14.10.2026, дословно: «4. Разрешен полный контент в тч
    внутренний и данные с БДРВ»; повторно: «Разрашею передачу полного контента в модель!») — в запрос к модели
    уходит полный контент пакета (исходный вопрос и тексты карточек, включая внутренние и данные БДРВ, если
    они есть в пакете); на сервере ПЦБК включается этим же решением владельца (`deploy/env.example`). `none` —
    модель не вызывается вовсе.
    ANALYTICS_REVIEWERS — кто решает заявки согласования (`reviews.py`, решение владельца 14.10.2026):
    `grants` (умолчание) — право только по явному назначению (`ReviewStore.grant_authority`/
    `bootstrap_authority`), как было Task 7; `all_students` — любой вошедший `student` (не `teacher`) решает
    любую заявку области КДМ-1 без назначения, в одно действие (без второго согласующего, без запрета автору)
    — вся политика у `reviews.py` (`decide`/`list_requests`/`approved_claim`/`resolved_link`, параметр
    `reviewers_mode`), эта настройка — только переключатель, сам по сети/в БД ничего не решает.
    ANALYTICS_TEACHER_PAYER — пусто (умолчание) — платный разбор для преподавателя недоступен (мастер-промпт
    §6: без явно настроенного плательщика не списывать скрыто на технолога/`ops`); непустая строка — владелец
    подтвердил, что платящий настроен (`llm_adapter.py`, Task 5 — сам `caller` для журнала не отсюда, его уже
    фиксирует `gateway/analytics_api.py::TEACHER_CALLER`). `service.py` (`AnalyticsService._select_with_llm`,
    хвост Task 5, коммит `830a782`) передаёт это значение в `llm_adapter.request_selection` как есть; умолчание
    `""` держит путь отказанным — безопасное поведение при отсутствии настройки.
    ANALYTICS_HISTORY_RETENTION_DAYS/ANALYTICS_EVIDENCE_RETENTION_DAYS — сроки хранения диалогов/сообщений и
    неизменяемых пакетов доказательств (§4: «история, экспорты и резервные копии имеют права и срок хранения»);
    раздельные сроки — пакет доказательств должен переживать ответ, на который он опирается, дольше истории
    чата, если владелец захочет короче чистить переписку. ANALYTICS_JOB_TIMEOUT_S — срок одной стадии задания
    (`queued → … → completed`) до `failed` по таймауту (§10: «ограничить число заданий, историю, пакет, сроки»).
    """
    ANALYTICS_ENABLED: bool = False
    ANALYTICS_CORPUS_DIR: str = "/srv/analytics"
    ANALYTICS_STATE_PATH: str = "/var/lib/pcbk-core/analytics.db"
    ANALYTICS_LLM_MODELS: str = ""
    ANALYTICS_LLM_EXPORT_POLICY: str = DEFAULT_LLM_EXPORT_POLICY
    ANALYTICS_REVIEWERS: str = DEFAULT_ANALYTICS_REVIEWERS
    ANALYTICS_TEACHER_PAYER: str = ""
    ANALYTICS_HISTORY_RETENTION_DAYS: float = 365.0
    ANALYTICS_EVIDENCE_RETENTION_DAYS: float = 365.0
    ANALYTICS_JOB_TIMEOUT_S: float = 120.0

    @classmethod
    def _parse(cls, env: Mapping[str, str] | None) -> tuple[dict[str, object], list[str]]:
        """Поля, прошедшие проверку по отдельности, и тексты ошибок остальных (по порядку полей). Пустое
        значение — умолчание. Приём — `GatewaySettings._parse`: опечатка в одном поле не сбрасывает прочие."""
        env = os.environ if env is None else env
        values: dict[str, object] = {}
        errors: list[str] = []
        for f in fields(cls):
            raw = env.get(f.name, "")
            if raw == "":
                continue
            try:
                values[f.name] = _FIELD_CHECKS[f.name](raw)
            except ValueError as exc:
                errors.append(str(exc))
        return values, errors

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AnalyticsSettings":
        """Пустое значение — умолчание (аналитика выключена); ошибка — `ValueError` с именем поля, без
        значения. env — только он, иначе `os.environ`."""
        values, errors = cls._parse(env)
        if errors:
            raise ValueError(errors[0])
        return cls(**values)

    @classmethod
    def valid_from_env(cls, env: Mapping[str, str] | None = None) -> "AnalyticsSettings":
        """Запасная настройка при ошибке в ANALYTICS_* (приём `GatewaySettings.valid_from_env`): поля,
        прошедшие проверку по отдельности, — из env, прочие — умолчания, и среди них ANALYTICS_ENABLED —
        умолчание `False`, если само оно не читается: опечатка в ANALYTICS_LLM_MODELS не включает аналитику
        молча и не прячет её отдельно от прочих ошибок настройки (закрыто при сомнении, как `GW_ORIGIN`)."""
        return cls(**cls._parse(env)[0])
