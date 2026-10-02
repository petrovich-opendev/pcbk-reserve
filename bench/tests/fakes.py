"""Подставные шлюз, стенд и часы для тестов прогонщика (А5); импорт явный: from fakes import ….

- FakeChat — шлюз всех мест сразу: view(n) — клиент места n с интерфейсом GatewayChat. Честный Конструктор (вопрос →
  поиск и список → черновик → на согласие save_agent) и честный агент данных; задержки хода (delay_s по месту, minutes
  — всем), сон места до wake (asleep_until_wake), удалённые сессии (abort → 404), ход без конца (never_idle), сбой
  места (fail_place — на new_session и send), первый send с отказом (fail_first_send), отказ прокси в каждом ходе
  (error — код pcbk_* в responseBody; error_place — только у этих мест), B3 с уточнением (b3_asks), место засыпает
  после send (fall_asleep_after_send), сбой ожидания хода (fail_wait — GatewayError из wait_idle), on_send(место, n) —
  после n-й реплики, ignore_aborts — столько первых abort место не видит, slow_new_session — пауза перед сессией,
  stall_after_turns — после стольких ходов сессии места следующий ход не кончается.
  Журнал calls, wakes, aborted, sessions; wakes_while_waiting(n) — пробуждения, пока у места нет идущего хода.
  advance=FakeClock — задержка идёт по общим подставным часам шагами FAKE_STEP_S с короткой настоящей паузой
  (m16: ждущее место видит, как идут часы, и будится по clock).
- FakeStand — стенд: блок run (расход — по сессиям привязанных FakeChat и cost_per_run группы), вызывающие, модели
  мест, mcp=connected через mcp_after_s, единицы каталога, агенты мест (bench-uzel есть с начала), архив,
  saved_after_abort, visible_after_s, archive_delay_s (архивный агент ещё виден в «Мои агенты» — перезагрузка
  места), строки сторожа, калькулятор (calc_rate — столько первых ответов «rate»; calc_status — {тег: статус item}
  для tag_now/tag_period: unlisted, unknown, no_data …, Б3); строки журнала: rows_none — ни
  одной, rows_skip_bridge — без моста; health_advance=(FakeClock, с) — /health/llm двигает часы;
  health_fail_after_gone — после первого ответа без блока run /health/llm бросает исключение; health_fail_calls —
  вызовы /health/llm с этими номерами (с 1) бросают исключение. Для стенда «агент = калькулятор» (Д9б, Б1):
  calc_answers — ответы калькулятора по инструментам ({инструмент: ответ | функция(args) → ответ}); matches — каталог
  поиска (catalog_search отдаёт совпадения, в имени которых есть запрос, без регистра; [] — ничего, m-7);
  search_status — статус ответа поиска (не ok — отказ без совпадений, учение — error); seen_tags — теги, которые
  стенд видел во входах калькулятора и отдал в его ответах (положительный контроль «тегов нет в выводе»);
  agent_files — {(место, имя): текст файла агента}.
- FakeClock — подставные часы: mono, now (с поясом), sleep двигает время.
- cfg(…), runner(…) — фабрики; out по умолчанию — свой каталог под OUT_BASE (автофикстура test_runner — tmp_path).
  runner(kill_after_session_create=True) — смерть процесса сразу после создания первой сессии (m14): без abort и строк
  конца, замок снимает close() (как система при смерти процесса).
- write_config(tmp, **patch), fake_stand_raising(exc), T0, M1–M4, now(), iso().
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import itertools
import json
import math
import re
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from helpers import CARDS
from pcbk_bench.cards import CardSet, load_cards
from pcbk_bench.config import BenchConfig
from pcbk_bench.gateway import GatewayError, WorkplaceNotReady
from pcbk_bench.numbers import tag_tokens
from pcbk_bench.runner import Runner
from pcbk_bench.scenarios import BENCH_AGENT, BY_ID, REPS, RUN_TIMEOUT_S
from pcbk_bench.student import CONSENT_RE, render_draft

M1 = "pcbk/z-ai/glm-5.3"
M2 = "pcbk/deepseek/deepseek-v4.1-flash"
M3 = "pcbk/google/gemini-3.8-flash"
M4 = "pcbk/openai/gpt-6-sol"
T0 = datetime(2026, 10, 8, 5, 0, tzinfo=timezone.utc)
STAMP = "2026-10-02T11:59:40+05:00"
FAKE_UNITS = {"20FAKE_101_PV": "°C", "20FAKE_102_PV": "°C", "20FAKE_103_PV": "°C", "20FAKE_104_PV": "бар",
              "20FAKE_105_PV": "т/ч", "20FAKE_106_SP": "т/ч", "20FAKE_107_PV": "°C"}
BUILTIN = ("konstruktor", "pomoshnik", BENCH_AGENT)
OUT_BASE: list[Path | None] = [None]
_RUNNERS: list[Runner] = []


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.isoformat()


@functools.cache
def twin_cards() -> CardSet:
    return load_cards(CARDS)


class FakeClock:
    """Общие подставные часы: mono() и now() идут только от sleep()/advance()."""

    def __init__(self, start: datetime = T0):
        self._start = start
        self._t = 0.0
        self._lock = threading.Lock()

    def mono(self) -> float:
        with self._lock:
            return 10_000.0 + self._t

    def now(self) -> datetime:
        with self._lock:
            return self._start + timedelta(seconds=self._t)

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self._t += max(0.0, seconds)

    advance = sleep


# --- подставной шлюз ---

def _tool(n: int, name: str, args: dict, output: dict) -> dict:
    return {"type": "tool", "tool": name, "callID": f"call_{n}",
            "state": {"status": "completed", "input": args, "output": json.dumps(output, ensure_ascii=False)}}


def _search_out(tags: list[str]) -> dict:
    return {"tool": "catalog_search", "status": "ok", "notes": [], "cache": "none", "query": "пар", "similar": False,
            "matches": [{"tag": t, "description": "Описание тега стенда", "unit": FAKE_UNITS.get(t, "бар"),
                         "kind": "analog", "min_eu": 0.0, "max_eu": None, "live": True,
                         "description_truncated": False} for t in tags], "total": len(tags)}


def _now_out(tag: str) -> dict:
    return {"tool": "tag_now", "status": "ok", "notes": [], "asof": STAMP, "tz": "+05:00", "source": "Live",
            "items": [{"tag": tag, "status": "ok", "unit": FAKE_UNITS.get(tag, "бар"), "value": 3.42, "time": STAMP,
                       "age_s": 20, "quality": "хорошее"}]}


def _period_out(tag: str) -> dict:
    return {"tool": "tag_period", "status": "ok", "notes": [], "source": "Summary",
            "period": {"start": "2026-10-02T06:00:00+05:00", "end": "2026-10-02T14:00:00+05:00",
                       "preset": "prev_shift"},
            "items": [{"tag": tag, "status": "ok", "unit": FAKE_UNITS.get(tag, "бар"), "avg": 3.4, "min": 3.1,
                       "max": 3.7}]}


def _status_out(tool: str, tag: str, status: str) -> dict:
    """Ответ службы по тегу со статусом item (calc_status, Б3): unlisted/unknown — отказ «none» с items (service.py
    _other_item), no_data — partial без чисел, ok — обычный ответ инструмента."""
    if status == "ok":
        return _now_out(tag) if tool == "tag_now" else _period_out(tag)
    item = {"tag": tag, "status": status, "text": "служба: " + status}
    head = "refused" if status in ("unlisted", "unknown") else "partial"
    return {"tool": tool, "status": head, "notes": [], "items": [item]}


_FOUND = ["20FAKE_101_PV", "20FAKE_102_PV", "20FAKE_103_PV", "20FAKE_104_PV"]
_LIST = ("Нашёл похожие теги:\n" + "\n".join(f"- {t} — Описание тега стенда — {FAKE_UNITS[t]}" for t in _FOUND[:3])
         + "\nОписания одинаковые. Какой ваш?")
_DRAFT = render_draft(title="Сушильная часть", name="bench-a1b2c3", node="Тестовая сушильная часть",
                      purpose="Показывать текущие значения и сводку за смену по узлу",
                      tags=[("20FAKE_101_PV", "Описание тега стенда", "°C"),
                            ("20FAKE_104_PV", "Описание тега стенда", "бар")],
                      questions=["что сейчас с давлением пара", "что было за прошлую смену"])
_PROMPT = ("Ты — помощник технолога по узлу «Тестовая сушильная часть». Показывай текущие значения и сводку за смену "
           "по температуре и давлению пара; числа — только из инструментов, нормативов не называй. ") * 4


@dataclass
class _Session:
    id: str
    place: int
    agent: str | None = None
    first: str | None = None
    messages: list = field(default_factory=list)
    busy: bool = False
    aborted: bool = False
    delayed: bool = False
    pending: tuple | None = None
    stage: str = "start"
    turns: int = 0
    stalled: bool = False


class FakeChat:
    """Подставной шлюз всех мест (см. шапку модуля)."""

    FAKE_STEP_S = 60.0
    REAL_STEP_S = 0.002

    def __init__(self, *, never_idle: bool = False, asleep_until_wake=(), deleted_sessions=(),
                 fail_place: dict | None = None, delay_s: dict | None = None, minutes: float | None = None,
                 advance: FakeClock | None = None, error: str | None = None, b3_asks: bool = False,
                 fail_first_send: Exception | None = None, fall_asleep_after_send=(), error_place: dict | None = None,
                 fail_wait: dict | None = None, on_send=None, ignore_aborts: int = 0,
                 slow_new_session: dict | None = None, stall_after_turns: dict | None = None):
        self.never = never_idle
        self.asleep = set(asleep_until_wake)
        self.deleted = set(deleted_sessions)
        self.fail_place = dict(fail_place or {})
        self.delay_s = dict(delay_s or {})
        self.default_delay = (minutes or 0.0) * 60.0
        self.clock = advance
        self.error = error
        self.error_place = dict(error_place or {})
        self.fail_wait = dict(fail_wait or {})
        self.on_send = on_send
        self.slow_new_session = dict(slow_new_session or {})
        self.stall_after_turns = dict(stall_after_turns or {})
        self.ignore_aborts = ignore_aborts
        self._sends = 0
        self.b3_asks = b3_asks
        self._fail_first_send = fail_first_send
        self.fall_asleep_after_send = set(fall_asleep_after_send)
        self.calls: list[tuple] = []
        self.wakes: Counter = Counter()
        self._idle_wakes: Counter = Counter()
        self.aborted: list[str] = []
        self.abort_places: list[tuple[int, str]] = []
        self.sessions: list[str] = []
        self._sess: dict[str, _Session] = {}
        self._sent: list[tuple[int, str, float]] = []
        self._ids = itertools.count(1)
        self._parts = itertools.count(1)
        self._lock = threading.RLock()
        self.stand: FakeStand | None = None
        self.cards: CardSet | None = None
        self._mono = time.monotonic

    @classmethod
    def honest(cls, **kw: Any) -> FakeChat:
        return cls(**kw)

    @classmethod
    def never_idle(cls, **kw: Any) -> FakeChat:
        return cls(never_idle=True, **kw)

    def bind(self, stand: FakeStand | None, cards: CardSet, mono) -> None:
        self.stand, self.cards, self._mono = stand, cards, mono

    def view(self, place: int) -> PlaceChat:
        return PlaceChat(self, place)

    def wakes_while_waiting(self, place: int) -> int:
        return self._idle_wakes[place]

    def sent_at(self, place: int, kind: str) -> float | None:
        with self._lock:
            return next((t for p, k, t in self._sent if p == place and k == kind), None)

    def session_info(self) -> list[tuple[str, int, str | None, str | None]]:
        with self._lock:
            return [(s.id, s.place, s.agent, s.first) for s in self._sess.values()]

    # --- вызовы мест ---

    def _check(self, place: int, conversation: bool = False) -> None:
        if place in self.asleep:
            raise WorkplaceNotReady(503, "WorkplaceNotReady")
        if conversation and place in self.fail_place:
            raise self.fail_place[place]

    def _wake(self, place: int) -> None:
        with self._lock:
            self.calls.append(("wake", place))
            self.wakes[place] += 1
            if not any(s.busy for s in self._sess.values() if s.place == place):
                self._idle_wakes[place] += 1
            self.asleep.discard(place)

    def _new_session(self, place: int) -> str:
        if place in self.slow_new_session:
            time.sleep(self.slow_new_session[place])
        with self._lock:
            self.calls.append(("new_session", place))                  # и неудачная попытка — в журнале
        self._check(place, conversation=True)
        with self._lock:
            sid = f"ses_{next(self._ids):026d}"
            self._sess[sid] = _Session(sid, place)
            self.sessions.append(sid)
            return sid

    def _send(self, place: int, session: str, text: str, agent: str) -> None:
        self._check(place, conversation=True)
        with self._lock:
            if self._fail_first_send is not None:
                exc, self._fail_first_send = self._fail_first_send, None
                raise exc
            s = self._sess[session]
            self.calls.append(("send", session))
            if s.first is None:
                s.first, s.agent = text, agent
            self._sent.append((place, agent if agent in BUILTIN else "bridge", self._mono()))
            user = {"info": {"id": f"msg_u{next(self._parts)}", "role": "user", "sessionID": session,
                             "time": {"created": int(time.time() * 1000)}}, "parts": [{"type": "text", "text": text}]}
            s.messages.append(user)
            s.busy = True
            stalled = sum(1 for m in s.messages if m["info"]["role"] == "user") > self.stall_after_turns.get(
                place, math.inf)
            s.stalled = stalled
            s.pending = None if self.never or stalled else (user["info"]["id"], *self._answer(s, text, agent))
            if place in self.fall_asleep_after_send:
                self.fall_asleep_after_send.discard(place)
                self.asleep.add(place)
            self._sends += 1
            count = self._sends
        if self.on_send is not None:
            self.on_send(place, count)

    def _pass(self, seconds: float, s: _Session) -> None:
        if seconds <= 0:
            return
        if self.clock is not None:
            left = seconds
            while left > 0 and not s.aborted:
                step = min(self.FAKE_STEP_S, left)
                self.clock.sleep(step)
                left -= step
                time.sleep(self.REAL_STEP_S)
            return
        end = time.monotonic() + seconds
        while not s.aborted:
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.01, left))

    def _wait_idle(self, place: int, session: str, timeout_s: float) -> list[dict]:
        self._check(place)
        if place in self.fail_wait:
            raise self.fail_wait[place]
        s = self._sess[session]
        delay = 0.0
        if not s.delayed and s.agent in BUILTIN:
            s.delayed = True
            delay = self.delay_s.get(place, self.default_delay)
        if self.never or s.stalled:
            delay = math.inf
        self._pass(min(delay, max(0.0, timeout_s)), s)
        with self._lock:
            if s.aborted:
                s.busy = False
                return [dict(m) for m in s.messages]
            if delay > timeout_s:
                raise TimeoutError("ход не кончился в срок")
            if s.pending is not None:
                parent, texts, tools, error = s.pending
                s.pending = None
                ms = int(time.time() * 1000)
                info: dict[str, Any] = {"id": f"msg_a{next(self._parts)}", "role": "assistant", "parentID": parent,
                                        "sessionID": session, "time": {"created": ms, "completed": ms}}
                if error:
                    info["error"] = error
                s.messages.append({"info": info, "parts": [*tools, *({"type": "text", "text": t} for t in texts)]})
            s.busy = False
            return [dict(m) for m in s.messages]

    def _abort(self, place: int, session: str) -> None:
        self._check(place)
        with self._lock:
            self.calls.append(("abort", session))
            if session in self.deleted:
                raise GatewayError(404, "NotFound")
            if self.ignore_aborts > 0:                  # abort пришёл раньше реплики — место его не увидело
                self.ignore_aborts -= 1
                return
            self.aborted.append(session)
            self.abort_places.append((place, session))
            s = self._sess.get(session)
            if s is not None:
                if s.busy:
                    ms = int(time.time() * 1000)
                    s.messages.append({"info": {"id": f"msg_a{next(self._parts)}", "role": "assistant",
                                                "time": {"created": ms, "completed": ms},
                                                "error": {"name": "MessageAbortedError", "data": {"message": "x"}}},
                                       "parts": []})
                s.aborted, s.busy = True, False
        if self.stand is not None:
            self.stand.on_abort(place)

    def _status(self, place: int) -> dict:
        self._check(place)
        with self._lock:
            return {s.id: {"type": "busy"} for s in self._sess.values() if s.place == place and s.busy}

    def _agents(self, place: int) -> list[dict]:
        own = self.stand.listing(place) if self.stand is not None else []
        return [{"name": n, "title": n, "purpose": "", "kind": "builtin", "state": "ready"}
                for n in ("pomoshnik", "konstruktor")] + own

    # --- ответы честной модели ---

    def _answer(self, s: _Session, text: str, agent: str) -> tuple[list[str], list[dict], dict | None]:
        code = self.error_place.get(s.place, self.error)
        if code:
            body = json.dumps({"error": {"message": "Отказ прокси", "type": "pcbk", "code": code}})
            return [], [], {"name": "APIError", "data": {"message": "Отказ прокси", "statusCode": 402,
                                                         "isRetryable": False, "responseBody": body}}
        if agent == "konstruktor":
            return self._konstruktor(s, text)
        return self._data(s, text, agent)

    def _konstruktor(self, s: _Session, text: str) -> tuple[list[str], list[dict], dict | None]:
        if s.stage == "start":
            s.stage = "asked"
            return ["Какой узел, что агент должен показывать и на какие вопросы отвечать?"], [], None
        if s.stage == "asked":
            s.stage = "listed"
            return [_LIST], [_tool(next(self._parts), "pcbk_catalog_search", {"query": "пар", "limit": 10},
                                   _search_out(_FOUND))], None
        if s.stage == "listed" or (s.stage == "draft" and not CONSENT_RE.match(text)):
            s.stage = "draft"
            return [_DRAFT], [], None
        if s.stage == "draft":
            s.stage = "saved"
            name = "bench-" + hashlib.sha256(s.id.encode()).hexdigest()[:6]
            args = {"name": name, "title": "Сушильная часть", "node": "Тестовая сушильная часть",
                    "purpose": "Показывать текущие значения и сводку за смену по узлу",
                    "tools": ["catalog_search", "tag_now", "tag_period"], "tags": ["20FAKE_101_PV", "20FAKE_104_PV"],
                    "prompt": _PROMPT, "student_reply": text}
            if self.stand is not None:
                self.stand.add_agent(s.place, name)
            out = {"tool": "save_agent", "status": "ok", "name": name, "title": args["title"], "already": False,
                   "message": "", "problems": [], "notes": []}
            return (["Агент сохранён, он появится в «Мои агенты» через несколько секунд."],
                    [_tool(next(self._parts), "pcbk_save_agent", args, out)], None)
        return ["Готово."], [], None

    def _data(self, s: _Session, text: str, agent: str) -> tuple[list[str], list[dict], dict | None]:
        s.turns += 1
        tags = tag_tokens(text)
        if self.b3_asks and agent == "pomoshnik" and not tags and s.turns == 1:
            return ["Уточните, пожалуйста: какой именно параметр нужен?"], [], None
        if tags:
            tag = tags[0]
            unit = FAKE_UNITS.get(tag, "бар")
            if re.search(r"средн|смен|сутк|максимум|был", text, re.IGNORECASE):
                call = _tool(next(self._parts), "pcbk_tag_period", {"tags": [tag], "period": "prev_shift"},
                             _period_out(tag))
                return [f"Среднее за прошлую смену — 3,40 {unit} ({tag})."], [call], None
            call = _tool(next(self._parts), "pcbk_tag_now", {"tags": [tag]}, _now_out(tag))
            return [f"Сейчас 3,42 {unit} ({tag}, 11:59, качество хорошее)."], [call], None
        if agent == "pomoshnik":
            search = _tool(next(self._parts), "pcbk_catalog_search", {"query": "расход пара", "limit": 10},
                           _search_out(["20FAKE_105_PV"]))
            call = _tool(next(self._parts), "pcbk_tag_now", {"tags": ["20FAKE_105_PV"]}, _now_out("20FAKE_105_PV"))
            return ["Сейчас расход пара — 3,42 т/ч (20FAKE_105_PV, 11:59)."], [search, call], None
        if agent not in BUILTIN:                                   # мост к сохранённому агенту
            call = _tool(next(self._parts), "pcbk_tag_now", {"tags": ["20FAKE_104_PV"]}, _now_out("20FAKE_104_PV"))
            return ["Сейчас 3,42 бар (20FAKE_104_PV, 11:59, качество хорошее)."], [call], None
        return ["Норматива в данных стенда нет — назвать его не могу."], [], None


class PlaceChat:
    """Клиент места n поверх FakeChat — интерфейс GatewayChat."""

    def __init__(self, fake: FakeChat, place: int):
        self.fake = fake
        self.place = place

    def login(self, login: str | None = None, password: str | None = None) -> None:
        with self.fake._lock:
            self.fake.calls.append(("login", self.place))

    def workplace(self) -> dict:
        state = "sleeping" if self.place in self.fake.asleep else "ready"
        return {"place": f"{self.place:02d}", "state": state, "detail": ""}

    def wake(self, timeout: float = 120.0) -> None:
        self.fake._wake(self.place)

    def agents(self) -> list[dict]:
        return self.fake._agents(self.place)

    def new_session(self) -> str:
        return self.fake._new_session(self.place)

    def send(self, session: str, text: str, agent: str) -> None:
        self.fake._send(self.place, session, text, agent)

    def wait_idle(self, session: str, timeout_s: float) -> list[dict]:
        return self.fake._wait_idle(self.place, session, timeout_s)

    def abort(self, session: str) -> None:
        self.fake._abort(self.place, session)

    def session_status(self) -> dict:
        return self.fake._status(self.place)

    def sent_at(self, kind: str) -> float | None:
        return self.fake.sent_at(self.place, kind)


# --- подставной стенд ---

class FakeStand:
    """Подставной стенд (см. шапку модуля)."""

    def __init__(self, *, run: dict | None = None, run_callers=None, place_models: dict | None = None,
                 rows_model: str | None = None, units: dict | None = None, mcp_after_s: dict | None = None,
                 save_answer: dict | None = None, saved_after_abort: dict | None = None,
                 visible_after_s: float = 0.0, cost_per_run: dict | None = None, status_rows: dict | None = None,
                 health: dict | None = None, calc_rate: int = 0, archive_delay_s: float = 0.0,
                 rows_none: bool = False, rows_skip_bridge: bool = False, health_advance: tuple | None = None,
                 health_fail_after_gone: bool = False, health_fail_calls=(), calc_status: dict | None = None,
                 calc_answers: dict | None = None, matches: list | None = None, search_status: str = "ok",
                 agent_files: dict | None = None):
        self.run_over = dict(run or {})
        self.calc_status = dict(calc_status or {})
        self.calc_answers = dict(calc_answers or {})
        self.matches = None if matches is None else list(matches)
        self.search_status = search_status
        self.agent_files = dict(agent_files or {})
        self.seen_tags: set[str] = set()
        self._run_callers = None if run_callers is None else frozenset(run_callers)
        self.place_models = dict(place_models or {})
        self.rows_model = rows_model
        self.units = {**FAKE_UNITS, **(units or {})}
        self.mcp_after_s = dict(mcp_after_s or {})
        self.save_answer = save_answer
        self.saved_after_abort = dict(saved_after_abort or {})
        self.visible_after_s = visible_after_s
        self.cost_per_run = {"create": 0.01, "number": 0.01, "trap": 0.01, **(cost_per_run or {})}
        self.status_rows = {"llm": "ok", "memory": "ok", **(status_rows or {})}
        self.health_extra = dict(health or {})
        self.calc_rate = calc_rate
        self.archive_delay_s = archive_delay_s
        self.rows_none = rows_none
        self.rows_skip_bridge = rows_skip_bridge
        self.health_advance = health_advance
        self.health_fail_after_gone = health_fail_after_gone
        self.health_fail_calls = set(health_fail_calls)
        self.health_calls = 0
        self._gone_seen = False
        self._leaving: dict[tuple[int, str], float] = {}
        self.archived: list[tuple[int, str]] = []
        self.mcp_checks: Counter = Counter()
        self.bench_saves: list[tuple[int, dict]] = []
        self.calc_calls: list[tuple[str, dict]] = []
        self._mcp_first: dict[int, float] = {}
        self._agents: dict[int, dict[str, dict]] = {}
        self._visible: dict[tuple[int, str], float] = {}
        self._lock = threading.Lock()
        self.cfg: BenchConfig | None = None
        self.chats: list[FakeChat] = []
        self._openings: dict[str, str] = {}
        self._mono = time.monotonic

    def bind(self, cfg: BenchConfig, chat: FakeChat, cards: CardSet, mono) -> None:
        self.cfg, self._mono = cfg, mono
        self._openings = {c.opening: sid for sid, c in cards.cards.items()}
        if chat not in self.chats:
            self.chats.append(chat)
        for n in cfg.places:
            if BENCH_AGENT not in self._agents.setdefault(n, {}):
                self._add(n, BENCH_AGENT, 0.0, at_start=True)

    def _add(self, place: int, name: str, delay: float, at_start: bool = False) -> None:
        mono = -math.inf if at_start else self._mono() + delay
        wall = datetime.now(timezone.utc) + timedelta(seconds=delay)
        with self._lock:
            self._agents.setdefault(place, {})[name] = {"name": name, "mono": mono, "visible_at":
                                                        wall.isoformat(timespec="seconds"),
                                                        "created_at": datetime.now(timezone.utc).isoformat()}
            self._visible[(place, name)] = mono

    def add_agent(self, place: int, name: str) -> None:
        """Агент, сохранённый Конструктором: виден через visible_after_s."""
        self._add(place, name, self.visible_after_s)

    def on_abort(self, place: int) -> None:
        name = self.saved_after_abort.get(place)
        if name:
            self._add(place, name, 0.0)

    def listing(self, place: int) -> list[dict]:
        t = self._mono()
        with self._lock:
            live = [{"name": a["name"], "title": a["name"], "purpose": "", "kind": "own",
                     "state": "ready" if t >= a["mono"] else "pending"} for a in self._agents.get(place, {}).values()]
            leaving = [{"name": n, "title": n, "purpose": "", "kind": "own", "state": "ready"}
                       for (p, n), until in self._leaving.items() if p == place and t < until]
        return live + leaving

    def agents_listed(self, place: int) -> list[str]:
        with self._lock:
            return list(self._agents.get(place, {}))

    def visible_at(self, place: int, name: str) -> float:
        return self._visible[(place, name)]

    @property
    def sessions_started(self) -> int:
        return sum(len(c.sessions) for c in self.chats)

    def _group(self, agent: str | None, first: str | None) -> str | None:
        if agent == "konstruktor":
            return "create"
        if agent in ("pomoshnik", BENCH_AGENT) and first in self._openings:
            return BY_ID[self._openings[first]].group
        return None

    def _sessions(self) -> dict[str, tuple[int, str | None, str | None]]:
        out = {}
        for chat in self.chats:
            for sid, place, agent, first in chat.session_info():
                out[sid] = (place, agent, first)
        return out

    def spent(self) -> float:
        return sum(self.cost_per_run[g] for _, agent, first in self._sessions().values()
                   if (g := self._group(agent, first)) is not None)

    # --- Stand ---

    def health_llm(self) -> dict:
        with self._lock:
            self.health_calls += 1
            n = self.health_calls
        if n in self.health_fail_calls:                 # этот вызов /health/llm не читается
            raise RuntimeError("health read failed")
        if self.health_advance is not None:             # /health/llm отвечает долго (часы — подставные)
            clock, seconds = self.health_advance
            clock.sleep(seconds)
        cfg = self.cfg
        run = {"id": cfg.run_id, "cap_usd": cfg.cap_usd, "spent_usd": 0.0, "callers": len(cfg.places),
               "calls_10m": 0, "idle_s": 0.0}
        run.update(self.run_over)
        if self.health_fail_after_gone and run["id"] != cfg.run_id:
            with self._lock:
                seen, self._gone_seen = self._gone_seen, True
            if seen:                                    # блок пропал, и /health/llm больше не читается
                raise RuntimeError("health down")
        run["spent_usd"] = round(float(run["spent_usd"]) + self.spent(), 6)
        return {"run": run, "spent_usd": run["spent_usd"], "key_spent_usd": None, **self.health_extra}

    def run_callers(self) -> frozenset[str]:
        if self._run_callers is not None:
            return self._run_callers
        return frozenset(f"student-{n:02d}" for n in self.cfg.places)

    def place_model(self, place: int) -> str:
        return self.place_models.get(place, self.cfg.places[place])

    def place_mcp_ok(self, place: int) -> bool:
        t = self._mono()
        with self._lock:
            self.mcp_checks[place] += 1
            first = self._mcp_first.setdefault(place, t)
        return t - first >= self.mcp_after_s.get(place, 0.0)

    def status(self) -> dict:
        return {"checks": [{"component": c, "state": s} for c, s in self.status_rows.items()]}

    def _seen(self, answer: dict) -> dict:
        """Теги ответа калькулятора (совпадения поиска и элементы) — в seen_tags."""
        for row in (answer.get("matches") or []) + (answer.get("items") or []):
            if isinstance(row, dict) and isinstance(row.get("tag"), str):
                self.seen_tags.add(row["tag"])
        return answer

    def calc(self, tool: str, args: dict) -> dict:
        self.calc_calls.append((tool, dict(args)))
        self.seen_tags.update(t for t in args.get("tags") or [] if isinstance(t, str))
        if self.calc_rate > 0:
            self.calc_rate -= 1
            return {"tool": tool, "status": "rate", "message": "слишком часто"}
        if tool == "catalog_search" and self.search_status != "ok":
            return {"tool": "catalog_search", "status": self.search_status, "message": "служба: " + self.search_status,
                    "notes": []}
        if tool == "catalog_search" and self.matches is not None:
            q = str(args.get("query", "")).upper()
            found = [dict(m) for m in self.matches if q and q in str(m.get("tag", "")).upper()]
            found = found[:int(args.get("limit") or 10)]
            return self._seen({"tool": "catalog_search", "status": "ok", "notes": [], "query": args.get("query"),
                               "matches": found, "similar": False, "total": len(found)})
        if tool in self.calc_answers:
            answer = self.calc_answers[tool]
            return self._seen(dict(answer(dict(args)) if callable(answer) else answer))
        if tool == "catalog_search":
            q = args.get("query")
            matches = [{"tag": q, "description": "Описание тега стенда", "unit": self.units[q], "kind": "analog"}] \
                if q in self.units else []
            return {"tool": "catalog_search", "status": "ok", "matches": matches, "total": len(matches)}
        status = self.calc_status.get(args["tags"][0]) if tool in ("tag_now", "tag_period") else None
        if status is not None:
            return _status_out(tool, args["tags"][0], status)
        if tool == "tag_now":
            return _now_out(args["tags"][0])
        return _period_out(args["tags"][0])

    def llm_rows(self, sessions: list[str]) -> list[dict]:
        known = self._sessions()
        rows = []
        if self.rows_none:
            return rows
        for i, sid in enumerate(sessions):
            if sid not in known or known[sid][1] is None:
                continue
            if self.rows_skip_bridge and known[sid][1] not in BUILTIN:
                continue
            place, agent, first = known[sid]
            group = self._group(agent, first)
            cost = self.cost_per_run[group] if group else 0.0
            model = self.rows_model or self.cfg.places[place].split("/", 1)[1]
            rows.append({"id": i + 1, "session": sid, "caller": f"student-{place:02d}", "model": model,
                         "status": "ok", "code": "ok", "cost": cost, "cost_est": cost})
        return rows

    def agent_rows(self, place: int) -> list[dict]:
        t = self._mono()
        with self._lock:
            return [{"name": a["name"], "title": a["name"], "state": "visible" if t >= a["mono"] else "saved",
                     "flags": "[]", "created_at": a["created_at"],
                     "visible_at": a["visible_at"] if t >= a["mono"] else None}
                    for a in self._agents.get(place, {}).values()]

    def agent_file(self, place: int, name: str) -> str:
        if (place, name) in self.agent_files:
            return self.agent_files[(place, name)]
        return ("---\nmode: primary\npermission:\n  \"*\": deny\n  pcbk_catalog_search: allow\n  pcbk_tag_now: allow\n"
                "  pcbk_tag_period: allow\n---\n")

    def save_agent(self, place: int, args: dict) -> dict:
        self.bench_saves.append((place, dict(args)))
        if self.save_answer is not None:
            return dict(self.save_answer)
        name = args["name"]
        if name in self.agents_listed(place):
            return {"tool": "save_agent", "status": "ok", "name": name, "already": True}
        self._add(place, name, 0.0)
        return {"tool": "save_agent", "status": "ok", "name": name, "already": False}

    def archive_agent(self, place: int, name: str) -> None:
        t = self._mono()
        with self._lock:
            self.archived.append((place, name))
            self._agents.get(place, {}).pop(name, None)
            if self.archive_delay_s:
                self._leaving[(place, name)] = t + self.archive_delay_s


class _RaisingStand:
    """Стенд, у которого любой вызов бросает exc (fake_stand_raising)."""

    def __init__(self, exc: BaseException):
        self.exc = exc

    def __getattr__(self, name: str):
        def fail(*args: Any, **kwargs: Any) -> Any:
            raise self.exc
        return fail


@contextlib.contextmanager
def fake_stand_raising(exc: BaseException) -> Iterator[None]:
    """Подменяет connect CLI: стенд бросает exc на любом вызове, шлюз — подставной."""
    import pcbk_bench.__main__ as cli

    original = cli.connect

    def connect(config: BenchConfig):
        chat = FakeChat.honest()
        return _RaisingStand(exc), {n: chat.view(n) for n in config.places}

    cli.connect = connect
    try:
        yield
    finally:
        cli.connect = original


# --- фабрики ---

def cfg(**kw: Any) -> BenchConfig:
    timeouts = {**RUN_TIMEOUT_S, **kw.pop("run_timeout_s", {})}
    base: dict[str, Any] = dict(run_id="d8-test", places={7: M1, 8: M2}, reps=dict(REPS), cap_usd=80.0,
                                stop_margin_usd=10.0, window_start=None, out_dir=None, cards=CARDS)
    base.update(kw)
    return BenchConfig(**base, run_timeout_s=timeouts)


def _fresh_out() -> Path:
    base = OUT_BASE[0]
    if base is None:
        return Path(tempfile.mkdtemp(prefix="pcbk-bench-out-"))
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=base))


class _Killed(BaseException):
    """Смерть процесса прогонщика (kill_after_session_create): не Exception — обработчики прогона её не ловят."""


class _KillSwitch:
    def __init__(self) -> None:
        self.armed = False
        self.lock = threading.Lock()


class _KillingChat:
    """Клиент места: после первой созданной сессии любой вызов — _Killed (строка start уже в журнале)."""

    def __init__(self, inner: PlaceChat, switch: _KillSwitch):
        self._inner = inner
        self._switch = switch

    def new_session(self) -> str:
        with self._switch.lock:
            if self._switch.armed:
                raise _Killed
            sid = self._inner.new_session()
            self._switch.armed = True
            return sid

    def __getattr__(self, name: str):
        method = getattr(self._inner, name)

        def call(*args: Any, **kwargs: Any) -> Any:
            if self._switch.armed:
                raise _Killed
            return method(*args, **kwargs)
        return call


class _KillableRunner(Runner):
    def run(self) -> str:
        try:
            return super().run()
        except _Killed:
            return "killed"


def runner(*, out: Path | None = None, chat: FakeChat | None = None, stand: FakeStand | None = None,
           places: dict | None = None, now=None, clock=None, sleep=None, only=None,
           kill_after_session_create: bool = False, **kw: Any) -> Runner:
    """Runner на подставных; kw — поля BenchConfig (run_timeout_s — частично, остальное из RUN_TIMEOUT_S)."""
    c = cfg(places=dict(places or {7: M1, 8: M2}), out_dir=Path(out) if out is not None else _fresh_out(), **kw)
    chat = chat if chat is not None else FakeChat.honest()
    stand = stand if stand is not None else FakeStand()
    cards = twin_cards()
    mono = clock or time.monotonic
    stand.bind(c, chat, cards, mono)
    chat.bind(stand, cards, mono)
    views: dict[int, Any] = {n: chat.view(n) for n in c.places}
    cls = Runner
    if kill_after_session_create:
        switch = _KillSwitch()
        views = {n: _KillingChat(v, switch) for n, v in views.items()}
        cls = _KillableRunner
    r = cls(c, stand, views, cards, sleep=sleep or time.sleep, clock=mono,
            now=now or (lambda: datetime.now(timezone.utc)), only=only)
    r.poll_s = 0.01
    _RUNNERS.append(r)
    return r


def close_runners() -> None:
    """Закрыть все прогонщики теста (П7: пульс и замок не переживают тест)."""
    while _RUNNERS:
        _RUNNERS.pop().close()


def write_config(tmp: Path, **patch: Any) -> Path:
    """Конфиг прогона JSON в tmp/config.json: умолчания и patch поверх (значение None — null)."""
    raw: dict[str, Any] = {"run_id": "d8-test", "places": {"7": M1, "8": M2}, "reps": dict(REPS), "cap_usd": 80.0,
                           "stop_margin_usd": 10.0, "window_start": "2026-10-01T10:00:00+05:00", "out_dir": "out",
                           "cards": str(CARDS), "stand": "stand.json"}
    raw.update(patch)
    path = Path(tmp) / "config.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


__all__ = ["FakeChat", "FakeClock", "FakeStand", "M1", "M2", "M3", "M4", "OUT_BASE", "PlaceChat", "T0", "cfg",
           "close_runners", "fake_stand_raising", "iso", "now", "runner", "twin_cards", "write_config"]
