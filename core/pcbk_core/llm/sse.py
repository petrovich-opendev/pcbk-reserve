"""Наблюдатель потока SSE OpenRouter: id ответа, провайдер, usage (цена), событие ошибки, [DONE].

Прокси отдаёт клиенту байты потока как есть, а копию кормит сюда: наблюдатель ничего не меняет и
не бросает никогда (П21). Событие странной формы — только счётчик bad_events; в журнал — не чаще раза
на наблюдатель класс исключения и число, без текста события: в событиях — ответ модели.
"""
import json
import logging
import re

log = logging.getLogger("pcbk_core.llm.sse")

# неполное событие дольше этого — сбой формы: буфер не растёт без предела на потоке без пустых строк
MAX_EVENT_BYTES = 1024 * 1024
_BOUNDARY = re.compile(rb"\r?\n\r?\n")          # события делятся пустой строкой: \n\n и \r\n\r\n
_LINE = re.compile(r"\r?\n")


class OddEvent(ValueError):
    """Событие не той формы; текст — только имя поля или причина, без содержимого события."""


class SseObserver:
    """feed(кусок) — сколько угодно раз, куски любой нарезки. Поля:

    - generation_id — первый id (только строка);
    - provider — первый provider (только строка);
    - usage — последний встреченный usage (только объект);
    - error — первый объект error (событие ошибки посреди потока);
    - done — пришёл data: [DONE];
    - bad_events — событий странной формы.
    """

    def __init__(self) -> None:
        self.generation_id: str | None = None
        self.provider: str | None = None
        self.usage: dict | None = None
        self.error: dict | None = None
        self.done = False
        self.bad_events = 0
        self._buf = b""
        self._logged = False

    def feed(self, chunk: bytes) -> None:
        """Разбирает все события, закрытые пустой строкой; хвост ждёт следующего куска. Не бросает."""
        try:
            self._feed(chunk)
        except Exception as err:        # ни одно исключение не рвёт поток клиенту
            self._buf = b""
            self._bad(err)

    def _feed(self, chunk: bytes) -> None:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("кусок потока — не байты")
        buf = self._buf + bytes(chunk)
        pos, start = 0, max(0, len(self._buf) - 3)  # граница могла начаться в прошлом куске
        while (m := _BOUNDARY.search(buf, start)) is not None:
            raw, pos = buf[pos:m.start()], m.end()
            start = pos
            try:
                self._event(raw)
            except Exception as err:
                self._bad(err)
        self._buf = buf[pos:]
        if len(self._buf) > MAX_EVENT_BYTES:
            self._buf = b""
            self._bad(OddEvent("событие длиннее предела"))

    def _event(self, raw: bytes) -> None:
        text = raw.decode("utf-8")                  # битый UTF-8 — UnicodeDecodeError, событие странной формы
        data = [line[5:].removeprefix(" ") for line in _LINE.split(text) if line.startswith("data:")]
        if not data:                                # комментарий «: OPENROUTER PROCESSING» и прочие поля
            return
        payload = "\n".join(data)
        if payload.strip() == "[DONE]":
            self.done = True
            return
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:                # не-JSON пропускается
            return
        if not isinstance(event, dict):
            raise OddEvent("событие — не объект")
        odd = None
        for name in ("id", "provider"):
            value = event.get(name)
            if value is None:
                continue
            if not isinstance(value, str):
                odd = odd or name
            elif name == "id" and self.generation_id is None:
                self.generation_id = value
            elif name == "provider" and self.provider is None:
                self.provider = value
        for name in ("usage", "error"):
            value = event.get(name)
            if value is None:
                continue
            if not isinstance(value, dict):
                odd = odd or name
            elif name == "usage":
                self.usage = value
            elif self.error is None:
                self.error = value
        choices = event.get("choices")
        if choices is not None and not (isinstance(choices, list) and all(isinstance(c, dict) for c in choices)):
            odd = odd or "choices"
        if odd:                                     # годные поля того же события уже взяты: цена не теряется
            raise OddEvent(f"поле {odd}")

    def _bad(self, err: BaseException) -> None:
        self.bad_events += 1
        if not self._logged:
            self._logged = True
            log.warning("поток OpenRouter: событие странной формы (%s), всего %d", type(err).__name__,
                        self.bad_events)
