"""Ручки LLM-прокси: POST /llm/v1/chat/completions (цепочка отказов, учёт, перекачка потока) и GET /health/llm.

Проверки по порядку, первая сработавшая отвечает (Д4а-R12, тексты — LLM_MESSAGES):
1. токен LLM (Bearer без учёта регистра, как Д3б) — иначе 401 в форме OpenAI и строка журнала без токена;
1а. ошибка настройки роли; 2. выключатель; 3. нет ключа OpenRouter; 4. тело — объект JSON;
5. модель — точным ключом role.models; 6. тело собирается заново (rebuild_body);
6а. журнал вызовов не пишется (ledger_error) — 503 pcbk_ledger сразу, без резерва и без потока;
7. у вызывающего уже LLM_CALLER_CONCURRENCY идущих вызовов — busy; 8. доля и общий бюджет (с резервами
   идущих вызовов, Д4а-R13); 9. предел шагов хода — только у запроса с инструментами (step = 0 у прочих).
Отказы 1а–9 пишутся ledger.refused. Шаги 7–10 — один синхронный участок: между budget.refusal() и
ledger.open_call() (begin_call) нет ни одного await, иначе два одновременных запроса прошли бы проверки
раньше, чем любой поставил резерв.

Дальше строка вызова должна лечь на диск до OpenRouter (Д4а-R19, «деньги важнее доступности»): flush
журнала в потоке до LEDGER_DURABLE_S; не легла (срок, LedgerWriteLost, журнал закрыт) — 503 pcbk_ledger, шаг
возвращается, в OpenRouter запрос не уходит.

Учёт вызова — в LlmCall.finish(), а не в генераторе потока (П7): Starlette 1.7.0 при spec_version 2.3 на
обрыве клиента отменяет задачу stream_response, и генератор, не начатый или стоящий на yield, до сборки мусора
свой finally не выполнит. finish() зовут LlmStream.__call__ (finally) и обработчик — на всём, что не отдано
потоком. Общий срок вызова CHAT_DEADLINE_S — от begin_call до конца потока: срок чтения 120 с ловит
молчание, общий — струйку, которая держала бы место одновременности и резерв бесконечно.

Цена (Д4а-R13, ревью задачи 4): none — только отказ до первого байта (статус не 200, связь не открылась,
запрос не ушёл); stream — только usage.cost итогового чанка с [DONE] (без потока — body); всё прочее после
ответа 200 — pending и дозапрос /generation по X-Generation-Id (иначе id чанка), без id — сразу unknown.
Тело ушло, а ответа нет (UpstreamError.sent) — unknown: запрос мог дойти до модели.

Тексты клиенту — постоянные, без чисел, сумм и ввода клиента (имя модели — только в строке журнала), кроме
предела шагов (не больше 100): OpenCode ищет 429|500|502|503|504|524 и в тексте, и в теле ответа. Текст
OpenRouter клиенту не уходит; у rejected он только в строке журнала процесса. Отказ OpenRouter 4xx: переполнение
контекста — наш 413 pcbk_body_too_large (OpenCode сожмёт разговор), 400/404/422 — как есть, прочие — 400.
"""
import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import anyio
import anyio.lowlevel
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from ..data.http_api import bearer_caller
from ..hooks import safe_path
from .ledger import Usage
from .models import provider_matches
from .request import BodyRefused, body_bytes, rebuild_body, session_of, turn_mark
from .sse import SseObserver
from .state import DRILL_SUFFIX
from .turns import TurnLimitReached
from .upstream import UpstreamError, UpstreamKind, classify

if TYPE_CHECKING:
    from . import LlmRole

CHAT_PATH = "/llm/v1/chat/completions"
HEALTH_PATH = "/health/llm"
CHAT_DEADLINE_S = 600.0         # 10 мин: 16 000 токенов ответа — при ≥ 27 токенов/с
LEDGER_DURABLE_S = 2.0          # строка вызова на диске до OpenRouter (Д4а-R19)
STREAM_SEND_GRACE_S = 5.0       # клиент не читает поток: после срока вызова и этого запаса перекачка снимается
BUSY_RETRY_AFTER = "2"
LEDGER_RETRY_AFTER = "5"
OR_MESSAGE_CHARS = 200          # текст OpenRouter в строке журнала
MODEL_LABEL_CHARS = 100         # модель клиента в журнале вызовов и журнале процесса (в текст отказа не идёт)
PROVIDER_LOG_CHARS = 40
_RETRY_AFTER = re.compile(r"[0-9]{1,6}")

LLM_MESSAGES = {
    "unauthorized": "нужен токен",
    "stopped": "LLM-прокси остановлен администратором — модель сейчас не отвечает.",
    "key_missing": "LLM-прокси не настроен: нет ключа OpenRouter. Сообщите преподавателю.",
    "body": "Запрос к модели отклонён: ",
    "model": "Эта модель не разрешена на учебном стенде.",
    "share_spent": "Ваша доля бюджета на модель израсходована — модель больше не отвечает. "
                   "Обратитесь к преподавателю.",
    "budget_spent": "Общий бюджет стенда на модель израсходован — модель не отвечает. "
                    "Сообщите преподавателю.",
    "turn_limit": "Предел шагов на ответ: {limit} обращений к модели за один ход. Ход остановлен — "
                  "задайте вопрос заново, короче или по частям.",
    "busy": "Идёт другой запрос к модели с этого места — дождитесь его и отправьте сообщение снова.",
    "config": "LLM-прокси не настроен: ошибка настройки на сервере. Сообщите преподавателю.",
    "money_out": "На счёте OpenRouter кончились деньги — модель не отвечает. Сообщите преподавателю.",
    "key_limit": "Исчерпан лимит ключа OpenRouter — модель не отвечает. Сообщите преподавателю.",
    "key_rejected": "OpenRouter отклонил ключ стенда — модель не отвечает. Сообщите преподавателю.",
    "region": "OpenRouter отказывает по региону — модель недоступна. Сообщите преподавателю.",
    "wait": "OpenRouter просит подождать — повтор через несколько секунд.",
    "down": "OpenRouter не ответил — повторите позже.",
    "rejected": "OpenRouter отклонил запрос — модель не ответила. Сообщите преподавателю.",
    "ledger": "Учёт расходов временно недоступен — повторите позже.",
}
LLM_MESSAGES["too_large"] = ("Запрос к модели отклонён: разговор слишком длинный для учебного стенда — "
                             "начните новую сессию.")
# тело не объект JSON — шаг 4 (текст как у BodyRefused задачи 3)
NOT_OBJECT = "тело запроса — не объект JSON"

# ключ LLM_MESSAGES → (статус, code), Д4а-R12; у rejected статус — тот же, что у OpenRouter (здесь — запасной)
REFUSALS: dict[str, tuple[int, str]] = {
    "unauthorized": (401, "pcbk_unauthorized"),
    "too_large": (413, "pcbk_body_too_large"),
    "stopped": (403, "pcbk_stopped"),
    "key_missing": (403, "pcbk_key_missing"),
    "body": (400, "pcbk_body_refused"),
    "model": (403, "pcbk_model_refused"),
    "share_spent": (402, "pcbk_share_spent"),
    "budget_spent": (402, "pcbk_budget_spent"),
    "turn_limit": (403, "pcbk_turn_limit"),
    "busy": (429, "pcbk_busy"),
    "config": (403, "pcbk_config"),
    "money_out": (402, "pcbk_money_out"),
    "key_limit": (402, "pcbk_key_limit"),
    "key_rejected": (403, "pcbk_key_rejected"),
    "region": (403, "pcbk_region"),
    "wait": (429, "pcbk_upstream_wait"),
    "rejected": (400, "pcbk_upstream_rejected"),
    "ledger": (503, "pcbk_ledger"),
    "down": (502, "pcbk_upstream_down"),
}
# вид отказа OpenRouter (upstream.classify) → ключ REFUSALS
UPSTREAM_REFUSALS: dict[str, str] = {"money_out": "money_out", "key_limit": "key_limit",
                                     "key_rejected": "key_rejected", "region": "region", "rate": "wait",
                                     "in_flight": "wait", "rejected": "rejected", "server": "down",
                                     "unreachable": "down"}
MONEY_KINDS = frozenset({"money_out", "key_limit"})     # статус вызова money, а не upstream_error
# 4xx OpenRouter, чей статус клиент получает как есть; прочие 4xx — 400 (текст всегда постоянный)
PASS_THROUGH_4XX = frozenset({400, 404, 422})
# переполнение контекста — образцы OpenCode 1.18.33 (packages/llm/src/provider-error.ts): на них и на 413
# OpenCode сжимает разговор, поэтому прокси отвечает своим 413 pcbk_body_too_large
_OVERFLOW = re.compile("|".join((
    r"prompt is too long", r"request_too_large", r"input is too long for requested model",
    r"exceeds the context window",
    r"exceeds (?:the )?(?:model'?s )?maximum context length(?: of [\d,]+ tokens?|\s*\([\d,]+\))",
    r"input token count.*exceeds the maximum", r"tokens in request more than max tokens allowed",
    r"maximum prompt length is \d+", r"reduce the length of the messages", r"maximum context length is \d+ tokens",
    r"exceeds (?:the )?maximum allowed input length of [\d,]+ tokens?",
    r"input \(\d+ tokens\) is longer than the model'?s context length \(\d+ tokens\)",
    r"exceeds the limit of \d+", r"exceeds the available context size", r"greater than the context length",
    r"context window exceeds limit", r"exceeded model token limit", r"context[_ ]length[_ ]exceeded",
    r"request entity too large", r"context length is only \d+ tokens", r"input length.*exceeds.*context length",
    r"prompt too long; exceeded (?:max )?context length", r"too large for model with \d+ maximum context length",
    r"prompt has [\d,]+ tokens?, but the configured context size is [\d,]+ tokens?",
    r"model_context_window_exceeded", r"too many tokens", r"token limit exceeded")), re.I)
_NOT_OVERFLOW = re.compile(r"^(?:throttling error|service unavailable):|rate limit|too many requests", re.I)
DEADLINE_CODE = "pcbk_deadline"

log = logging.getLogger("pcbk_core.llm")

_NO_RECORD = object()


def error_body(code: str, message: str) -> dict:
    return {"error": {"message": message, "type": "pcbk", "code": code}}


def too_large_body(limit: int) -> dict:
    """Тело 413 для /llm/ в форме OpenAI (П19); предел в текст не идёт — текст без чисел."""
    return error_body(REFUSALS["too_large"][1], LLM_MESSAGES["too_large"])


def refusal_response(role: "LlmRole", key: str, *, text: str | None = None, status: int | None = None,
                     headers: dict[str, str] | None = None) -> JSONResponse:
    """Отказ по REFUSALS; при учениях к тексту money_out — « (учения)»."""
    default, code = REFUSALS[key]
    if text is None:
        text = LLM_MESSAGES[key] + (DRILL_SUFFIX if key == "money_out" and role.drill else "")
    return JSONResponse(error_body(code, text), status_code=status or default, headers=headers)


def is_overflow(error: UpstreamError) -> bool:
    """4xx OpenRouter о переполнении контекста: 413 или текст по образцам OpenCode (кроме «rate limit» и пр.)."""
    return error.status == 413 or (bool(_OVERFLOW.search(error.message)) and not _NOT_OVERFLOW.search(error.message))


def upstream_refusal_key(error: UpstreamError) -> tuple[str, int]:
    """(ключ REFUSALS, статус) отказа OpenRouter до ответа. rejected: переполнение контекста — наш 413
    pcbk_body_too_large (OpenCode сожмёт разговор); 400, 404, 422 — как есть; прочие 4xx — 400."""
    key = UPSTREAM_REFUSALS.get(error.kind, "down")
    if key != "rejected":
        return key, REFUSALS[key][0]
    if is_overflow(error):
        return "too_large", REFUSALS["too_large"][0]
    return key, error.status if error.status in PASS_THROUGH_4XX else REFUSALS["rejected"][0]


def upstream_refusal(role: "LlmRole", error: UpstreamError) -> JSONResponse:
    """Отказ OpenRouter до ответа — по upstream_refusal_key; wait — с его Retry-After (только число)."""
    key, status = upstream_refusal_key(error)
    headers = None
    retry = (error.retry_after or "").strip()
    if key == "wait" and _RETRY_AFTER.fullmatch(retry):
        headers = {"Retry-After": retry}
    return refusal_response(role, key, status=status, headers=headers)


def _json_object(raw: bytes) -> dict | None:
    try:
        value = json.loads(raw)
    except Exception:               # не JSON, не UTF-8, слишком глубокая вложенность, число длиннее предела
        return None
    return value if isinstance(value, dict) else None


def _model_label(value: object) -> str:
    """Модель клиента для журнала вызовов: строка, не длиннее MODEL_LABEL_CHARS; не строка — пусто."""
    return value[:MODEL_LABEL_CHARS] if isinstance(value, str) else ""


def _shown(text: str | None, limit: int) -> str:
    """Строка из чужих данных для журнала процесса: управляющие и не-ASCII — экранами, обрезанная."""
    return "-" if not text else ascii(text)[1:-1][:limit]


def log_call(*, caller: str, model: str, provider: str | None, step: int, status: str, code: str,
             cost: float | None, cost_source: str, usage: Usage | None, ms: int, estimate: float,
             or_message: str | None = None) -> None:
    """Строка журнала на вызов и отказ: числа, коды, имена — без текста, токенов и ключей (П12)."""
    level = logging.WARNING if status in ("upstream_error", "money", "unknown") else logging.INFO
    tail = "" if or_message is None else " or_message=" + json.dumps(or_message[:OR_MESSAGE_CHARS],
                                                                     ensure_ascii=False)
    log.log(level, "llm caller=%s model=%s provider=%s step=%d status=%s%s cost=%s cost_source=%s cached=%d "
            "reasoning=%d ms=%d est=%.6g%s", caller, _shown(model, MODEL_LABEL_CHARS),
            _shown(provider, PROVIDER_LOG_CHARS), step, status, f" code={code}" if code else "",
            "-" if cost is None else f"{cost:.6g}", cost_source, usage.cached_tokens if usage else 0,
            usage.reasoning_tokens if usage else 0, ms, estimate, tail)


class LlmCall:
    """Вызов после begin_call: резерв в журнале и место одновременности — до finish().

    Флаги: opened — OpenRouter ответил 200; completed — ответ OpenRouter дочитан; upstream_broken — httpx
    бросил при чтении; deadline_hit — истёк общий срок вызова; sent_upstream — запрос отдан open_chat.
    """

    def __init__(self, role: "LlmRole", call_id: int, *, caller: str, session: str | None, parent: str | None,
                 model: str, step: int, estimate: float):
        self.role = role
        self.call_id = call_id
        self.caller = caller
        self.session = session
        self.parent = parent
        self.model = model
        self.step = step
        self.estimate = estimate
        self.started = role.monotonic()
        self.deadline = self.started + role.chat_deadline_s
        self.observer = SseObserver()
        self.response: httpx.Response | None = None
        self.streaming = True
        self.sent_upstream = False
        self.opened = False
        self.completed = False
        self.upstream_broken = False
        self.deadline_hit = False
        self.body: dict | None = None               # ответ без потока, разобранный
        self.chunks: AsyncIterator[bytes] | None = None   # aiter_bytes() ответа потока
        self._generation_header: str | None = None
        self._finished = False

    def remaining(self) -> float:
        """Секунд до общего срока вызова, не меньше нуля."""
        return max(0.0, self.deadline - self.role.monotonic())

    def attach(self, response: httpx.Response) -> None:
        """Ответ 200 OpenRouter: его закроет finish(); id генерации — из заголовка."""
        self.response = response
        self.opened = True
        self._generation_header = response.headers.get("x-generation-id") or None

    async def read_body(self) -> bytes | None:
        """Ответ без потока целиком, под общим сроком; не дочитан — None (флаги — почему)."""
        raw = None
        with anyio.move_on_after(self.remaining()) as scope:
            try:
                raw = await self.response.aread()
            except httpx.HTTPError:
                self.upstream_broken = True
            except Exception as exc:
                self.upstream_broken = True
                log.error("LLM-прокси: ответ OpenRouter не дочитан (%s)", type(exc).__name__)
        if scope.cancelled_caught:
            self.deadline_hit = True
            return None
        if raw is not None:
            self.completed = True
            self.body = _json_object(raw)
        return raw

    def _result(self) -> tuple[str | None, str | None, dict | None, dict | None, bool]:
        """(id генерации, провайдер, usage, error, «ответ до конца»): у потока — наблюдатель, иначе тело."""
        if self.streaming:
            o = self.observer
            return self._generation_header or o.generation_id, o.provider, o.usage, o.error, o.done
        b = self.body if self.body is not None else {}
        pick = lambda name, kind: b.get(name) if isinstance(b.get(name), kind) else None   # noqa: E731
        return (self._generation_header or pick("id", str), pick("provider", str), pick("usage", dict),
                pick("error", dict), self.body is not None)

    async def finish(self, *, error: UpstreamError | None = None, ledger_refused: bool = False) -> None:
        """Итог вызова; идемпотентна. Сначала синхронно, без await: место одновременности, строка журнала,
        возврат шага, состояние OpenRouter, строка журнала процесса, дозапрос цены. Потом — закрытие потока
        OpenRouter под щитом от отмены (finish зовут и из finally отменённой задачи)."""
        if self._finished:
            return
        self._finished = True
        inflight = self.role.inflight
        held = inflight.get(self.caller, 0)
        if held < 1:                    # место уже освобождено — ошибка учёта, а не повод уйти в минус
            log.error("LLM-прокси: вызов id=%d: место одновременности уже свободно", self.call_id)
        elif held == 1:
            del inflight[self.caller]
        else:
            inflight[self.caller] = held - 1
        try:
            self._settle(error, ledger_refused)
        except Exception as exc:        # учёт не роняет ответ; в журнал — только класс
            log.error("LLM-прокси: вызов id=%d не учтён (%s)", self.call_id, type(exc).__name__)
        chunks, response, self.chunks, self.response = self.chunks, self.response, None, None
        if chunks is None and response is None:
            return
        with anyio.CancelScope(shield=True):
            for closer in (getattr(chunks, "aclose", None), getattr(response, "aclose", None)):
                if closer is None:
                    continue
                try:
                    await closer()
                except Exception:       # поток уже оборван — закрывать нечего
                    pass

    def _settle(self, error: UpstreamError | None, ledger_refused: bool) -> None:
        role = self.role
        now = role.wallclock()
        ms = max(0, round((role.monotonic() - self.started) * 1000))
        gen, provider, usage_raw, stream_error, done = self._result()
        usage = Usage.from_openrouter(usage_raw) if usage_raw is not None else None
        policy = role.models.get(self.model)
        record: object = _NO_RECORD
        http_status = 200 if self.opened else 0
        or_message = None
        if ledger_refused or error is not None:
            gen = provider = usage = None
            if ledger_refused:
                http_status, code = REFUSALS["ledger"]
                status, source = "refused", "none"
            else:
                status = "money" if error.kind in MONEY_KINDS else "upstream_error"
                code = REFUSALS[upstream_refusal_key(error)[0]][1]      # тот же код, что в ответе клиенту
                http_status, record = error.status, error.kind
                source = "unknown" if error.sent else "none"
                or_message = error.message if error.kind == "rejected" else None
        elif not self.opened:           # до ответа OpenRouter: срок или снятие обработчика
            status, code = ("upstream_error", DEADLINE_CODE) if self.deadline_hit else ("cancelled", "")
            source = "unknown" if self.sent_upstream else "none"
        else:
            if stream_error is not None:
                kind: UpstreamKind = classify(stream_error.get("code"), stream_error)
                status = "money" if kind in MONEY_KINDS else "upstream_error"
                code, record = REFUSALS[UPSTREAM_REFUSALS[kind]][1], kind
            elif self.completed and done:
                status, code, record = "ok", "", None
            elif self.deadline_hit:
                status, code = "upstream_error", DEADLINE_CODE
            elif self.upstream_broken or self.completed:      # оборван или кончился без [DONE]
                status, code = "upstream_error", REFUSALS["down"][1]
            else:                                              # клиент ушёл: генератор снят или не начат
                status, code = "cancelled", ""
            priced = done and stream_error is None and usage is not None and usage.cost is not None
            source = ("stream" if self.streaming else "body") if priced else ("pending" if gen else "unknown")
        provider_ok = provider_matches(policy.provider_only, provider) if policy is not None and provider else None
        role.ledger.close_call(self.call_id, status=status, code=code, http_status=http_status, provider=provider,
                               provider_ok=provider_ok, generation_id=gen, usage=usage, cost_source=source,
                               duration_ms=ms, now=now)
        if source == "none" and self.step > 0:     # модель не отвечала: шаг хода не потрачен
            role.turns.refund(self.caller, self.session, parent=self.parent)
        if record is not _NO_RECORD:
            role.note_upstream(record, now, source="chat")
        cost = usage.cost if source in ("stream", "body") else (0.0 if source == "none" else None)
        log_call(caller=self.caller, model=self.model, provider=provider, step=self.step, status=status, code=code,
                 cost=cost, cost_source=source, usage=usage, ms=ms, estimate=self.estimate, or_message=or_message)
        if source == "pending":
            role.spawn_resolve(self.call_id, gen)


class LlmStream(StreamingResponse):
    """Поток SSE OpenRouter клиенту как есть (aiter_bytes: Accept-Encoding identity задаёт Upstream), копия —
    наблюдателю. Учёт — в __call__ (finally), а не в генераторе (П7)."""

    def __init__(self, call: LlmCall, upstream: httpx.Response):
        call.attach(upstream)
        call.streaming = True
        self.call = call
        super().__init__(self._pump(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    async def _pump(self) -> AsyncIterator[bytes]:
        """Только перекачка: дочитал — completed, httpx бросил — upstream_broken, срок — deadline_hit; в каждом
        случае поток клиенту кончается чисто (без [DONE], если его не было). Область срока — только вокруг
        чтения, не через yield."""
        call = self.call
        chunks = call.response.aiter_bytes()
        call.chunks = chunks
        while True:
            got, chunk = False, b""
            with anyio.move_on_after(call.remaining()) as scope:
                try:
                    chunk, got = await chunks.__anext__(), True
                except StopAsyncIteration:
                    call.completed = True
                except httpx.HTTPError:
                    call.upstream_broken = True
                except Exception as exc:
                    call.upstream_broken = True
                    log.error("LLM-прокси: поток OpenRouter оборван непредвиденно (%s)", type(exc).__name__)
            if not got:
                if scope.cancelled_caught:
                    call.deadline_hit = True
                return
            call.observer.feed(chunk)
            yield chunk

    async def stream_response(self, send: Send) -> None:
        # точка отмены до первого байта: обрыв клиента, уже стоящий в очереди, снимает поток до начала
        await anyio.lowlevel.checkpoint()
        await super().stream_response(send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        call = self.call
        try:
            # клиент не читает (send ждёт): перекачку снимает срок вызова с запасом — место и резерв не вечны
            with anyio.CancelScope(deadline=anyio.current_time() + call.remaining() + STREAM_SEND_GRACE_S) as cs:
                await super().__call__(scope, receive, send)
            if cs.cancelled_caught:
                call.deadline_hit = True
        finally:
            await call.finish()
            with anyio.CancelScope(shield=True):
                try:
                    await self.body_iterator.aclose()     # генератор, снятый на yield, — сразу, не сборщиком
                except Exception:
                    pass


class _Ask:
    """Запрос до резерва: кто и о чём спрашивает — для отказов шагов 1а–9 (ledger.refused, строка журнала)."""

    def __init__(self, role: "LlmRole", caller: str, session: str | None, parent: str | None):
        self.role = role
        self.caller = caller
        self.session = session
        self.parent = parent
        self.model = ""

    def refuse(self, key: str, *, text: str | None = None, headers: dict[str, str] | None = None) -> JSONResponse:
        status, code = REFUSALS[key]
        role = self.role
        role.ledger.refused(ts=role.wallclock(), caller=self.caller, session=self.session, model=self.model,
                            code=code, http_status=status)
        log_call(caller=self.caller, model=self.model, provider=None, step=0, status="refused", code=code, cost=0.0,
                 cost_source="none", usage=None, ms=0, estimate=0.0)
        return refusal_response(role, key, text=text, headers=headers)


async def chat(role: "LlmRole", request: Request) -> Response:
    caller = bearer_caller(request.headers.get("authorization"), role.tokens)
    if caller is None:          # вызывающий неизвестен: без строки журнала вызовов, строка процесса — без токена
        log.warning("outcome=unauthorized channel=llm path=%s", safe_path(request.scope["path"]))
        return refusal_response(role, "unauthorized", headers={"WWW-Authenticate": "Bearer"})
    ask = _Ask(role, caller, session_of(request.headers.get("x-session-id")),
               session_of(request.headers.get("x-parent-session-id")))
    if role.config_error:
        return ask.refuse("config")
    if role.stopped:
        return ask.refuse("stopped")
    if role.upstream is None:
        return ask.refuse("key_missing")
    raw = _json_object(await request.body())
    if raw is None:
        return ask.refuse("body", text=LLM_MESSAGES["body"] + NOT_OBJECT)
    model = raw.get("model")
    ask.model = _model_label(model)
    policy = role.models.get(model) if isinstance(model, str) else None   # :nitro, ~…, регистр — отказ
    if policy is None:                  # имя модели — только в строке журнала (model=), не в тексте клиенту
        return ask.refuse("model")
    try:
        body = rebuild_body(raw, policy, caller=caller, session=ask.session, max_tokens_cap=role.llm.LLM_MAX_TOKENS)
    except BodyRefused as exc:
        return ask.refuse("body", text=LLM_MESSAGES["body"] + str(exc))
    estimate = policy.estimate_usd(body_bytes(body), body["max_tokens"])
    mark = turn_mark(body) if "tools" in body else None
    if role.ledger_error() is not None:     # журнал не пишется: сразу, без резерва и без потока (flush не нужен)
        return ask.refuse("ledger", headers={"Retry-After": LEDGER_RETRY_AFTER})
    # --- шаги 7–10: один синхронный участок, ни одного await до begin_call ---
    if role.inflight.get(caller, 0) >= role.llm.LLM_CALLER_CONCURRENCY:
        return ask.refuse("busy", headers={"Retry-After": BUSY_RETRY_AFTER})       # до бюджета и шагов
    spent = role.budget.refusal(caller)
    if spent is not None:
        return ask.refuse(spent)
    step = 0
    if mark is not None:            # без инструментов (заголовок, сжатие) ход не начинается и не считается
        try:
            step = role.turns.step(caller, ask.session, mark, parent=ask.parent)
        except TurnLimitReached:
            return ask.refuse("turn_limit", text=LLM_MESSAGES["turn_limit"].format(limit=role.turns.limit))
    call = role.begin_call(caller=caller, session=ask.session, parent=ask.parent, model=policy.model, step=step,
                           estimate=estimate)
    return await forward(role, call, body)


async def forward(role: "LlmRole", call: LlmCall, body: dict) -> Response:
    """От резерва до ответа: строка на диске, OpenRouter, поток или тело. finish() — на всём, что не отдано
    потоком (LlmStream зовёт её сам)."""
    handed = False
    try:
        try:
            if not role.ledger.opened:          # журнал закрыт: flush() без очереди вернулся бы сразу
                raise TimeoutError("журнал вызовов закрыт")
            await asyncio.to_thread(role.ledger.flush, LEDGER_DURABLE_S)
        except TimeoutError:                    # и LedgerWriteLost: строки нет на диске — в OpenRouter не идём
            await call.finish(ledger_refused=True)
            return refusal_response(role, "ledger", headers={"Retry-After": LEDGER_RETRY_AFTER})
        call.streaming = body.get("stream") is True
        call.sent_upstream = True
        response = None
        try:
            with anyio.move_on_after(call.remaining()) as scope:
                response = await role.upstream.open_chat(body)
        except UpstreamError as exc:
            await call.finish(error=exc)
            return upstream_refusal(role, exc)
        if response is None:
            call.deadline_hit = scope.cancelled_caught
            await call.finish()
            return refusal_response(role, "down")
        if call.streaming:
            stream = LlmStream(call, response)
            handed = True
            return stream
        call.attach(response)
        raw = await call.read_body()
        await call.finish()
        if raw is None:
            return refusal_response(role, "down")
        return Response(raw, media_type="application/json")
    finally:
        if not handed:
            await call.finish()                 # идемпотентна; ловит исключения и отмену обработчика


def llm_router(role: "LlmRole") -> APIRouter:
    router = APIRouter()

    @router.post(CHAT_PATH)
    async def chat_completions(request: Request) -> Response:
        return await chat(role, request)

    @router.get(HEALTH_PATH)
    async def health_llm() -> JSONResponse:
        # без токена: числа, коды и флаги — ни токенов, ни id вызывающих, ни текстов
        return JSONResponse(role.health_json(role.wallclock()))

    return router
