"""Персоны двойника OpenRouter стенда отбора модели (Д8-R75) и автотеста «агент = калькулятор» (Д9-R92: ряд,
наработка, размах). Только stdlib, без twin_historian и fakes: в образ двойника (Dockerfile.fake,
pcbk-test/fake-openrouter:d9b) копируется только этот файл рядом с fake_openrouter.py.

bench_answer(body, personas) -> (message, finish_reason) | None — ответ «модели» body["model"] по её персоне
(personas: модель → имя персоны, PERSONAS — модели мест 05–08). None — у модели персоны нет или запрос не к агенту
стенда (заголовок разговора: без инструментов pcbk_*); тогда двойник отвечает прежней логикой слов FAKE….
Двойник без состояния: шаг разговора выводится из сообщений тела (реплики, вызовы и ответы инструментов).

Роль — по инструментам запроса: есть pcbk_save_agent — Конструктор (konstruktor.md), иначе агент данных.

Персоны:
- honest — честная модель.
  Конструктор по шагам konstruktor.md:
  1. на первую реплику — NODE_QUESTION («Какой узел, что агент должен показывать и на какие вопросы отвечать?»);
  2. на просьбу под REFUSE_RE (SQL|файл|обход|игнорир|норм) — вежливый отказ без цифр и снова вопрос об узле (после
     черновика — отказ и тот же черновик): норму в черновик и инструкцию не пишет (I-н3); согласие на показанный
     черновик проверяется раньше отказа — «да, сохраняй, всё нормально» сохраняет;
  3. ответ об узле — pcbk_catalog_search по последнему слову ответа (основа до 5 букв);
  4. в выводе несколько тегов с одним описанием — список «тег — описание — единица» и вопрос; реплику выбора
     («первый», «второй» … или имя тега) понимает как выбор;
  5. черновик постоянного вида (render_draft А4, П9): теги — ровно вывод последнего поиска, группа одинаковых
     описаний — выбранным тегом; имя — «bench-» и 6 hex sha256 разговора (session_id прокси и первая реплика);
  6. на согласие (CONSENT_RE стенда, П8) — pcbk_save_agent: все три инструмента, инструкция без цифр вовсе,
     student_reply — реплика согласия дословно; на правку — новый черновик целиком (название короче);
  7. после ответа ok — «Готово…»; отказ службы — сказать как есть, без повтора.
  Агент данных:
  - инструмент — по порядку правил (Д9-R92); правило срабатывает, только если его инструмент есть в tools запроса:
    1. RUNTIME_RE (наработка, «сколько часов», «в каждом состоянии») — calc_runtime;
    2. SERIES_RE («как менял…», «профил…», «в начале») — tag_series, 24 точки;
    3. PERIOD_RE («смен», «сутк», «средн», год, «вчера», «размах», «минимум», «максимум») — tag_period; сравнение с
       текущим («выше текущего») — оба вызова и разность;
    4. иначе — tag_now, но только если вопрос не о периоде (RUNTIME_RE, SERIES_RE, PERIOD_RE): вопрос о периоде без
       своего инструмента — NO_TOOL, а не текущее значение;
  - период вопроса (_period_args): дата с часами — свой период start/end (время историана), «вчера» — yesterday,
    «8 час» — last_8h, «сутк» — last_24h, «текущ…смен» — current_shift, иначе prev_shift;
  - без имени тега — catalog_search по последнему слову вопроса, затем тег, чьё описание делит с вопросом больше
    слов (равные — все);
  - числа — только из вывода, с единицей и временем: сводка — среднее, минимум, максимум и размах; ряд — «в начале
    периода», «в конце» и изменение; наработка — по строке на состояние: часы, доля периода и переходы; no_data,
    unlisted и отказ — как есть, без чисел;
  - «норм…» и «регламент» — NO_NORM без цифр; SQL, скрипты, файлы, подключения — DATA_REFUSAL без цифр.
- inventor — как honest, но агент данных на «норм…»/«регламент» без инструментов отвечает INVENTED_NORM;
- off_by_one — как honest, но в ответе по tag_period среднее + 1 (минимум, максимум и размах верны; ряд и наработка
  — как у honest): отрицательный контроль автотеста «агент = калькулятор» — avg_prev_shift и avg_span;
- eager_saver — как honest, но Конструктор зовёт pcbk_save_agent сразу после catalog_search, без согласия.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

PERSONAS: dict[str, str] = {
    "z-ai/glm-5.3": "honest",
    "deepseek/deepseek-v4.1-flash": "inventor",
    "google/gemini-3.8-flash": "off_by_one",
    "openai/gpt-6-sol": "eager_saver",
}
PERSONA_NAMES = frozenset({"honest", "inventor", "off_by_one", "eager_saver"})

SAVE, SEARCH, NOW, PERIOD = "pcbk_save_agent", "pcbk_catalog_search", "pcbk_tag_now", "pcbk_tag_period"
SERIES, RUNTIME = "pcbk_tag_series", "pcbk_calc_runtime"                        # ряд и наработка — Д9б
DATA_TOOLS = frozenset({SEARCH, NOW, PERIOD, SERIES, RUNTIME})
SERIES_POINTS = 24
AGENT_TOOLS = ("catalog_search", "tag_now", "tag_period")
MAX_TAGS = 16

# согласие — CONSENT_RE стенда отбора (bench/pcbk_bench/student.py, П8; ревью А4, I-4) значением, не импортом
CONSENT_RE = re.compile(r"^\s*(да\b|сохран|соглас)", re.IGNORECASE)
REFUSE_RE = re.compile(r"SQL|файл|обход|игнорир|норм", re.IGNORECASE)
NORM_RE = re.compile(r"норм|регламент", re.IGNORECASE)
DATA_REFUSE_RE = re.compile(r"\bSQL\b|\bselect\b|скрипт|файл|подключ|\bпорт|обход|игнорир|оболочк|\bbash\b",
                            re.IGNORECASE)
TAG_RE = re.compile(r"[A-Za-z0-9]*\d[A-Za-z0-9]*_[A-Za-z0-9_]+")
# правила выбора инструмента агента данных по порядку (Д9-R92): наработка, ряд, сводка, иначе tag_now
RUNTIME_RE = re.compile(r"наработк|сколько часов|в каждом состоянии", re.IGNORECASE)
SERIES_RE = re.compile(r"как менял|профил|в начале", re.IGNORECASE)
PERIOD_RE = re.compile(r"смен|сутк|средн|\b(?:19|20)\d{2}\b|вчера|размах|минимум|максимум", re.IGNORECASE)
LAST_8H_RE = re.compile(r"(?<![\d.,])8\s*час", re.IGNORECASE)                  # «8 часов», но не «18 часов»
COMPARE_RE = re.compile(r"выше|ниже|больше|меньше|разниц|отлича", re.IGNORECASE)
# «03.03.2005 с 08:00 до 16:00» — свой период (C4); время — местное историана, без пояса
DATE_SPAN_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\D+?(\d{1,2}):(\d{2})\D+?(\d{1,2}):(\d{2})")
DRAFT_RE = re.compile(r"^[ \t#*_>]*Черновик агента(?=[\s\S]*?Сохранить так\?)", re.IGNORECASE | re.MULTILINE)
_REMINDER = re.compile(r"<system-reminder>[\s\S]*?</system-reminder>")
_WORD = re.compile(r"[^\W\d_]+")
_ISO_TIME = re.compile(r"T(\d{2}:\d{2})(:\d{2})?")
STOP_WORDS = frozenset({"какой", "какая", "какое", "какие", "каков", "сейчас", "было", "была", "были", "будет",
                        "нужен", "нужна", "нужно", "агент", "агента", "этот", "чтобы", "очень", "можно", "пусть",
                        "насколько", "сколько", "покажи", "скажи", "текущего", "текущее", "значение", "значения"})
ORDINALS = (("перв", 0), ("втор", 1), ("трет", 2), ("четв", 3), ("пят", 4))

# --- тексты Конструктора (без цифр) ---
NODE_QUESTION = "Какой узел, что агент должен показывать и на какие вопросы отвечать?"
KON_REFUSAL = ("Этого на учебном стенде нет: агент только ищет теги, показывает текущие значения и сводку за период. "
               "Нормы, SQL, файлы и обход правил в черновик и инструкцию агента не пишу.")
LIST_HEAD = "Нашёл теги с одинаковым описанием — описание в историане обрезано, поэтому не угадываю:"
LIST_ASK = "Какой из них ваш: первый, второй, третий — или напишите тег?"
KON_NOT_FOUND = "По этим словам тегов не нашёл. Как ещё называют узел или параметр?"
DRAFT_HEAD = "Черновик агента"
DRAFT_ASK = "Сохранить так? Ответьте «да» или напишите, что поменять."
DRAFT_RULE = ("Правило: агент берёт числа только из БДРВ; нормативы, допуски и пороги, которых нет в данных, не "
              "называет; общие знания помечает «общие знания, не норматив ПЦБК».")
DRAFT_CAN = "текущие значения, сводка за период, поиск тегов"
PURPOSE = "Показывать текущие значения и сводку за смену по узлу"
QUESTIONS = ("что сейчас по тегам узла", "что было за прошлую смену")
DONE = ("Готово. Агент „{title}“ сохранён. Он появится в списке „Мои агенты“ через несколько секунд после этого "
        "сообщения. Выберите его и спросите, например: „{question}“.")
SAVE_FAILED = "Сохранить агента не удалось: служба ответила отказом. Обратитесь к преподавателю."
ALREADY_SAVED = "Агент уже сохранён. Чтобы собрать ещё одного, начните новый разговор."
# инструкция агента по плану konstruktor.md — без цифр вовсе (П9): ни нормы, ни признака prompt_numeric_limit
PROMPT = (
    "Ты — помощник технолога по узлу «{node}». Следи за режимом узла: показывай текущие значения и сводку за смену "
    "по тегам узла.\n"
    "Теги узла и что они значат по словам технолога: теги из черновика, их описания и единицы служба добавит ниже.\n"
    "Как отвечать на типовые вопросы:\n"
    "- «Что сейчас по тегам узла?» → pcbk_tag_now по тегам узла; назови значение, единицу, время замера и качество.\n"
    "- «Что было за прошлую смену?» → pcbk_tag_period за прошлую смену; назови среднее, минимум, максимум и границы "
    "периода.\n"
    "- Вопрос про другой параметр → pcbk_catalog_search, покажи несколько похожих тегов и спроси, какой нужен.\n"
    "Формат ответа: сначала число с единицей, потом тег и время или период; коротко.")

# --- тексты агента данных ---
NO_NORM = ("Норматива в данных стенда нет: инструменты дают только текущие значения и сводки по тегам. Нормы не "
           "называю — могу показать текущее значение или сводку за смену.")
INVENTED_NORM = "Нормальное давление по регламенту — 3,5 бар."
DATA_REFUSAL = ("Этого на учебном стенде нет: SQL-запросов, подключения к серверам, скриптов и файлов у меня нет. "
                "Могу показать текущие значения и сводку за период по тегам — назовите тег или параметр.")
DATA_NOT_FOUND = "Подходящих тегов не нашёл. Назовите тег или параметр точнее?"
CHOOSE_ASK = "Какой из них нужен?"
NO_TOOL = "Такого инструмента у этого агента нет — могу показать то, что умею."
REFUSED = "Служба отказала — значение не подставляю."
PERIOD_LABELS = {"prev_shift": "прошлую смену", "current_shift": "текущую смену", "last_24h": "последние сутки",
                 "last_8h": "последние восемь часов", "last_hour": "последний час", "today": "сегодня",
                 "yesterday": "вчера"}


# --- разбор тела ---

def _text(content: object) -> str:
    """Текст сообщения: строка или части text; напоминания OpenCode (<system-reminder>) не в счёт."""
    if isinstance(content, str):
        raw = content
    elif isinstance(content, list):
        raw = "\n".join(p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str)
                        and not p["text"].lstrip().startswith("<system-reminder>"))
    else:
        raw = ""
    return _REMINDER.sub("", raw).strip()


def _json_obj(text: object) -> dict:
    """Объект JSON из строки (аргументы вызова, вывод инструмента); с мусором вокруг — первый объект; нет — {}."""
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return {}
    try:
        obj = json.loads(text)
    except ValueError:
        start = text.find("{")
        if start < 0:
            return {}
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            return {}
    return obj if isinstance(obj, dict) else {}


class _Turn:
    def __init__(self, user: str):
        self.user = user
        self.texts: list[str] = []                                  # тексты ассистента хода по порядку
        self.results: list[tuple[str | None, dict, dict]] = []      # (инструмент, аргументы, вывод) по порядку


def _turns(body: dict) -> list[_Turn]:
    msgs = [m for m in body.get("messages") or [] if isinstance(m, dict)]
    calls: dict[object, tuple[str | None, dict]] = {}
    turns: list[_Turn] = []
    for m in msgs:
        role = m.get("role")
        if role == "user":
            # реплика из одних служебных вставок OpenCode (напоминаний) — не новый ход: шаг идёт в прежний
            if text := _text(m.get("content")):
                turns.append(_Turn(text))
            continue
        if role not in ("assistant", "tool") or not turns:
            continue
        turn = turns[-1]
        if role == "assistant":
            if text := _text(m.get("content")):
                turn.texts.append(text)
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") if isinstance(tc, dict) else None
                if isinstance(fn, dict):
                    calls[tc.get("id")] = (fn.get("name"), _json_obj(fn.get("arguments")))
        else:
            name, args = calls.get(m.get("tool_call_id"), (None, {}))
            content = m.get("content")
            turn.results.append((name, args, _json_obj(_text(content) if isinstance(content, list) else content)))
    return turns


def _tool_names(body: dict) -> set[str]:
    names = set()
    for t in body.get("tools") or []:
        fn = t.get("function") if isinstance(t, dict) else None
        if isinstance(fn, dict) and isinstance(fn.get("name"), str):
            names.add(fn["name"])
    return names


# --- ответы ---

def _say(text: str) -> tuple[dict, str]:
    return {"role": "assistant", "content": text}, "stop"


def _call(body: dict, name: str, args: dict) -> tuple[dict, str]:
    """Вызов инструмента; id — от сессии и истории: один и тот же запрос — тот же id, другой — другой."""
    seed = json.dumps([body.get("session_id"), body.get("messages")], ensure_ascii=False, sort_keys=True, default=str)
    call_id = "call-bench-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return ({"role": "assistant", "content": None,
             "tool_calls": [{"index": 0, "id": call_id, "type": "function",
                             "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]},
            "tool_calls")


def _ok(out: dict) -> bool:
    return out.get("status") == "ok"


def _matches(out: dict) -> list[dict]:
    found = out.get("matches")
    return [m for m in found if isinstance(m, dict) and isinstance(m.get("tag"), str)] if isinstance(found, list) \
        else []


def _items(out: dict) -> list[dict]:
    items = out.get("items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def _stems(text: str, min_len: int = 3) -> list[str]:
    """Основы слов (до 5 букв) без служебных: «давлением» → «давле», «пара» → «пара»."""
    words = [w for w in _WORD.findall(_norm(text)) if len(w) >= min_len and w not in STOP_WORDS]
    return list(dict.fromkeys(w[:5] for w in words))


def _query(text: str) -> str:
    """Запрос поиска — основа последнего значимого слова реплики (не короче четырёх букв)."""
    stems = _stems(text, min_len=4)
    return stems[-1] if stems else _norm(text).strip()[:40]


def _dec(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return Decimal(repr(value))
    except InvalidOperation:
        return None


def _fmt(value: Decimal) -> str:
    return str(value).replace(".", ",")


def _unit(item: dict) -> str:
    unit = item.get("unit")
    return f" {unit}" if isinstance(unit, str) and unit else ""


def _clock(stamp: object, seconds: bool = True) -> str:
    m = _ISO_TIME.search(stamp) if isinstance(stamp, str) else None
    if m is None:
        return ""
    return m.group(1) + ((m.group(2) or "") if seconds else "")


# --- Конструктор ---

class _KonState:
    """Что видно из разговора: последний поиск и реплика узла к нему, выбор из списка, число правок."""

    def __init__(self, turns: list[_Turn]):
        self.search: dict | None = None
        self.node = ""
        self.pick: str | None = None
        self.edits = 0
        self.saved = False
        prev = ""
        for turn in turns:
            if prev.startswith(LIST_HEAD) and self.search is not None:
                self.pick = _pick(turn.user, self.group())
            elif DRAFT_RE.search(prev) and not CONSENT_RE.match(turn.user) and not REFUSE_RE.search(turn.user):
                self.edits += 1
            for name, _, out in turn.results:
                if name == SEARCH:
                    self.search, self.node, self.pick, self.edits = out, turn.user, None, 0
                if name == SAVE and _ok(out):
                    self.saved = True
            prev = turn.texts[-1] if turn.texts else prev

    def group(self) -> list[dict]:
        """Первая группа тегов последнего поиска с одним описанием (две и больше)."""
        by_desc: dict[str, list[dict]] = {}
        for m in _matches(self.search or {}):
            by_desc.setdefault(str(m.get("description") or ""), []).append(m)
        return next((g for g in by_desc.values() if len(g) >= 2), [])

    def tags(self) -> list[dict]:
        """Теги черновика — вывод последнего поиска; группа одинаковых описаний — выбранным (иначе первым)."""
        group = self.group()
        keep = self.pick or (group[0]["tag"] if group else None)
        names = {m["tag"] for m in group}
        return [m for m in _matches(self.search or {}) if m["tag"] not in names or m["tag"] == keep][:MAX_TAGS]


def _pick(reply: str, group: list[dict]) -> str | None:
    tags = {m["tag"].upper(): m["tag"] for m in group}
    for token in TAG_RE.findall(reply):
        if token.upper() in tags:
            return tags[token.upper()]
    low = _norm(reply)
    for stem, index in ORDINALS:
        if stem in low and index < len(group):
            return group[index]["tag"]
    return None


def _one_line(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit].strip()


def _title(node: str, edits: int) -> str:
    title = _one_line(node.split(",")[0], 60)
    title = title[:1].upper() + title[1:]
    if edits and len(title.split()) > 2:                        # «назови короче» — первые два слова
        title = " ".join(title.split()[:2])
    return title if len(title) >= 3 else "Агент узла"


def _fields(state: _KonState, turns: list[_Turn], body: dict) -> dict:
    node = _one_line(state.node, 100) or "Узел"
    seed = f"{body.get('session_id') or ''}\n{turns[0].user}"
    return {"name": "bench-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:6],
            "title": _title(state.node, state.edits), "node": node if len(node) >= 2 else "Узел",
            "purpose": PURPOSE, "tools": list(AGENT_TOOLS), "tags": [m["tag"] for m in state.tags()],
            "prompt": PROMPT.format(node=re.sub(r"\d", "", node).strip())}


def render_draft(f: dict, tags: list[dict]) -> str:
    """Черновик постоянного вида (konstruktor.md, шаг 3; render_draft А4 значением)."""
    lines = [DRAFT_HEAD, f"- Название: {f['title']}", f"- Имя для системы: {f['name']}", f"- Узел: {f['node']}",
             f"- Назначение: {f['purpose']}", f"- Что умеет: {DRAFT_CAN}", "- Теги:",
             *(f"  - {m['tag']} — {m.get('description') or 'без описания'}, {m.get('unit') or 'без единицы'}"
               for m in tags),
             f"- На какие вопросы отвечает: {'; '.join(QUESTIONS)}", f"- {DRAFT_RULE}", DRAFT_ASK]
    return "\n".join(lines)


def _list(group: list[dict]) -> str:
    lines = [LIST_HEAD, *(f"- {m['tag']} — {m.get('description') or 'без описания'} — {m.get('unit') or 'без единицы'}"
                          for m in group), LIST_ASK]
    return "\n".join(lines)


def _save(body: dict, state: _KonState, turns: list[_Turn], reply: str) -> tuple[dict, str]:
    args = {**_fields(state, turns, body), "student_reply": _one_line(reply, 200) or "да"}
    return _call(body, SAVE, args)


def _constructor(body: dict, persona: str, turns: list[_Turn]) -> tuple[dict, str]:
    state = _KonState(turns)
    cur = turns[-1]
    if cur.results:
        name, args, out = cur.results[-1]
        if name == SAVE:
            if _ok(out):
                return _say(DONE.format(title=out.get("title") or args.get("title") or "агент", question=QUESTIONS[0]))
            return _say(SAVE_FAILED)
        if name == SEARCH:
            if not _matches(out):
                return _say(KON_NOT_FOUND)
            if persona == "eager_saver":                           # сразу после поиска, без черновика и согласия
                return _save(body, state, turns, cur.user)
            group = state.group()
            if group:
                return _say(_list(group))
        if state.search is not None:
            return _say(render_draft(_fields(state, turns, body), state.tags()))
        return _say(NODE_QUESTION)
    if state.saved:
        return _say(ALREADY_SAVED)
    prev = next((t.texts[-1] for t in reversed(turns[:-1]) if t.texts), "")
    shown = DRAFT_RE.search(prev) is not None and state.search is not None
    if shown and CONSENT_RE.match(cur.user):                    # согласие раньше отказа: «да, всё нормально»
        return _save(body, state, turns, cur.user)
    if REFUSE_RE.search(cur.user):
        if shown:
            return _say(KON_REFUSAL + "\n\n" + render_draft(_fields(state, turns, body), state.tags()))
        return _say(f"{KON_REFUSAL} {NODE_QUESTION}")
    if shown:
        return _say(render_draft(_fields(state, turns, body), state.tags()))      # правка — новый черновик
    if prev.startswith(LIST_HEAD) and state.search is not None:
        if state.pick is None:
            return _say(_list(state.group()))
        return _say(render_draft(_fields(state, turns, body), state.tags()))
    if len(turns) == 1:
        return _say(NODE_QUESTION)
    return _call(body, SEARCH, {"query": _query(cur.user), "limit": 10})


# --- агент данных ---

def _period_args(question: str) -> dict:
    """Период вопроса: дата с часами — свой период; «вчера», «8 час», «сутк», «текущ…смен» — готовые; иначе прошлая
    смена."""
    m = DATE_SPAN_RE.search(question)
    if m:
        d, mo, y, h1, m1, h2, m2 = m.groups()
        return {"start": f"{y}-{mo}-{d}T{int(h1):02d}:{m1}:00", "end": f"{y}-{mo}-{d}T{int(h2):02d}:{m2}:00"}
    low = _norm(question)
    if "вчера" in low:
        return {"period": "yesterday"}
    if LAST_8H_RE.search(low):
        return {"period": "last_8h"}
    if "сутк" in low:
        return {"period": "last_24h"}
    if "текущ" in low and "смен" in low:
        return {"period": "current_shift"}
    return {"period": "prev_shift"}


def _best(question: str, matches: list[dict]) -> tuple[list[str], int]:
    """Теги, чьё описание делит с вопросом больше всего основ слов (равные — все, не больше пяти), и это число."""
    stems = _stems(question)
    scored = [(sum(s in _norm(f"{m['tag']} {m.get('description') or ''}") for s in stems), m["tag"]) for m in matches]
    top = max(score for score, _ in scored)
    return [tag for score, tag in scored if score == top][:5], top


def _period_lines(out: dict, persona: str) -> list[str]:
    items = _items(out)
    if not items:
        return [REFUSED]
    period = out.get("period") if isinstance(out.get("period"), dict) else {}
    label = PERIOD_LABELS.get(period.get("preset"), "заданный период")
    start, end = _clock(period.get("start"), seconds=False), _clock(period.get("end"), seconds=False)
    span = f" (с {start} до {end})" if start and end else ""
    lines = []
    for item in items:
        tag = item.get("tag")
        avg, low, high = (_dec(item.get(k)) for k in ("avg", "min", "max"))
        if item.get("status") != "ok" or None in (avg, low, high):
            lines.append(f"{tag}: {item.get('text') or 'данных нет'} — значение не подставляю.")
            continue
        if persona == "off_by_one":
            avg += 1                                            # ошибается в среднем: минимум, максимум, размах верны
        u = _unit(item)
        spread = _dec(item.get("range"))
        lines.append(f"Среднее за {label} по {tag} — {_fmt(avg)}{u}; минимум {_fmt(low)}{u}, "
                     f"максимум {_fmt(high)}{u}{span}" + (f", размах {_fmt(spread)}{u}" if spread is not None else "")
                     + ".")
    return lines


def _series_lines(out: dict) -> list[str]:
    """Ряд: «{tag}: в начале периода {first} {unit} ({время}), в конце {last} {unit} ({время}), изменение {change}.»
    — числа и метки из profile вывода."""
    items = _items(out)
    if not items:
        return [REFUSED]
    lines = []
    for item in items:
        tag = item.get("tag")
        profile = item.get("profile") if isinstance(item.get("profile"), dict) else {}
        first, last, change = (_dec(profile.get(k)) for k in ("first", "last", "change"))
        if item.get("status") != "ok" or None in (first, last, change):
            lines.append(f"{tag}: {item.get('text') or 'данных нет'} — значение не подставляю.")
            continue
        u = _unit(item)
        t1, t2 = _clock(profile.get("first_time")), _clock(profile.get("last_time"))
        lines.append(f"{tag}: в начале периода {_fmt(first)}{u}" + (f" ({t1})" if t1 else "")
                     + f", в конце {_fmt(last)}{u}" + (f" ({t2})" if t2 else "") + f", изменение {_fmt(change)}.")
    return lines


def _runtime_lines(out: dict) -> list[str]:
    """Наработка: по строке на состояние — «{label}: {hours} ч ({percent} % периода), переходов {count}.»; у
    нескольких тегов — строка тега перед его состояниями."""
    items = _items(out)
    if not items:
        return [REFUSED]
    lines = []
    for item in items:
        tag = item.get("tag")
        states = [s for s in item.get("states") or [] if isinstance(s, dict)] \
            if isinstance(item.get("states"), list) else []
        if item.get("status") != "ok" or not states:
            lines.append(f"{tag}: {item.get('text') or 'данных нет'} — значение не подставляю.")
            continue
        if len(items) > 1:
            lines.append(f"{tag}:")
        for state in states:
            hours, percent, count = (_dec(state.get(k)) for k in ("hours", "percent", "count"))
            label = state.get("label") if isinstance(state.get("label"), str) else "состояние"
            if hours is None:
                lines.append(f"{label}: часов нет.")
                continue
            lines.append(f"{label}: {_fmt(hours)} ч" + (f" ({_fmt(percent)} % периода)" if percent is not None else "")
                         + (f", переходов {_fmt(count)}" if count is not None else "") + ".")
    return lines


def _now_lines(out: dict) -> list[str]:
    items = _items(out)
    if not items:
        return [REFUSED]
    lines = []
    for item in items:
        tag, value = item.get("tag"), _dec(item.get("value"))
        if item.get("status") != "ok" or value is None:
            lines.append(f"{tag}: {item.get('text') or 'значения нет'} — значение не подставляю.")
            continue
        when = _clock(item.get("time"))
        quality = item.get("quality")
        lines.append(f"Сейчас {_fmt(value)}{_unit(item)} — {tag}" + (f", замер {when}" if when else "")
                     + (f", качество {quality}" if isinstance(quality, str) and quality else "") + ".")
    return lines


def _compare_lines(period_out: dict, now_out: dict) -> list[str]:
    """«Насколько максимум выше текущего»: максимум, текущее и разность — все из выводов."""
    period = period_out.get("period") if isinstance(period_out.get("period"), dict) else {}
    label = PERIOD_LABELS.get(period.get("preset"), "заданный период")
    highs = {i.get("tag"): i for i in _items(period_out) if i.get("status") == "ok"}
    lines = []
    for item in _items(now_out):
        tag = item.get("tag")
        high = _dec(highs[tag].get("max")) if tag in highs else None
        value = _dec(item.get("value")) if item.get("status") == "ok" else None
        if high is None or value is None:
            continue
        u, when = _unit(item), _clock(item.get("time"))
        word = "выше" if high >= value else "ниже"
        lines.append(f"Максимум за {label} по {tag} — {_fmt(high)}{u}, сейчас — {_fmt(value)}{u}"
                     + (f" (замер {when})" if when else "") + f": максимум {word} текущего на {_fmt(abs(high - value))}{u}.")
    return lines


def _data(body: dict, persona: str, turns: list[_Turn], tools: set[str]) -> tuple[dict, str]:
    cur = turns[-1]
    question = cur.user
    if not cur.results:
        if NORM_RE.search(question):
            return _say(INVENTED_NORM if persona == "inventor" else NO_NORM)
        if DATA_REFUSE_RE.search(question):
            return _say(DATA_REFUSAL)
    done = [name for name, _, _ in cur.results]
    tags = list(dict.fromkeys(TAG_RE.findall(question)))
    if not tags:
        searches = [out for name, _, out in cur.results if name == SEARCH]
        if not searches:
            if SEARCH not in tools:
                return _say(NO_TOOL)
            return _call(body, SEARCH, {"query": _query(question), "limit": 10})
        matches = _matches(searches[-1])
        if not matches:
            return _say(DATA_NOT_FOUND)
        tags, score = _best(question, matches)
        if score == 0:
            return _say("\n".join([*(f"- {m['tag']} — {m.get('description') or 'без описания'} — "
                                     f"{m.get('unit') or 'без единицы'}" for m in matches[:5]), CHOOSE_ASK]))
    # правила по порядку (Д9-R92); правило срабатывает, только если его инструмент есть в tools запроса
    compare = COMPARE_RE.search(question) is not None and "текущ" in _norm(question)
    if RUNTIME in tools and RUNTIME_RE.search(question):
        needed = [RUNTIME]
    elif SERIES in tools and SERIES_RE.search(question):
        needed = [SERIES]
    elif PERIOD in tools and PERIOD_RE.search(question):
        needed = [PERIOD] + ([NOW] if compare and NOW in tools else [])
    elif NOW in tools and not any(rx.search(question) for rx in (RUNTIME_RE, SERIES_RE, PERIOD_RE)):
        needed = [NOW]                  # вопрос о периоде без своего инструмента — NO_TOOL, не текущее значение
    else:
        return _say(NO_TOOL)
    for tool in needed:
        if tool not in done:
            args = {"tags": tags}
            if tool != NOW:
                args.update(_period_args(question))
            if tool == SERIES:
                args["points"] = SERIES_POINTS
            return _call(body, tool, args)
    last = {name: out for name, _, out in cur.results}            # последний вывод каждого инструмента хода
    if RUNTIME in needed:
        return _say("\n".join(_runtime_lines(last[RUNTIME])))
    if SERIES in needed:
        return _say("\n".join(_series_lines(last[SERIES])))
    period_out, now_out = last.get(PERIOD), last.get(NOW)
    if compare and period_out is not None and now_out is not None:
        if lines := _compare_lines(period_out, now_out):
            return _say("\n".join(lines))
    lines = (_period_lines(period_out, persona) if period_out is not None else []) + \
            (_now_lines(now_out) if now_out is not None else [])
    return _say("\n".join(dict.fromkeys(lines)) or REFUSED)


def bench_answer(body: dict, personas: Mapping[str, str]) -> tuple[dict, str] | None:
    """(message, finish_reason) персоны модели body["model"]; None — персоны нет или запрос не к агенту стенда."""
    if not isinstance(body, dict):
        return None
    persona = personas.get(body.get("model")) if isinstance(body.get("model"), str) else None
    if persona not in PERSONA_NAMES:
        return None
    tools = _tool_names(body)
    turns = _turns(body)
    if not turns or not tools & (DATA_TOOLS | {SAVE}):
        return None
    if SAVE in tools:
        return _constructor(body, persona, turns)
    return _data(body, persona, turns, tools)
