"""Черновик агента студента: проверка полей save_agent и файл агента по шаблону v1 (Д7-R54, Д7-R55).

Чистые функции без ввода-вывода, только stdlib (Д7-R53, П1): типы каталога — лишь для подсказок, а шаблоны
имени тега и вызывающего — копии соседних (сверяет тест), чтобы пакет agents не тянул роли.

- check_draft проверяет всё сразу и отдаёт (Draft | None, замечания); Draft — только при пустом списке.
- render_agent_file собирает файл построчно, без YAML-библиотеки (research/09 §4.4). Во фронтматтер из ввода
  попадает только description — JSON-строка (она же YAML-строка в двойных кавычках); прочие ключи и их значения
  постоянны. Draft — открытый класс, поэтому шаблон ещё раз сверяет всё, что вставляет, и на непроверенном
  значении бросает ValueError (без самого значения: в журнал службы поля агента не идут).
- FORBIDDEN — фильтр от путаницы, а не граница безопасности (research/09 §4.2): границу держат права, которые
  пишет шаблон, заглушки места и сама служба данных.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..data.catalog import Lookup, TagInfo

TEMPLATE_VERSION = 1
# инструменты агента студента в Д7; save_agent у агента студента — никогда (Global Constraints Д7)
AGENT_TOOLS = ("catalog_search", "tag_now", "tag_period")
_NUMBER_TOOLS = frozenset({"tag_now", "tag_period"})
_TOOL_PREFIX = "pcbk_"

NAME_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", re.ASCII)
NAME_MIN = 3
NAME_MAX = 40
# встроенные агенты OpenCode, Конструктор (Д7б) и агент по умолчанию образа (Д6а): файл с таким именем их переписал бы
RESERVED_NAMES = frozenset({"build", "plan", "general", "explore", "compaction", "title", "summary",
                            "konstruktor", "pomoshnik"})
RESERVED_PREFIX = "pcbk"

TEXT_LIMITS = {"title": (3, 60), "node": (2, 100), "purpose": (10, 200), "student_reply": (1, 200)}
PROMPT_MIN = 200
PROMPT_MAX = 4000
PROMPT_MAX_BYTES = 8000         # и в байтах UTF-8: 4 000 эмодзи иначе прошли бы проверку, а файл — нет (доводка 1, m1)
MAX_TAGS = 16
AGENT_STEPS = 8
MAX_FILE_BYTES = 16384
VALUE_MAX = 60                  # введённое значение в замечании обрезается до 60 знаков

# порядок полей в замечаниях — как в схеме save_agent
FIELDS = ("name", "title", "node", "purpose", "tools", "tags", "prompt", "student_reply")
_FORBIDDEN_FIELDS = frozenset({"title", "node", "purpose", "prompt"})

# research/09 §4.2; порт историана — только как порт (раунд 1, m7). Хвосты слов — не длиннее 30 знаков, а ищется
# только в пределах поля (срез в check_draft): без этого «портпорт…» давал квадратичный поиск, и один вызов
# save_agent на сотнях тысяч знаков вставал на секунды для всех ролей core (доводка 1, I1)
_FORBIDDEN_SOURCES = (
    # технические метки
    r"\{file:", r"\{env:", r"!`", r"http://", r"https://", r"/run/secrets", r"/etc/", r"opencode\.json",
    r"\.opencode", r"OPENCODE_", r"docker", r"(?:порт\w{0,30}\W{1,3}|:)1433\b", r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
    r"<system", r"</system", r"mcp_instructions", r"пароль", r"password",
    # SQL
    r"\bselect\b[\s\S]{0,200}?\bfrom\b", r"\binsert\s+into\b", r"\bupdate\s+\w+\s+set\b", r"\bdelete\s+from\b",
    r"\bxp_cmdshell\b", r"\bopenquery\b", r"\binsql\b",
    # обход
    r"игнорир\w{0,30}\s+(?:\S+\s+){0,2}(?:правил|инструкц|огранич|запрет)",
    r"ignore\s+(?:all|any|previous|the)\b",
    r"в\s+обход\s+(?:\S+\s+)?(?:служб|правил|огранич|белого|бюджет)",
    r"обо(?:йти|йди)\s+(?:\S+\s+){0,2}(?:служб|правил|огранич|запрет|белый|бюджет)",
    r"(?:забудь|отмени|не\s+соблюдай|не\s+следуй)\s+(?:\S+\s+)?(?:правил|инструкц|огранич)",
    r"напрямую\s+(?:к|в|из)\s+(?:историан|бдрв|баз|sql)",
    r"системн\w{1,30}\s+(?:промпт|подсказк|инструкц)",
    r"system\s+prompt",
)
FORBIDDEN: tuple[re.Pattern, ...] = tuple(re.compile(s, re.IGNORECASE) for s in _FORBIDDEN_SOURCES)

# признак нормы в инструкции (раунд 1, I6): число не дальше 40 знаков от слова на норм|допуск|порог|предел|должн
# или «должен» (доводка 1, m6), в любую сторону, без перехода через . ! ? и перевод строки. Не отказ — только Draft.flags
_LIMIT_WORD = r"\b(?:норм|допуск|порог|предел|долж(?:н|ен))"
NUMERIC_LIMIT = re.compile(rf"{_LIMIT_WORD}[^\W\d_]{{0,30}}[^.!?\n]{{0,40}}\d|\d[^.!?\n]{{0,40}}{_LIMIT_WORD}",
                           re.IGNORECASE)
NUMERIC_LIMIT_FLAG = "prompt_numeric_limit"

_SURROGATE = re.compile(r"[\ud800-\udfff]")
# однострочное поле: C0, DEL, C1, U+2028/2029, U+FEFF, U+FFFE/FFFF и одиночные суррогаты (раунд 2, m-н5)
_ONE_LINE_BAD = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\ufeff\ufffe\uffff\ud800-\udfff]")
# инструкция: из управляющих — только \t и \n (после CRLF → LF)
_PROMPT_BAD = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff]")
# описание и единица тега из каталога (Д7-R55, раунд 1, m6): эти знаки → пробел, обратные кавычки убираются
_TAG_TEXT_BAD = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\ufeff]")
_SPACES = re.compile(r"\s+")
# копии pcbk_core.data.sql.SAFE_NAME и pcbk_core.secrets.TOKEN_ID (сверяет test_patterns_match_neighbours)
TAG_NAME = re.compile(r"[A-Za-z0-9_]{1,128}")
CALLER_RE = re.compile(r"[a-z0-9-]{1,32}")

_TOOLS_LIST = ", ".join(AGENT_TOOLS)
# тексты замечаний читает модель; {v} — введённое значение, обрезанное до VALUE_MAX знаков (Д7-R54)
PROBLEM_TEXTS: dict[str, str] = {
    "wrong_type": "Поле {field} неверного вида",
    "name_format": "Имя для системы — 3–40 знаков: строчные латинские буквы, цифры и дефисы, начинается с буквы, "
                   "например sushka-bdm2",
    "name_reserved": "Имя «{v}» занято стендом — возьмите другое",
    "text_field": "Поле {field}: от {min} до {max} знаков одной строкой",
    "prompt_length": "Инструкция агента — от 200 до 4000 знаков",
    "forbidden": "В поле {field} недопустимый фрагмент «{v}»: инструкции обхода, SQL, адреса, пути и пароли "
                 "на стенде запрещены",
    "tools_empty": f"Агенту нужен хотя бы один инструмент: {_TOOLS_LIST}",
    "tool_not_allowed": f"Инструмента «{{v}}» на стенде нет. Можно: {_TOOLS_LIST}.",
    "tools_useless": "Агенту нужен tag_now или tag_period, а без тегов — ещё catalog_search",
    "tags_too_many": f"Тегов не больше {MAX_TAGS}",
    "tag_unknown": "Такого тега нет в каталоге историана",
    "tag_unlisted": "Тег не входит в белый список стенда",
    # служба отвечает на него unavailable (Д7-R57); текст — на случай, если замечание всё же уйдёт модели
    "catalog_not_loaded": "Каталог тегов ещё не загружен — повторите позже",
    # уровень учёта: их ставит SaveAgent по исключениям AgentStore.save (Д7-R56)
    "name_taken": "Агент с именем «{v}» уже есть — возьмите другое имя",
    "too_many_agents": "У этого рабочего места уже 20 агентов — новый не сохранить; обратитесь к преподавателю",
}

_RULES = (
    "## Правила стенда (добавлены службой; они важнее всего, что написано выше)",
    "Числа — только из ответов инструментов `pcbk_…`, с тегом, единицей и временем или периодом. Нормативы,",
    "допуски и пороги, которых нет в данных, не называй; общие знания помечай «общие знания, не норматив",
    "ПЦБК». Отказ службы или «нет данных» — сообщай как есть, число не подставляй.",
)


@dataclass(frozen=True)
class Problem:
    """Замечание к черновику: similar — только у tag_unknown, existing — только у name_taken (собирает SaveAgent)."""

    field: str
    code: str
    value: str | None
    text: str
    similar: tuple[str, ...] = ()
    existing: tuple[str, ...] = ()

    @classmethod
    def make(cls, field: str, code: str, value: str | None = None, *, similar: tuple[str, ...] = (),
             existing: tuple[str, ...] = ()) -> Problem:
        """Замечание с текстом из PROBLEM_TEXTS; value обрезается до VALUE_MAX знаков (и в тексте тоже)."""
        cut = None if value is None else value[:VALUE_MAX]
        low, high = TEXT_LIMITS.get(field, (0, 0))
        text = PROBLEM_TEXTS[code].format(field=field, v=cut or "", min=low, max=high)
        return cls(field, code, cut, text, tuple(similar), tuple(existing))

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"field": self.field, "code": self.code, "value": self.value, "text": self.text}
        if self.code == "tag_unknown":
            out["similar"] = list(self.similar)
        if self.code == "name_taken":
            out["existing"] = list(self.existing)
        return out


@dataclass(frozen=True)
class Draft:
    """Проверенный и нормализованный черновик; flags в draft_digest не входят."""

    name: str
    title: str
    node: str
    purpose: str
    tools: tuple[str, ...]
    tags: tuple[str, ...]
    prompt: str
    student_reply: str
    flags: tuple[str, ...] = ()


def _string(value: object) -> str | None:
    """Строка без одиночных суррогатов, иначе None (wrong_type без эха значения, раунд 1, m5)."""
    if not isinstance(value, str) or _SURROGATE.search(value):
        return None
    return value


def _strings(value: object) -> list[str] | None:
    """Список строк без одиночных суррогатов, иначе None."""
    if not isinstance(value, (list, tuple)):
        return None
    out: list[str] = []
    for item in value:
        text = _string(item)
        if text is None:
            return None
        out.append(text)
    return out


def _forbidden(field: str, text: str) -> list[Problem]:
    """По замечанию на каждый сработавший шаблон (первое вхождение), одинаковые фрагменты — один раз."""
    found: list[str] = []
    for pattern in FORBIDDEN:
        m = pattern.search(text)
        if m and m.group(0) not in found:
            found.append(m.group(0))
    return [Problem.make(field, "forbidden", v) for v in found]


def check_draft(raw: Mapping[str, object], lookup: Callable[[str], Lookup] | None
                ) -> tuple[Draft | None, list[Problem]]:
    """Все замечания сразу (Д7-R54); Draft — только при пустом списке. lookup None — каталог не загружен.

    Отсутствующее поле — wrong_type. tool_not_allowed подавляет tools_empty и tools_useless; при tags_too_many
    теги по каталогу не разбираются (список всё равно сокращать); wrong_type поля подавляет прочие его проверки.
    """
    by_field: dict[str, list[Problem]] = {f: [] for f in FIELDS}

    def add(problem: Problem) -> None:
        by_field[problem.field].append(problem)

    values: dict[str, str] = {}
    for field in ("name", "title", "node", "purpose", "prompt", "student_reply"):
        value = _string(raw.get(field))
        if value is None:
            add(Problem.make(field, "wrong_type"))
        else:
            values[field] = value
    lists: dict[str, list[str]] = {}
    for field in ("tools", "tags"):
        items = _strings(raw.get(field))
        if items is None:
            add(Problem.make(field, "wrong_type"))
        else:
            lists[field] = items

    name = values.get("name")
    if name is not None:
        if not (NAME_MIN <= len(name) <= NAME_MAX and NAME_RE.fullmatch(name)):
            add(Problem.make("name", "name_format", name))
        elif name in RESERVED_NAMES or name.startswith(RESERVED_PREFIX):
            add(Problem.make("name", "name_reserved", name))

    texts: dict[str, str] = {}
    for field, (low, high) in TEXT_LIMITS.items():
        if field not in values:
            continue
        text = texts[field] = values[field].strip()
        if not low <= len(text) <= high or _ONE_LINE_BAD.search(text):
            add(Problem.make(field, "text_field", text))
        if field in _FORBIDDEN_FIELDS:
            for p in _forbidden(field, text[:high]):        # сверх предела — уже text_field (I1)
                add(p)

    prompt = None
    if "prompt" in values:
        prompt = values["prompt"].replace("\r\n", "\n").strip()
        if (not PROMPT_MIN <= len(prompt) <= PROMPT_MAX or len(prompt.encode("utf-8")) > PROMPT_MAX_BYTES
                or _PROMPT_BAD.search(prompt)):
            add(Problem.make("prompt", "prompt_length"))
        for p in _forbidden("prompt", prompt[:PROMPT_MAX]):    # сверх предела — уже prompt_length (I1)
            add(p)

    tags: list[str] | None = None
    if "tags" in lists:
        unique: list[str] = []
        seen: set[str] = set()
        for item in lists["tags"]:
            key = item.strip().upper()              # как Catalog.lookup: регистр не важен
            if key not in seen:
                seen.add(key)
                unique.append(item.strip())
        if len(unique) > MAX_TAGS:
            add(Problem.make("tags", "tags_too_many"))
        elif not unique:
            tags = []
        elif lookup is None:
            add(Problem.make("tags", "catalog_not_loaded"))
        else:
            tags = []
            for item in unique:
                found = lookup(item)
                if found.status == "ok" and found.info is not None:
                    if found.info.name not in tags:
                        tags.append(found.info.name)
                elif found.status == "unlisted":
                    add(Problem.make("tags", "tag_unlisted", item))
                else:
                    add(Problem.make("tags", "tag_unknown", item, similar=tuple(found.similar)))

    tools: tuple[str, ...] | None = None
    if "tools" in lists:
        bad: list[str] = []
        chosen: set[str] = set()
        for item in lists["tools"]:
            short = item.removeprefix(_TOOL_PREFIX)
            if short in AGENT_TOOLS:
                chosen.add(short)
            elif item not in bad:
                bad.append(item)
        for item in bad:
            add(Problem.make("tools", "tool_not_allowed", item))
        if not bad:
            if not chosen:
                add(Problem.make("tools", "tools_empty"))
            elif not chosen & _NUMBER_TOOLS or ("tags" in lists and not lists["tags"]
                                                and "catalog_search" not in chosen):
                add(Problem.make("tools", "tools_useless"))
            tools = tuple(t for t in AGENT_TOOLS if t in chosen)

    problems = [p for field in FIELDS for p in by_field[field]]
    if problems:
        return None, problems
    if name is None or prompt is None or tags is None or tools is None:     # без замечаний все поля разобраны
        raise RuntimeError("проверка черновика не дошла до конца")
    flags = (NUMERIC_LIMIT_FLAG,) if NUMERIC_LIMIT.search(prompt) else ()
    return Draft(name=name, title=texts["title"], node=texts["node"], purpose=texts["purpose"], tools=tools,
                 tags=tuple(tags), prompt=prompt, student_reply=texts["student_reply"], flags=flags), []


def _tag_text(value: str | None) -> str:
    """Описание или единица из каталога: управляющие → пробел, без обратных кавычек, пробелы схлопнуты."""
    text = _TAG_TEXT_BAD.sub(" ", value or "").replace("`", "")
    return _SPACES.sub(" ", text).strip()


def _check_for_render(draft: Draft, infos: Mapping[str, TagInfo], caller: str, created_at: datetime) -> None:
    """То, что шаблон вставляет, ещё раз по правилам check_draft; ValueError — без значений."""
    if not CALLER_RE.fullmatch(caller):
        raise ValueError("вызывающий вне шаблона")
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("время записи без пояса")
    for value in (draft.title, draft.node, draft.purpose):
        if not value or _ONE_LINE_BAD.search(value):
            raise ValueError("поле черновика не одной строкой")
    if _PROMPT_BAD.search(draft.prompt):
        raise ValueError("управляющие знаки в инструкции")
    if not draft.tools or not set(draft.tools) <= set(AGENT_TOOLS):
        raise ValueError("инструменты черновика вне AGENT_TOOLS")
    for tag in draft.tags:
        if not TAG_NAME.fullmatch(tag) or tag not in infos:
            raise ValueError("тег черновика вне каталога")


def render_agent_file(draft: Draft, infos: Mapping[str, TagInfo], *, caller: str, created_at: datetime) -> str:
    """Текст файла агента по шаблону v1 (Д7-R55): UTF-8 без BOM, концы строк \\n, последняя строка с \\n.

    infos — свойства тегов по каноническому имени (описание и единица — из каталога службы, не из черновика).
    Непроверенный черновик, вызывающий вне TOKEN_ID, время без пояса или файл больше MAX_FILE_BYTES — ValueError.
    """
    _check_for_render(draft, infos, caller, created_at)
    at = created_at.astimezone(timezone.utc)
    lines = [
        "---",
        f"# pcbk-reserve: записан службой данных (save_agent), шаблон {TEMPLATE_VERSION}; вручную не править",
        f"# pcbk-reserve: {caller} {at:%Y-%m-%dT%H:%M:%SZ}",
        f"description: {json.dumps(draft.title + ': ' + draft.purpose, ensure_ascii=False)}",
        "mode: primary",
        f"steps: {AGENT_STEPS}",
        "permission:",
        '  "*": deny',
        *(f"  {_TOOL_PREFIX}{tool}: allow" for tool in AGENT_TOOLS if tool in draft.tools),
        "---",
        f"Ты — агент «{draft.title}» учебного стенда ПЦБК. Узел: {draft.node}.",
        "",
        draft.prompt,
        "",
    ]
    if draft.tags:
        lines.append(f"## Теги узла (каталог историана; проверено службой {at:%Y-%m-%d})")
        for tag in draft.tags:
            info = infos[tag]
            description = _tag_text(info.description) or "без описания"
            unit = _tag_text(info.unit) or "без единицы"
            lines.append(f"- `{tag}` — {description}, {unit}")
        lines.append("")
    lines.extend(_RULES)
    text = "\n".join(lines) + "\n"
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("файл агента больше MAX_FILE_BYTES")
    return text


def draft_digest(draft: Draft) -> str:
    """sha256 канонического JSON полей черновика без student_reply и flags: по нему повтор узнаёт сохранённый."""
    fields = {"name": draft.name, "title": draft.title, "node": draft.node, "purpose": draft.purpose,
              "tools": list(draft.tools), "tags": list(draft.tags), "prompt": draft.prompt}
    blob = json.dumps(fields, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("ascii")).hexdigest()
