"""Назначаемое согласование (Task 7; план §Task 7/Этап 7; мастер-промпт §6).

Роль `teacher` сама по себе не даёт полномочия утверждать технологические нормы (мастер-промпт §6: «роль
`teacher` не является автоматически полномочием утверждать технологические нормы»). Этот модуль хранит явно
назначенные полномочия согласования (кто, на какую область КДМ-1 и тип решения, кем назначено и когда) и сами
заявки/решения — отдельно от `store.py` (который владеет диалогами/заданиями), но на том же файле
`ANALYTICS_STATE_PATH` (приём `gateway.teacher_log.TeacherLog` рядом с `gateway.accounts.AccountStore` на одном
`core.db`): открывается в lifespan своим вызовом, не через `AnalyticsStore`.

Согласуются: спорные местные сокращения/термины, связи оборудования с историаном, местная применимость,
нормативы и открытые конфликты корпуса (`conflicts.jsonl`). Полномочие может получить реальная учётная запись
технолога без смены её роли (мастер-промпт §6) — соответственно, субъект полномочия — логин, не роль и не
рабочее место. Самовольное назначение (пользователь назначает себе полномочие) — запрещено отдельной проверкой
`grant_authority`, не только отсутствием ручки в API.

Кто может назначать (Task 7 фиксирует явно, план «не оставляет открытым») — ДВА РАЗНЫХ МЕТОДА, не один с
исключением для пустого хранилища (правка раунд 1, 2026-10-14: держать первое назначение только отсутствием
веб-маршрута к `grant_authority` было недостаточно — само API стора должно отказывать и при случайном вызове):
- `grant_authority(login, scope, decision_types, *, granted_by: RequestIdentity)` — требует у `granted_by`
  действующего полномочия (любой `decision_type`) в ТОЙ ЖЕ `scope`, БЕЗ ИСКЛЮЧЕНИЯ для пустого хранилища: на
  пустой базе `PermissionError` получит и обычная `RequestIdentity`, и даже операторская личность CLI — у
  всех активных полномочий ещё попросту нет ни у кого;
- `bootstrap_authority(login, scope, decision_types, *, operator: str)` — единственный путь завести самый
  первый активный ряд `review_authority_grants` (хранилище обязано быть полностью пустым, включая отозванные
  строки — иначе отзыв всех полномочий открывал бы повторный бутстрап); берёт не `RequestIdentity`, а голый
  `operator: str` — такую сигнатуру `gateway`/`service.py` не вызывают (у них есть только `RequestIdentity` из
  сессии). Единственный вызывающий обоих методов — офлайн-команда ниже (`python -m pcbk_core.analytics.reviews
  bootstrap|grant`, запуск внутри контейнера core оператором), не любой залогиненный `teacher`; «самоназначение
  через API запрещено» и веб-ручки такого пути не открывают, но теперь это не единственная граница.

Отзыв права или уже выданного решения сразу запрещает новое применение (мастер-промпт §6: «отзыв сразу
запрещает новое применение»); старые ответы, построенные на отозванном решении, остаются историческими с
предупреждением — их не трогает `reviews.py` (это разметка `store.py`/`rendering.py` при показе истории, не
переписывание сохранённого `JobResponse`). Механизм отзыва РЕШЕНИЯ (не только полномочия) — не отдельный метод
(интерфейс `docs/kdm1/INTERFACES.md` его не заводит: решённая заявка не решается повторно, «нет
переголосования без отдельного отзыва»), а повторная проверка полномочия решившего в момент каждого обращения
к `resolved_link`/`approved_claim`: `revoke_authority` того, кто принял решение, сразу обесценивает все решения,
принятые по этому полномочию (`scope`/`decision_type`), не трогая сами строки `review_requests` (историческая
запись цела, решение остаётся «accepted» в журнале) — тот же приём, что `validator.py` использует для
`publication_state` («повторная проверка в момент выдачи», не доверие прежней отметке).

Связь оборудования с историаном (`equipment_links.jsonl`, 67 неподтверждённых подписей — мастер-промпт §2)
решается здесь (`ResolvedLink`), но саму проверку доступа к тегу всегда проводит прежний `DataService`
(`data_bridge.py`) — согласование не создаёт нового разрешённого списка и не меняет `historian_tag=null` самого
корпуса (это запись поверх, не правка `kdm1.seed.sqlite3`). `ReviewRequest` не несёт отдельных полей
`process_id`/`unit` (их нет в зафиксированном `docs/kdm1/INTERFACES.md`): для заявок `kind="equipment_link"`
`scope` несёт то же значение, что станет `ResolvedLink.process_id` (область заявки — участок/процесс, мастер-
промпт: «scope — KDM1_PCBK или её подмножество — участок/процесс»), `new_value` — предлагаемое имя тега
историана, которое станет `ResolvedLink.historian_tag`; `unit` интерфейс не даёт собрать иначе, чем `None` —
открытый пункт для `docs/kdm1/INTERFACES.md`, если `service.py`/`data_bridge.py` окажется, что единица нужна
именно отсюда (её и так возвращает сам `DataService` по каталогу тега).

**Правка F3 (итоговое ревью, 2026-10-14)** — три дополнения, все проверяются «здесь и сейчас» при каждом
обращении, тем же приёмом, что уже применён к `revoke_authority` (не по факту решения в прошлом):

1. **Срок полномочия.** `AuthorityGrant.valid_until` (UTC, `None` — без ограничения) — опциональный параметр
   `grant_authority`/`bootstrap_authority` и CLI (`--until ГГГГ-ММ-ДД[ЧЧ:ММ]`). «Действующее полномочие» —
   не отозвано И (`valid_until` пусто ИЛИ момент проверки строго раньше `valid_until`); этот момент — везде
   «реальное сейчас операции» (`decide`/`grant_authority` берут уже вычисленный `now`, `active_grant`/
   `resolved_link`/`has_any_active_grant`/`has_any_resolved_link` вычисляют свой), кроме `approved_claim`
   (см. её докстринг — решение контроллера: там момент проверки — тот же `at`, что и для `period_start/end`
   самой карточки, не отдельное «реальное сейчас», ради одной непротиворечивой точки времени на весь вызов).
   Истечение без отзыва отключает право ТАК ЖЕ, как отзыв: `decide`/`active_grant` видят такого держателя как
   постороннего, `resolved_link`/`approved_claim` — `None`; исторические решения, принятые до истечения, в
   `review_requests` не трогаются (та же развязка «применение» vs «факт в прошлом», что у отзыва). Миграция
   схемы — столбец `valid_until` добавлен и в `_SCHEMA` (новая база), и через `ALTER TABLE ... ADD COLUMN` в
   `open()` для базы без него (приём `store.AnalyticsStore._migrate_legacy_v1`): старые строки получают `NULL`
   — «без ограничения срока», то же поведение, что было до этой правки, без потери данных.
2. **`info_requested` не терминален.** Только `accepted`/`rejected` закрывают заявку — `decide()` на статусе
   `info_requested` (как и `pending`) решает заново, в т.ч. повторным `info_requested`. Каждое решение —
   отдельная строка новой таблицы `review_decisions` (id, request_id, decision, decided_by, decided_at,
   comment): `review_requests.status/decided_by/decided_at/comment` остаются «текущим» снимком (как раньше),
   а `review_decisions` — неурезаемый журнал всех событий решения с автором и временем («статусы и версии
   сохраняются», мастер-промпт §6). Строка журнала пишется при КАЖДОМ успешном `decide()`, независимо от
   `kind`/`decision`.
3. **Второй независимый согласующий для `local_claim`/`local_norm`.** `accepted` по заявке этих видов не
   может вынести логин, который уже вынес по ЭТОЙ ЖЕ заявке хоть одно решение ранее (видно по
   `review_decisions.decided_by` для этого `request_id`) — `PermissionError("нужен второй независимый
   согласующий: …")`, через HTTP (`gateway/analytics_api.py`, без изменений — `except PermissionError`
   уже отображает в 403) — та же категория отказа, что самозапрет автора, другая причина. Для `conflict`/
   `equipment_link` — решение контроллера: одного правообладателя достаточно (эксплуатационные/аппаратные
   решения не требуют независимого подтверждения второй учёткой, в отличие от локальных норм/фактов корпуса,
   которые меняют то, что видит LLM как «источник истины») — тот же логин, что оставил `info_requested`,
   может потом сам вынести `accepted`/`rejected` по этим двум видам.

**Правка F5 (итоговое ревью, 14.10.2026, решение владельца по `final-fix-F5-brief.md`)** — режим
`ANALYTICS_REVIEWERS=all_students`: «любому студенту без утверждения со стороны преподавателя — это
технологи, они сами всё знают» и «любой утверждает без дополнительных утверждений». Параметр
`reviewers_mode: ReviewersMode` (умолчание `"grants"` — прежнее поведение без исключений) у `decide`/
`list_requests`/`approved_claim`/`resolved_link`:

1. **`decide`.** `decided_by.role == "student"` И `reviewers_mode == "all_students"` — держатель решает ЛЮБУЮ
   заявку (любой `kind`/`scope`, не только там, где есть явное полномочие) в ОДНО действие: видимость не
   проверяется (видна всегда), самозапрет автора (п. «автор заявки не решает сам») не действует, второй
   независимый согласующий (F3, `local_claim`/`local_norm`) не требуется. `teacher` — ИСКЛЮЧЕНИЕ из этого
   режима, не «любой вошедший»: решает только по действующему полномочию, как раньше (мастер-промпт §6:
   «роль `teacher` сама по себе не даёт полномочия» — это решение владельца не отменяет, отменяет только
   требование назначения для `student`). Терминальный статус (`accepted`/`rejected` уже решённой заявки) —
   ValueError остаётся: «нет переголосования» не входит в список снятых правил.
2. **`list_requests`.** `viewer.role == "student"` И `reviewers_mode == "all_students"` — видит ВСЕ заявки
   области КДМ-1 (не только свои и не только там, где есть полномочие); `teacher` — видимость не меняется
   (свои + где есть полномочие, как раньше).
3. **`approved_claim`/`resolved_link`.** Действующим (для НОВОГО применения, «здесь и сейчас», тот же приём,
   что у `revoke_authority`/`valid_until`) считается решение, принятое студентом БЕЗ реального полномочия, —
   ТОЛЬКО пока `reviewers_mode == "all_students"` в момент ЭТОЙ проверки, не в момент решения: новый столбец
   `review_requests.decided_by_role` (кем по роли принято решение — `decided_by.role` на момент `decide()`,
   не перечитывается из учётки) позволяет узнать, что первоначально решение прошло по обходному пути студента,
   без привязки к его сегодняшнему логину/полномочию. Переключение `all_students` → `grants` НЕ отзывает сами
   заявки (журнал цел, `status` остаётся `accepted`) — но `approved_claim`/`resolved_link` для решений с
   `decided_by_role == "student"` и БЕЗ действующего полномочия перестают находить их действующими, тем же
   образом, что отзыв полномочия/истечение `valid_until` обесценивают применение, не историю (докстринги
   `resolved_link`/`approved_claim` выше). Решения `teacher` (всегда по действующему на момент решения
   полномочию) этим путём не затрагиваются — для них как раньше перепроверяется текущий `active_grant`.
   `decided_by_role` — миграция `ALTER TABLE ... ADD COLUMN` (как `valid_until`, F3): старые строки — `NULL`,
   трактуются как «не студент по обходному пути» (обычная перепроверка полномочия, прежнее поведение)."""
from __future__ import annotations

import re
import secrets
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .settings import AnalyticsSettings

if TYPE_CHECKING:
    from .models import RequestIdentity

ReviewKind = Literal["local_claim", "equipment_link", "local_norm", "conflict"]
ReviewDecision = Literal["accepted", "rejected", "info_requested"]
ReviewStatus = Literal["pending", "accepted", "rejected", "info_requested"]
# F5 (решение владельца, 14.10.2026): "grants" — умолчание, прежнее поведение (право только по назначению);
# "all_students" — любой вошедший student решает любую заявку области КДМ-1 без назначения, в одно действие.
# Настройка (`settings.AnalyticsSettings.ANALYTICS_REVIEWERS`) живёт у вызывающего (`service.py`) — этот модуль
# не читает env сам для этого параметра, получает его явно (см. `decide`/`list_requests`/`approved_claim`/
# `resolved_link` ниже), умолчание `"grants"` у каждого — не меняет поведение существующих вызывающих/тестов.
ReviewersMode = Literal["grants", "all_students"]

_REVIEW_KINDS: tuple[str, ...] = ("local_claim", "equipment_link", "local_norm", "conflict")
_REVIEWER_MODES: tuple[str, ...] = ("grants", "all_students")
_REVIEW_DECISIONS: tuple[str, ...] = ("accepted", "rejected", "info_requested")
# виды, что admitted в approved_claim (Task 3b, дополнение контроллера к Task 7, 2026-10-14): карточка/конфликт
# корпуса, не связь оборудования (та — resolved_link, отдельный путь данных, не публикационный статус карточки)
_CLAIM_APPROVAL_KINDS: tuple[str, ...] = ("local_claim", "local_norm", "conflict")

SQLITE_TIMEOUT_S = 2.0                  # как AnalyticsStore/AccountStore (Г14)
_SQLITE_CODE = re.compile(r"SQLITE_[A-Z_]{1,40}")

_SCHEMA: tuple[str, ...] = (
    # нет внешнего ключа на строку полномочия: отзыв полномочия не должен быть заблокирован наличием решений,
    # принятых по нему, и решения не должны исчезать вместе с отзывом (история целы, см. докстринг модуля).
    # valid_until (F3, 2026-10-14) — срок полномочия, NULL = без ограничения; у БАЗЫ БЕЗ этого столбца его
    # добавляет _migrate_valid_until в open() (ALTER TABLE ... ADD COLUMN, без потери данных)
    """CREATE TABLE IF NOT EXISTS review_authority_grants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        login TEXT NOT NULL,
        scope TEXT NOT NULL,
        decision_types TEXT NOT NULL,
        granted_by TEXT NOT NULL,
        granted_at TEXT NOT NULL,
        revoked_at TEXT,
        valid_until TEXT
    )""",
    ("CREATE INDEX IF NOT EXISTS review_authority_grants_login_scope ON review_authority_grants"
     " (login, scope, revoked_at)"),
    # decided_by_role (F5, 2026-10-14) — роль решившего НА МОМЕНТ decide() ('student'/'teacher'), не
    # перечитывается из учётки позже: approved_claim/resolved_link используют её, чтобы узнать, что решение
    # прошло обходным путём ANALYTICS_REVIEWERS=all_students (без реального полномочия), и держать его
    # действующим только пока этот режим включён — см. докстринг модуля, раздел «Правка F5». У БАЗЫ БЕЗ этого
    # столбца его добавляет _migrate_decided_by_role в open() (тот же приём, что _migrate_valid_until)
    """CREATE TABLE IF NOT EXISTS review_requests (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('local_claim', 'equipment_link', 'local_norm', 'conflict')),
        subject_id TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT NOT NULL,
        scope TEXT NOT NULL,
        period_start TEXT,
        period_end TEXT,
        rationale TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'rejected', 'info_requested')),
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        decided_by TEXT,
        decided_at TEXT,
        comment TEXT,
        decided_by_role TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS review_requests_subject ON review_requests (kind, subject_id, status)",
    "CREATE INDEX IF NOT EXISTS review_requests_created_by ON review_requests (created_by)",
    # журнал решений (F3, 2026-10-14): КАЖДОЕ успешное decide() — отдельная строка, не только последний снимок
    # в review_requests (мастер-промпт §6: «статусы и версии сохраняются») — нужен и для истории, и для
    # проверки «второй независимый согласующий» (кто уже решал по этой заявке). Без внешнего ключа на
    # review_requests по той же причине, что у review_authority_grants выше — своя строка истории не зависит
    # от того, что станет с самой заявкой дальше.
    """CREATE TABLE IF NOT EXISTS review_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL,
        decision TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected', 'info_requested')),
        decided_by TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        comment TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS review_decisions_request ON review_decisions (request_id)",
)
_REQUEST_COLUMNS = ("id, kind, subject_id, old_value, new_value, scope, period_start, period_end, rationale,"
                   " status, created_by, created_at, decided_by, decided_at, comment, decided_by_role")


def _why(exc: BaseException) -> str:
    """Класс ошибки и код SQLite, если он есть — без путей и значений (приём store._why/accounts._why)."""
    code = getattr(exc, "sqlite_errorname", None)
    code = code if isinstance(code, str) and _SQLITE_CODE.fullmatch(code) else None
    return type(exc).__name__ + (f" {code}" if code else "")


def _iso(dt: datetime) -> str:
    """ISO8601 UTC с фиксированной шириной микросекунд — строки сравнимы лексикографически как время
    (приём store._iso: срок/период сверяются прямо строками, без повторного разбора)."""
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text)


_UNTIL_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}):(\d{2}))?$")


def _parse_until(text: str) -> datetime:
    """`--until ГГГГ-ММ-ДД[ЧЧ:ММ]` (F3, CLI) → `datetime` UTC. Без времени — начало суток (00:00) той даты,
    как чтение ISO-даты без компонента времени (та же трактовка, что `date.fromisoformat` даёт сама дата без
    времени, не конец суток) — оператор, которому нужно «по конец дня», указывает `--until ГГГГ-ММ-ДД 23:59`
    явно, не угадывается здесь. `_UsageError` — формат не разобран (приём остальных проверок CLI-аргументов
    этого модуля, не `argparse`)."""
    m = _UNTIL_RE.match(text.strip())
    if m is None:
        raise _UsageError(f"--until: ожидался формат ГГГГ-ММ-ДД[ЧЧ:ММ], получено {text!r}")
    date_part, hour, minute = m.groups()
    try:
        return datetime.fromisoformat(f"{date_part}T{hour or '00'}:{minute or '00'}:00+00:00")
    except ValueError:
        raise _UsageError(f"--until: недействительная дата/время {text!r}") from None


class ReviewStoreError(Exception):
    """Отказ по правилам согласования (приём `store.StoreError`): «заявка не найдена»/«нет видимости» — обычный
    `raise` внутри транзакции `_op`, не сбой инфраструктуры (см. `ReviewStoreUnavailable` ниже, подкласс для
    настоящего сбоя SQLite). `decide()` бросает этот класс напрямую и для несуществующего `request_id`, и для
    «посторонний без видимости» — тот же класс/текст на оба случая, не раскрывает разницу (докстринг `decide`)."""


class ReviewStoreUnavailable(ReviewStoreError):
    """F5 (итоговое ревью, 14.10.2026, п.6 `final-fix-F5-brief.md`): хранилище согласования само недоступно —
    нет места/только чтение/заблокировано/файл повреждён/соединение не открылось (приём `store.StoreUnavailable`
    — тот же приём, другой стор). Отличимо от плоского `ReviewStoreError` выше, который `decide()` бросает
    НАПРЯМУЮ по правилам («заявка не найдена»/«нет видимости») — то решение по правилам, не сбой
    инфраструктуры, и этим подклассом НЕ оборачивается. Единственный источник — `_store_error` ниже, вызываемый
    ровно там, где `_op` перехватывает настоящий `sqlite3.Error`.

    Вызывающий (`gateway/analytics_api.py`) отображает именно этот подкласс в 503 «временно недоступна» на
    маршрутах `reviews/*` — НЕ 404 (как плоский `ReviewStoreError`, «заявка не найдена») и не необработанный
    500: сбой самого SQLite — временная недоступность хранилища, не решение по правилам и не диагноз
    настройки (`AnalyticsService.reviews_error`, который отвечает ДРУГИМ 503 — подсистема согласования не
    открылась вовсе при старте, см. `gateway/analytics_api.py::_reviews_broken`)."""


def _store_error(exc: BaseException) -> ReviewStoreUnavailable:
    """Сбой самого SQLite, долетевший до `_op`/`open` — см. `ReviewStoreUnavailable`: единственный вызывающий
    этой функции — `except sqlite3.Error` в `_op` (ниже), так что результат — всегда `ReviewStoreUnavailable`,
    никогда не плоский логический `ReviewStoreError`."""
    return ReviewStoreUnavailable(f"аналитика КДМ-1 (согласование): {_why(exc)}")


@dataclass(frozen=True)
class AuthorityGrant:
    """Явное полномочие согласования: `login` — субъект (учётка технолога, не роль/место), `scope` — область
    (`KDM1_PCBK` или её подмножество — участок/процесс), `decision_types` — какие `ReviewKind` разрешено решать,
    `granted_by`/`granted_at` — кем и когда назначено, `revoked_at` — `None`, пока действует.

    `valid_until` (F3, 2026-10-14) — срок полномочия, `None` — без ограничения. «Действующее» (определение
    везде в модуле) — `revoked_at is None` И (`valid_until is None` ИЛИ момент проверки строго раньше
    `valid_until`); истечение без отзыва отключает право так же, как отзыв (докстринг модуля, разделы
    `_active_decision_types`/`decide`)."""

    login: str
    scope: str
    decision_types: tuple[ReviewKind, ...]
    granted_by: str
    granted_at: datetime
    revoked_at: datetime | None = None
    valid_until: datetime | None = None


@dataclass(frozen=True)
class ReviewRequest:
    """Заявка: основания, старое/новое значение, область/срок, статус и решение (мастер-промпт §6)."""

    id: str
    kind: ReviewKind
    subject_id: str                 # id термина/карточки/связи/конфликта корпуса, на который заявка
    old_value: str | None
    new_value: str
    scope: str
    period_start: datetime | None
    period_end: datetime | None
    rationale: str
    status: ReviewStatus
    created_by: str
    created_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    comment: str | None = None
    decided_by_role: str | None = None   # F5: роль решившего на момент decide() — см. докстринг модуля


@dataclass(frozen=True)
class ResolvedLink:
    """Согласованная связь оборудования КДМ-1 с историаном (мастер-промпт §7, этап 7): продукт принятой заявки
    `kind="equipment_link"`, не поле корпуса (`equipment_links.jsonl.historian_tag` остаётся `null`). Подпись
    PNG/документа (`display_label`) её не устанавливает сама по себе — только решение с действующей `scope`/
    сроком. `data_bridge.py` подставляет `historian_tag` в вызов прежнего `DataService` и не читает его из
    корпуса напрямую."""

    link_id: str
    process_id: str
    historian_tag: str
    unit: str | None
    approved_by: str
    approved_at: datetime
    scope: str


def _row_to_request(row: tuple) -> ReviewRequest:
    (id_, kind, subject_id, old_value, new_value, scope, period_start, period_end, rationale, status, created_by,
     created_at, decided_by, decided_at, comment, decided_by_role) = row
    return ReviewRequest(
        id=id_, kind=kind, subject_id=subject_id, old_value=old_value, new_value=new_value, scope=scope,
        period_start=None if period_start is None else _parse_iso(period_start),
        period_end=None if period_end is None else _parse_iso(period_end), rationale=rationale, status=status,
        created_by=created_by, created_at=_parse_iso(created_at), decided_by=decided_by,
        decided_at=None if decided_at is None else _parse_iso(decided_at), comment=comment,
        decided_by_role=decided_by_role)


def _decision_types_of(raw: str) -> tuple[str, ...]:
    return tuple(t for t in raw.split(",") if t)


def _check_login_scope(login: str, scope: str) -> None:
    if not isinstance(login, str) or not login:
        raise ValueError("логин: нужна непустая строка")
    if not isinstance(scope, str) or not scope:
        raise ValueError("область: нужна непустая строка")


def _normalized_decision_types(decision_types: "tuple[ReviewKind, ...]") -> tuple[str, ...]:
    """Без повторов, отсортированный, проверенный список видов решения — общая проверка
    `grant_authority`/`bootstrap_authority` (тот же порядок, что хранение/`active_grant` восстановит из
    `decision_types TEXT`)."""
    types = tuple(sorted(set(decision_types)))
    if not types:
        raise ValueError("decision_types: нужен хотя бы один вид решения")
    for t in types:
        if t not in _REVIEW_KINDS:
            raise ValueError(f"decision_types: неизвестный вид {t!r}")
    return types


class ReviewStore:
    """Полномочия и заявки на `ANALYTICS_STATE_PATH` (тот же файл, что `store.AnalyticsStore`, отдельные
    таблицы). Конструктор только запоминает путь (Договор Role)."""

    def __init__(self, db_path: str):
        self.path = db_path
        self.last_error: str | None = None
        self.read_failures = 0
        self.write_failures = 0

    def _connect(self, create: bool = False) -> sqlite3.Connection:
        uri = f"{Path(self.path).absolute().as_uri()}?mode={'rwc' if create else 'rw'}"
        return sqlite3.connect(uri, uri=True, timeout=SQLITE_TIMEOUT_S, isolation_level=None)

    @contextmanager
    def _op(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        """Соединение настоящей операции (приём `store.AnalyticsStore._op`): сбой SQLite — `ReviewStoreError`,
        счётчик read/write_failures растёт; отказ по правилам (`ValueError`/`PermissionError`/
        `ReviewStoreError`, брошенный кодом внутри `with`) счётчики не трогает и идёт как есть — не
        перехватывается здесь, незавершённая транзакция откатывается закрытием соединения."""
        wrote = False
        try:
            db = self._connect()
            try:
                yield db
                wrote = db.total_changes > 0 and not db.in_transaction
            finally:
                db.close()
        except sqlite3.Error as exc:
            if write:
                self.write_failures += 1
            else:
                self.read_failures += 1
            self.last_error = _why(exc)
            raise _store_error(exc) from None
        self.read_failures = 0
        if wrote:
            self.write_failures = 0

    def open(self) -> bool:
        """`CREATE TABLE IF NOT EXISTS` таблиц модуля плюс миграция столбца `valid_until` (F3), одной
        транзакцией; идемпотентна, как `AnalyticsStore.open`. `False` — сбой, причина в `self.last_error`."""
        try:
            db = self._connect(create=True)
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("BEGIN IMMEDIATE")
                for statement in _SCHEMA:
                    db.execute(statement)
                self._migrate_valid_until(db)
                self._migrate_decided_by_role(db)
                db.execute("COMMIT")
            finally:
                db.close()
        except Exception as exc:
            self.last_error = _why(exc)
            return False
        self.last_error = None
        return True

    @staticmethod
    def _migrate_valid_until(db: sqlite3.Connection) -> None:
        """База, заведённая до F3 (`review_authority_grants` уже существует без `valid_until`) — добавить
        столбец `ALTER TABLE ... ADD COLUMN` (SQLite умеет это без потери данных, в отличие от смены CHECK —
        приём `store.AnalyticsStore._migrate_legacy_v1`); свежая база уже получает столбец через `_SCHEMA`
        выше — `ALTER` тогда не выполняется (проверка через `PRAGMA table_info`, не версия схемы отдельной
        таблицей — здесь единственное дополнение, версию по одной колонке усложнять не нужно). Старые строки
        получают `valid_until = NULL` — «без ограничения срока», то же поведение, что было до этой правки."""
        cols = {row[1] for row in db.execute("PRAGMA table_info(review_authority_grants)")}
        if "valid_until" not in cols:
            db.execute("ALTER TABLE review_authority_grants ADD COLUMN valid_until TEXT")

    @staticmethod
    def _migrate_decided_by_role(db: sqlite3.Connection) -> None:
        """База, заведённая до F5 (`review_requests` уже существует без `decided_by_role`) — добавить столбец
        тем же приёмом, что `_migrate_valid_until`. Старые строки получают `NULL` — трактуется как «неизвестно/
        не студент по обходному пути all_students» (обычная перепроверка `active_grant`, прежнее поведение,
        см. докстринг модуля, раздел «Правка F5», п.3)."""
        cols = {row[1] for row in db.execute("PRAGMA table_info(review_requests)")}
        if "decided_by_role" not in cols:
            db.execute("ALTER TABLE review_requests ADD COLUMN decided_by_role TEXT")

    # --- полномочия ---

    @staticmethod
    def _active_decision_types(db: sqlite3.Connection, login: str, scope: str, now_iso: str) -> set[str]:
        """Все действующие (не отозванные И не истёкшие по `valid_until`, F3) виды решения логина в этой
        `scope`, объединённые по всем строкам полномочий — общий код вместо отдельной копии SQL в
        `grant_authority`/`decide`/`resolved_link`/`approved_claim` (приняв готовое соединение операции, не
        открывает новое). `now_iso` — момент проверки «здесь и сейчас» в том же сравнимом лексикографически
        формате, что `granted_at`/`valid_until` (`_iso`) — вызывающий передаёт уже вычисленный момент (один и
        тот же на всю операцию), не пересчитывает `datetime.now()` внутри этой функции."""
        rows = db.execute(
            "SELECT decision_types FROM review_authority_grants WHERE login = ? AND scope = ?"
            " AND revoked_at IS NULL AND (valid_until IS NULL OR ? < valid_until)",
            (login, scope, now_iso)).fetchall()
        return {t for (raw,) in rows for t in _decision_types_of(raw)}

    def _insert_grant(self, db: sqlite3.Connection, login: str, scope: str, types: tuple[str, ...],
                      granted_by_login: str, now: str, valid_until_iso: str | None) -> None:
        db.execute(
            "INSERT INTO review_authority_grants (login, scope, decision_types, granted_by, granted_at,"
            " revoked_at, valid_until) VALUES (?, ?, ?, ?, ?, NULL, ?)",
            (login, scope, ",".join(types), granted_by_login, now, valid_until_iso))

    def grant_authority(self, login: str, scope: str, decision_types: tuple[ReviewKind, ...], *,
                        granted_by: "RequestIdentity", valid_until: datetime | None = None) -> AuthorityGrant:
        """Назначить полномочие. `granted_by.login == login` — `ValueError` (запрет самовольного назначения,
        мастер-промпт §6); у `granted_by` нет собственного действующего полномочия (любого вида решения) в
        этой же `scope` — `PermissionError`, БЕЗ ИСКЛЮЧЕНИЯ для пустого хранилища (итоговое ревью контроллера,
        2026-10-14: держать это только отсутствием веб-маршрута было недостаточно — первое назначение теперь
        возможно только через `bootstrap_authority`, которую этот метод не вызывает и которую `gateway`/
        `service.py` вызывать не должны). На пустом хранилище у ЛЮБОГО `granted_by`, включая операторскую
        личность CLI, действующих полномочий нет ни в одной `scope` — метод откажет и операторскому вызову,
        это ожидаемо: CLI для первого назначения зовёт `bootstrap_authority`, не этот метод (`main()` ниже).

        `valid_until` (F3, срок полномочия) — `None` (по умолчанию) — без ограничения, иначе `datetime`
        (naive трактуется как UTC, как везде в модуле, `_iso`); проверка «у granted_by есть действующее
        полномочие» и запись `granted_at` берут ОДИН момент `now` — не две разные точки времени на одну
        операцию."""
        _check_login_scope(login, scope)
        types = _normalized_decision_types(decision_types)
        if granted_by.login == login:
            raise ValueError("самоназначение полномочия запрещено (мастер-промпт §6)")
        if valid_until is not None and not isinstance(valid_until, datetime):
            raise ValueError("valid_until: нужен datetime или None")
        now = _iso(datetime.now(timezone.utc))
        valid_until_iso = None if valid_until is None else _iso(valid_until)
        with self._op(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            if not self._active_decision_types(db, granted_by.login, scope, now):
                raise PermissionError(
                    "у назначающего нет действующего полномочия в этой области — назначать может только "
                    "обладатель полномочия в той же области; первое назначение в пустом хранилище — только "
                    "операторская команда bootstrap (python -m pcbk_core.analytics.reviews bootstrap)")
            self._insert_grant(db, login, scope, types, granted_by.login, now, valid_until_iso)
            db.execute("COMMIT")
        return AuthorityGrant(login=login, scope=scope, decision_types=types, granted_by=granted_by.login,
                              granted_at=_parse_iso(now), revoked_at=None, valid_until=valid_until)

    def bootstrap_authority(self, login: str, scope: str, decision_types: tuple[ReviewKind, ...], *,
                            operator: str, valid_until: datetime | None = None) -> AuthorityGrant:
        """Первое назначение полномочия в полностью пустом хранилище (ни одной строки
        `review_authority_grants`, в т.ч. отозванных — иначе отзыв всех полномочий открывал бы повторный
        бутстрап): единственный путь, которым может появиться самый первый держатель права, когда проверять
        «есть ли у назначающего действующее полномочие» ещё не на чем (докстринг модуля). Берёт не
        `RequestIdentity`, а голый `operator: str` — такую сигнатуру `gateway`/`service.py` не вызывают (у них
        есть только `RequestIdentity` из сессии, не отдельная «операторская» личность); единственный
        вызывающий — офлайн-команда `main()` ниже (`python -m pcbk_core.analytics.reviews bootstrap`), оператор
        внутри контейнера core. Хранилище уже не пустое — `ReviewStoreError` («используйте grant_authority»),
        не тихий успех в обход проверки полномочия назначающего. `operator == login` — `ValueError`
        (самоназначение запрещено и здесь — операторская личность не освобождает от этого правила).

        `valid_until` (F3, срок полномочия) — тот же смысл и та же проверка типа, что у `grant_authority`."""
        _check_login_scope(login, scope)
        types = _normalized_decision_types(decision_types)
        if not isinstance(operator, str) or not operator:
            raise ValueError("operator: нужна непустая строка")
        if operator == login:
            raise ValueError("самоназначение полномочия запрещено (мастер-промпт §6)")
        if valid_until is not None and not isinstance(valid_until, datetime):
            raise ValueError("valid_until: нужен datetime или None")
        now = _iso(datetime.now(timezone.utc))
        valid_until_iso = None if valid_until is None else _iso(valid_until)
        with self._op(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            total = db.execute("SELECT count(*) FROM review_authority_grants").fetchone()[0]
            if total > 0:
                raise ReviewStoreError(
                    "аналитика КДМ-1 (согласование): хранилище не пустое — используйте grant_authority"
                    " (bootstrap только для самого первого назначения)")
            self._insert_grant(db, login, scope, types, operator, now, valid_until_iso)
            db.execute("COMMIT")
        return AuthorityGrant(login=login, scope=scope, decision_types=types, granted_by=operator,
                              granted_at=_parse_iso(now), revoked_at=None, valid_until=valid_until)

    def revoke_authority(self, login: str, scope: str, *, revoked_by: "RequestIdentity") -> bool:
        """Отозвать действующее полномочие; `False` — полномочия не было или уже отозвано. `revoked_by` не
        хранится отдельным полем (его нет у `AuthorityGrant`, `docs/kdm1/INTERFACES.md`) — операция только
        операторская (та же офлайн-команда), сам факт отзыва и так виден по `revoked_at`."""
        now = _iso(datetime.now(timezone.utc))
        with self._op(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute(
                "UPDATE review_authority_grants SET revoked_at = ? WHERE login = ? AND scope = ?"
                " AND revoked_at IS NULL", (now, login, scope))
            db.execute("COMMIT")
        return cur.rowcount > 0

    def active_grant(self, login: str, scope: str, decision_type: ReviewKind) -> AuthorityGrant | None:
        """Действующее (не отозванное И не истёкшее по `valid_until`, F3) полномочие логина на область/тип
        решения — для проверки «может ли этот технолог решать эту заявку» в `decide`/ручках API, не доверяя
        роли учётки."""
        now_iso = _iso(datetime.now(timezone.utc))
        with self._op() as db:
            rows = db.execute(
                "SELECT decision_types, granted_by, granted_at, valid_until FROM review_authority_grants"
                " WHERE login = ? AND scope = ? AND revoked_at IS NULL AND (valid_until IS NULL OR"
                " ? < valid_until) ORDER BY granted_at DESC", (login, scope, now_iso)).fetchall()
        for decision_types_raw, granted_by, granted_at, valid_until in rows:
            types = _decision_types_of(decision_types_raw)
            if decision_type in types:
                return AuthorityGrant(login=login, scope=scope, decision_types=types, granted_by=granted_by,
                                      granted_at=_parse_iso(granted_at), revoked_at=None,
                                      valid_until=None if valid_until is None else _parse_iso(valid_until))
        return None

    # --- заявки ---

    def create_request(self, kind: ReviewKind, subject_id: str, new_value: str, scope: str, rationale: str, *,
                       created_by: "RequestIdentity", old_value: str | None = None,
                       period_start: datetime | None = None, period_end: datetime | None = None) -> ReviewRequest:
        """Новая заявка; создание не требует полномочия (может отправить любой увидевший конфликт/подпись, не
        только обладатель права — `AnalyticsPage.tsx`, Task 6: «её может отправить любой», право нужно только
        на `decide`)."""
        if kind not in _REVIEW_KINDS:
            raise ValueError(f"kind: неизвестный вид {kind!r}")
        if not isinstance(subject_id, str) or not subject_id:
            raise ValueError("subject_id: нужна непустая строка")
        if not isinstance(new_value, str) or not new_value:
            raise ValueError("new_value: нужна непустая строка")
        if not isinstance(scope, str) or not scope:
            raise ValueError("scope: нужна непустая строка")
        if not isinstance(rationale, str) or not rationale:
            raise ValueError("rationale: нужна непустая строка")
        if period_start is not None and period_end is not None and period_end < period_start:
            raise ValueError("период: окончание раньше начала")
        request_id = secrets.token_hex(16)
        now = datetime.now(timezone.utc)
        now_iso = _iso(now)
        period_start_iso = None if period_start is None else _iso(period_start)
        period_end_iso = None if period_end is None else _iso(period_end)
        with self._op(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                f"INSERT INTO review_requests ({_REQUEST_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending',"
                " ?, ?, NULL, NULL, NULL, NULL)",
                (request_id, kind, subject_id, old_value, new_value, scope, period_start_iso, period_end_iso,
                 rationale, created_by.login, now_iso))
            db.execute("COMMIT")
        return ReviewRequest(id=request_id, kind=kind, subject_id=subject_id, old_value=old_value,
                             new_value=new_value, scope=scope, period_start=period_start, period_end=period_end,
                             rationale=rationale, status="pending", created_by=created_by.login, created_at=now,
                             decided_by=None, decided_at=None, comment=None)

    def list_requests(self, viewer: "RequestIdentity", *, status: ReviewStatus | None = None,
                      reviewers_mode: "ReviewersMode" = "grants") -> list[ReviewRequest]:
        """Заявки, видимые `viewer`: свои созданные или те, что покрывает его `active_grant` (область/тип) —
        не весь список всем подряд.

        F5 (`reviewers_mode="all_students"`, решение владельца 14.10.2026): `viewer.role == "student"` видит
        ВСЕ заявки области КДМ-1 целиком (видимость «один студент технолог» — любой видит всё, не только своё
        и не только где назначено) — `teacher` этим не затрагивается (видимость не меняется, как в `grants`)."""
        if reviewers_mode not in _REVIEWER_MODES:
            raise ValueError(f"reviewers_mode: неизвестный {reviewers_mode!r}")
        if status is not None and status not in ("pending", "accepted", "rejected", "info_requested"):
            raise ValueError(f"status: неизвестный {status!r}")
        with self._op() as db:
            if status is None:
                rows = db.execute(f"SELECT {_REQUEST_COLUMNS} FROM review_requests"
                                  " ORDER BY created_at DESC, id DESC").fetchall()
            else:
                rows = db.execute(f"SELECT {_REQUEST_COLUMNS} FROM review_requests WHERE status = ?"
                                  " ORDER BY created_at DESC, id DESC", (status,)).fetchall()
            grant_rows = db.execute(
                "SELECT scope, decision_types FROM review_authority_grants WHERE login = ? AND revoked_at IS NULL",
                (viewer.login,)).fetchall()
        if reviewers_mode == "all_students" and viewer.role == "student":
            return [_row_to_request(row) for row in rows]
        grants_by_scope: dict[str, set[str]] = {}
        for scope, decision_types_raw in grant_rows:
            grants_by_scope.setdefault(scope, set()).update(_decision_types_of(decision_types_raw))
        result = []
        for row in rows:
            req = _row_to_request(row)
            if req.created_by == viewer.login or req.kind in grants_by_scope.get(req.scope, set()):
                result.append(req)
        return result

    def decide(self, request_id: str, decision: ReviewDecision, comment: str, *,
              decided_by: "RequestIdentity", reviewers_mode: "ReviewersMode" = "grants") -> ReviewRequest:
        """Решить заявку: у `decided_by` обязан быть `active_grant` именно на `scope`/`kind` ЭТОЙ заявки —
        без него заявка для `decided_by` НЕВИДИМА (`ReviewStoreError`, тот же отказ и текст, что у
        несуществующего `request_id`), не просто «отказано» (истёкшее по `valid_until` полномочие, F3, —
        та же невидимость, что у постороннего, `_active_decision_types` его уже не вернёт). Автор заявки не
        решает её сам, даже держа нужное полномочие (Task 7, шаг 2 — тот же принцип разделения ролей, что
        запрет самоназначения) — заявку должен решить кто-то ДРУГОЙ с действующим правом.

        `info_requested` НЕ терминален (F3): решать можно заявку в статусе `pending` ИЛИ `info_requested`
        (в т.ч. повторным `info_requested` — «статусы и версии сохраняются», мастер-промпт §6); терминальны
        только `accepted`/`rejected` — повторное решение такой заявки — `ValueError` («уже решена», без
        отдельного отзыва «переголосования» нет). КАЖДЫЙ успешный вызов добавляет строку в `review_decisions`
        (журнал: кто, что решил и когда) — независимо от исхода и `kind`; `review_requests` хранит только
        «текущий» снимок (status/decided_by/decided_at/comment), как раньше.

        Второй независимый согласующий (F3): `accepted` по заявке `kind` в (`local_claim`, `local_norm`) не
        может вынести логин, который уже вынес по ЭТОЙ ЖЕ заявке хоть одно решение ранее (виден в
        `review_decisions.decided_by` для этого `request_id`, независимо от того, каким было то решение) —
        `PermissionError("нужен второй независимый согласующий: …")`. Для `conflict`/`equipment_link` —
        решение контроллера: одного правообладателя достаточно (это эксплуатационные/аппаратные решения, не
        изменение того, что LLM видит как источник истины местных норм/фактов корпуса) — тот же логин,
        оставивший `info_requested`, может сам потом вынести `accepted`/`rejected` по этим двум видам.

        `comment` — пустая строка (отправитель формы оставил поле пустым) хранится как `None`, не как `""`
        (веб `analytics.ts::isNullableText` ждёт null ИЛИ непустую строку, не пустую).

        Порядок проверок (правка контроллера, 2026-10-14, Important — ревью `e9af303`; дополнено F3): видимость
        — ПЕРВАЯ проверка после чтения строки, раньше статуса и раньше самозапрета. Без неё посторонний без
        права мог по одному `review_id` различить существование заявки (404 против 403/409) и даже её статус
        (403 «pending» против 409 «уже решена») — утечка через код ответа, не через текст. Теперь у
        `decided_by` без видимости (не автор И нет действующего полномочия на `kind`/`scope` именно этой
        заявки) запрос получает РОВНО ТО ЖЕ исключение, что при несуществующем `request_id` — не различить по
        ответу. Автор и держатель нужного полномочия ВСЕГДА видимы (иначе им самим нечего было бы решать/
        видеть в своём списке) — для них дальше идёт проверка терминального статуса (409), самозапрета (403)
        и, последней, второго независимого согласующего (403, F3) — полномочие ниже не проверяется повторно
        отдельной веткой, видимость уже его подтвердила (кроме случая «видим как автор», который самозапрет
        отсекает раньше).

        F5 (`reviewers_mode="all_students"`, решение владельца 14.10.2026): `decided_by.role == "student"` —
        видимость ВСЕГДА подтверждена (любая заявка любой `kind`/`scope`), самозапрет автора и второй
        независимый согласующий НЕ применяются — «любой утверждает без дополнительных утверждений», в одно
        действие, даже свою заявку. `decided_by.role == "teacher"` — ИСКЛЮЧЕНИЕ из этого режима, не «любой
        вошедший»: требуется действующее полномочие, как в `grants` (мастер-промпт §6 это решение владельца не
        отменяет). Терминальный статус (`accepted`/`rejected`) остаётся ValueError для всех — «нет
        переголосования» не входит в список снятых правил. Успешное решение студента по этому обходному пути
        записывает `decided_by_role="student"` в `review_requests` (не только в журнал) — `approved_claim`/
        `resolved_link` читают его при следующей проверке «здесь и сейчас» (см. их докстринги)."""
        if reviewers_mode not in _REVIEWER_MODES:
            raise ValueError(f"reviewers_mode: неизвестный {reviewers_mode!r}")
        if decision not in _REVIEW_DECISIONS:
            raise ValueError(f"decision: неизвестное решение {decision!r}")
        if not isinstance(comment, str):
            raise ValueError("comment: нужна строка")
        comment_value = comment.strip() or None
        now_iso = _iso(datetime.now(timezone.utc))
        # F5: «любой студент решает любую заявку без назначения, в одно действие» — teacher остаётся
        # исключением (только по действующему полномочию, как в grants), см. докстринг выше
        blanket_student = reviewers_mode == "all_students" and decided_by.role == "student"
        with self._op(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(f"SELECT {_REQUEST_COLUMNS} FROM review_requests WHERE id = ?",
                             (request_id,)).fetchone()
            if row is None:
                raise ReviewStoreError("аналитика КДМ-1 (согласование): заявка не найдена")
            current = _row_to_request(row)
            is_author = current.created_by == decided_by.login
            if not blanket_student:
                allowed_kinds = self._active_decision_types(db, decided_by.login, current.scope, now_iso)
                if not is_author and current.kind not in allowed_kinds:
                    # нет видимости вовсе — тот же отказ, что у несуществующей заявки, не "нет права" (не
                    # раскрывать посторонним ни факт существования, ни статус заявки, на которую у них нет
                    # никакого отношения)
                    raise ReviewStoreError("аналитика КДМ-1 (согласование): заявка не найдена")
            if current.status in ("accepted", "rejected"):
                raise ValueError("заявка уже решена — повторное решение запрещено")
            if not blanket_student and is_author:
                raise PermissionError("автор заявки не может решать её сам — нужен другой держатель права")
            if (not blanket_student and decision == "accepted"
                    and current.kind in ("local_claim", "local_norm")):
                prior_deciders = {r[0] for r in db.execute(
                    "SELECT DISTINCT decided_by FROM review_decisions WHERE request_id = ?",
                    (request_id,)).fetchall()}
                if decided_by.login in prior_deciders:
                    raise PermissionError(
                        "нужен второй независимый согласующий: этот логин уже выносил решение по этой заявке"
                        " ранее (local_claim/local_norm требуют подтверждения другим держателем права)")
            # дошли сюда — видимость подтверждена НЕ авторством (самозапрет выше уже отсёк автора), значит
            # current.kind уже внутри allowed_kinds — действующее полномочие на эту заявку есть (кроме
            # blanket_student, где видимость/право — сама принадлежность к student в режиме all_students)
            db.execute(
                "UPDATE review_requests SET status = ?, decided_by = ?, decided_at = ?, comment = ?,"
                " decided_by_role = ? WHERE id = ?",
                (decision, decided_by.login, now_iso, comment_value, decided_by.role, request_id))
            db.execute(
                "INSERT INTO review_decisions (request_id, decision, decided_by, decided_at, comment)"
                " VALUES (?, ?, ?, ?, ?)", (request_id, decision, decided_by.login, now_iso, comment_value))
            db.execute("COMMIT")
        return ReviewRequest(id=current.id, kind=current.kind, subject_id=current.subject_id,
                             old_value=current.old_value, new_value=current.new_value, scope=current.scope,
                             period_start=current.period_start, period_end=current.period_end,
                             rationale=current.rationale, status=decision, created_by=current.created_by,
                             created_at=current.created_at, decided_by=decided_by.login,
                             decided_at=_parse_iso(now_iso), comment=comment_value,
                             decided_by_role=decided_by.role)

    def has_any_active_grant(self, login: str) -> bool:
        """Хоть одно действующее (не отозванное И не истёкшее по `valid_until`, F3) полномочие логина, в
        ЛЮБОЙ `scope` (не только `"KDM1_PCBK"` целиком) — для `Capabilities.review_authority` (`service.py`,
        Task 7, шаг 2): «есть действующее полномочие у identity в области КДМ-1» означает «держит хоть
        какое-то право где-то внутри КДМ-1», не обязательно на весь верхний `scope` сразу (`AuthorityGrant.
        scope` может быть подмножеством — участком/процессом)."""
        now_iso = _iso(datetime.now(timezone.utc))
        with self._op() as db:
            row = db.execute(
                "SELECT 1 FROM review_authority_grants WHERE login = ? AND revoked_at IS NULL"
                " AND (valid_until IS NULL OR ? < valid_until) LIMIT 1", (login, now_iso)).fetchone()
        return row is not None

    def has_any_resolved_link(self) -> bool:
        """Хоть одна действующая связь оборудования с историаном (любой `link_id`) — для
        `Capabilities.data_bridge_available` (`service.py`, Task 7, шаг 2): «есть хоть одна действующая
        связь» рядом с «DataService подключён». Та же повторная проверка полномочия решившего (включая
        `valid_until`, F3), что `resolved_link` делает для конкретного `link_id` — здесь по всем принятым
        заявкам `equipment_link` сразу, останавливается на первой ещё действующей; один момент `now_iso` на
        весь перебор, не пересчитывается на каждой строке."""
        now_iso = _iso(datetime.now(timezone.utc))
        with self._op() as db:
            rows = db.execute(
                "SELECT DISTINCT scope, decided_by FROM review_requests WHERE kind = 'equipment_link'"
                " AND status = 'accepted'").fetchall()
            for scope, decided_by in rows:
                if "equipment_link" in self._active_decision_types(db, decided_by, scope, now_iso):
                    return True
        return False

    def resolved_link(self, link_id: str, *, reviewers_mode: "ReviewersMode" = "grants") -> ResolvedLink | None:
        """Текущая согласованная связь оборудования (после `accepted` по заявке `kind="equipment_link"`,
        `subject_id=link_id`, если её не отозвали) — то, что `data_bridge.py` подставляет в вызовы
        `DataService`. Нет принятой заявки, решивший с тех пор потерял полномочие на эту `scope`/
        `equipment_link` (`revoke_authority` — докстринг модуля, «повторная проверка в момент выдачи») —
        `None`: справка работает и без связи (мастер-промпт §7, этап 7: «без них справка полностью
        работает»).

        `period_start`/`period_end` заявки (если заполнены) здесь НАМЕРЕННО не проверяются, в отличие от
        `approved_claim`: связь оборудования с историаном — не временное утверждение с сроком действия
        («с такой-то даты по такую-то измерение валидно»), а решение «этот тег — это тот показатель» раз и
        навсегда, пока не отозвано (`revoke_authority`) или не заменено новой принятой заявкой того же
        `subject_id` (`resolved_link` берёт самое недавнее `accepted` решение). Поле периода в заявке
        `equipment_link` может быть заполнено (интерфейс `create_request` общий для всех `kind`), но
        `resolved_link` его не читает — отличие от `approved_claim` (карточка/норматив/конфликт — утверждения,
        у которых срок действия осмыслен) задокументировано здесь, не угадывается по коду.

        `valid_until` решившего (F3) — проверяется «здесь и сейчас» (реальный момент вызова), той же логикой,
        что отзыв: истёкшее без отзыва полномочие отключает связь так же, как отзыв.

        F5 (`reviewers_mode="all_students"`): решение, принятое студентом по обходному пути (`decided_by_role
        == "student"` — записано `decide()` на момент решения), действует БЕЗ проверки `active_grant`, но
        ТОЛЬКО пока `reviewers_mode == "all_students"` именно в момент ЭТОЙ проверки — переключение на
        `grants` делает его недействующим для нового применения (как отзыв/истечение `valid_until`, не трогая
        сама заявка/журнал). Решения `teacher` (`decided_by_role == "teacher"`, всегда по действующему на
        момент решения полномочию) этим путём не затрагиваются — для них перепроверяется `active_grant`, как в
        `grants`."""
        if reviewers_mode not in _REVIEWER_MODES:
            raise ValueError(f"reviewers_mode: неизвестный {reviewers_mode!r}")
        now_iso = _iso(datetime.now(timezone.utc))
        with self._op() as db:
            row = db.execute(
                f"SELECT {_REQUEST_COLUMNS} FROM review_requests WHERE kind = 'equipment_link'"
                " AND subject_id = ? AND status = 'accepted' ORDER BY decided_at DESC, rowid DESC LIMIT 1",
                (link_id,)).fetchone()
            if row is None:
                return None
            req = _row_to_request(row)
            blanket_ok = reviewers_mode == "all_students" and req.decided_by_role == "student"
            if not blanket_ok and (
                    "equipment_link" not in self._active_decision_types(db, req.decided_by, req.scope, now_iso)):
                return None
        return ResolvedLink(link_id=link_id, process_id=req.scope, historian_tag=req.new_value, unit=None,
                            approved_by=req.decided_by, approved_at=req.decided_at, scope=req.scope)

    def approved_claim(self, card_id: str, scope: str, at: datetime | None = None, *,
                       reviewers_mode: "ReviewersMode" = "grants") -> ReviewRequest | None:
        """Действующее согласование карточки/конфликта/норматива (дополнение контроллера к Task 7, 2026-10-14:
        стык с `validator.py`, Task 3b, коммит 7b22774 — `APPROVED_CRITICAL_FACT_STATES={"reviewer_approved"}`
        требует именно действующего согласования, не просто факта когда-то принятого решения). Принятое решение
        `kind` в `local_claim`/`local_norm`/`conflict` по `subject_id=card_id`, в этой `scope`, в сроке
        `period_start..period_end` (период не задан у заявки — без ограничения по времени) и не обесцененное
        отзывом полномочия решившего (как `resolved_link` — полномочие проверяется заново, здесь и сейчас, не
        по факту решения в прошлом). `at` — момент проверки (по умолчанию «сейчас»): подключение к
        `validator.py`/`service.py` — отдельный шаг (контроллер прислал это отдельно от встраивания), здесь —
        только функция стора и её тесты.

        `valid_until` решившего (F3) — проверяется НЕ по реальному «сейчас», а по тому же `moment_iso`, что и
        `period_start`/`period_end` самой карточки: одна точка времени «момент проверки» на весь вызов, а не
        две разные (решение контроллера — отличие от `resolved_link`/`decide`/`active_grant`, у которых
        отдельного параметра «момент» нет и они честно берут реальное «сейчас»; здесь он уже есть как `at`,
        заводить вторую независимую временную ось внутри одной проверки было бы лишним и противоречивым).

        F5 (`reviewers_mode="all_students"`): решение, принятое студентом по обходному пути (`decided_by_role
        == "student"`), действующее БЕЗ проверки `active_grant`, ТОЛЬКО пока `reviewers_mode == "all_students"`
        в момент ЭТОЙ проверки (не в момент решения) — см. докстринг `resolved_link`, тот же приём. Решения
        `teacher` этим путём не затрагиваются — для них перепроверяется `active_grant`, как в `grants`."""
        if reviewers_mode not in _REVIEWER_MODES:
            raise ValueError(f"reviewers_mode: неизвестный {reviewers_mode!r}")
        moment_iso = _iso(datetime.now(timezone.utc) if at is None else at)
        with self._op() as db:
            rows = db.execute(
                f"SELECT {_REQUEST_COLUMNS} FROM review_requests WHERE kind IN ('local_claim', 'local_norm',"
                " 'conflict') AND subject_id = ? AND scope = ? AND status = 'accepted'"
                " ORDER BY decided_at DESC, rowid DESC", (card_id, scope)).fetchall()
            if not rows:
                return None
            requests = [_row_to_request(row) for row in rows]
            decided_logins = {req.decided_by for req in requests}
            grants_by_login = {login: self._active_decision_types(db, login, scope, moment_iso)
                               for login in decided_logins}
        for req in requests:
            if req.period_start is not None and moment_iso < _iso(req.period_start):
                continue
            if req.period_end is not None and moment_iso > _iso(req.period_end):
                continue
            if reviewers_mode == "all_students" and req.decided_by_role == "student":
                return req
            if req.kind in grants_by_login.get(req.decided_by, set()):
                return req
        return None


# --- офлайн-команда оператора: python -m pcbk_core.analytics.reviews grant|revoke|list ---

class _UsageError(Exception):
    """Ошибка разбора аргументов команды — код 1 и одна строка в stderr (приём `gateway.accounts._Parser`), не
    usage/код 2 модуля `argparse`: команд всего три и без вложенных вариантов, разбор проще собрать вручную."""


_COMMANDS = ("bootstrap", "grant", "revoke", "list")


def _parse_args(argv: list[str]) -> dict:
    if not argv:
        raise _UsageError("команда: " + ", ".join(_COMMANDS))
    command, rest = argv[0], argv[1:]
    if command not in _COMMANDS:
        raise _UsageError(f"команда: неизвестная {command!r} ({', '.join(_COMMANDS)})")
    opts: dict[str, str] = {}
    it = iter(rest)
    for token in it:
        if not token.startswith("--"):
            raise _UsageError(f"аргумент: ожидался --ключ, получено {token!r}")
        try:
            value = next(it)
        except StopIteration:
            raise _UsageError(f"{token}: нужно значение") from None
        opts[token[2:]] = value
    return {"command": command, **opts}


def _require(opts: dict, *names: str) -> list[str]:
    missing = [n for n in names if n not in opts]
    if missing:
        raise _UsageError("нужны аргументы: " + ", ".join(f"--{n}" for n in missing))
    return [opts[n] for n in names]


def _fail(message: str) -> int:
    print(message.replace("\r", " ").replace("\n", " "), file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    """Команда оператора (запуск внутри контейнера core, не из веб-интерфейса, мастер-промпт §6/план Task 7).
    Оператор отвечает за то, что `--login`/`--by` — существующие логины реальных учёток (`gateway.accounts`):
    эта команда, как и `ReviewStore`, не сверяет их с `AccountStore` — полномочие можно назначить логину,
    которого ещё нет или уже нет в `gw_accounts`, без отдельной ошибки (то же решение, что `AuthorityGrant.
    login` — хранит логин, не ссылку на строку учётки).

    - `bootstrap --login L --scope S --types t1,t2 --by BY [--until ГГГГ-ММ-ДД[ЧЧ:ММ]]` — САМОЕ ПЕРВОЕ
      назначение в пустом хранилище (ни одного полномочия не выдано ранее); на непустом хранилище — отказ,
      дальше только `grant` (`ReviewStore.bootstrap_authority`);
    - `grant --login L --scope S --types t1,t2 --by BY [--until ГГГГ-ММ-ДД[ЧЧ:ММ]]` — назначить полномочие,
      когда `BY` уже сам держит действующее полномочие (любого вида решения) в той же `S` (`ReviewStore.
      grant_authority` — без исключения для пустого хранилища, см. докстринг метода);
    - `revoke --login L --scope S --by BY` — отозвать;
    - `list [--login L]` — действующие, истёкшие и отозванные полномочия, по логину или все.

    `--until` (F3, срок полномочия) — необязательный, формат `ГГГГ-ММ-ДД` или `ГГГГ-ММ-ДДTЧЧ:ММ` (без
    времени — начало суток UTC той даты, `_parse_until`); без него — полномочие без ограничения по сроку, как
    раньше. Путь базы — `--db PATH`, по умолчанию `AnalyticsSettings.from_env().ANALYTICS_STATE_PATH`. Удача —
    код 0; ошибка — код 1 и одна строка в stderr (как `gateway.accounts.main`).
    """
    try:
        opts = _parse_args(sys.argv[1:] if argv is None else argv)
    except _UsageError as exc:
        return _fail(f"команда: {exc}")
    try:
        db_path = opts.get("db") or AnalyticsSettings.from_env().ANALYTICS_STATE_PATH
    except ValueError as exc:
        return _fail(f"настройки: {exc}")
    store = ReviewStore(db_path)
    if not store.open():
        return _fail(f"база согласования не открывается: {store.last_error}")
    try:
        command = opts["command"]
        if command == "bootstrap":
            login, scope, types_raw, by = _require(opts, "login", "scope", "types", "by")
            types = tuple(t.strip() for t in types_raw.split(",") if t.strip())
            until = _parse_until(opts["until"]) if "until" in opts else None
            grant = store.bootstrap_authority(login, scope, types, operator=by, valid_until=until)
            print(f"{grant.login}\t{grant.scope}\t{','.join(grant.decision_types)}\t{grant.granted_by}\t"
                 f"{grant.granted_at.isoformat()}\t{grant.valid_until.isoformat() if grant.valid_until else '-'}")
        elif command == "grant":
            login, scope, types_raw, by = _require(opts, "login", "scope", "types", "by")
            types = tuple(t.strip() for t in types_raw.split(",") if t.strip())
            until = _parse_until(opts["until"]) if "until" in opts else None
            by_identity = _OperatorIdentity(by)
            grant = store.grant_authority(login, scope, types, granted_by=by_identity, valid_until=until)
            print(f"{grant.login}\t{grant.scope}\t{','.join(grant.decision_types)}\t{grant.granted_by}\t"
                 f"{grant.granted_at.isoformat()}\t{grant.valid_until.isoformat() if grant.valid_until else '-'}")
        elif command == "revoke":
            login, scope, by = _require(opts, "login", "scope", "by")
            ok = store.revoke_authority(login, scope, revoked_by=_OperatorIdentity(by))
            if not ok:
                return _fail("полномочия не было или оно уже отозвано")
        else:
            login = opts.get("login")
            rows = _list_grants(store, login)
            for row in rows:
                print("\t".join(row))
    except (ValueError, PermissionError, ReviewStoreError, _UsageError) as exc:
        return _fail(str(exc))
    return 0


class _OperatorIdentity:
    """Личность оператора для `grant`/`revoke` CLI (не из сессии — её тут нет): только `login` использует
    `ReviewStore`, `role`/`place`/`caller` — заглушки, не читаются (приём «минимальный двойник интерфейса», не
    `models.RequestIdentity`). `bootstrap` CLI её не создаёт — `ReviewStore.bootstrap_authority` берёт голый
    `operator: str`, не объект с этим протоколом (докстринг метода: такую сигнатуру `gateway` не вызовет).
    Формат логина не проверяется — оператор отвечает за то, что логин существует (докстринг `main()`)."""

    def __init__(self, login: str):
        self.login = login
        self.role = "teacher"
        self.place = None
        self.caller = "teacher"


def _list_grants(store: ReviewStore, login: str | None) -> list[tuple[str, ...]]:
    """Строка на полномочие: логин/область/виды/кем/когда/до-какого-срока (F3)/статус текстом — «отозвано»
    (`revoked_at` стоит), иначе «истекло» (`valid_until` в прошлом относительно `now_iso`, не отозвано явно —
    та же граница, что `ReviewStore._active_decision_types`, F3), иначе «действует»."""
    now_iso = _iso(datetime.now(timezone.utc))
    with store._op() as db:                                     # own, read-only listing — не часть публичного API
        if login is None:
            rows = db.execute(
                "SELECT login, scope, decision_types, granted_by, granted_at, revoked_at, valid_until"
                " FROM review_authority_grants ORDER BY login, scope, granted_at").fetchall()
        else:
            rows = db.execute(
                "SELECT login, scope, decision_types, granted_by, granted_at, revoked_at, valid_until"
                " FROM review_authority_grants WHERE login = ? ORDER BY scope, granted_at",
                (login,)).fetchall()
    result = []
    for login_, scope, types, by, at, revoked_at, valid_until in rows:
        if revoked_at:
            state = "отозвано"
        elif valid_until and now_iso >= valid_until:
            state = "истекло"
        else:
            state = "действует"
        result.append((login_, scope, types, by, at, revoked_at or "-", valid_until or "-", state))
    return result


if __name__ == "__main__":
    sys.exit(main())
