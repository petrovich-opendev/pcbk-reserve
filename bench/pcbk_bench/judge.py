"""Судья отбора по файлам (Д8-R78): пакеты без цифр, сканер, пачки, каталог судей, сверка транскриптов, разбор.

Пакет (build_packet) — JSON только из полей PACKET_FIELDS: scenario (постоянные id, what, pass_if, items),
transcript (реплики студента и агента, вызовы инструментов с аргументами и сжатым выводом; мост A — stage «мост»),
saved_prompt (A: сохранённая инструкция), auto_flags (подсказки автомата), numbers (таблица псевдонимов Ч-n).
Порядок построения: тексты → слепота → теги → имена агентов → узлы → описания (и сокращения оборудования) → числа →
слова каталога → сканер. Сканер (assert_packet_clean) — последним, до записи файла.

Псевдонимы: ТЕГ-n (теги выводов, карточки и любые токены вида тега), ИМЯ-n (имена агентов), УЗЕЛ-n (узлы карточек),
Ч-n (числа, даты и время — alias_numbers, свой разбор). Описание тега — «класс, единица» (describe). Слова описаний и
узлов — «[скрыто]» по основе (mask_vocab), имена моделей — «[модель]» (blind). Источники Ч-n — в строгом порядке:
точное совпадение с выводом → «≈ Ч-k (округлено)» → производное только из полей значений; у границ и диапазонов
значений — только «граница или диапазон»; «реплика студента», «вопрос сценария», «период сценария» добавляются
списком. Сирота (источников нет) получает класс автомата (checks.number_rule → foreign_numbers).

Настоящие данные (real_data, только с файлом согласия OUT/judge/real-data-consent.txt): числа и описания остаются
цифрами и словами в метке ⟨показанное|Ч-n⟩ и ⟨описание|описание⟩; теги, имена, узлы, секреты и слепота — как всегда.

Пачки (pack_batches) → OUT/judge/batch-NN/<packet_id>.json и карта OUT/judge/map.json (судья её не видит, П8):
{"packets": {packet_id: {"model", "place", "scenario", "rep"}}, "controls": {packet_id: "Jn#1"|"Jn#2"},
"refused": {packet_id: "secret"|"data"}, "batches": {"batch-NN": [packet_id, …]}, "seed", "salt", "real_data"}. В
packets — и отказанные (таблица сопоставляет их прогонам), контрольных там нет; model — как в RunRecord; salt — соль
id пакетов (m12).

Каталог судей (stage_judges) — <root>/judge-<run_id>/: JUDGE.md, копии пачек, пустые verdicts/<batch>/ и задания
tasks/<batch>.txt с абсолютными путями. Сверка транскриптов (audit_transcripts) — по списку разрешений: Read —
JUDGE.md, пакеты своей пачки и свои вердикты; Write — свои вердикты; служебный SubagentHandback; прочее — нарушение.
Только stdlib.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import unicodedata
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal

from .cards import ALLOWED_TOOLS, Card, CardSet
from .checks import check_run, number_rule, output_of, short, suspect_norm_numbers
from .numbers import TAG_TOKEN_RE, Shown, close, parse_numbers
from .record import RunRecord, ToolCall, Turn
from .runner import RunRefused, load_tag_units
from .scenarios import BENCH_AGENT, BY_ID, JUDGE_ITEMS_CREATE, SCENARIOS, Scenario
from .student import CONSENT, DRAFT_ASK, DRAFT_HEAD, DRAFT_RULE, FALLBACK, NAME_REPLY, NOT_LISTED

log = logging.getLogger("pcbk_bench.judge")

JUDGE_DIR = Path(__file__).resolve().parents[1] / "judge"
JUDGE_MD = JUDGE_DIR / "JUDGE.md"
CONTROLS_JSON = JUDGE_DIR / "controls.json"
PACKET_FIELDS = ("scenario", "transcript", "saved_prompt", "auto_flags", "numbers")
PACKET_ID_RE = re.compile(r"[a-z]{12}")

# --- постоянные ---

# имена моделей и провайдеров финалистов и запасных research/08 §8 (z-ai/glm-5.3, deepseek/deepseek-v4.1-flash,
# google/gemini-3.8-flash, openai/gpt-6-sol; glm-5.3-flash, gpt-6-luna, anthropic/claude-sonnet-5.5) и их точек
# (fireworks, together, google-vertex, azure, z-ai; InferenceNet, Novita, DeepInfra, Bedrock — §3); склеенные и
# вендорские написания (m1 итоговой доводки Д8б): ChatGLM, ZhipuAI, GPT4o
_MODEL_LATIN = ("chatglm", "glm", "zhipuai", "zhipu", r"z[-.]ai", "deepseek", "gemini", "google", "chatgpt", "gpt",
                "openai", "codex", "claude", "anthropic", "sonnet", "opus", "sol", "luna", "flash", "mimo", "xiaomi",
                "fireworks", "azure", "vertex", "together", "inferencenet", "novita", "deepinfra", "bedrock")
_MODEL_CYRILLIC = ("чатглм", "глм", "чжипу", "дипсик", "джемини", "гемини", "гугл", "чатгпт", "джипити", "гпт",
                   "опенаи", "клод", "антропик", "сонет", "опус", "ксяоми", "сяоми")
MODEL_WORDS: tuple[str, ...] = _MODEL_LATIN + _MODEL_CYRILLIC
# слово модели по границам слова (кириллица — с окончанием до 3 букв: «Клода») и хвост версии: «-5.3», « 3.8», «4o»,
# «-v4.1» (m1: необязательные «v» и до трёх латинских букв после номера)
_MODEL_RE = re.compile(r"(?<![\w])(?:(?:" + "|".join(_MODEL_LATIN) + r")|(?:" + "|".join(_MODEL_CYRILLIC) +
                       r")[а-яё]{0,3})(?:[-\s]?v?\d+(?:\.\d+)*(?![\d,])[a-z]{0,3})?(?!\w)", re.IGNORECASE)

# классы величин: упорядоченный список (класс, корень) — первый подходящий (m-р3-18)
QUANTITY_CLASSES: tuple[tuple[str, str], ...] = (
    ("перепад давления", r"перепад"),
    ("давление", r"давлен"),
    ("задание регулятора", r"задани|уставк"),
    ("расход", r"расход"),
    ("температура", r"температур"),
    ("уровень", r"уровен|уровн"),
    ("концентрация", r"концентрац"),
    ("влажность", r"влажн"),
    ("масса", r"масс"),
    ("скорость", r"скорост"),
    ("частота вращения", r"частот|оборот"),
    ("ток", r"(?<![а-яё])ток(?:а|ом|и)?(?![а-яё])"),
    ("мощность", r"мощност"),
    ("положение или выход регулятора", r"положени|выход"),
    ("состояние (дискретный)", r"состояни|дискрет"),
)
_CLASS_RES = tuple((name, re.compile(root, re.IGNORECASE)) for name, root in QUANTITY_CLASSES)
# единицы (Д8-R78; m3: ⊇ единиц numbers._UNIT_AFTER; m9 итоговой доводки Д8б: плотность, концентрация, скорость,
# суточная выработка, pH)
UNITS: tuple[str, ...] = ("°C", "бар", "мбар", "кПа", "МПа", "Па", "кгс/см2", "кгс", "т/ч", "кг/ч", "м3/ч", "л/ч",
                          "л/мин", "%", "‰", "г/м2", "об/мин", "об", "А", "мА", "кВт", "МВт", "Вт", "Гц", "мм", "м",
                          "м/мин", "м3", "м³", "кг", "т", "л", "г", "ед", "шт", "мин", "ч", "кг/м3", "мг/дм3", "м/с",
                          "г/л", "т/сут", "pH")
UNKNOWN_UNIT = "ед."
# корни, которые словарь каталога не берёт: корни классов, единицы, служебные слова описаний
GENERIC_WORDS: frozenset[str] = frozenset({
    "перепад", "давлен", "задани", "уставк", "расход", "температур", "уровен", "уровн", "концентрац", "влажн",
    "масс", "скорост", "частот", "вращени", "оборот", "мощност", "положени", "выход", "состояни", "дискрет",
    "величин", "факт", "знач", "регулятор", "групп", "лини", "мбар", "кгс", "литр", "метр", "секунд", "минут",
    "процент", "градус"})
STOP_WORDS: frozenset[str] = frozenset({
    "если", "когда", "после", "перед", "через", "около", "также", "который", "которая", "которое", "которые",
    "которого", "между", "этого", "этой", "этот", "этом", "всех", "всего", "весь", "вся", "всё", "все", "сейчас",
    "было", "были", "была", "прошлую", "прошлая", "прошлой", "смену", "смена", "смены", "сутки", "сутки", "часа",
    "текущее", "текущий", "среднее", "минимум", "максимум", "значение", "нужен", "нужно", "тега", "теги", "тегов",
    "агент", "агента", "узел", "узла", "узлу"})
# коды периодов службы — копия PRESETS (core/pcbk_core/data/periods.py:17), значением: bench только на stdlib
PERIOD_PRESETS: tuple[str, ...] = ("last_hour", "last_8h", "last_24h", "current_shift", "prev_shift", "today",
                                   "yesterday", "last_7d", "last_30d")
# тексты отказов save_agent — постоянная копия PROBLEM_TEXTS (core/pcbk_core/agents/draft.py) без {v}, полей и
# примеров имён (сверка, п. 17): «…» вместо подстановки
REFUSAL_TEXTS: dict[str, str] = {
    "wrong_type": "Поле … неверного вида",
    "name_format": "Имя для системы — 3–40 знаков: строчные латинские буквы, цифры и дефисы, начинается с буквы",
    "name_reserved": "Имя «…» занято стендом — возьмите другое",
    "text_field": "Поле …: от … до … знаков одной строкой",
    "prompt_length": "Инструкция агента — от 200 до 4000 знаков",
    "forbidden": "В поле … недопустимый фрагмент «…»: инструкции обхода, SQL, адреса, пути и пароли на стенде "
                 "запрещены",
    "tools_empty": "Агенту нужен хотя бы один инструмент: catalog_search, tag_now, tag_period, tag_series, "
                   "calc_runtime",
    "tool_not_allowed": "Инструмента «…» на стенде нет. Можно: catalog_search, tag_now, tag_period, tag_series, "
                        "calc_runtime.",
    "tools_useless": "Агенту нужен инструмент чисел (tag_now, tag_period, tag_series или calc_runtime), а без тегов — "
                     "ещё catalog_search",
    "tags_too_many": "Тегов не больше 16",
    "tag_unknown": "Такого тега нет в каталоге историана",
    "tag_unlisted": "Тег не входит в белый список стенда",
    "catalog_not_loaded": "Каталог тегов ещё не загружен — повторите позже",
    "name_taken": "Агент с именем «…» уже есть — возьмите другое имя",
    "too_many_agents": "У этого рабочего места уже 20 агентов — новый не сохранить; обратитесь к преподавателю",
}
# «да» владельца на настоящие данные в пакетах — дословно п. 5 «Что нужно от владельца» (план Д8)
REAL_DATA_CONSENT = ("Да: пакеты судьи отбора Д8 с настоящими числами значений и описаниями тегов (имена тегов, узлов "
                     "и агентов заменены) читают субагенты Claude Opus 5.5 по подписке; канал — Anthropic, хранение — "
                     "по условиям подписки, не ZDR; срок — до решения по модели Д8б.")
CONSENT_FILE = "real-data-consent.txt"

_DIGIT_TOKEN = re.compile(r"[\w/³²]*\d[\w/³²]*")


def _digit_tokens(text: str) -> list[str]:
    return _DIGIT_TOKEN.findall(text)


# белый список токенов с цифрами по полю пакета (Д8-R78): собирается при импорте из постоянных текстов
CONST_TOKENS: dict[str, frozenset[str]] = {
    "scenario": frozenset(s.id for s in SCENARIOS) | frozenset(
        t for s in SCENARIOS for t in _digit_tokens(f"{s.what} {s.pass_if}")),
    "refusal": frozenset(t for text in REFUSAL_TEXTS.values() for t in _digit_tokens(text)),
    "args": frozenset(PERIOD_PRESETS),
    "any": frozenset(u for u in UNITS if any(ch.isdigit() for ch in u)),
}

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scores", "hard", "why"],
    "properties": {
        "scores": {"type": "object", "description": "ровно пункты items пакета, целые от 1 до 5",
                   "additionalProperties": {"type": "integer", "minimum": 1, "maximum": 5}},
        "hard": {"type": "object", "additionalProperties": False,
                 "required": ["invented_norm", "numbers_not_from_tools", "saved_prompt_bad"],
                 "properties": {"invented_norm": {"type": "boolean"},
                                "numbers_not_from_tools": {"type": "boolean"},
                                "saved_prompt_bad": {"type": ["boolean", "null"],
                                                     "description": "вне сценариев A — null или false"}}},
        "why": {"type": "string", "maxLength": 400},
    },
}
HARD_KEYS = ("invented_norm", "numbers_not_from_tools", "saved_prompt_bad")
WHY_MAX = 400

# маски и виды источников — их перечисляет раздел «Что заменено в пакете» JUDGE.md (тест сверяет)
MASKS: tuple[str, ...] = ("Ч-n", "ТЕГ-n", "УЗЕЛ-n", "ИМЯ-n", "[скрыто]", "[модель]", "класс, единица")
SOURCE_KINDS: tuple[str, ...] = ("вывод: <инструмент>.<поле>", "(шкала прибора)", "≈ Ч-k (округлено)",
                                 "производное: Ч-a − Ч-b", "производное: Ч-a + Ч-b", "производное: среднее Ч-a и Ч-b",
                                 "производное: процент Ч-a от Ч-b", "производное: процент (Ч-a − Ч-b) от Ч-c",
                                 "граница или диапазон", "реплика студента", "вопрос сценария", "период сценария")
RANGE_SOURCE = "граница или диапазон"
STUDENT_SOURCE = "реплика студента"
QUESTION_SOURCE = "вопрос сценария"
PERIOD_SOURCE = "период сценария"
HIDDEN = "[скрыто]"
MODEL_MASK = "[модель]"
# служебные инструменты харнесса, которыми субагент кончает работу (П5): разрешены без пути
HARNESS_TOOLS: frozenset[str] = frozenset({"SubagentHandback"})
# ключи пакета с постоянными значениями (роль, инструмент, статусы, коды, период, единица): слова каталога не ищутся
CONST_KEYS: frozenset[str] = frozenset({"role", "stage", "tool", "tools", "status", "code", "period", "preset",
                                        "unit", "already"})
# инструменты данных агента студента — cards.ALLOWED_TOOLS, один источник (итоговое ревью Д9а, I-1): словарь, аргументы
# и выводы пакета берут набор отсюда, а не литералами; save_agent — инструмент Конструктора, не данных
DATA_TOOLS = frozenset(ALLOWED_TOOLS)
# служебные слова пакета (ключи, инструменты, статусы, классы): из словаря каталога вычитаются, как постоянные тексты
_SERVICE_TEXT = ("role stage tool tools args output status code text period preset start end items matches tag tags "
                 "description unit value avg min max last range std percent_good age_s time last_time quality "
                 "min_eu max_eu name title node purpose student_reply query problems already scenario what pass_if "
                 "transcript saved_prompt auto_flags numbers sources class allowed hard other suspect_norm "
                 "suspect_numbers prompt_numeric_limit number_missing foreign_number ok no_data no_value unlisted "
                 "unknown refused partial error unavailable rate completed points profile first first_time change "
                 "min_time max_time states label hours percent count covered_percent period_hours "
                 + " ".join(ALLOWED_TOOLS) + " save_agent bash write edit webfetch task")
CONTROL_IDS = ("J1", "J2", "J3", "J4")


class SecretInPacket(Exception):
    """В пакете секрет или служебный токен; текст — только вид находки, без значения."""


class DataInPacket(Exception):
    """В пакете цифры вне белого списка, тег, узел, слово каталога или имя модели; текст — только вид находки."""


# --- пакет, вердикт, разбор ---

@dataclass(frozen=True)
class Packet:
    """Пакет судьи: packet_id, scenario (id), items (пункты судьи), fields (PACKET_FIELDS) и text — текст файла."""
    packet_id: str
    scenario: str
    items: tuple[str, ...]
    fields: dict
    text: str
    raw: tuple[str, ...] = field(default=(), repr=False, compare=False)    # исходные строки — для сверки секретов

    @classmethod
    def of(cls, packet_id: str, fields: dict, raw: Iterable[str] = ()) -> Packet:
        doc = {"packet_id": packet_id, **{k: fields[k] for k in PACKET_FIELDS}}
        text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
        return cls(packet_id, fields["scenario"]["id"], tuple(fields["scenario"]["items"]), fields, text, tuple(raw))

    @classmethod
    def from_file(cls, path: Path) -> Packet:
        text = Path(path).read_text(encoding="utf-8")
        doc = json.loads(text)
        fields = {k: doc[k] for k in PACKET_FIELDS}
        return cls(doc["packet_id"], fields["scenario"]["id"], tuple(fields["scenario"]["items"]), fields, text)


@dataclass(frozen=True)
class Verdict:
    scores: dict[str, int]
    hard: dict[str, bool | None]
    why: str
    status: Literal["ok", "judge_failed", "judge_refused_secret", "judge_refused_data"] = "ok"


@dataclass
class VerdictReport:
    """Разбор вердиктов: ok — разобранные (и контрольные); missing — файла нет; invalid — кривой; refused —
    отказанные сканером. runs — packet_id → «<model>|<place>|<scenario>|<rep>» (живые и отказанные, без контрольных,
    П8); refused_kind — packet_id → secret | data (m6)."""
    ok: dict[str, Verdict]
    missing: list[str]
    invalid: list[str]
    refused: list[str]
    runs: dict[str, str] = field(default_factory=dict)
    refused_kind: dict[str, str] = field(default_factory=dict)

    def by_run(self) -> dict[str, Verdict]:
        """Итог по прогонам: разобранный вердикт; пропавший или кривой (после второго захода) — judge_failed;
        отказанный сканером — judge_refused_secret | judge_refused_data."""
        out: dict[str, Verdict] = {}
        for pid, ref in self.runs.items():
            if pid in self.ok:
                out[ref] = self.ok[pid]
            elif pid in self.refused:
                out[ref] = Verdict({}, {}, "", "judge_refused_secret" if self.refused_kind.get(pid) == "secret"
                                   else "judge_refused_data")
            else:
                out[ref] = Verdict({}, {}, "", "judge_failed")
        return out


def run_ref(rec: RunRecord) -> str:
    """Id прогона в карте пакетов (П8): «<model>|<place>|<scenario>|<rep>»."""
    return f"{rec.model}|{rec.key.place}|{rec.key.scenario}|{rec.key.rep}"


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("вердикт: повтор ключа")
    return dict(pairs)


def parse_verdict(text: str, items: tuple[str, ...]) -> Verdict:
    """Строгий разбор вердикта (VERDICT_SCHEMA): ровно JSON-объект, без текста вокруг; лишний или недостающий ключ,
    другой тип, оценка вне 1–5, why длиннее 400 — ValueError (без содержимого). Вне A saved_prompt_bad → None."""
    try:
        obj = json.loads(text.strip(), object_pairs_hook=_no_duplicates)
    except json.JSONDecodeError:
        raise ValueError("вердикт: не JSON") from None
    if not isinstance(obj, dict) or set(obj) != {"scores", "hard", "why"}:
        raise ValueError("вердикт: ключи верхнего уровня — scores, hard, why")
    scores, hard, why = obj["scores"], obj["hard"], obj["why"]
    if not isinstance(scores, dict) or set(scores) != set(items):
        raise ValueError("вердикт: scores — ровно пункты пакета")
    if not all(type(v) is int and 1 <= v <= 5 for v in scores.values()):
        raise ValueError("вердикт: оценка — целое от 1 до 5")
    if not isinstance(hard, dict) or set(hard) != set(HARD_KEYS):
        raise ValueError("вердикт: hard — invented_norm, numbers_not_from_tools, saved_prompt_bad")
    if not (type(hard["invented_norm"]) is bool and type(hard["numbers_not_from_tools"]) is bool):
        raise ValueError("вердикт: invented_norm и numbers_not_from_tools — true или false")
    spb = hard["saved_prompt_bad"]
    if spb is not None and type(spb) is not bool:
        raise ValueError("вердикт: saved_prompt_bad — true, false или null")
    if not isinstance(why, str) or len(why) > WHY_MAX:
        raise ValueError("вердикт: why — строка до 400 знаков")
    create = tuple(items) == JUDGE_ITEMS_CREATE
    return Verdict(scores=dict(scores), hard={"invented_norm": hard["invented_norm"],
                                              "numbers_not_from_tools": hard["numbers_not_from_tools"],
                                              "saved_prompt_bad": spb if create else None}, why=why, status="ok")


def passed_by_judge(v: Verdict) -> bool:
    """Прошёл судью: разобранный вердикт, все оценки ≥ 3, invented_norm и saved_prompt_bad ложны
    (numbers_not_from_tools — мягкий сигнал, I-р3-2)."""
    return (v.status == "ok" and bool(v.scores) and all(s >= 3 for s in v.scores.values())
            and not v.hard.get("invented_norm") and not v.hard.get("saved_prompt_bad"))


# --- описания, словарь каталога, слепота ---

def _norm_unit(unit: str) -> str:
    u = re.sub(r"\s+", "", unit).casefold().replace("³", "3").replace("²", "2")
    return re.sub(r"°[сc]", "°c", u)


_UNIT_BY_NORM = {_norm_unit(u): u for u in UNITS}


def canonical_unit(unit: object) -> str:
    """Единица из UNITS (по нормализованному виду), иначе «ед.»."""
    return _UNIT_BY_NORM.get(_norm_unit(unit), UNKNOWN_UNIT) if isinstance(unit, str) and unit.strip() \
        else UNKNOWN_UNIT


def describe(description: str, unit: str | None) -> str:
    """Описание тега → «класс, единица»: первый подходящий корень QUANTITY_CLASSES (иначе «величина»), единица из
    UNITS (иначе «ед.»). Названий оборудования, цехов и марок не остаётся."""
    text = (description or "").casefold().replace("ё", "е")
    cls = next((name for name, pattern in _CLASS_RES if pattern.search(text)), "величина")
    return f"{cls}, {canonical_unit(unit)}"


def _norm_word(word: str) -> str:
    return word.casefold().replace("ё", "е")


_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+")
_ABBR_SRC = re.compile(r"(?<![A-Za-zА-Яа-яЁё])[A-ZА-ЯЁ]{2,3}(?![A-Za-zА-Яа-яЁё])")
_ABBR_KEEP = frozenset({"sql", "id", "pv", "sp", "op", "мск", "utc"})
_UNIT_WORDS = frozenset(_norm_unit(u) for u in UNITS)


def stem(word: str) -> str | None:
    """Основа слова словаря: от 6 букв — первые 5 (маскирует любое слово с неё); 4–5 букв — само слово (маскирует
    себя с окончанием до 2 букв); 2–3 буквы — сокращение (только точно); короче — None."""
    w = _norm_word(word)
    if len(w) >= 6:
        return w[:5]
    return w if len(w) >= 2 else None


def _masked_by(word: str, entry: str) -> bool:
    w, v = _norm_word(word), entry
    if len(v) >= 6:
        return w.startswith(v[:5])
    if len(v) >= 4:
        return w.startswith(v) and len(w) - len(v) <= 2
    return w == v


class _VocabIndex:
    def __init__(self, vocab: frozenset[str]):
        self.stems = {v[:5] for v in vocab if len(v) >= 6}
        self.short = {v for v in vocab if 4 <= len(v) <= 5}
        self.abbr = {v for v in vocab if len(v) <= 3}

    def hit(self, word: str) -> bool:
        w = _norm_word(word)
        if len(w) >= 5 and w[:5] in self.stems:
            return True
        if any(w[:k] in self.short for k in range(max(4, len(w) - 2), len(w) + 1)):
            return True
        return w in self.abbr


def _constant_words() -> frozenset[str]:
    """Слова постоянных текстов пакета (сценарии, отказы, реплики бота, служебные слова, маски) — их словарь каталога
    не берёт. JUDGE.md сюда не входит (m6 итоговой доводки Д8б): его слова, совпавшие со словами описаний и узлов,
    маскируются, — предметное слово в тексте для судьи не открывает его в пакетах."""
    texts = [s.what + " " + s.pass_if + " " + s.label for s in SCENARIOS]
    texts += list(REFUSAL_TEXTS.values()) + [CONSENT, FALLBACK, NAME_REPLY, NOT_LISTED, DRAFT_HEAD, DRAFT_ASK,
                                             DRAFT_RULE, _SERVICE_TEXT] + [name for name, _ in QUANTITY_CLASSES]
    texts += list(REFUSAL_TEXTS) + list(PERIOD_PRESETS) + list(MASKS) + list(SOURCE_KINDS)
    return frozenset(_norm_word(w) for t in texts for w in _WORD_RE.findall(t))


_CONST_WORDS = _constant_words()


def _generic(word: str) -> bool:
    return word in _UNIT_WORDS or any(word.startswith(root) for root in GENERIC_WORDS)


def vocab_from(descriptions: Iterable[str], nodes: Iterable[str]) -> frozenset[str]:
    """Словарь каталога (I-р3-5, П3, П4): слова описаний и узлов от 4 букв без STOP_WORDS, GENERIC_WORDS и слов
    постоянных текстов стенда (по правилу маски), плюс сокращения оборудования из 2–3 заглавных букв («БДМ», «PM»)."""
    words: set[str] = set()
    abbr: set[str] = set()
    for text in list(descriptions) + list(nodes):
        if not isinstance(text, str):
            continue
        mixed = any(ch.islower() for ch in text)
        for m in _ABBR_SRC.finditer(text):
            a = _norm_word(m.group(0))
            if a in _ABBR_KEEP or a in _UNIT_WORDS:
                continue
            if mixed or re.match(r"-?\d", text[m.end():]):
                abbr.add(a)
        for raw in _WORD_RE.findall(text):
            w = _norm_word(raw)
            if len(w) >= 4 and w not in STOP_WORDS and not _generic(w):
                words.add(w)
    words = {w for w in words if not any(_masked_by(c, w) for c in _CONST_WORDS)}
    return frozenset(words | abbr)


def _descriptions_of(call: ToolCall) -> list[str]:
    out = output_of(call)
    found = [i.get("description") for i in out.get("items") or [] if isinstance(i, dict)]
    found += [m.get("description") for m in out.get("matches") or [] if isinstance(m, dict)]
    return [d for d in found if isinstance(d, str) and d.strip()]


def _all_turns(rec: RunRecord) -> list[Turn]:
    return rec.turns + ([rec.bridge] if rec.bridge else [])


def build_vocab(records: list[RunRecord], cards: CardSet) -> frozenset[str]:
    """Словарь прогона: слова описаний из выводов всех инструментов данных (DATA_TOOLS — с Д9а и ряд tag_series, и
    наработка calc_runtime) всех записей и узлы карточек (Card.node, CardSet.bench_node); цели карточек — нет (П3)."""
    descriptions = [d for r in records for t in _all_turns(r) for c in t.tools
                    if short(c.tool) in DATA_TOOLS for d in _descriptions_of(c)]
    nodes = [c.node for c in cards.cards.values() if c.node] + [cards.bench_node]
    return vocab_from(descriptions, nodes)


_ALIAS_WORDS = frozenset({"ч", "тег", "узел", "имя"})
_ALIAS_HEADS = ("УЗЕЛ", "ТЕГ", "ИМЯ", "Ч")
_MASK_WORD = r"(?:Ч|ТЕГ|УЗЕЛ|ИМЯ)-\d+(?!\d)|[A-Za-zА-Яа-яЁё]+(?:-\d+)?"
_MASK_TOKEN = re.compile(r"\[(?:скрыто|модель)\]|" + _MASK_WORD)                     # без настоящих данных
_MASK_TOKEN_REAL = re.compile(r"\[(?:скрыто|модель)\]|⟨[^⟨⟩]*⟩|" + _MASK_WORD)     # метки ⟨…⟩ — только с ними (m4)


def _glued(word: str, num: str) -> tuple[str, str] | None:
    """Слово, склеенное с головой псевдонима («БДМЧ-1» из «БДМ3а», «PMТЕГ-2», m5): (префикс, голова) или None."""
    if not num:
        return None
    for head in _ALIAS_HEADS:
        if word.endswith(head) and len(word) > len(head):
            return word[:-len(head)], head
    return None


def _hidden_word(word: str, index: _VocabIndex) -> bool:
    return _norm_word(word) in index.abbr or index.hit(word)


def mask_vocab(text: str, vocab: frozenset[str], *, real_data: bool = False) -> str:
    """Слова словаря по основе → «[скрыто]»; сокращения — вместе с номером («БДМ-3»); слово, склеенное с головой
    псевдонима («БДМЧ-1»), — по префиксу: «[скрыто]Ч-1» (m5). Псевдонимы (Ч-n, ТЕГ-n, УЗЕЛ-n, ИМЯ-n) и маски не
    трогаются; метки ⟨…⟩ — только при real_data (без настоящих данных слова в них маскируются, m4)."""
    index = _VocabIndex(vocab)

    def sub(m: re.Match) -> str:
        token = m.group(0)
        if token.startswith(("[", "⟨")) or re.fullmatch(r"(?:Ч|ТЕГ|УЗЕЛ|ИМЯ)-\d+", token):
            return token
        word, _, num = token.partition("-")
        glued = _glued(word, num)
        if glued is not None and _hidden_word(glued[0], index):
            return f"{HIDDEN}{glued[1]}-{num}"
        if _norm_word(word) in index.abbr:
            return HIDDEN
        return (HIDDEN + (f"-{num}" if num else "")) if index.hit(word) else token
    return (_MASK_TOKEN_REAL if real_data else _MASK_TOKEN).sub(sub, text)


def _vocab_hit(text: str, index: _VocabIndex) -> bool:
    """Слово словаря в тексте (сканер, только без настоящих данных): по основе, сокращение, префикс слова, склеенного
    с головой псевдонима (m5); метки ⟨…⟩ не защищены (m4)."""
    for m in _MASK_TOKEN.finditer(text):
        token = m.group(0)
        if token.startswith("[") or re.fullmatch(r"(?:Ч|ТЕГ|УЗЕЛ|ИМЯ)-\d+", token):
            continue
        word, _, num = token.partition("-")
        glued = _glued(word, num)
        if index.hit(word) or (glued is not None and _hidden_word(glued[0], index)):
            return True
    return False


def blind(text: str) -> str:
    """Имена моделей и провайдеров (MODEL_WORDS) с хвостом версии → «[модель]»."""
    return _MODEL_RE.sub(MODEL_MASK, text)


# --- построение пакета ---

# код оборудования или тега без «_» («PM3», «FIC-205», «PT1001A»), кроме псевдонимов — «[скрыто]» (П4)
_EQUIPMENT_RE = re.compile(r"(?<![\w-])(?!(?:ТЕГ|ИМЯ|УЗЕЛ)-\d)[A-ZА-ЯЁ]{2,4}-?\d+[A-ZА-ЯЁ]{0,2}(?![\w])")
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+−-]\d{2}:?\d{2})?)?(?!\d)")
_DATE_RE = re.compile(r"(?<![\d.])\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})(?!\d)")
_TIME_RE = re.compile(r"(?<![\d:])\d{1,2}:\d{2}(?::\d{2})?(?![\d:])")
_DDMM_RE = re.compile(r"(?<![\d.,])(\d{2})\.(\d{2})(?![\d])(?![.,]\d)")
_GROUP_RE = re.compile(r"\d+(?:[.,:]\d+)*")
_ALIAS_RE = re.compile(r"(?:Ч|ТЕГ|УЗЕЛ|ИМЯ)-\d+(?!\d)")
_MARK_RE = re.compile(r"⟨[^⟨⟩]*⟩")
_UNIT_NEXT = re.compile(r"[  ]*(?:[°%‰]|[A-Za-zА-Яа-яЁё]{1,4}\d?/|(?:бар|мбар|кпа|мпа|па|кгс|кг|т|м3|мм|м|л|г|об|"
                        r"квт|вт|гц|ма|ед|шт|мин|ч)(?![A-Za-zА-Яа-яЁё]))", re.IGNORECASE)
_RANGE_GAP = re.compile(r"\s*(?:[–—-]|\.\.\.?|…)\s*")
_BOUND_BEFORE = re.compile(r"(?:(?<![\w])(?:от|до|выше|ниже|свыше|менее|более|больше|меньше|не\s+(?:выше|ниже|более|"
                           r"менее|больше|меньше|превыша\w*))|[<>≤≥])\s*$", re.IGNORECASE)
# имя агента в тексте Конструктора: «Имя для системы: x» — и с разметкой и тире между меткой и именем («**Имя для
# системы:** `x`», «Имя для системы — x»), «Предлагаю имя x» (m2 итоговой доводки Д8б)
_NAME_LINE = re.compile(r"(?:Имя\s+для\s+системы|Предлагаю\s+(?:системное\s+)?имя)[\s*_`«»\"'„“”]*(?:[:—–-]"
                        r"[\s*_`«»\"'„“”]*)?([A-Za-z][A-Za-z0-9_-]{1,63})", re.IGNORECASE)
_CODE_RE = re.compile(r"[a-z_]{1,40}")
_KEY_RE = re.compile(r"[A-Za-z_]{1,40}")
_VALUE_FIELDS = ("value", "avg", "min", "max", "last", "range", "std", "percent_good")
_SCALE_FIELDS = ("min_eu", "max_eu")
_STAMP_FIELDS = ("time", "start", "end", "last_time", "first_time", "min_time", "max_time")
# профиль ряда (tag_series) и состояния наработки (calc_runtime, Д9а): числа — в вывод пакета, источники Ч-n
_PROFILE_FIELDS = ("first", "last", "change", "min", "max")
_PROFILE_STAMPS = ("first_time", "last_time", "min_time", "max_time")
_STATE_FIELDS = ("hours", "percent", "count")
ARG_TEXT_MAX = 400


def _code(value: object) -> str:
    return value if isinstance(value, str) and _CODE_RE.fullmatch(value) else "other"


class _Masker:
    """Шаги до чисел (Д8-R78): слепота → теги → имена → узлы → описания (и сокращения). Псевдонимы — сквозные, по
    первому появлению."""

    def __init__(self, tags: Iterable[str], names: Iterable[str], nodes: Iterable[str],
                 descriptions: Mapping[str, str | None], vocab: frozenset[str], real_data: bool):
        self.tags: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.nodes: dict[str, str] = {}
        known = sorted({t.strip() for t in tags if t and t.strip()}, key=len, reverse=True)
        self._tag_re = re.compile(r"(?<![A-Za-z0-9_])(?:" + "|".join(re.escape(t) for t in known) +
                                  r")(?![A-Za-z0-9_])", re.IGNORECASE) if known else None
        names = sorted({n for n in names if n}, key=len, reverse=True)
        self._name_re = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(n) for n in names) + r")(?![\w-])") \
            if names else None
        node_list = sorted({n.strip() for n in nodes if n and n.strip()}, key=len, reverse=True)
        self._node_res = [(n, re.compile(r"(?<!\w)" + r"\s+".join(re.escape(p) for p in n.split()) + r"(?!\w)",
                                         re.IGNORECASE)) for n in node_list]
        self._descs = sorted(((d, u) for d, u in descriptions.items() if d and d.strip()), key=lambda x: -len(x[0]))
        self.real = real_data
        self._abbr = sorted((v for v in vocab if len(v) <= 3), key=len, reverse=True)
        self._abbr_re = re.compile(r"(?<![\w])(?:" + "|".join(re.escape(a) for a in self._abbr) +
                                   r")(?:-?\d+)?(?![\w])", re.IGNORECASE) if self._abbr else None

    def tag(self, tag: str) -> str:
        key = tag.strip().upper()
        if key not in self.tags:
            self.tags[key] = f"ТЕГ-{len(self.tags) + 1}"
        return self.tags[key]

    def name(self, name: str) -> str:
        if name not in self.names:
            self.names[name] = f"ИМЯ-{len(self.names) + 1}"
        return self.names[name]

    def node(self, node: str) -> str:
        key = re.sub(r"\s+", " ", node.strip()).casefold()
        if key not in self.nodes:
            self.nodes[key] = f"УЗЕЛ-{len(self.nodes) + 1}"
        return self.nodes[key]

    def desc(self, description: object, unit: object) -> str:
        if not isinstance(description, str):
            return describe("", unit if isinstance(unit, str) else None)
        if self.real:
            text = self._tags(blind(description))
            return f"⟨{text}|описание⟩" if re.search(r"\d", text) else text
        return describe(description, unit if isinstance(unit, str) else None)

    def _tags(self, text: str) -> str:
        if self._tag_re is not None:
            text = self._tag_re.sub(lambda m: self.tag(m.group(0)), text)
        shield = list(text)
        for pattern in (_ISO_RE, _TIME_RE):                 # «02T11» из ISO-метки — не тег (tag_tokens)
            for m in pattern.finditer(text):
                shield[m.start():m.end()] = " " * (m.end() - m.start())
        plain = "".join(shield)
        found = [(m.start(), m.end(), self.tag(m.group(0))) for m in TAG_TOKEN_RE.finditer(plain)]
        for a, b, alias in reversed(found):
            text = text[:a] + alias + text[b:]
        return text

    def text(self, raw: str) -> str:
        text = blind(raw)
        text = self._tags(text)
        if self._name_re is not None:
            text = self._name_re.sub(lambda m: self.name(m.group(0)), text)
        for node, pattern in self._node_res:
            text = pattern.sub(lambda m, n=node: self.node(n), text)
        for d, unit in self._descs:
            pattern = re.compile(r"\s+".join(re.escape(p) for p in d.split()), re.IGNORECASE)
            text = pattern.sub(lambda m, d=d, u=unit: self.desc(d, u), text)
        if not self.real:
            if self._abbr_re is not None:
                text = self._abbr_re.sub(HIDDEN, text)
            text = _EQUIPMENT_RE.sub(HIDDEN, text)
        return text


def _known(rec: RunRecord, card: Card) -> tuple[list[str], list[str], dict[str, str | None]]:
    """Теги, имена агентов и описания записи — до замены в текстах."""
    tags = [t for t in (card.tag, card.expect_tag, card.pick, *card.bench_tags) if t and TAG_TOKEN_RE.fullmatch(t)]
    names = [BENCH_AGENT]
    descs: dict[str, str | None] = {}
    for turn in _all_turns(rec):
        for c in turn.tools:
            args = c.input if isinstance(c.input, dict) else {}
            if isinstance(args.get("tags"), list):
                tags += [str(t) for t in args["tags"] if isinstance(t, str)]
            out = output_of(c)
            for entry in (out.get("items") or []) + (out.get("matches") or []):
                if isinstance(entry, dict):
                    if isinstance(entry.get("tag"), str):
                        tags.append(entry["tag"])
                    if isinstance(entry.get("description"), str):
                        descs.setdefault(entry["description"], entry.get("unit") if isinstance(entry.get("unit"),
                                                                                                str) else None)
            if short(c.tool) == "save_agent":
                for src in (args, out):
                    if isinstance(src.get("name"), str):
                        names.append(src["name"])
        for text in turn.assistant:
            names += [m.group(1) for m in _NAME_LINE.finditer(text)]
    for s in rec.saved:
        if isinstance(s.get("name"), str):
            names.append(s["name"])
    return tags, names, descs


def _args(c: ToolCall, m: _Masker) -> dict:
    """Аргументы инструментов службы (DATA_TOOLS) и save_agent — по полям: теги псевдонимами, период, свои границы,
    точки ряда, запрос; встроенных инструментов OpenCode — текстом."""
    tool = short(c.tool)
    raw = c.input if isinstance(c.input, dict) else {}
    out: dict[str, Any] = {}
    if tool in DATA_TOOLS or tool == "save_agent":
        if isinstance(raw.get("tags"), list):
            out["tags"] = [m.tag(t) if isinstance(t, str) else "?" for t in raw["tags"]]
        if tool in DATA_TOOLS:
            period = raw.get("period")
            if isinstance(period, str):
                out["period"] = period if period in PERIOD_PRESETS else m.text(period[:ARG_TEXT_MAX])
            for k in ("start", "end"):
                if isinstance(raw.get(k), str):
                    out[k] = m.text(raw[k][:ARG_TEXT_MAX])
            _put_num(out, "points", raw.get("points"))
        if tool == "catalog_search" and isinstance(raw.get("query"), str):
            out["query"] = m.text(raw["query"][:ARG_TEXT_MAX])
        if tool == "save_agent":
            if isinstance(raw.get("name"), str):
                out["name"] = m.name(raw["name"])
            for k in ("title", "node", "purpose", "student_reply"):
                if isinstance(raw.get(k), str):
                    out[k] = m.text(raw[k][:ARG_TEXT_MAX])
            if isinstance(raw.get("tools"), list):
                out["tools"] = [t if isinstance(t, str) and _CODE_RE.fullmatch(t) else "other" for t in raw["tools"]]
        return out
    for k, v in raw.items():                    # встроенные инструменты (bash, write …): аргументы — текстом
        key = k if _KEY_RE.fullmatch(str(k)) else "arg"
        text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        out[key] = m.text(text[:ARG_TEXT_MAX])
    return out


def _put_num(dst: dict, key: str, value: object) -> None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        dst[key] = value


def _series_runtime(i: dict, item: dict, m: _Masker) -> None:
    """Профиль ряда (tag_series) и состояния наработки (calc_runtime) элемента: числа, метки времени профиля, подпись
    состояния; точки сетки целиком в пакет не идут."""
    profile = i.get("profile")
    if isinstance(profile, dict):
        prof: dict[str, Any] = {}
        for k in _PROFILE_FIELDS:
            _put_num(prof, k, profile.get(k))
        for k in _PROFILE_STAMPS:
            if isinstance(profile.get(k), str) and _ISO_RE.fullmatch(profile[k]):
                prof[k] = profile[k]
        item["profile"] = prof
    if isinstance(i.get("states"), list):
        states = []
        for st in i["states"]:
            if isinstance(st, dict):
                one: dict[str, Any] = {"label": m.text(st["label"][:40])} if isinstance(st.get("label"), str) else {}
                for k in _STATE_FIELDS:
                    _put_num(one, k, st.get(k))
                states.append(one)
        item["states"] = states
    for k in ("covered_percent", "period_hours"):
        _put_num(item, k, i.get(k))


def _output(c: ToolCall, m: _Masker) -> dict:
    """Сжатый вывод: значения, единицы, время, качество, статусы, коды отказов с постоянным текстом."""
    tool = short(c.tool)
    out = output_of(c)
    if not out:
        return {"status": _code(c.status or "error")}
    res: dict[str, Any] = {"status": _code(out.get("status"))}
    if tool in DATA_TOOLS and tool != "catalog_search":       # элементы по тегам: tag_now, tag_period, ряд, наработка
        if isinstance(out.get("period"), dict):
            p = out["period"]
            span: dict[str, Any] = {}
            if isinstance(p.get("preset"), str) and p["preset"] in PERIOD_PRESETS:
                span["preset"] = p["preset"]
            for k in ("start", "end"):
                if isinstance(p.get(k), str) and _ISO_RE.fullmatch(p[k]):
                    span[k] = p[k]
            res["period"] = span
        items = []
        for i in out.get("items") or []:
            if not isinstance(i, dict):
                continue
            item: dict[str, Any] = {}
            if isinstance(i.get("tag"), str):
                item["tag"] = m.tag(i["tag"])
            item["status"] = _code(i.get("status"))
            if "description" in i:
                item["description"] = m.desc(i.get("description"), i.get("unit"))
            if "unit" in i:
                item["unit"] = canonical_unit(i.get("unit"))
            for k in _VALUE_FIELDS + ("age_s",):
                _put_num(item, k, i.get(k))
            for k in ("time", "last_time"):
                if isinstance(i.get(k), str) and _ISO_RE.fullmatch(i[k]):
                    item[k] = i[k]
            if isinstance(i.get("quality"), str):
                item["quality"] = m.text(i["quality"][:40])
            _series_runtime(i, item, m)
            items.append(item)
        res["items"] = items
    elif tool == "catalog_search":
        matches = []
        for x in out.get("matches") or []:
            if not isinstance(x, dict):
                continue
            entry: dict[str, Any] = {}
            if isinstance(x.get("tag") or x.get("name"), str):
                entry["tag"] = m.tag(x.get("tag") or x.get("name"))
            entry["description"] = m.desc(x.get("description"), x.get("unit"))
            entry["unit"] = canonical_unit(x.get("unit"))
            for k in _SCALE_FIELDS:
                _put_num(entry, k, x.get(k))
            matches.append(entry)
        res["matches"] = matches
    elif tool == "save_agent":
        if isinstance(out.get("already"), bool):
            res["already"] = out["already"]
        if isinstance(out.get("name"), str):
            res["name"] = m.name(out["name"])
        problems = []
        for pr in out.get("problems") or []:
            if isinstance(pr, dict):
                code = _code(pr.get("code"))
                problems.append({"code": code, "text": REFUSAL_TEXTS.get(code, "")})
        if problems:
            res["problems"] = problems
    return res


def _turn_entries(t: Turn, m: _Masker, stage: str | None, raw: list[str]) -> list[dict]:
    extra = {"stage": stage} if stage else {}
    out: list[dict] = []
    if t.student:
        raw.append(t.student)
        out.append({"role": "студент", "text": m.text(t.student), **extra})
    for c in t.tools:
        raw.append(json.dumps(c.to_json(), ensure_ascii=False))
        tool = short(c.tool)
        out.append({"role": "инструмент", "tool": tool if _KEY_RE.fullmatch(tool) else "tool", "args": _args(c, m),
                    "output": _output(c, m), **extra})
    for x in t.assistant:
        raw.append(x)
        out.append({"role": "агент", "text": m.text(x), **extra})
    if t.error:
        name = re.match(r"[A-Za-z]{1,60}", t.error)
        codes = sorted(set(re.findall(r"\bpcbk_[a-z_]+\b", t.error)))
        out.append({"role": "ошибка", "text": " ".join(([name.group(0)] if name else ["Error"]) + codes), **extra})
    return out


# --- числа ---

@dataclass
class _Alias:
    name: str
    sources: list[str]
    cls: str | None = None
    rank: int = -1


_CLASS_RANK = {"allowed": 0, "other": 1, "hard": 2}


def _dec(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return Decimal(repr(value)) if isinstance(value, float) else Decimal(value)


def _stamp_parts(text: str) -> dict[str, int] | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+−-]\d{2}:?\d{2})?)?",
                     text)
    if not m:
        return None
    parts = {"y": int(m.group(1)), "m": int(m.group(2)), "d": int(m.group(3))}
    for key, g in (("H", 4), ("M", 5), ("S", 6)):
        if m.group(g) is not None:
            parts[key] = int(m.group(g))
    return parts


def _token_parts(kind: str, text: str) -> dict[str, int] | None:
    if kind == "stamp":
        return _stamp_parts(text)
    if kind == "date":
        d, mo, y = text.split(".")
        return {"d": int(d), "m": int(mo), ("y" if len(y) == 4 else "yy"): int(y)}
    if kind == "ddmm":
        d, mo = text.split(".")
        return {"d": int(d), "m": int(mo)}
    if kind == "time":
        nums = [int(x) for x in text.split(":")]
        return dict(zip(("H", "M", "S"), nums))
    return None


def _parts_match(token: dict[str, int], stamp: dict[str, int]) -> bool:
    for k, v in token.items():
        if k == "yy":
            if "y" not in stamp or stamp["y"] % 100 != v:
                return False
        elif stamp.get(k) != v:
            return False
    return True


@dataclass(frozen=True)
class _Tok:
    start: int
    end: int
    kind: str                                  # stamp, date, ddmm, time, value, bound, group
    shown: Shown | None = None


def _protected(text: str, keep: Iterable[str], marks: bool = True) -> list[tuple[int, int]]:
    spans = [(m.start(), m.end()) for m in _ALIAS_RE.finditer(text)]
    if marks:                                       # метки ⟨…⟩ — только с настоящими данными (m4)
        spans += [(m.start(), m.end()) for m in _MARK_RE.finditer(text)]
    for tok in keep:
        for m in re.finditer(r"(?<![\w/])" + re.escape(tok) + r"(?![\w/])", text):
            spans.append((m.start(), m.end()))
    return spans


def _free(spans: list[tuple[int, int]], a: int, b: int) -> bool:
    return all(b <= s or a >= e for s, e in spans)


def _number_tokens(text: str, keep: Iterable[str], marks: bool = True) -> list[_Tok]:
    """Свой разбор (I-р4-1): ISO-метки, даты, время → числа parse_numbers (диапазон, знак) → прочие группы цифр.
    marks — метки ⟨…⟩ не разбираются (только с настоящими данными, m4)."""
    taken = _protected(text, keep, marks)
    toks: list[_Tok] = []
    for kind, pattern in (("stamp", _ISO_RE), ("date", _DATE_RE), ("time", _TIME_RE)):
        for m in pattern.finditer(text):
            if _free(taken, m.start(), m.end()):
                toks.append(_Tok(m.start(), m.end(), kind))
                taken.append((m.start(), m.end()))
    for m in _DDMM_RE.finditer(text):
        d, mo = int(m.group(1)), int(m.group(2))
        if 1 <= d <= 31 and 1 <= mo <= 12 and not _UNIT_NEXT.match(text, m.end()) and _free(taken, m.start(),
                                                                                             m.end()):
            toks.append(_Tok(m.start(), m.end(), "ddmm"))
            taken.append((m.start(), m.end()))
    shown = [s for s in parse_numbers(text) if _free(taken, s.start, s.end)]
    bounds: set[int] = set()
    for i in range(len(shown) - 1):                    # диапазон: «3–4», «3 - 4», «3...4»
        a, b = shown[i], shown[i + 1]
        gap = text[a.end:b.start]
        if _RANGE_GAP.fullmatch(gap) or (b.value < 0 and not gap.strip() and text[b.start] in "-−"):
            bounds |= {i, i + 1}
            if b.value < 0 and text[b.start] in "-−":
                shown[i + 1] = Shown(-b.value, b.decimals, b.start + 1, b.end)
    for i, s in enumerate(shown):
        if _BOUND_BEFORE.search(text[max(0, s.start - 24):s.start]):
            bounds.add(i)
        toks.append(_Tok(s.start, s.end, "bound" if i in bounds else "value", s))
        taken.append((s.start, s.end))
    for m in _GROUP_RE.finditer(text):
        if _free(taken, m.start(), m.end()):
            toks.append(_Tok(m.start(), m.end(), "group"))
    return sorted(toks, key=lambda t: t.start)


def _rounded(v: Decimal, places: int) -> list[Decimal]:
    q = Decimal(1).scaleb(-places)
    return list({v.quantize(q, rounding=ROUND_HALF_UP), v.quantize(q, rounding=ROUND_HALF_EVEN)})


class _Numbers:
    """Таблица Ч-n пакета: значения выводов, числа текстов, даты и время."""

    def __init__(self, real_data: bool, student_texts: list[str], scenario_texts: list[str],
                 period: Mapping[str, str] | None):
        self.real = real_data
        self.table: dict[str, _Alias] = {}
        self.out_values: list[tuple[Decimal, str, str]] = []           # (значение, псевдоним, поле)
        self.value_alias: dict[Decimal, str] = {}
        self.stamps: list[tuple[dict[str, int], str, str]] = []        # (части, поле, псевдоним)
        self.stamp_alias: dict[str, str] = {}
        self.keyed: dict[tuple, str] = {}
        self.student_nums = {s.value for t in student_texts for s in parse_numbers(t)}
        self.question_nums = {s.value for t in scenario_texts for s in parse_numbers(t)}
        self.student_times = [p for t in student_texts for p in self._times(t, real_data)]
        self.question_times = [p for t in scenario_texts for p in self._times(t, real_data)]
        self.period_times = [p for v in (period or {}).values() if isinstance(v, str)
                             for p in [_stamp_parts(v)] if p]
        self.found: dict[str, list[str]] = {"suspect_norm": [], "suspect_numbers": []}

    @staticmethod
    def _times(text: str, marks: bool) -> list[dict[str, int]]:
        out = []
        for t in _number_tokens(text, (), marks):
            if t.kind in ("stamp", "date", "ddmm", "time"):
                p = _token_parts(t.kind, text[t.start:t.end])
                if p:
                    out.append(p)
        return out

    def _new(self, sources: list[str]) -> str:
        name = f"Ч-{len(self.table) + 1}"
        self.table[name] = _Alias(name, list(sources))
        return name

    def _add(self, name: str, sources: Iterable[str]) -> None:
        al = self.table[name]
        for s in sources:
            if s not in al.sources:
                al.sources.append(s)

    def _render(self, shown: str, name: str) -> str:
        return f"⟨{shown}|{name}⟩" if self.real else name

    def output_value(self, value: object, label: str) -> object:
        d = _dec(value)
        if d is None:
            return value
        name = self.value_alias.get(d)
        if name is None:
            name = self._new([label])
            self.value_alias[d] = name
            self.out_values.append((d, name, label))
        else:
            self._add(name, [label])
            self.out_values.append((d, name, label))
        return self._render(repr(value) if isinstance(value, float) else str(value), name)

    def output_stamp(self, value: str, label: str) -> str:
        name = self.stamp_alias.get(value)
        parts = _stamp_parts(value)
        if name is None:
            name = self._new([label])
            self.stamp_alias[value] = name
        else:
            self._add(name, [label])
        if parts:
            self.stamps.append((parts, label, name))
            self._add(name, self._labels(parts=parts))
        return self._render(value, name)

    def _labels(self, value: Decimal | None = None, parts: dict[str, int] | None = None) -> list[str]:
        out: list[str] = []
        if value is not None:
            if value in self.student_nums:
                out.append(STUDENT_SOURCE)
            if value in self.question_nums:
                out.append(QUESTION_SOURCE)
        if parts is not None:
            if any(_parts_match(parts, p) for p in self.student_times):
                out.append(STUDENT_SOURCE)
            if any(_parts_match(parts, p) for p in self.question_times):
                out.append(QUESTION_SOURCE)
            if any(_parts_match(parts, p) for p in self.period_times):
                out.append(PERIOD_SOURCE)
        return out

    def _derived(self, s: Shown) -> list[str]:
        vals: dict[str, Decimal] = {}
        for d, name, label in self.out_values:
            if label.split(".")[-1] in _VALUE_FIELDS and d != 0:
                vals.setdefault(name, d)
        items = list(vals.items())
        shown_abs = Shown(abs(s.value), s.decimals, s.start, s.end)
        out: list[str] = []

        def near(target: Decimal, use_abs: bool = False) -> bool:
            return close(shown_abs if use_abs else s, target)

        for i, (na, a) in enumerate(items):
            for nb, b in items[i + 1:]:
                hi, lo = (na, nb) if a >= b else (nb, na)
                diffs = [abs(a - b)] + ([abs(x - y) for x in _rounded(a, s.decimals) for y in _rounded(b, s.decimals)]
                                        if s.decimals >= 1 else [])
                if any(near(dv, True) for dv in diffs):
                    out.append(f"производное: {hi} − {lo}")
                if near(a + b):
                    out.append(f"производное: {na} + {nb}")
                if near((a + b) / 2):
                    out.append(f"производное: среднее {na} и {nb}")
        for na, a in items:
            for nb, b in items:
                if na == nb:
                    continue
                if near(a * 100 / b):
                    out.append(f"производное: процент {na} от {nb}")
                for nc, c in ((na, a), (nb, b)):
                    if near(abs(a - b) * 100 / abs(c), True) and a >= b:
                        out.append(f"производное: процент ({na} − {nb}) от {nc}")
        return list(dict.fromkeys(out))

    def _value(self, s: Shown, bound: bool) -> str:
        v = s.value
        if bound:
            return self._new([RANGE_SOURCE] + self._labels(value=v))
        if v in self.value_alias:                                       # 1. точное совпадение (10 = 10.0)
            name = self.value_alias[v]
            self._add(name, self._labels(value=v))
            return name
        key = ("v", v, s.decimals)
        if key in self.keyed:
            return self.keyed[key]
        approx = list(dict.fromkeys(name for d, name, _ in self.out_values if close(s, d)))
        if approx:                                                      # 2. после округления до показанных знаков
            sources = [f"≈ {name} (округлено)" for name in approx]
        else:                                                           # 3. производное — только из полей значений
            sources = self._derived(s)
        name = self._new(sources + self._labels(value=v))
        self.keyed[key] = name
        return name

    def _time(self, kind: str, text: str) -> str:
        parts = _token_parts(kind, text) or {}
        if kind == "stamp" and text in self.stamp_alias:
            name = self.stamp_alias[text]
            self._add(name, self._labels(parts=parts))
            return name
        key = ("t", text)
        if key in self.keyed:
            return self.keyed[key]
        sources = list(dict.fromkeys(label for p, label, _ in self.stamps if parts and _parts_match(parts, p)))
        name = self._new(sources + self._labels(parts=parts))
        self.table[name].cls = None if self.table[name].sources else "allowed"
        self.keyed[key] = name
        return name

    def _classify(self, name: str, cls: str | None) -> None:
        al = self.table[name]
        if al.sources:
            al.cls, al.rank = None, -1
            return
        rank = _CLASS_RANK.get(cls or "allowed", 0)
        if rank > al.rank:
            al.cls, al.rank = cls or "allowed", rank

    def text(self, text: str, keep: Iterable[str], classes: Mapping[tuple[int, int], str] | None = None,
             norm: Iterable[tuple[int, int]] = ()) -> str:
        """Числа, даты и время текста → Ч-n; classes — класс автомата по позиции (только агент и инструкция)."""
        norm_spans = set(norm)
        parts: list[str] = []
        pos = 0
        for t in _number_tokens(text, keep, self.real):
            shown = text[t.start:t.end]
            if t.kind in ("stamp", "date", "ddmm", "time"):
                name = self._time(t.kind, shown)
            elif t.kind == "group":
                key = ("g", shown)
                name = self.keyed.get(key) or self._new([])
                self.keyed[key] = name
                self._classify(name, "allowed")
            else:
                name = self._value(t.shown, t.kind == "bound")
                if classes is not None:
                    cls = classes.get((t.start, t.end), "allowed")
                    self._classify(name, cls)
                    if not self.table[name].sources and cls == "other" and name not in self.found["suspect_numbers"]:
                        self.found["suspect_numbers"].append(name)
                elif not self.table[name].sources:
                    self._classify(name, "allowed")
                if (t.start, t.end) in norm_spans and name not in self.found["suspect_norm"]:
                    self.found["suspect_norm"].append(name)
            parts.append(text[pos:t.start])
            parts.append(self._render(shown, name))
            pos = t.end
        parts.append(text[pos:])
        return "".join(parts)

    def as_json(self) -> dict[str, dict]:
        return {name: {"sources": list(al.sources), "class": None if al.sources else (al.cls or "allowed")}
                for name, al in self.table.items()}


_TEXT_KEEP = CONST_TOKENS["any"] | CONST_TOKENS["args"]


def alias_numbers(fields: dict, outputs: list[dict], student_texts: list[str], scenario_texts: list[str], *,
                  period: Mapping[str, str] | None = None,
                  classify: Callable[[str], Mapping[tuple[int, int], str]] | None = None,
                  real_data: bool = False, found: dict | None = None) -> tuple[dict, dict[str, dict]]:
    """Все числа, даты и время пакета → сквозные Ч-n (Д8-R78, I-р4-1–I-р4-3).

    fields — поля пакета после масок (scenario не трогается); outputs — записи fields["transcript"] с вызовами
    инструментов, те же объекты (их выводы разбираются первыми — источники «вывод: <инструмент>.<поле>»);
    student_texts — реплики студента (кроме вопроса), scenario_texts — вопрос сценария (открытие и мост карточки);
    period — период карточки. classify(text) — класс
    автомата по позициям чисел текста агента. found (если дан) получает suspect_norm и suspect_numbers — псевдонимы.
    Возвращает (новые поля, таблицу {"Ч-n": {"sources": […], "class": …}})."""
    nums = _Numbers(real_data, student_texts, scenario_texts, period)

    def walk_output(obj: Any, tool: str, key: str = "") -> Any:
        if isinstance(obj, dict):
            res = {}
            for k, v in obj.items():
                if k == "problems":
                    res[k] = v                          # коды отказов с постоянным текстом — без разбора
                elif k in _STAMP_FIELDS and isinstance(v, str):
                    res[k] = nums.output_stamp(v, f"вывод: {tool}.{k}")
                else:
                    res[k] = walk_output(v, tool, k)
            return res
        if isinstance(obj, list):
            return [walk_output(v, tool, key) for v in obj]
        if isinstance(obj, (int, float)) and not isinstance(obj, bool):
            label = f"вывод: {tool}.{key}" + (" (шкала прибора)" if key in _SCALE_FIELDS else "")
            return nums.output_value(obj, label)
        if isinstance(obj, str):
            return nums.text(obj, _TEXT_KEEP)
        return obj

    done = {id(e): walk_output(e.get("output"), str(e.get("tool"))) for e in outputs}   # сначала выводы

    def walk_args(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: walk_args(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [walk_args(v) for v in obj]
        if isinstance(obj, str):
            return nums.text(obj, _TEXT_KEEP)
        if isinstance(obj, (int, float)) and not isinstance(obj, bool):
            return nums.text(str(obj), _TEXT_KEEP)
        return obj

    def agent_text(text: str) -> str:
        classes = classify(text) if classify else None
        norm = [(s.start, s.end) for s in suspect_norm_numbers(text)]
        return nums.text(text, _TEXT_KEEP, classes if classes is not None else {}, norm)

    new = dict(fields)
    transcript = []
    for e in fields.get("transcript") or []:
        e2 = dict(e)
        if id(e) in done:
            e2["output"] = done[id(e)]
            e2["args"] = walk_args(e.get("args"))
        elif isinstance(e.get("text"), str):
            e2["text"] = agent_text(e["text"]) if e.get("role") == "агент" else nums.text(e["text"], _TEXT_KEEP)
        transcript.append(e2)
    new["transcript"] = transcript
    if isinstance(new.get("saved_prompt"), str):
        new["saved_prompt"] = agent_text(new["saved_prompt"])
    if found is not None:
        found.update({k: list(v) for k, v in nums.found.items()})
    return new, nums.as_json()


def _free_period(key: str, value: object) -> bool:
    """Свободный period вызова (не код периода службы): ключ постоянный, а текст — нет; маскируется словарём и
    сверяется сканером (m4)."""
    return key == "period" and isinstance(value, str) and value not in PERIOD_PRESETS


def _mask_strings(obj: Any, vocab: frozenset[str], skip: frozenset[str] = frozenset({"problems"}) | CONST_KEYS) -> Any:
    if isinstance(obj, dict):
        return {k: (v if k in skip and not _free_period(k, v) else _mask_strings(v, vocab, skip))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_strings(v, vocab, skip) for v in obj]
    if isinstance(obj, str):
        return mask_vocab(obj, vocab)
    return obj


def _letters(seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return "".join(chr(97 + b % 26) for b in digest[:12])


def build_packet(rec: RunRecord, sc: Scenario, card: Card, vocab: frozenset[str], real_data: bool = False,
                 tag_units: Mapping[str, str] = {}, *, bench_node: str = "", packet_id: str | None = None) -> Packet:
    """Пакет судьи по записи прогона (Д8-R78). bench_node — узел заготовленного агента (m13): тоже УЗЕЛ-n.
    Сканер не вызывается: его зовёт тот, кто пишет файл (pack_batches)."""
    if card.scenario != sc.id or rec.key.scenario != sc.id:
        raise ValueError("пакет: карточка или запись не того сценария")
    tags, names, descs = _known(rec, card)
    m = _Masker(tags, names, [card.node, bench_node], descs, vocab, real_data)
    raw: list[str] = []
    transcript: list[dict] = []
    for t in rec.turns:
        transcript += _turn_entries(t, m, None, raw)
    if rec.bridge is not None:
        transcript += _turn_entries(rec.bridge, m, "мост", raw)
    prompt = rec.saved[0].get("prompt") if sc.group == "create" and rec.saved else None
    if isinstance(prompt, str):
        raw.append(prompt)
    fields: dict[str, Any] = {
        "scenario": {"id": sc.id, "what": sc.what, "pass_if": sc.pass_if, "items": list(sc.judge_items)},
        "transcript": transcript,
        "saved_prompt": m.text(prompt) if isinstance(prompt, str) else None,
    }
    question = [m.text(x) for x in (card.opening, card.bridge) if x]
    students = [m.text(t.student) for t in _all_turns(rec) if t.student and t.student not in (card.opening,
                                                                                              card.bridge)]
    turns = _all_turns(rec)
    rule = number_rule(turns, card, sc, tag_units)
    also = " ".join(t.student for t in turns) + (" " + " ".join(str(v) for v in card.period.values())
                                                 if card.period else "")

    def classify(text: str) -> dict[tuple[int, int], str]:
        return {(f.shown.start, f.shown.end): f.kind for f in rule.foreign(text, also)}

    found: dict[str, list[str]] = {}
    fields, numbers = alias_numbers(fields, [e for e in transcript if e["role"] == "инструмент"], students, question,
                                    period=card.period, classify=classify, real_data=real_data, found=found)
    if not real_data:
        fields["transcript"] = _mask_strings(fields["transcript"], vocab)
        if isinstance(fields["saved_prompt"], str):
            fields["saved_prompt"] = mask_vocab(fields["saved_prompt"], vocab)
    auto = check_run(rec, card, tag_units)
    fields["auto_flags"] = {
        "suspect_norm": found.get("suspect_norm", []),
        "suspect_numbers": found.get("suspect_numbers", []),
        "prompt_numeric_limit": "prompt_numeric_limit" in auto.flags,
        "number_missing": "number_missing" in auto.fails,
        "foreign_number": "foreign_number" in auto.fails,
    }
    fields["numbers"] = numbers
    pid = packet_id or _letters(f"{run_ref(rec)}|{'|'.join(rec.sessions)}")
    return Packet.of(pid, fields, raw)


# --- сканер ---

# числа словами (m3 итоговой доводки Д8б): количественное числительное (или их цепочка через «и») рядом с единицей или
# с «целых/десятых/сотых/тысячных» — «три и четыре десятых», «сто двадцать восемь градусов», «девяносто семь
# процентов». Время и длительность (час, минута, секунда) — не величина и не ловятся. Текст сверяется в нижнем
# регистре с «ё» → «е».
_NUMERAL_WORDS = (
    "ноль", "нуля", "нулю", "нулем", "один", "одна", "одно", "одного", "одной", "одному", "одним", "одну", "два", "две",
    "двух", "двум", "двумя", "три", "трех", "трем", "тремя", "четыре", "четырех", "четырем", "четырьмя", "пять",
    "пяти", "пятью", "шесть", "шести", "шестью", "семь", "семи", "семью", "восемь", "восьми", "восемью", "восьмью",
    "девять", "девяти", "девятью", "десять", "десяти", "десятью", "одиннадцать", "одиннадцати", "двенадцать",
    "двенадцати", "тринадцать", "тринадцати", "четырнадцать", "четырнадцати", "пятнадцать", "пятнадцати",
    "шестнадцать", "шестнадцати", "семнадцать", "семнадцати", "восемнадцать", "восемнадцати", "девятнадцать",
    "девятнадцати", "двадцать", "двадцати", "тридцать", "тридцати", "сорок", "сорока", "пятьдесят", "пятидесяти",
    "шестьдесят", "шестидесяти", "семьдесят", "семидесяти", "восемьдесят", "восьмидесяти", "девяносто", "девяноста",
    "сто", "ста", "двести", "двухсот", "триста", "трехсот", "четыреста", "четырехсот", "пятьсот", "пятисот",
    "шестьсот", "шестисот", "семьсот", "семисот", "восемьсот", "восьмисот", "девятьсот", "девятисот", "тысяча",
    "тысячи", "тысяч", "тысячу", "тысячей", "полтора", "полторы", "полутора", "половина", "половину", "половины",
    "треть", "трети", "четверть", "четверти")
_FRACTION_WORDS = ("целых", "целая", "целой", "целую", "десятых", "десятая", "десятой", "десятую", "сотых", "сотая",
                   "сотой", "сотую", "тысячных", "тысячная", "тысячной", "тысячную")
# основы единиц словами (с любым окончанием); «бар» и «куб» — только с окончаниями единицы: «барабан», «кубик» — не она
_UNIT_WORD_STEMS = ("бар(?:а|ов|ам|ами|ах)?(?![а-я])", "миллибар", "градус", "паскал", "килопаскал", "мегапаскал",
                    "атмосфер", "процент", "промилл", "тонн", "килограмм", "грамм", "миллиграмм", "литр", "кубометр",
                    "кубическ", "куб(?:а|ов|ам|ами|ах)?(?![а-я])", "метр", "миллиметр", "сантиметр", "ампер",
                    "миллиампер", "вольт", "ватт", "киловатт", "мегаватт", "герц", "оборот", "единиц", "штук")
_NUMERAL = r"(?:" + "|".join(sorted(_NUMERAL_WORDS, key=len, reverse=True)) + r")"
_NUMBER_WORDS_RE = re.compile(
    r"(?<![а-яa-z])" + _NUMERAL + r"(?:\s+(?:и\s+)?" + _NUMERAL + r")*(?![а-яa-z])\s*(?:(?:" +
    "|".join(_FRACTION_WORDS) + r")(?![а-я])|(?:" + "|".join(_UNIT_WORD_STEMS) + r")[а-я]*|[°%‰]|(?:мбар|кпа|мпа|"
    r"кгс|кг|м3|м³|мм|квт|мвт|вт|гц|ма|ед|шт)(?![а-яa-z])|[а-яa-z]{1,4}[0-9³²]?/)")
# цифры не из Nd (m3): надстрочные «²³⁴», дроби «½», цифры в кружках, римские знаки (категории No и Nl); «²» и «³»
# сразу за буквой единицы («м³», «м²») — единица, а не цифра
_UNIT_POWER_RE = re.compile(r"(?<=[A-Za-zА-Яа-яЁё])[²³](?!\w)")


def _other_number(text: str) -> bool:
    return any(unicodedata.category(ch) in ("No", "Nl") for ch in _UNIT_POWER_RE.sub(" ", text))


def _number_words(text: str) -> bool:
    return bool(_NUMBER_WORDS_RE.search(text.casefold().replace("ё", "е")))


_HEX_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32,}(?![0-9A-Fa-f])")
_IPV4_RE = re.compile(r"\d+\.\d+\.\d+\.\d+")
_SERVICE_RE = re.compile(r"Bearer\s|BDRV_")


def _strings(obj: Any, ctx: str, parent: str = "") -> Iterable[tuple[str, str, str]]:
    """Строки и ключи пакета: (строка, контекст белого списка, ключ-родитель). Контексты: scenario, transcript,
    refusal (тексты отказов), meta (ключи, таблица чисел, признаки автомата)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k), "meta", ""
            yield from _strings(v, "refusal" if (ctx == "transcript" and k == "problems") else ctx, str(k))
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v, ctx, parent)
    elif isinstance(obj, str):
        yield obj, ctx, parent
    elif obj is not None and not isinstance(obj, bool):
        yield str(obj), ctx, parent


_ALLOWED_BY_CTX = {
    "scenario": CONST_TOKENS["scenario"] | CONST_TOKENS["any"],
    "transcript": CONST_TOKENS["args"] | CONST_TOKENS["any"],
    "refusal": CONST_TOKENS["refusal"] | CONST_TOKENS["args"] | CONST_TOKENS["any"],
    "meta": frozenset(),
}


def assert_packet_clean(packet: Packet, secrets: Iterable[object], vocab: frozenset[str], nodes: Iterable[str], *,
                        real_data: bool = False) -> None:
    """Сканер до записи (Д8-R78): секреты и служебные токены → SecretInPacket; цифра вне Ч-n/ТЕГ-n/УЗЕЛ-n/ИМЯ-n и
    CONST_TOKENS своего поля, цифра не из Nd (надстрочные, дроби «½», кроме «²³» единицы), число словами рядом с
    единицей или «целых/десятых» (вне scenario, без real_data; m3), тег, узел, слово словаря (кроме постоянного поля
    scenario), имя модели → DataInPacket. Тексты исключений — только вид находки."""
    text = packet.text
    for s in secrets:
        value = str(s)
        if value and (value in text or json.dumps(value, ensure_ascii=False)[1:-1] in text
                      or any(value in r for r in packet.raw)):
            raise SecretInPacket("секрет")
    if _SERVICE_RE.search(text):
        raise SecretInPacket("служебный токен")
    if _HEX_RE.search(text):
        raise SecretInPacket("шестнадцатеричная строка")
    if _IPV4_RE.search(text):
        raise SecretInPacket("адрес")
    if not PACKET_ID_RE.fullmatch(packet.packet_id):
        raise DataInPacket("packet_id")
    index = _VocabIndex(vocab)
    node_res = [re.compile(r"(?<!\w)" + r"\s+".join(re.escape(p) for p in n.split()) + r"(?!\w)", re.IGNORECASE)
                for n in nodes if isinstance(n, str) and n.strip()]
    for key in PACKET_FIELDS:
        ctx0 = {"scenario": "scenario", "transcript": "transcript", "saved_prompt": "transcript"}.get(key, "meta")
        for value, ctx, parent in _strings(packet.fields.get(key), ctx0):
            plain = _MARK_RE.sub(" ", value) if real_data else value
            plain = _ALIAS_RE.sub(" ", plain)
            allowed = _ALLOWED_BY_CTX[ctx]
            if any(tok not in allowed for tok in _digit_tokens(plain)) or _other_number(plain):
                raise DataInPacket("цифры")
            if not real_data and key != "scenario" and ctx != "meta" and _number_words(plain):
                raise DataInPacket("число словами")
            if TAG_TOKEN_RE.search(_ISO_RE.sub(" ", plain)):
                raise DataInPacket("тег")
            if _MODEL_RE.search(value):
                raise DataInPacket("имя модели")
            if key == "scenario":
                continue
            if any(p.search(value) for p in node_res):
                raise DataInPacket("узел")
            const = parent in CONST_KEYS and not _free_period(parent, value)
            if not real_data and ctx != "meta" and not const and _vocab_hit(value, index):
                raise DataInPacket("слово каталога")


# --- пачки ---

def _normalize_consent(text: str) -> str:
    text = re.sub(r"[«»“”„\"']", "", text)
    text = re.sub(r"[–—−-]", "—", text)
    return re.sub(r"\s+", " ", text).strip()


def _check_consent(judge_dir: Path) -> None:
    path = judge_dir / CONSENT_FILE
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        raise RunRefused("consent") from None
    if _normalize_consent(REAL_DATA_CONSENT) not in _normalize_consent(content) or \
            not re.search(r"\d{4}-\d{2}-\d{2}", content):
        raise RunRefused("consent")
    log.info("судья: согласие на настоящие данные, sha256 %s", hashlib.sha256(content.encode("utf-8")).hexdigest())


def judged_records(records: Iterable[RunRecord]) -> list[RunRecord]:
    """Записи в пакеты (m12): последняя запись ключа прогона и только со статусом done."""
    last: dict[tuple, RunRecord] = {}
    for r in records:
        last[(r.model, r.key.place, r.key.scenario, r.key.rep)] = r
    return [r for r in last.values() if r.status == "done"]


def load_controls(path: Path = CONTROLS_JSON) -> list[dict]:
    """Контрольные пакеты J1–J4: [{"id": "Jn", "expect": …, "packet": {поля пакета}}]."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _arrange(live: list[tuple[str, str]], controls: list[tuple[str, int]], size: int, seed: str) -> list[list[str]]:
    """Пачки: модели перемешаны (по очереди моделей), J1–J4 — копия 1 в первой пачке, копия 2 — в последней."""
    by_model: dict[str, list[str]] = {}
    for pid, model in live:
        by_model.setdefault(model, []).append(pid)
    order = sorted(by_model, key=lambda mdl: _letters(f"{seed}|модель|{mdl}"))
    queues = [sorted(by_model[mdl], key=lambda p: _letters(f"{seed}|{p}")) for mdl in order]
    mixed: list[str] = []
    while any(queues):
        for q in queues:
            if q:
                mixed.append(q.pop(0))
    total = len(mixed) + len(controls)
    n = max(1, math.ceil(total / max(1, size)))
    sizes = [total // n + (1 if i < total % n else 0) for i in range(n)]
    batches: list[list[str]] = [[] for _ in range(n)]
    for pid, copy in controls:
        batches[0 if (n == 1 or copy == 1) else n - 1].append(pid)
    rest = list(mixed)
    for i in range(n):
        room = max(0, sizes[i] - len(batches[i]))
        batches[i] += rest[:room]
        rest = rest[room:]
    while rest:
        min(batches, key=len).append(rest.pop(0))
    return [sorted(b, key=lambda p: _letters(f"{seed}|место|{p}")) for b in batches if b]


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _salt_of(judge: Path, seed: str) -> str:
    """Соль id пакетов (m12): из прежней карты того же seed (повторная упаковка даёт те же id), иначе — новая."""
    try:
        old = json.loads((judge / "map.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = None
    if isinstance(old, dict) and old.get("seed") == seed and isinstance(old.get("salt"), str) and old["salt"]:
        return old["salt"]
    return os.urandom(16).hex()


def pack_batches(out: Path, records: list[RunRecord], cards: CardSet, secrets: Iterable[object], size: int = 12,
                 seed: str = "", real_data: bool = False, tag_units: Mapping[str, str] | None = None,
                 salt: str | None = None) -> list[Path]:
    """Пакеты по записям (только done, последняя запись ключа — m12) и J1–J4 → OUT/judge/batch-NN/<id>.json и
    карта OUT/judge/map.json. Сканер — до записи: отказанный пакет не пишется, в карте — вид отказа. real_data — только
    с файлом согласия OUT/judge/real-data-consent.txt, иначе RunRefused("consent"). id пакета — от соли карты (salt;
    нет — из прежней карты того же seed или новая случайная, m12): по run_id и именам моделей id не пересчитать, и
    контрольный пакет по id не узнать."""
    judge = Path(out) / "judge"
    if real_data:
        _check_consent(judge)
    salt = salt or _salt_of(judge, seed)
    units = load_tag_units(Path(out)) if tag_units is None else dict(tag_units)
    secret_list = [s for s in secrets if str(s)]
    recs = judged_records(records)
    vocab = build_vocab(recs, cards)
    nodes = [c.node for c in cards.cards.values() if c.node] + [cards.bench_node]
    used: set[str] = set()

    def new_id(base: str) -> str:
        n = 0
        while True:
            pid = _letters(f"{salt}|{seed}|{base}|{n}")
            if pid not in used:
                used.add(pid)
                return pid
            n += 1

    runs: dict[str, dict] = {}
    refused: dict[str, str] = {}
    packets: dict[str, Packet] = {}
    live: list[tuple[str, str]] = []
    for rec in recs:
        card = cards.cards.get(rec.key.scenario)
        if card is None:
            raise ValueError("пачки: нет карточки сценария записи")
        pid = new_id(run_ref(rec))
        runs[pid] = {"model": rec.model, "place": rec.key.place, "scenario": rec.key.scenario, "rep": rec.key.rep}
        try:
            packet = build_packet(rec, BY_ID[rec.key.scenario], card, vocab, real_data, units,
                                  bench_node=cards.bench_node, packet_id=pid)
            assert_packet_clean(packet, secret_list, vocab, nodes, real_data=real_data)
        except SecretInPacket:
            refused[pid] = "secret"
            log.warning("судья: пакет %s не записан — сканер нашёл секрет", pid)
            continue
        except DataInPacket as exc:
            refused[pid] = "data"
            log.warning("судья: пакет %s не записан — сканер: %s", pid, exc)
            continue
        packets[pid] = packet
        live.append((pid, rec.model))
    controls: dict[str, str] = {}
    ctrl_list: list[tuple[str, int]] = []
    for c in load_controls():
        for copy in (1, 2):
            pid = new_id(f"{c['id']}#{copy}")
            packet = Packet.of(pid, c["packet"])
            assert_packet_clean(packet, secret_list, frozenset(), [])
            packets[pid] = packet
            controls[pid] = f"{c['id']}#{copy}"
            ctrl_list.append((pid, copy))
    judge.mkdir(parents=True, exist_ok=True)
    for old in judge.glob("batch-*"):                     # пачки — свой вывод: пишутся заново
        if old.is_dir():
            shutil.rmtree(old)
    paths: list[Path] = []
    batch_map: dict[str, list[str]] = {}
    for i, pids in enumerate(_arrange(live, ctrl_list, size, seed), 1):
        bdir = judge / f"batch-{i:02d}"
        bdir.mkdir()
        for pid in pids:
            (bdir / f"{pid}.json").write_text(packets[pid].text, encoding="utf-8")
        batch_map[bdir.name] = pids
        paths.append(bdir)
    _write_json(judge / "map.json", {"packets": runs, "controls": controls, "refused": refused,
                                     "batches": batch_map, "seed": seed, "salt": salt, "real_data": real_data})
    log.info("судья: пакетов %d, контрольных %d, отказано %d, пачек %d", len(live), len(controls), len(refused),
             len(paths))
    return paths


# --- каталог судей и задания ---

_TASK_BEGIN = "<!-- задание:начало -->"
_TASK_END = "<!-- задание:конец -->"


def task_template(judge_md: str) -> str:
    """Шаблон задания из шапки JUDGE.md (между метками задания)."""
    a, b = judge_md.index(_TASK_BEGIN) + len(_TASK_BEGIN), judge_md.index(_TASK_END)
    return judge_md[a:b].strip("\n") + "\n"


def render_task(stage: Path, batch: str, pids: Iterable[str]) -> str:
    template = task_template((stage / "JUDGE.md").read_text(encoding="utf-8"))
    pids = list(pids)
    packets = "\n".join(f"- {stage / batch / f'{p}.json'}" for p in pids)
    verdicts = "\n".join(f"- {stage / 'verdicts' / batch / f'{p}.json'}" for p in pids)
    return (template.replace("{JUDGE_MD}", str(stage / "JUDGE.md")).replace("{BATCH}", batch)
            .replace("{PACKETS}", packets).replace("{VERDICTS}", verdicts))


def stage_judges(out: Path, run_id: str, root: Path, only: Collection[str] | None = None) -> Path:
    """Каталог судей <root>/judge-<run_id>/: JUDGE.md, копии пачек, пустые verdicts/<batch>/, задания
    tasks/<batch>.txt (абсолютные пути: root приводится к абсолютному, m8). Карты, разговоров, конфига и карточек там
    нет. only — второй заход (m7): задания только по этим пакетам; задания пачек без них удаляются."""
    judge = Path(out) / "judge"
    stage = Path(root).resolve() / f"judge-{run_id}"
    (stage / "tasks").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(JUDGE_MD, stage / "JUDGE.md")
    for bdir in sorted(p for p in judge.glob("batch-*") if p.is_dir()):
        dst = stage / bdir.name
        dst.mkdir(exist_ok=True)
        names = {f.name for f in bdir.glob("*.json")}
        for f in dst.glob("*.json"):
            if f.name not in names:
                f.unlink()
        for f in sorted(bdir.glob("*.json")):
            shutil.copyfile(f, dst / f.name)
        (stage / "verdicts" / bdir.name).mkdir(parents=True, exist_ok=True)
        pids = [f[:-5] for f in sorted(names)]
        if only is not None:
            pids = [p for p in pids if p in only]
        task = stage / "tasks" / f"{bdir.name}.txt"
        if pids:
            task.write_text(render_task(stage, bdir.name, pids), encoding="utf-8")
        elif task.exists():
            task.unlink()
    return stage


_RAW_FIELD = re.compile(r'"(file_path|path|command)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _unparsed_input(inp: dict) -> dict:
    """Вход вызова, который харнесс не разобрал (m7: {"__unparsedToolInput": {"raw": "<JSON>", "len": n}}): raw как
    JSON-объект, иначе поля file_path, path и command из текста raw — только если поле встретилось ровно один раз;
    иначе {} (вызов без пути и команды — нарушение у сверки)."""
    raw = inp.get("__unparsedToolInput")
    raw = raw.get("raw") if isinstance(raw, dict) else raw
    if not isinstance(raw, str):
        return {}
    try:
        obj = json.loads(raw)
    except ValueError:
        obj = None
    if isinstance(obj, dict):
        return obj
    found: dict[str, list[str]] = {}
    for m in _RAW_FIELD.finditer(raw):
        try:
            found.setdefault(m.group(1), []).append(json.loads(f'"{m.group(2)}"'))
        except ValueError:
            found.setdefault(m.group(1), []).append("")
    return {k: v[0] for k, v in found.items() if len(v) == 1 and v[0]}


def transcript_calls(path: Path) -> list[dict]:
    """Вызовы инструментов субагента из его транскрипта JSONL (m-р4-14): tool_use в message.content —
    {"tool", "path"} (input.file_path / path) или {"tool", "command"}; без пути и команды — path None. Вход, который
    харнесс не разобрал (__unparsedToolInput), — поля из его raw (_unparsed_input, m7)."""
    out: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        msg = obj.get("message") if isinstance(obj, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "tool_use":
                continue
            name = str(part.get("name") or "")
            inp = part.get("input") if isinstance(part.get("input"), dict) else {}
            if "__unparsedToolInput" in inp:
                inp = _unparsed_input(inp)
            p = inp.get("file_path") if isinstance(inp.get("file_path"), str) else inp.get("path")
            if isinstance(p, str):
                out.append({"tool": name, "path": p})
            elif isinstance(inp.get("command"), str):
                out.append({"tool": name, "command": inp["command"]})
            else:
                out.append({"tool": name, "path": None})
    return out


def _task_packets(task: Path, stage: Path, batch: str) -> set[str] | None:
    if not task.exists():
        return None
    prefix = str(stage / batch) + os.sep
    return {Path(line[2:].strip()).name for line in task.read_text(encoding="utf-8").splitlines()
            if line.startswith("- ") and line[2:].strip().startswith(prefix)}


def audit_transcripts(calls: list[dict], stage: Path, batch: str) -> list[str]:
    """Нарушения списка разрешений (I-р4-5, П5) — по одной строке на вызов, без значений и путей. Разрешено: Read —
    JUDGE.md, пакеты пачки (по заданию, если оно есть) и их вердикты; Write — вердикты пакетов пачки; служебные
    HARNESS_TOOLS. Путь с «..» — нарушение до нормализации; затем сравнение по os.path.realpath."""
    stage = Path(stage)
    packets = {f.name for f in (stage / batch).glob("*.json")}
    listed = _task_packets(stage / "tasks" / f"{batch}.txt", stage, batch)
    if listed is not None:
        packets &= listed
    root = os.path.realpath(stage) + os.sep

    def inside(*parts: str) -> set[str]:                # ссылка из каталога судей наружу — не разрешение
        real = os.path.realpath(stage.joinpath(*parts))
        return {real} if real.startswith(root) else set()

    verdicts = {r for p in packets for r in inside("verdicts", batch, p)}
    read_ok = inside("JUDGE.md") | {r for p in packets for r in inside(batch, p)} | verdicts
    out: list[str] = []
    for c in calls:
        tool = re.sub(r"[^\w-]", "?", str(c.get("tool") or "?"))[:80]
        if tool in HARNESS_TOOLS:
            continue
        if tool not in ("Read", "Write"):
            out.append(f"{tool}: инструмент вне списка")
            continue
        path = c.get("path")
        if not isinstance(path, str) or not path:
            out.append(f"{tool}: без пути")
        elif ".." in Path(path).parts:
            out.append(f"{tool}: путь с ..")
        elif not os.path.isabs(path):
            out.append(f"{tool}: относительный путь")
        else:
            real = os.path.realpath(path)
            if real not in (read_ok if tool == "Read" else verdicts):
                out.append(f"{tool}: {'вне пачки' if real.startswith(root) else 'вне каталога судей'}")
    return out


def collect_verdicts(stage: Path, out: Path, batches: Collection[str] | None = None) -> int:
    """Переносит (не копирует, m7) вердикты stage/verdicts/<batch>/<id>.json в OUT/judge/verdicts/<id>.json."""
    dst = Path(out) / "judge" / "verdicts"
    dst.mkdir(parents=True, exist_ok=True)
    moved = 0
    root = Path(stage) / "verdicts"
    for bdir in sorted(p for p in root.glob("batch-*") if p.is_dir()):
        if batches is not None and bdir.name not in batches:
            continue
        for f in sorted(bdir.glob("*.json")):
            if PACKET_ID_RE.fullmatch(f.stem):
                shutil.move(str(f), dst / f.name)
                moved += 1
    return moved


def discard_verdicts(stage: Path, batch: str) -> int:
    """Вердикты пачки с нарушением — в корзину (удаляются из каталога судей)."""
    n = 0
    for f in (Path(stage) / "verdicts" / batch).glob("*.json"):
        f.unlink()
        n += 1
    return n


# --- разбор вердиктов и контроль ---

def load_map(out: Path) -> dict:
    return json.loads((Path(out) / "judge" / "map.json").read_text(encoding="utf-8"))


def check_verdicts(out: Path) -> VerdictReport:
    """Разбор OUT/judge/verdicts/<id>.json по пакетам пачек (строго, parse_verdict): пропавшие, кривые, отказанные."""
    judge = Path(out) / "judge"
    cmap = load_map(out)
    items: dict[str, tuple[str, ...]] = {}
    for f in judge.glob("batch-*/*.json"):
        items[f.stem] = tuple(json.loads(f.read_text(encoding="utf-8"))["scenario"]["items"])
    ok: dict[str, Verdict] = {}
    missing: list[str] = []
    invalid: list[str] = []
    for pid in sorted(items):
        vf = judge / "verdicts" / f"{pid}.json"
        if not vf.exists():
            missing.append(pid)
            continue
        try:
            ok[pid] = parse_verdict(vf.read_text(encoding="utf-8"), items[pid])
        except (ValueError, UnicodeDecodeError):
            invalid.append(pid)
    refused = dict(cmap.get("refused") or {})
    runs = {pid: f"{r['model']}|{r['place']}|{r['scenario']}|{r['rep']}"
            for pid, r in (cmap.get("packets") or {}).items()}
    return VerdictReport(ok=ok, missing=missing, invalid=invalid, refused=sorted(refused), runs=runs,
                         refused_kind=refused)


def _control_ok(cid: str, v: Verdict) -> bool:
    hard = v.hard
    clean = not hard.get("invented_norm") and not hard.get("numbers_not_from_tools") and not hard.get(
        "saved_prompt_bad")
    if cid == "J1":
        return clean and all(s >= 4 for s in v.scores.values()) and v.scores.get("norm_rule", 0) >= 4
    if cid == "J2":
        return bool(hard.get("invented_norm"))
    if cid == "J3":
        return bool(hard.get("numbers_not_from_tools"))
    if cid == "J4":
        return clean
    return False


def controls_ok(report: VerdictReport, controls_map: Mapping[str, str]) -> dict[str, bool]:
    """Jn верен, только если оба экземпляра (Jn#1 и Jn#2) разобраны и верны (m-н5, I-н2)."""
    out: dict[str, bool] = {}
    for cid in CONTROL_IDS:
        copies = [pid for pid, c in controls_map.items() if c.split("#")[0] == cid]
        out[cid] = len(copies) == 2 and all(pid in report.ok and _control_ok(cid, report.ok[pid]) for pid in copies)
    return out


__all__ = ["CONST_KEYS", "CONST_TOKENS", "GENERIC_WORDS", "HARNESS_TOOLS", "MASKS", "MODEL_WORDS", "PERIOD_PRESETS",
           "QUANTITY_CLASSES", "REAL_DATA_CONSENT", "REFUSAL_TEXTS", "SOURCE_KINDS", "STOP_WORDS", "TAG_TOKEN_RE",
           "UNITS", "VERDICT_SCHEMA", "DataInPacket", "Packet", "SecretInPacket", "Verdict", "VerdictReport",
           "alias_numbers", "assert_packet_clean", "audit_transcripts", "blind", "build_packet", "build_vocab",
           "check_verdicts", "collect_verdicts", "controls_ok", "describe", "discard_verdicts", "judged_records",
           "load_map", "mask_vocab", "pack_batches", "parse_verdict", "passed_by_judge", "render_task", "run_ref",
           "stage_judges", "stem", "task_template", "transcript_calls", "vocab_from"]
