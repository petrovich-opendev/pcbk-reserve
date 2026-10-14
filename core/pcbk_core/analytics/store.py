"""Состояние и принадлежность диалогов аналитики КДМ-1 (Task 2; план §Task 2/Этап 2; мастер-промпт §4, §6).

Собственный файл SQLite — `AnalyticsSettings.ANALYTICS_STATE_PATH` (мастер-промпт §3/§4: «отдельный файл
состояния аналитики в прежнем томе»; `core.db` и исходный корпус не трогаются). Договор Role (как
`AccountStore`/`AgentStore`/`TeacherLog`, приём — `gateway.accounts.AccountStore`/`llm.shares.Shares`):
конструктор только запоминает путь, `open()` создаёт файл/таблицы и выполняет миграцию версии схемы в потоке
(`asyncio.to_thread`) из lifespan шлюза — не раньше; соединение — на каждую операцию (`_connect`, mode=rw/rwc),
явные транзакции (`BEGIN IMMEDIATE` … `COMMIT`); незавершённая транзакция откатывается закрытием соединения.
Ни один вызов не бросает кроме `StoreError`, без пути и значений в тексте (`_why`/`_store_error`, как
`accounts._store_error`/`shares._store_error`). Правка F4b, итоговое ревью (H-18): `StoreError` различает два
смысла, которые раньше показывались вызывающему одним и тем же кодом ответа — «не найдено/чужое» (решение по
правилам, плоский `StoreError`, 404 у `gateway/analytics_api.py`) и «хранилище само недоступно» (сбой самого
SQLite, подкласс `StoreUnavailable`, 503) — см. докстринги обоих классов ниже.

Хранит диалоги, задания (`request_id` → стадия/результат, исходный `AnalysisRequest` и проверенный `JobResponse`
— не рассуждения модели, мастер-промпт §4: «не хранить рассуждения модели в пользовательской истории»),
неизменяемые пакеты доказательств и версию схемы.

Владелец диалога — логин человека (`RequestIdentity.login`), не рабочее место (мастер-промпт §6: «владелец
истории — человек, а не рабочее место: смена закрепления не передаёт историю»). Уникальность
`(owner_login, conversation_id, client_request_id)` — конкурентный повтор одного клиентского запроса не создаёт
второе задание и не списывает второй раз (ОСОБЫЙ ФОКУС ПРОВЕРКИ №3 плана): `create_request` вставляет строку
через `INSERT … ON CONFLICT(…) DO NOTHING` под `BEGIN IMMEDIATE` — SQLite сериализует конкурентных писателей,
первый получает `created=True`, прочие читают уже вставленную строку и получают тот же `request_id`,
`created=False`, без второй строки и без второго вызова LLM.

Терминальные стадии задания (`models.JOB_TERMINAL_STATES`) наступают только через `publish_response` (итоговый
ответ) или `cancel_request`/`reap_unfinished_after_restart` (отмена/обрыв) — `set_preanalysis` для решений
`needs_clarification`/`limited_answer`/`refused` переводит стадию в `validating` (не в терминальную): эти три
решения обходят `llm_adapter.py` («терминальные для LLM-пути» в смысле маршрута, не стадии задания), но сам
клиентский ответ и итоговая стадия приходят из `rendering.render_response()` → `publish_response`, как и для
решения `ready`. Так `publish_response` одной и той же проверкой (стадия не терминальна и ответ ещё не
опубликован) защищает все четыре исхода преданализа от гонки с отменой/перезапуском (ОСОБЫЙ ФОКУС ПРОВЕРКИ №3):
конкурентная отмена успевает раньше — `publish_response` увидит терминальную стадию `cancelled` и откажется
перезаписывать её `False`, не теряя и не повторяя оплату.

После перезапуска неизвестно оплаченный вызов не повторяется автоматически (мастер-промпт §10):
`reap_unfinished_after_restart`, вызываемая явно при старте (после `open()`), переводит все задания не в
терминальной стадии и без опубликованного ответа в `failed` с причиной «перезапуск core» одной транзакцией.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from .models import JOB_TERMINAL_STATES, AnalysisRequest, JobResponse, JobState, Preanalysis, RequestIdentity

SQLITE_TIMEOUT_S = 2.0                  # запись ждёт блокировку недолго, как AccountStore/Shares (Г14)
CURRENT_SCHEMA_VERSION = 2              # 1 — без analytics_schema/analytics_heartbeat и requests.updated_at
_SQLITE_CODE = re.compile(r"SQLITE_[A-Z_]{1,40}")

_STAGE_QUEUED = "принято в очередь"
_STAGE_RETRIEVING = "поиск оснований"
_STAGE_CANCELLED = "отменено пользователем"
_STAGE_RESTARTED = "прервано перезапуском службы"
# решения преданализа, что обходят llm_adapter.py (мастер-промпт §7/§10) — стадия задания при этом переходит в
# "validating" (не терминальная): итоговую терминальную стадию и клиентский JobResponse даёт только rendering.py
# через publish_response, как и для "ready" (см. докстринг модуля)
_SKIP_LLM_STAGE_LABEL: dict[str, str] = {
    "needs_clarification": "нужно уточнение",
    "limited_answer": "ограниченный ответ",
    "refused": "отказ предварительного анализа",
}

_SCHEMA_CURRENT: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        owner_login TEXT NOT NULL,
        mode TEXT NOT NULL CHECK (mode IN ('reference', 'deviation_review')),
        created_at TEXT NOT NULL,
        last_activity_at TEXT NOT NULL,
        corpus_version TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS conversations_owner ON conversations (owner_login, last_activity_at)",
    """CREATE TABLE IF NOT EXISTS requests (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        owner_login TEXT NOT NULL,
        client_request_id TEXT NOT NULL,
        state TEXT NOT NULL,
        stage_label TEXT NOT NULL,
        corpus_version TEXT NOT NULL,
        request_json TEXT NOT NULL,
        preanalysis_json TEXT,
        response_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    ("CREATE UNIQUE INDEX IF NOT EXISTS requests_idempotency ON requests"
     " (owner_login, conversation_id, client_request_id)"),
    "CREATE INDEX IF NOT EXISTS requests_conversation ON requests (conversation_id)",
    """CREATE TABLE IF NOT EXISTS evidence_packs (
        id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS evidence_packs_request ON evidence_packs (request_id)",
    """CREATE TABLE IF NOT EXISTS evidence_items (
        pack_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        data_json TEXT NOT NULL,
        PRIMARY KEY (pack_id, item_id)
    )""",
    # пульс ping(): одна служебная строка, перезаписывается каждым вызовом (приём gw_heartbeat)
    """CREATE TABLE IF NOT EXISTS analytics_heartbeat (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        at TEXT NOT NULL
    )""",
    # версия схемы: одна строка (приём gw_heartbeat/llm_shares — явная таблица, не PRAGMA user_version, ради
    # единого стиля с прочими состояниями проекта)
    """CREATE TABLE IF NOT EXISTS analytics_schema (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        version INTEGER NOT NULL
    )""",
)


def _why(exc: BaseException) -> str:
    """Класс ошибки и код SQLite (если он есть) — без путей и значений (приём accounts._why/shares._why)."""
    code = getattr(exc, "sqlite_errorname", None)
    code = code if isinstance(code, str) and _SQLITE_CODE.fullmatch(code) else None
    return type(exc).__name__ + (f" {code}" if code else "")


class StoreError(Exception):
    """Сбой хранилища аналитики (приём `gateway.accounts.StoreError`): класс ошибки и код SQLite, без пути и
    значений. `create_request` использует этот же класс (НЕ `StoreUnavailable` ниже) и для явного отказа
    «чужой/несуществующий диалог» (без различия между двумя причинами в тексте — чужой идентификатор не
    подтверждает существование ресурса, Global Constraints): это решение по правилам (обычный `raise` внутри
    транзакции `_op`, не `sqlite3.Error`), не сбой инфраструктуры — см. `StoreUnavailable`."""


class StoreUnavailable(StoreError):
    """Правка F4b, итоговое ревью (H-18): хранилище само недоступно — нет места/только чтение/заблокировано/
    файл повреждён/соединение не открылось — отличимо от `StoreError`, который `create_request` бросает явно
    для «диалог не найден/чужой» (то — решение по правилам, не сбой инфраструктуры, и этим классом НЕ
    оборачивается). Единственный источник этого класса — `_store_error` ниже, вызываемый ровно там, где `_op`
    перехватывает настоящий `sqlite3.Error` (отказ самого SQLite); логический отказ, брошенный ВНУТРИ блока
    `with self._op(...)` вызывающим кодом модуля (как в `create_request`), — обычный Python-`raise`, не
    `sqlite3.Error`, и этим перехватом не ловится (см. докстринг `_op`) — остаётся плоским `StoreError`.

    Вызывающий (`gateway/analytics_api.py`) отображает именно этот класс в 503 «Аналитика КДМ-1 временно
    недоступна» — новый вопрос (запись невозможна) и чтение существующей истории/задания (прочитать нельзя) —
    это НЕ «не найдено» (404) и не необработанное падение (500)."""


def _store_error(exc: BaseException) -> StoreUnavailable:
    """Сбой самого SQLite, долетевший до `_op`/`open`/`ping` — см. `StoreUnavailable`: единственный вызывающий
    этой функции — `except sqlite3.Error` в `_op` (ниже), так что результат — всегда `StoreUnavailable`, никогда
    не плоский логический `StoreError`."""
    return StoreUnavailable(f"аналитика КДМ-1: {_why(exc)}")


def _iso(dt: datetime) -> str:
    """ISO8601 UTC с фиксированной шириной микросекунд — строки сравнимы лексикографически как время."""
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _question_of(request_json: str) -> str:
    """Текст вопроса из сохранённого `AnalysisRequest` как есть (Правка, раунд 1, Minor «д») — разбор только
    нужного поля, без полной `AnalysisRequest.model_validate_json` (история не должна падать на старой строке,
    если контракт запроса когда-нибудь поменяется; здесь — только чтение для показа владельцу, не валидация)."""
    try:
        return str(json.loads(request_json).get("question", ""))
    except ValueError:
        return ""


@dataclass(frozen=True)
class ConversationMeta:
    """Строка списка диалогов (GET .../conversations): видимые технологу поля, без внутренних стадий заданий."""

    id: str
    owner_login: str
    mode: Literal["reference", "deviation_review"]
    created_at: datetime
    last_activity_at: datetime
    corpus_version: str


@dataclass(frozen=True)
class RequestRecord:
    """Строка задания: стадия, видимый результат (если есть) и внутренние ссылки на преданализ/пакет
    доказательств — последние два наружу (клиенту) не отдаются как есть, их заворачивает `rendering.py`.

    `question` — дополнение Task 4, правка раунд 1 (решение контроллера, Minor «д»): исходный текст вопроса
    (`AnalysisRequest.question`), как хранится, без изменений. Отдаётся только владельцу диалога (проверка —
    на вызывающем, `service.py`), в историю `GET conversations/{id}` — рядом с `response`, не внутри
    `JobResponse` (закрытый контракт `job-response.schema.json`, лишнего поля там быть не может)."""

    id: str
    conversation_id: str
    owner_login: str
    client_request_id: str
    state: JobState
    stage_label: str
    corpus_version: str
    created_at: datetime
    response: JobResponse | None
    question: str = ""


class AnalyticsStore:
    """Состояние аналитики на `ANALYTICS_STATE_PATH`. Конструктор ничего не открывает (Договор Role).

    Поля для здоровья (как у `AccountStore`): `last_error` — класс и код последней ошибки (операции или
    открытия), `read_failures`/`write_failures`/`op_failures` — сбоев подряд (снимает удачная операция того же
    вида), `ok`/`checked_at` — итог последнего `ping()`.
    """

    def __init__(self, db_path: str, *, clock: Callable[[], float] | None = None):
        self.path = db_path
        self.clock: Callable[[], float] = time.time if clock is None else clock
        self.last_error: str | None = None
        self.ok = False
        self.checked_at: datetime | None = None
        self.read_failures = 0
        self.write_failures = 0

    @property
    def op_failures(self) -> int:
        return self.read_failures + self.write_failures

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), tz=timezone.utc)

    # --- SQLite: соединение на операцию (приём AccountStore._connect/_op) ---

    def _connect(self, create: bool = False) -> sqlite3.Connection:
        uri = f"{Path(self.path).absolute().as_uri()}?mode={'rwc' if create else 'rw'}"
        return sqlite3.connect(uri, uri=True, timeout=SQLITE_TIMEOUT_S, isolation_level=None)

    @contextmanager
    def _op(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        """Соединение настоящей операции. Сбой SQLite — StoreError, счётчик read/write_failures растёт;
        отказ по правилам (StoreError/ValueError, брошенный вызывающим кодом внутри `with`) счётчики не
        трогает и идёт дальше как есть — не перехватывается здесь."""
        wrote = False
        try:
            db = self._connect()
            try:
                yield db
                wrote = db.total_changes > 0 and not db.in_transaction
            finally:
                db.close()                  # незавершённая транзакция откатывается при закрытии
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

    # --- открытие, миграция, пульс ---

    @staticmethod
    def _migrate_legacy_v1(db: sqlite3.Connection) -> None:
        """V1 (таблицы есть, но без `analytics_schema`/`analytics_heartbeat` и без `requests.updated_at`) →
        текущая версия: добавить колонку и заполнить её `created_at` (точный момент перехода неизвестен),
        затем — остальные таблицы V2 (`CREATE … IF NOT EXISTS`, безопасно повторить) и строка версии."""
        cols = {row[1] for row in db.execute("PRAGMA table_info(requests)")}
        if "updated_at" not in cols:
            db.execute("ALTER TABLE requests ADD COLUMN updated_at TEXT")
            db.execute("UPDATE requests SET updated_at = created_at WHERE updated_at IS NULL")
        for stmt in _SCHEMA_CURRENT:
            db.execute(stmt)
        db.execute("INSERT INTO analytics_schema (id, version) VALUES (1, ?)"
                  " ON CONFLICT (id) DO UPDATE SET version = excluded.version", (CURRENT_SCHEMA_VERSION,))

    def open(self) -> bool:
        """`CREATE TABLE IF NOT EXISTS` всех таблиц модуля и миграция версии схемы, одной транзакцией. Пустая
        база и свежая база с уже текущей версией — один и тот же путь (идемпотентно, повтор open() не бросает и
        не меняет данных); база версии 1 (таблицы есть, `analytics_schema` нет) — путь миграции. `False` — сбой,
        причина — `self.last_error`; вызывается в потоке из lifespan шлюза."""
        try:
            db = self._connect(create=True)
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("BEGIN IMMEDIATE")
                has_meta = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'analytics_schema'").fetchone()
                has_requests = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'requests'").fetchone()
                if has_meta is None and has_requests is not None:
                    self._migrate_legacy_v1(db)
                else:
                    for stmt in _SCHEMA_CURRENT:
                        db.execute(stmt)
                    db.execute("INSERT INTO analytics_schema (id, version) VALUES (1, ?)"
                              " ON CONFLICT (id) DO NOTHING", (CURRENT_SCHEMA_VERSION,))
                db.execute("COMMIT")
            finally:
                db.close()
        except Exception as exc:
            self.last_error = _why(exc)
            return False
        self.last_error = None
        return True

    def ping(self) -> None:
        """Пульс — настоящая запись с COMMIT (как `AccountStore.ping`): итог в `self.ok`/`self.checked_at`/
        `self.last_error`, не бросает и не возвращает значения. Сбои операций (`op_failures`) не трогает."""
        now = self._now()
        try:
            db = self._connect()
            try:
                db.execute("SELECT count(*) FROM conversations").fetchone()
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO analytics_heartbeat (id, at) VALUES (1, ?)"
                          " ON CONFLICT (id) DO UPDATE SET at = excluded.at", (_iso(now),))
                db.execute("COMMIT")
            finally:
                db.close()
        except Exception as exc:
            self.ok = False
            self.last_error = _why(exc)
            self.checked_at = now
            return
        self.ok = True
        self.last_error = None
        self.checked_at = now

    # --- диалоги ---

    def create_conversation(self, identity: RequestIdentity, mode: Literal["reference", "deviation_review"],
                            corpus_version: str) -> str:
        """Новый диалог, владелец — `identity.login`. Возвращает `conversation_id`."""
        if mode not in ("reference", "deviation_review"):
            raise ValueError("mode: reference или deviation_review")
        conversation_id = secrets.token_hex(16)
        now = _iso(self._now())
        with self._op(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO conversations (id, owner_login, mode, created_at, last_activity_at,"
                      " corpus_version) VALUES (?, ?, ?, ?, ?, ?)",
                      (conversation_id, identity.login, mode, now, now, corpus_version))
            db.execute("COMMIT")
        return conversation_id

    def list_conversations(self, identity: RequestIdentity) -> list[ConversationMeta]:
        """Диалоги текущего владельца, новые первыми. Чужие диалоги сюда не попадают никогда (мастер-промпт
        §6: «пользователь видит свои диалоги; чужие — только при конкретном действующем праве») — право на
        чужой диалог проверяет отдельно `reviews.py`/`service.py`, не эта выборка."""
        with self._op() as db:
            rows = db.execute(
                "SELECT id, owner_login, mode, created_at, last_activity_at, corpus_version FROM conversations"
                " WHERE owner_login = ? ORDER BY last_activity_at DESC, id DESC", (identity.login,)).fetchall()
        return [ConversationMeta(id=r[0], owner_login=r[1], mode=r[2], created_at=_parse_iso(r[3]),
                                 last_activity_at=_parse_iso(r[4]), corpus_version=r[5]) for r in rows]

    def conversation_owner(self, conversation_id: str) -> str | None:
        """Логин владельца диалога или `None`, если диалога нет — чужой `id` не подтверждает существование
        ресурса (мастер-промпт §10): вызывающий обязан явно сравнить результат с `identity.login` сам, эта
        функция прав не проверяет."""
        with self._op() as db:
            row = db.execute("SELECT owner_login FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return None if row is None else row[0]

    # --- задания ---

    def create_request(self, identity: RequestIdentity, conversation_id: str,
                       request: AnalysisRequest) -> tuple[str, bool]:
        """Новое задание по `AnalysisRequest`, либо — при повторе `(identity.login, conversation_id,
        request.client_request_id)` — существующее: `(request_id, created)`, `created=False` на повторе (без
        второго списания и без второго задания, ОСОБЫЙ ФОКУС ПРОВЕРКИ №3). Чужой `conversation_id` —
        `StoreError`/отказ, не молчаливое создание не там (один и тот же текст для «нет диалога» и «диалог
        чужой» — без оракула)."""
        request_id = secrets.token_hex(16)
        now = _iso(self._now())
        with self._op(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT owner_login, corpus_version FROM conversations WHERE id = ?",
                             (conversation_id,)).fetchone()
            if row is None or row[0] != identity.login:
                raise StoreError("аналитика КДМ-1: диалог не найден")
            corpus_version = row[1]
            cur = db.execute(
                "INSERT INTO requests (id, conversation_id, owner_login, client_request_id, state, stage_label,"
                " corpus_version, request_json, created_at, updated_at) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?,"
                " ?) ON CONFLICT (owner_login, conversation_id, client_request_id) DO NOTHING",
                (request_id, conversation_id, identity.login, request.client_request_id, _STAGE_QUEUED,
                 corpus_version, request.model_dump_json(), now, now))
            if cur.rowcount == 1:
                final_id, created = request_id, True
                db.execute("UPDATE conversations SET last_activity_at = ? WHERE id = ?", (now, conversation_id))
            else:
                existing = db.execute(
                    "SELECT id FROM requests WHERE owner_login = ? AND conversation_id = ? AND"
                    " client_request_id = ?", (identity.login, conversation_id, request.client_request_id)
                ).fetchone()
                final_id, created = existing[0], False
            db.execute("COMMIT")
        return final_id, created

    def get_request(self, request_id: str) -> RequestRecord | None:
        """Запись задания по id или `None`. Прав не проверяет (как `conversation_owner`) — сравнение с
        `identity` делает вызывающий."""
        with self._op() as db:
            row = db.execute(
                "SELECT id, conversation_id, owner_login, client_request_id, state, stage_label, corpus_version,"
                " created_at, response_json, request_json FROM requests WHERE id = ?", (request_id,)).fetchone()
        if row is None:
            return None
        response = None if row[8] is None else JobResponse.model_validate_json(row[8])
        return RequestRecord(id=row[0], conversation_id=row[1], owner_login=row[2], client_request_id=row[3],
                             state=row[4], stage_label=row[5], corpus_version=row[6],
                             created_at=_parse_iso(row[7]), response=response, question=_question_of(row[9]))

    def list_requests(self, conversation_id: str) -> list[RequestRecord]:
        """Дополнение Task 4 к `docs/kdm1/INTERFACES.md` (приём `knowledge.all_ids`, Task 1: осознанное
        добавление сверх выданного интерфейса, не изменение уже описанной сигнатуры — задокументировано там же).
        Задания диалога по возрастанию `created_at` (история — в порядке «старые первыми», как чат) — нужно
        `gateway/analytics_api.py` (GET `conversations/{id}`, веб `ConversationDetail.requests`, Task 6):
        `get_request` отдаёт одно задание по его собственному id, но у диалога целиком нет листинга без этого
        метода, хотя индекс `requests_conversation` ровно под такой запрос уже существовал. Прав не проверяет
        (как `get_request`/`conversation_owner`) — сравнение `conversation_id` с `identity` делает вызывающий
        (`service.py`) через `conversation_owner` раньше этого вызова; чужой/несуществующий `conversation_id` —
        пустой список, не отказ."""
        with self._op() as db:
            rows = db.execute(
                "SELECT id, conversation_id, owner_login, client_request_id, state, stage_label, corpus_version,"
                " created_at, response_json, request_json FROM requests WHERE conversation_id = ?"
                " ORDER BY created_at, id", (conversation_id,)).fetchall()
        result = []
        for row in rows:
            response = None if row[8] is None else JobResponse.model_validate_json(row[8])
            result.append(RequestRecord(id=row[0], conversation_id=row[1], owner_login=row[2],
                                        client_request_id=row[3], state=row[4], stage_label=row[5],
                                        corpus_version=row[6], created_at=_parse_iso(row[7]), response=response,
                                        question=_question_of(row[9])))
        return result

    def set_preanalysis(self, request_id: str, preanalysis: Preanalysis) -> None:
        """Сохранить результат координатора и перевести стадию в `preanalysis.decision`-производную: `ready` →
        `retrieving` (путь через `llm_adapter.py` продолжается), прочие решения → `validating` (путь обходит
        `llm_adapter.py` — «терминальные для LLM-пути» в докстринге модуля, не для стадии задания: итоговую
        терминальную стадию и `JobResponse` даёт `publish_response`). Задание уже отменено/завершено —
        преданализ сохраняется (для журнала), стадия не трогается."""
        if preanalysis.decision == "ready":
            next_state, stage_label = "retrieving", _STAGE_RETRIEVING
        else:
            next_state, stage_label = "validating", _SKIP_LLM_STAGE_LABEL[preanalysis.decision]
        now = _iso(self._now())
        payload = preanalysis.model_dump_json()
        with self._op(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state, response_json FROM requests WHERE id = ?", (request_id,)).fetchone()
            if row is None:
                db.execute("COMMIT")
                return
            if row[0] not in JOB_TERMINAL_STATES and row[1] is None:
                db.execute("UPDATE requests SET preanalysis_json = ?, state = ?, stage_label = ?, updated_at = ?"
                          " WHERE id = ?", (payload, next_state, stage_label, now, request_id))
            else:
                db.execute("UPDATE requests SET preanalysis_json = ? WHERE id = ?", (payload, request_id))
            db.execute("COMMIT")

    def advance_stage(self, request_id: str, state: JobState, stage_label: str) -> bool:
        """Сменить стадию задания; `False` — задание уже в терминальной стадии, отменено или ответ уже
        опубликован (гонка стадий, §10: «версия/права между поиском и выдачей проверяются повторно»), тогда
        вызывающий не публикует результат дальше."""
        now = _iso(self._now())
        with self._op(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state, response_json FROM requests WHERE id = ?", (request_id,)).fetchone()
            if row is None or row[0] in JOB_TERMINAL_STATES or row[1] is not None:
                db.execute("COMMIT")
                return False
            db.execute("UPDATE requests SET state = ?, stage_label = ?, updated_at = ? WHERE id = ?",
                      (state, stage_label, now, request_id))
            db.execute("COMMIT")
        return True

    def publish_response(self, request_id: str, response: JobResponse) -> bool:
        """Атомарно сохранить итоговый `JobResponse` и перевести задание в терминальную стадию
        (`models.JOB_TERMINAL_STATES`) — публикация ответа одной транзакцией (мастер-промпт §10: «публикация
        ответа и пакета атомарна»; сам пакет доказательств публикует `save_evidence_pack` отдельно, он
        неизменяем независимо от ответа). `False` — задание уже отменено/завершено другим путём (терминальная
        стадия или уже опубликованный ответ), ответ не публикуется (не перезатирает чужую публикацию, ОСОБЫЙ
        ФОКУС ПРОВЕРКИ №3: конкурентная отмена не теряет и не повторяет оплату)."""
        now = _iso(self._now())
        with self._op(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state, response_json FROM requests WHERE id = ?", (request_id,)).fetchone()
            if row is None or row[0] in JOB_TERMINAL_STATES or row[1] is not None:
                db.execute("COMMIT")
                return False
            db.execute("UPDATE requests SET response_json = ?, state = ?, stage_label = ?, updated_at = ?"
                      " WHERE id = ?", (response.model_dump_json(), response.state, response.stage_label, now,
                                        request_id))
            db.execute("COMMIT")
        return True

    def cancel_request(self, identity: RequestIdentity, request_id: str) -> tuple[bool, str | None]:
        """Отменить своё незавершённое задание; чужое или уже терминальное — `(False, …)`, без изменений.
        Возвращает `(отменено ли сейчас, стадия ДО перехода)` — обе части одной и той же транзакции (Правка,
        раунд 1, Important 4): вызывающий (`service.cancel_request`) решает, прерывать ли фоновую
        `asyncio.Task`, по стадии НЕ отдельным более ранним чтением (окно гонки — задание успевает перейти в
        `generating`/`validating` между снимком и отменой), а по этому значению. `stage_before` — `None`, только
        если задания нет/оно чужое; для уже терминального своего задания — сама терминальная стадия (не `None`),
        чтобы вызывающий не путал «чужое/нет» с «уже завершено»."""
        now = _iso(self._now())
        with self._op(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT owner_login, state, response_json FROM requests WHERE id = ?",
                             (request_id,)).fetchone()
            if row is None or row[0] != identity.login:
                db.execute("COMMIT")
                return False, None
            stage_before = row[1]
            if row[1] in JOB_TERMINAL_STATES or row[2] is not None:
                db.execute("COMMIT")
                return False, stage_before
            db.execute("UPDATE requests SET state = 'cancelled', stage_label = ?, updated_at = ? WHERE id = ?",
                      (_STAGE_CANCELLED, now, request_id))
            db.execute("COMMIT")
        return True, stage_before

    def reap_unfinished_after_restart(self) -> int:
        """При открытии после перезапуска — все задания не в терминальной стадии и без опубликованного ответа
        переводятся в `failed` с причиной «перезапуск core» (мастер-промпт §10: «неизвестно оплаченный вызов
        автоматически не повторяется»). Возвращает число затронутых заданий — для строки журнала запуска."""
        now = _iso(self._now())
        placeholders = ", ".join("?" for _ in JOB_TERMINAL_STATES)
        with self._op(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute(
                f"UPDATE requests SET state = 'failed', stage_label = ?, updated_at = ? WHERE state NOT IN"
                f" ({placeholders}) AND response_json IS NULL", (_STAGE_RESTARTED, now, *JOB_TERMINAL_STATES))
            db.execute("COMMIT")
        return cur.rowcount

    # --- пакеты доказательств ---

    def save_evidence_pack(self, request_id: str, pack: tuple[tuple[str, dict], ...]) -> str:
        """Сохранить неизменяемый пакет доказательств задания, вернуть его id. Повторная запись для того же
        `request_id` — новый `evidence_pack_id` (старый не переписывается, он уже мог попасть в ответ).
        Повторный `item_id` внутри одного пакета — нарушение первичного ключа, вся вставка (включая уже
        вставленные элементы того же вызова) откатывается закрытием соединения — пакет либо весь, либо
        не создаётся вовсе."""
        pack_id = secrets.token_hex(16)
        now = _iso(self._now())
        with self._op(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO evidence_packs (id, request_id, created_at) VALUES (?, ?, ?)",
                      (pack_id, request_id, now))
            for item_id, payload in pack:
                db.execute("INSERT INTO evidence_items (pack_id, item_id, data_json) VALUES (?, ?, ?)",
                          (pack_id, item_id, json.dumps(payload, ensure_ascii=False)))
            db.execute("COMMIT")
        return pack_id

    def get_evidence_item(self, evidence_pack_id: str, item_id: str) -> dict | None:
        """Один элемент пакета по id, или `None` — ни пакета, ни элемента; права запроса (чей это пакет)
        проверяет вызывающий (GET .../evidence/{id}, `service.py`)."""
        with self._op() as db:
            row = db.execute("SELECT data_json FROM evidence_items WHERE pack_id = ? AND item_id = ?",
                             (evidence_pack_id, item_id)).fetchone()
        return None if row is None else json.loads(row[0])

    def evidence_pack_owner(self, evidence_pack_id: str) -> str | None:
        """Дополнение Task 4, правка раунд 1 (Important 3): `request_id`, которому принадлежит пакет, или
        `None` — пакета нет. `get_evidence_item` сам это не проверяет (докстринг выше) — цепочка прав
        мастер-промпта §10 («пакет принадлежит заданию, задание — диалогу, диалог — identity») требует сверить
        ИМЕННО эту связь: составной `evidence_id` клиента несёт `request_id` и `pack_id` раздельно, и без этой
        проверки подмена `pack_id` на чужой при своём `request_id` проходила бы (свой `request_id` подтверждает
        владение заданием, но не то, что НАЗВАННЫЙ пакет — пакет ИМЕННО этого задания)."""
        with self._op() as db:
            row = db.execute("SELECT request_id FROM evidence_packs WHERE id = ?", (evidence_pack_id,)).fetchone()
        return None if row is None else row[0]

    # --- хранение ---

    def sweep_retention(self, now: datetime, *, history_days: float, evidence_days: float) -> tuple[int, int]:
        """Удалить диалоги/задания старше `history_days` и пакеты доказательств старше `evidence_days`
        (`AnalyticsSettings`, мастер-промпт §9: «история, экспорты и резервные копии имеют … срок хранения»).
        Возвращает (число удалённых диалогов, число удалённых пакетов) — для журнала; не удаляет записи с
        действующим назначенным правом согласования, если `reviews.py` (Task 7) их помечает отдельно."""
        history_cutoff = _iso(now - timedelta(days=history_days))
        evidence_cutoff = _iso(now - timedelta(days=evidence_days))
        with self._op(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            conv_ids = [r[0] for r in db.execute(
                "SELECT id FROM conversations WHERE last_activity_at < ?", (history_cutoff,)).fetchall()]
            deleted_conversations = 0
            if conv_ids:
                marks = ", ".join("?" for _ in conv_ids)
                db.execute(f"DELETE FROM requests WHERE conversation_id IN ({marks})", conv_ids)
                deleted_conversations = db.execute(
                    f"DELETE FROM conversations WHERE id IN ({marks})", conv_ids).rowcount
            pack_ids = [r[0] for r in db.execute(
                "SELECT id FROM evidence_packs WHERE created_at < ?", (evidence_cutoff,)).fetchall()]
            deleted_packs = 0
            if pack_ids:
                marks = ", ".join("?" for _ in pack_ids)
                db.execute(f"DELETE FROM evidence_items WHERE pack_id IN ({marks})", pack_ids)
                deleted_packs = db.execute(
                    f"DELETE FROM evidence_packs WHERE id IN ({marks})", pack_ids).rowcount
            db.execute("COMMIT")
        return deleted_conversations, deleted_packs
