"""Разбор чисел ответа и одно правило чисел (Д8-R72; I-н4, I-р3-8, I-р3-9, I-р4-4, m-р3-6, m-р4-10, m6).

parse_numbers — числа, как их показала модель: запятая или точка, пробел и неразрывный пробел между разрядами по три
цифры, знаки «−» и «-». Не числа: время (11:59), даты (03.03.2005, 2026-10-01, «3 марта», «2005 года», дд.мм без
единицы после — «12.05 бар» число, m-р3-6), ISO-метки и пояса, токены вида тега, цифры, приклеенные к букве (м3, см2,
БДМ-2, H2O), номера пунктов списка в начале строки («1.», «2)»). Дефис или тире между двумя числами — диапазон, а не
знак второго числа.

matches — число после округления до показанных знаков равно значению (правило Д6-R47): |значение − показанное| не
больше половины единицы последнего показанного знака, в обе стороны (округление «вверх» и «к чётному» — оба честные).

foreign_numbers — «чужие» числа: их нет среди источников. Полные источники — числа выводов инструментов, производные,
числа вопроса (parse_numbers); слабые (weak; группы цифр дат и времени вопроса и периода) закрывают только other
(доводка р1, I-2). Классы:
- allowed — неизмеряемые (NON_MEASURE): длительности («24 ч», «Длительность, ч | 8»), порядковые, номера смен и «№»
  (только целое 1–9: «за смену 128,9» — значение), счёт и номер тега («по 3 тегам», «по тегу 104»), проценты (кроме
  чисел с единицей тега «%»), доля достоверности, часы «с 6 до 14 часов»;
- hard — не allowed и за числом единица тега; или слово значения (VALUE_WORDS, только с начала слова, re.I) в том же
  отрезке — до ближайшего «.!?;», «|» или перевода строки — без другого числа между словом и числом (неизмеряемые
  числа слово не заслоняют); в C2 и C4 (strict_value_words) — любой процент, кроме доли достоверности (Д8-R72 в
  редакции доводки р1, I-3);
- other — прочие: провал foreign_number (не жёсткий) и признак suspect_numbers для судьи.
value_label — какому полю вывода (avg, min, max, value) число отвечает по ближнему слову значения (m2 доводки р1).
tag_tokens — токены вида тега без ISO-меток («02T11» — не тег).
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
_SEP = rf"[{_SP}]*[:—–|-]?[{_SP}]*"                # «Смена: 2», «Номер смены — 2», «| Смена | 2 |»
NON_MEASURE: dict[str, re.Pattern] = {
    # после числа: «24 ч», «8 часов», «20 с назад»; «с» — секунды только перед концом, знаком или «назад»
    "duration": re.compile(_AFTER + r"(?:сек\w*|мин\w*|ч(?![^\W\d_])|час\w*|сут\w*|дн\w*|дней|недел\w*|мес\w*|"
                           r"год\w*|лет(?![^\W\d_])|с(?=[" + _SP + r"]*(?:$|[.,;:!?)\]—–-]|назад|тому)))",
                           re.IGNORECASE),
    # перед числом: «Длительность, ч | 8», «продолжительность: 8»
    "duration_label": re.compile(r"\b(?:длительн|продолжительн)\w*[^|\n\d]{0,12}" + _SEP + "$", re.IGNORECASE),
    "ordinal": re.compile(r"^-(?:й|я|е|ю|го|му|ой|ом|ая|ую|ий|ее|ей|х|м|ми)(?![^\W\d_])", re.IGNORECASE),
    # номер смены: «смена 2», «Номер смены — 2», «| Смена | 2 |» (перед числом) и «2 смену», «2-ю смену» (после)
    "shift": re.compile(rf"\bсмен\w*{_SEP}№?[{_SP}]*$", re.IGNORECASE),
    "shift_after": re.compile(rf"^(?:-(?:я|ю|й|ой|у))?[{_SP}]+смен", re.IGNORECASE),
    "count": re.compile(_AFTER + r"(?:тег|значени|вызов|раз|точк|запрос|запрош|агент|доступн|найден)", re.IGNORECASE),
    # счёт и номер тега перед числом: «Тегов | 1», «по тегу 104» (имя тега сокращено), «вызовов: 2»
    "count_label": re.compile(rf"\b(?:тег|вызов|запрос|агент|точк)\w*{_SEP}№?[{_SP}]*$", re.IGNORECASE),
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
_BEFORE_KINDS = frozenset({"duration_label", "shift", "count_label", "label", "quality_share", "clock"})
_SMALL_INT_KINDS = frozenset({"shift", "shift_after", "label"})     # номер смены и «№» — только целое от 1 до 9
_INT_KINDS = frozenset({"count_label"})                              # счёт и номер тега — только целое
# слова значения (I-р3-9, m-р4-10; I-3 доводки р1: «был/была/было/были», «держал…»)
VALUE_WORDS = re.compile(r"\b(средн\w*|мин(имум\w*|имальн\w*)?\b|макс(имум\w*|имальн\w*)?\b|значени\w*|сейчас|"
                         r"составля\w*|равн(о|а|ы|яется|ялось)\b|был[аио]?\b|держал\w*)", re.IGNORECASE)
# подпись числа (m2 доводки р1): какому полю вывода число отвечает по слову значения рядом
LABELS = (("avg", re.compile(r"средн", re.IGNORECASE)), ("min", re.compile(r"мин", re.IGNORECASE)),
          ("max", re.compile(r"макс", re.IGNORECASE)), ("value", re.compile(r"сейчас", re.IGNORECASE)))
_MINUTES_ABBR = re.compile(rf"\d[{_SP}]*$")          # «5 мин» — минуты, а не «минимум»
# граница окна слова значения: «.!?;», ячейка таблицы «|» и перевод строки (I-5 доводки р1)
_BOUNDARY = re.compile(r"[!?;|\n]|\.(?![ \t]*[0-9a-zа-яё])")
# граница предложения (suspect_norm: «общие знания» — в том же предложении): «.!?» и перевод строки
_SENTENCE = re.compile(r"[!?\n]|\.(?![ \t]*[0-9a-zа-яё])")
# шкала прибора (доводка р1б): число с подписью шкалы при ТОЧНОМ совпадении с min_eu/max_eu вывода catalog_search
# покрыто; подпись нормы в той же строке («норма», «допустимо», «должно быть», «предел» не прибора) — нет
SCALE_WORDS = re.compile(r"\bшкал\w*|\bдиапазон\w*\s+измерен\w*|\bпредел\w*\s+(?:измерен\w*\s+)?(?:прибор|датчик)\w*",
                         re.IGNORECASE)
_NORM_WORDS = re.compile(r"\b(?:норм|допуск|допустим|долж|порог|регламент)\w*|"
                         r"\bпредел\w*\b(?!\s+(?:измерен\w*\s+)?(?:прибор|датчик))", re.IGNORECASE)
_ROW = re.compile(r"[!?;\n]|\.(?![ \t]*[0-9a-zа-яё])")            # строка таблицы — одна: «| Шкала | 0–60 |»
_TIME_Z = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z?\b")


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


def tag_tokens(text: str) -> list[str]:
    """Токены вида тега без ISO-меток и времени: «02T11» и «40Z» из «2026-10-02T11:59:40Z» — не теги (C-1, р1)."""
    return TAG_TOKEN_RE.findall(_TIME_Z.sub(" ", _ISO_STAMP.sub(" ", text)))


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
    """Числа вопроса и периода. Сильные — разобранные parse_numbers (also_strong), слабые — каждая группа цифр дат
    и времени (03.03.2005 → 3, 3, 2005; also_weak). Цифры токенов вида тега источниками не считаются: «20» из
    «20FAKE_104_PV» — не число вопроса."""
    return also_strong(also) + also_weak(also)


def _also_plain(also: str) -> str:
    plain = _ISO_STAMP.sub(lambda m: re.sub(r"\D", " ", m.group(0)), also)     # «03T08» — не тег: сначала метки
    return TAG_TOKEN_RE.sub(" ", plain)


def also_strong(also: str) -> list[Decimal]:
    """Числа вопроса, как их разобрал бы ответ: даты, время, метки и теги parse_numbers сам не считает числами."""
    return [s.value for s in parse_numbers(also)]


def also_weak(also: str) -> list[Decimal]:
    return [Decimal(int(d)) for d in re.findall(r"\d+", _also_plain(also))]


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
        if name in ("quality_share", "clock") or (name in _SMALL_INT_KINDS and not small_int) or (
                name in _INT_KINDS and s.decimals):
            continue                                # «за смену 128,9» — значение, а не номер смены
        if name in _BEFORE_KINDS and NON_MEASURE["percent"].search(after):
            continue                                # «Смена: 55 %» — процент, подпись слева его не прячет
        if pattern.search(before if name in _BEFORE_KINDS else after):
            if name == "percent" and (NON_MEASURE["quality_share"].search(before) or _QUALITY_AFTER.match(after)):
                return "quality_share"
            return name
    if (s.decimals == 0 and 0 <= s.value <= 24 and NON_MEASURE["clock"].search(before)
            and _CLOCK_AFTER.match(after)):
        return "clock"
    return None


def segment_of(text: str, start: int, end: int, boundary: re.Pattern = _BOUNDARY) -> tuple[int, int]:
    """Отрезок вокруг [start, end) до ближайших «.!?;», «|» или перевода строки (точка внутри числа, «мин.» перед
    числом — не граница)."""
    left, right = 0, len(text)
    for m in boundary.finditer(text):             # без endpos: заглядывание вперёд видит текст за границей поиска
        if m.end() <= start:
            left = m.end()
        elif m.start() >= end:
            right = m.start()
            break
    return left, right


def sentence_of(text: str, start: int, end: int) -> tuple[int, int]:
    """Предложение вокруг [start, end): до «.!?» или перевода строки."""
    return segment_of(text, start, end, _SENTENCE)


def _bound_words(text: str, s: Shown, blockers: Iterable[Shown]) -> list[re.Match]:
    """Слова значения того же отрезка без числа-заслона между словом и числом; ближние — первыми, слева раньше."""
    left, right = segment_of(text, s.start, s.end)
    walls = [b for b in blockers if b is not s and left <= b.start and b.end <= right]
    found: list[tuple[int, int, re.Match]] = []
    for w in VALUE_WORDS.finditer(text, left, right):
        if w.group(0).casefold() == "мин" and _MINUTES_ABBR.search(text, 0, w.start()):
            continue
        lo, hi = (w.end(), s.start) if w.end() <= s.start else (s.end, w.start())
        if lo <= hi and not any(lo <= b.start and b.end <= hi for b in walls):
            found.append((0 if w.end() <= s.start else 1, hi - lo, w))
    return [w for _, _, w in sorted(found, key=lambda x: (x[0], x[1]))]


def value_label(text: str, s: Shown, blockers: Iterable[Shown]) -> str | None:
    """Поле вывода, которому число отвечает по ближнему слову значения (слева — раньше): avg, min, max, value;
    слово без поля («значение», «было») или слова нет — None."""
    words = _bound_words(text, s, blockers)
    if not words:
        return None
    for name, pattern in LABELS:
        if pattern.match(words[0].group(0)):
            return name
    return None


def scale_covered(text: str, s: Shown, shown: Iterable[Shown], scale: Iterable[Decimal]) -> bool:
    """Число — граница шкалы прибора: точно равно min_eu или max_eu, в той же строке есть подпись шкалы («шкала»,
    «диапазон измерения», «пределы прибора/датчика»), между подписью и числом — только другие точные границы шкалы,
    а подписи нормы в строке нет. «норма — до 60» и «шкала 0–70» при max_eu = 60 не покрыты."""
    bounds = list(scale)
    if not any(s.value == v for v in bounds):
        return False
    left, right = segment_of(text, s.start, s.end, _ROW)
    if _NORM_WORDS.search(text, left, right):
        return False
    for w in SCALE_WORDS.finditer(text, left, right):
        lo, hi = (w.end(), s.start) if w.end() <= s.start else (s.end, w.start())
        if lo > hi:
            continue
        between = [b for b in shown if b is not s and lo <= b.start and b.end <= hi]
        if all(any(b.value == v for v in bounds) for b in between):
            return True
    return False


def value_word_near(text: str, s: Shown, blockers: Iterable[Shown]) -> bool:
    """Слово значения в том же отрезке без числа-заслона между словом и числом (I-р3-9, m-р4-10)."""
    return bool(_bound_words(text, s, blockers))


def foreign_numbers(text: str, sources: Iterable[float], also: str = "", tag_units: Iterable[str] = (),
                    strict_value_words: bool = False, weak: Iterable[float] = (),
                    scale: Iterable[float] = ()) -> list[Foreign]:
    """Числа текста, которых нет в источниках, с классом allowed / hard / other (Д8-R72).

    sources и числа also, разобранные parse_numbers, — полные источники. weak и группы цифр дат и времени also —
    слабые (I-2 доводки р1): закрывают только число, которое иначе было бы other, и никогда — hard. Так шкала прибора
    (min_eu, max_eu), числа в строках службы и цифры периода не прячут «0 °C», «16 °C» или «150 °C» в C4.
    strict_value_words (C2, C4): процент, кроме доли достоверности, — hard (I-3 доводки р1).
    scale — min_eu/max_eu вывода catalog_search: граница шкалы с подписью шкалы покрыта (scale_covered, р1б)."""
    src = [v for v in (_dec(x) for x in sources) if v is not None] + also_strong(also)
    soft = [v for v in (_dec(x) for x in weak) if v is not None] + also_weak(also)
    bounds = [v for v in (_dec(x) for x in scale) if v is not None]
    units = [u for u in tag_units if u]
    shown = parse_numbers(text)
    kinds = {id(s): non_measure_kind(text, s) for s in shown}
    walls = [s for s in shown if kinds[id(s)] is None]          # неизмеряемое число слово значения не заслоняет
    out: list[Foreign] = []
    for s in shown:
        if any(close(s, v) for v in src) or (bounds and scale_covered(text, s, shown, bounds)):
            continue
        kind = kinds[id(s)]
        if kind is not None and kind != "percent":
            cls = "allowed"
        elif kind == "percent" and "%" not in {_norm_unit(u) for u in units}:
            cls = "hard" if strict_value_words else "allowed"
        elif unit_after(text, s.end, units) or value_word_near(text, s, walls):
            cls = "hard"
        else:
            cls = "other"
        if cls == "other" and any(close(s, v) for v in soft):
            continue
        out.append(Foreign(s, cls))
    return out
