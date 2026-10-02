"""Разбор чисел ответа и одно правило чисел (Д8-R72; I-н4, I-р3-8, I-р3-9, I-р4-4, m-р3-6, m-р4-10, m6).

parse_numbers — числа, как их показала модель: запятая или точка, пробел и неразрывный пробел между разрядами по три
цифры, знаки «−» и «-». Не числа: время (11:59), даты (03.03.2005, 2026-10-01, «3 марта», «2005 года», дд.мм без
единицы после — «12.05 бар» число, m-р3-6), ISO-метки и пояса, токены вида тега, цифры, приклеенные к букве (м3, см2,
БДМ-2, H2O), номера пунктов списка в начале строки («1.», «2)»). Дефис или тире между двумя числами — диапазон, а не
знак второго числа.

matches — число после округления до показанных знаков равно значению (правило Д6-R47): |значение − показанное| не
больше половины единицы последнего показанного знака, в обе стороны (округление «вверх» и «к чётному» — оба честные).

foreign_numbers — «чужие» числа: их нет среди источников (числа выводов инструментов, производные, числа вопроса и
периода — also). Классы:
- allowed — неизмеряемые (NON_MEASURE): длительности, порядковые, номера смен и «№» (только целое 1–9: «за смену
  128,9» — значение), счёт, проценты (кроме чисел с единицей тега «%»), доля достоверности, часы «с 6 до 14 часов»;
- hard — не allowed и за числом единица тега; или слово значения (VALUE_WORDS, только с начала слова, re.I) в том же
  отрезке до ближайшего «.!?;» без другого числа между словом и числом (неизмеряемые числа слово не заслоняют); в
  C2 и C4 (strict_value_words) так же и процент;
- other — прочие: провал foreign_number (не жёсткий) и признак suspect_numbers для судьи.
Только stdlib; модуль — основание пакета (ничего из pcbk_bench не импортирует).
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

# токен вида тега (Д8-R72; второй шаблон — «21FAKE0042», как TAG_TOKEN_RE Б1)
TAG_TOKEN_RE = re.compile(r"[A-Za-z0-9]*\d[A-Za-z0-9]*_[A-Za-z0-9_]+|\b\d{2}[A-Z]+[A-Z0-9_]*\b")

_SP = " \u00a0\u202f\u2009"                     # пробел, неразрывный, узкий неразрывный, тонкий
_MONTHS = r"(?:январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр)"
# что в разбор чисел не попадает вовсе; найденное заменяется знаком \x00 (позиции сохраняются). Тег — с учётом
# регистра: «10min» не тег
_ISO_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+−-]\d{2}:?\d{2})?)?")
_MASKS = (TAG_TOKEN_RE, _ISO_STAMP) + tuple(re.compile(p, re.IGNORECASE | re.MULTILINE) for p in (
    r"\b\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})(?!\d)",                                       # дд.мм.гггг, дд.мм.гг
    r"\b\d{1,2}:\d{2}(?::\d{2})?(?!\d)",                                                # время
    r"\b(?:UTC|GMT|MSK|МСК)[ \t]*[+−-][ \t]*\d{1,2}(?::?\d{2})?",                       # пояс
    rf"\b\d{{1,2}}[{_SP}]+{_MONTHS}\w*",                                                # «3 марта»
    rf"\b(?:19|20)\d{{2}}[{_SP}]*(?:г\.|год\w*)",                                       # «2005 года»
    r"^[ \t]*(?:[-*•][ \t]*)?\d{1,2}[.)](?=[ \t])",                                     # «1.», «2)» пункта
))
_NUM = re.compile(rf"(?P<sign>[−+-])?(?P<int>\d{{1,3}}(?:[{_SP}]\d{{3}})+(?!\d)|\d+)(?:(?P<sep>[.,])(?P<frac>\d+))?")
# единица после дд.мм: тогда это число, а не дата (m-р3-6)
_UNIT_AFTER = re.compile(rf"[{_SP}]*(?:[°%‰]|(?:бар|мбар|кпа|мпа|па|кгс|кг|т|м3|м³|мм|м|л|г|об|квт|мвт|вт|гц|ма|ед|шт|"
                         r"мин|ч)(?![^\W\d_])|[^\W\d_]{1,4}[0-9³²]?/)", re.IGNORECASE)

_AFTER = rf"^[{_SP}]*"
NON_MEASURE: dict[str, re.Pattern] = {
    # после числа: «24 ч», «8 часов», «20 с назад»; «с» — секунды только перед концом, знаком или «назад»
    "duration": re.compile(_AFTER + r"(?:сек\w*|мин\w*|ч(?![^\W\d_])|час\w*|сут\w*|дн\w*|дней|недел\w*|мес\w*|"
                           r"год\w*|лет(?![^\W\d_])|с(?=[" + _SP + r"]*(?:$|[.,;:!?)\]—–-]|назад|тому)))",
                           re.IGNORECASE),
    "ordinal": re.compile(r"^-(?:й|я|е|го|му|ой|ом|ая|ую|ий|ее|ей|х|м|ми)(?![^\W\d_])", re.IGNORECASE),
    # «смена 2», «смене №3» (перед числом) и «2-я смена», «2 смена» (после)
    "shift": re.compile(rf"смен\w*[{_SP}]*№?[{_SP}]*$|^(?:-(?:я|ю|й|ой))?[{_SP}]+смен", re.IGNORECASE),
    "count": re.compile(_AFTER + r"(?:тег|значени|вызов|раз|точк|запрос|агент)", re.IGNORECASE),
    "label": re.compile(rf"№[{_SP}]*$"),                                                 # «узел №2»
    "percent": re.compile(_AFTER + r"(?:%|процент)", re.IGNORECASE),
    # доля достоверности: слово не дальше 15 знаков до числа с «%» (или сразу за «%»: «0 % достоверных»)
    "quality_share": re.compile(r"(?:достоверн|качеств|дол[яеиюй])\w*[^\d.!?;\n]{0,15}$", re.IGNORECASE),
    # часы «с 6 до 14 часов»: целое до 24 после «с»/«от», затем «до N» и слово часа (у второго — длительность)
    "clock": re.compile(rf"(?:^|[{_SP}(])(?:с|от)[{_SP}]+$", re.IGNORECASE),
}
_QUALITY_AFTER = re.compile(rf"^[{_SP}]*%[{_SP}]*(?:достоверн|качеств)", re.IGNORECASE)
_CLOCK_AFTER = re.compile(rf"^[{_SP}]*до[{_SP}]+\d{{1,2}}[{_SP}]*(?:ч(?![^\W\d_])|час\w*|утра|вечера|ночи|дня)",
                          re.IGNORECASE)
_BEFORE_KINDS = ("shift", "label", "quality_share")
_INT_KINDS = ("shift", "label")                     # номер смены и «№» — только целое от 1 до 9
VALUE_WORDS = re.compile(r"\b(средн\w*|мин(имум\w*|имальн\w*)?\b|макс(имум\w*|имальн\w*)?\b|значени\w*|сейчас|"
                         r"составля\w*|равн(о|а|ы|яется|ялось)\b|было\b)", re.IGNORECASE)
_MINUTES_ABBR = re.compile(rf"\d[{_SP}]*$")          # «5 мин» — минуты, а не «минимум»
_BOUNDARY = re.compile(r"[!?;]|\.(?![ \t]*[0-9a-zа-яё])")


@dataclass(frozen=True)
class Shown:
    value: Decimal
    decimals: int
    start: int
    end: int


@dataclass(frozen=True)
class Foreign:
    shown: Shown
    kind: Literal["allowed", "hard", "other"]


def _masked(text: str) -> str:
    out = list(text)
    for pattern in _MASKS:
        for m in pattern.finditer(text):
            out[m.start():m.end()] = "\x00" * (m.end() - m.start())
    return "".join(out)


def _is_word(ch: str) -> bool:
    return ch.isalpha() or ch == "_"


def parse_numbers(text: str) -> list[Shown]:
    """Числа текста в порядке появления (Shown.start/end — позиции в исходном тексте)."""
    s = _masked(text)
    out: list[Shown] = []
    for m in _NUM.finditer(s):
        sign, start = m.group("sign"), m.start("int")
        if sign:
            before = s[m.start() - 1] if m.start() > 0 else ""
            if _is_word(before):                                # «БДМ-2», «T-3» — часть имени
                continue
            if before.isdigit():                                # «3-4» — диапазон: знака нет
                sign = ""
            elif sign == "+":
                sign = ""
                start = m.start()
            else:
                start = m.start()
        else:
            before = s[start - 1] if start > 0 else ""
            if _is_word(before) or before.isdigit():            # «м3», «H2O», «a1b2»
                continue
            if before in ".," and start > 1 and s[start - 2].isdigit():     # хвост «1.2.3»
                continue
        end = m.end()
        if end + 1 < len(s) and s[end] in ".," and s[end + 1].isdigit():    # «1.2.3», «192.168.…» — не число
            continue
        digits = re.sub(rf"[{_SP}]", "", m.group("int"))
        frac, sep = m.group("frac"), m.group("sep")
        if (sep == "." and not sign and len(digits) == 2 and len(frac) == 2 and 1 <= int(digits) <= 31
                and 1 <= int(frac) <= 12 and not _UNIT_AFTER.match(s, end)):
            continue                                            # дд.мм без единицы — дата (m-р3-6)
        raw = ("-" if sign in ("−", "-") else "") + digits + (f".{frac}" if frac else "")
        out.append(Shown(Decimal(raw), len(frac) if frac else 0, start, end))
    return out


def _dec(value: float | int | Decimal) -> Decimal | None:
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        return Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    except InvalidOperation:
        return None


def _half_unit(decimals: int) -> Decimal:
    return Decimal(5).scaleb(-(decimals + 1))


def close(shown: Shown, value: Decimal, tolerance: Decimal | None = None) -> bool:
    """Показанное равно значению после округления до показанных знаков (или в пределах tolerance)."""
    return abs(value - shown.value) <= (tolerance if tolerance is not None else _half_unit(shown.decimals))


def matches(value: float, text: str) -> bool:
    v = _dec(value)
    return v is not None and any(close(s, v) for s in parse_numbers(text))


def also_numbers(also: str) -> list[Decimal]:
    """Числа вопроса и периода — источники: каждая группа цифр (03.03.2005 → 3, 3, 2005) и разобранные числа.
    Цифры токенов вида тега источниками не считаются: «20» из «20FAKE_104_PV» — не число вопроса."""
    plain = _ISO_STAMP.sub(lambda m: re.sub(r"\D", " ", m.group(0)), also)     # «03T08» — не тег: сначала метки
    plain = TAG_TOKEN_RE.sub(" ", plain)
    return [Decimal(int(d)) for d in re.findall(r"\d+", plain)] + [s.value for s in parse_numbers(plain)]


def _norm_unit(text: str) -> str:
    text = re.sub(rf"[{_SP}]+", " ", text).casefold()
    text = re.sub(r"° ?[сc]", "°c", text).replace("³", "3").replace("²", "2")
    return text.lstrip().lstrip("°").lstrip()


def unit_after(text: str, end: int, units: Iterable[str]) -> bool:
    """За числом (через пробелы) — одна из единиц; после единицы — не буква и не цифра."""
    tail = _norm_unit(text[end:end + 40])
    for unit in units:
        u = _norm_unit(unit or "")
        if u and tail.startswith(u) and (len(tail) == len(u) or not tail[len(u)].isalnum()):
            return True
    return False


def non_measure_kind(text: str, s: Shown) -> str | None:
    """Вид неизмеряемого числа по соседнему тексту (без слов значения); None — число величины."""
    after, before = text[s.end:s.end + 40], text[max(0, s.start - 60):s.start]
    small_int = s.decimals == 0 and 0 < s.value < 10
    for name, pattern in NON_MEASURE.items():
        if name in ("quality_share", "clock") or (name in _INT_KINDS and not small_int):
            continue                                # «за смену 128,9» — значение, а не номер смены
        if pattern.search(before if name in _BEFORE_KINDS else after):
            if name == "percent" and (NON_MEASURE["quality_share"].search(before) or _QUALITY_AFTER.match(after)):
                return "quality_share"
            return name
    if (s.decimals == 0 and 0 <= s.value <= 24 and NON_MEASURE["clock"].search(before)
            and _CLOCK_AFTER.match(after)):
        return "clock"
    return None


def segment_of(text: str, start: int, end: int) -> tuple[int, int]:
    """Отрезок вокруг [start, end) до ближайших «.!?;» (точка внутри числа, «мин.» перед числом — не граница)."""
    left, right = 0, len(text)
    for m in _BOUNDARY.finditer(text):            # без endpos: заглядывание вперёд видит текст за границей поиска
        if m.end() <= start:
            left = m.end()
        elif m.start() >= end:
            right = m.start()
            break
    return left, right


def value_word_near(text: str, s: Shown, blockers: Iterable[Shown]) -> bool:
    """Слово значения в том же отрезке без числа-заслона между словом и числом (I-р3-9, m-р4-10)."""
    left, right = segment_of(text, s.start, s.end)
    walls = [b for b in blockers if b is not s and left <= b.start and b.end <= right]
    for w in VALUE_WORDS.finditer(text, left, right):
        if w.group(0).casefold() == "мин" and _MINUTES_ABBR.search(text, 0, w.start()):
            continue
        lo, hi = (w.end(), s.start) if w.end() <= s.start else (s.end, w.start())
        if lo <= hi and not any(lo <= b.start and b.end <= hi for b in walls):
            return True
    return False


def foreign_numbers(text: str, sources: Iterable[float], also: str = "", tag_units: Iterable[str] = (),
                    strict_value_words: bool = False) -> list[Foreign]:
    """Числа текста, которых нет в источниках, с классом allowed / hard / other (Д8-R72)."""
    src = [v for v in (_dec(x) for x in sources) if v is not None] + also_numbers(also)
    units = [u for u in tag_units if u]
    shown = parse_numbers(text)
    kinds = {id(s): non_measure_kind(text, s) for s in shown}
    walls = [s for s in shown if kinds[id(s)] is None]          # неизмеряемое число слово значения не заслоняет
    out: list[Foreign] = []
    for s in shown:
        if any(close(s, v) for v in src):
            continue
        kind = kinds[id(s)]
        if kind is not None and kind != "percent":
            out.append(Foreign(s, "allowed"))
            continue
        if kind == "percent" and "%" not in {_norm_unit(u) for u in units}:
            hard = strict_value_words and value_word_near(text, s, walls)
            out.append(Foreign(s, "hard" if hard else "allowed"))
            continue
        if unit_after(text, s.end, units) or value_word_near(text, s, walls):
            out.append(Foreign(s, "hard"))
        else:
            out.append(Foreign(s, "other"))
    return out
