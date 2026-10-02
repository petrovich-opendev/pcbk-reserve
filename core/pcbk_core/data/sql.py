"""Шаблоны SQL к историану — единственный источник SQL службы.

Провайдер INSQL ищет имена тегов в тексте WHERE ещё при подготовке запроса:
параметр драйвера он не видит («History queries must contain at least one valid
tagname»), а второй LIKE отвергает. Поэтому здесь нет ни LIKE, ни параметров:
имя попадает в SQL литералом через TagName IN (…), не больше 16 имён и только
из безопасного набора символов. IN и OR на ww-столбцы не ставятся.

Каждый шаблон регистрируется формой в TEMPLATE_FORMS: ворота пускают только
инструкцию, целиком совпавшую с одной из форм, — так NOT, OR, вторая
инструкция или комментарий не проходят даже с именами из белого списка.

Сводка (summary_sql) несёт ещё даты по DATE_FMT и значение интерполяции из
WW_CONSTANTS; ворота пускают их через allowed_literal. Её SQL и форма
строятся из одних констант пробы (SummaryShape) — правка DATE_FMT,
PIN_INTERPOLATION или HAS_LAST меняет и то, и другое.
"""
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

MAX_NAMES = 16
SAFE_NAME = re.compile(r"[A-Za-z0-9_]{1,128}")
# представления провайдера INSQL: на них действуют ограничения выше
PROVIDER_VIEWS = ("Live", "AnalogHistory", "AnalogSummaryHistory", "StateSummaryHistory")

# решения по пробе историана (Д3б, задача 1, шаг 8): правятся только эти три строки;
# SummaryShape, SUMMARY_COLUMNS и форма TEMPLATE_FORMS["summary"] строятся из них
DATE_FMT: str = "%Y-%m-%d %H:%M:%S.000"
PIN_INTERPOLATION: str | None = None
HAS_LAST: bool = False

_LITERAL = r"'(?:[^']|'')*'"
_LITERAL_RE = re.compile(_LITERAL)
# список имён — только литералы: TagName IN ('a', 'b'); NOT IN и выражения в списке сюда не попадают
_NAMES_LIST = rf"\bTagName\s+IN\s*\(\s*{_LITERAL}(?:\s*,\s*{_LITERAL})*\s*\)"
# слева направо: литерал целиком съедает свой текст, поэтому «TagName IN (…)» внутри строки не список
_TOKENS = re.compile(rf"(?P<names>{_NAMES_LIST})|(?P<literal>{_LITERAL})", re.IGNORECASE)
# вне литералов: комментарии, двойные кавычки, [имена] и вторая инструкция сбили бы разбор литералов;
# одиночная ' здесь — незакрытый литерал
_LEX_BREAKERS = re.compile(r"--|/\*|[\"\[;']")


def lit_name(name: str) -> str:
    """Имя тега литералом SQL; имя вне SAFE_NAME — ValueError (без самого имени в тексте)."""
    if not isinstance(name, str) or SAFE_NAME.fullmatch(name) is None:
        raise ValueError("имя тега вне набора [A-Za-z0-9_]{1,128}")
    return "'" + name.replace("'", "''") + "'"


def clock_sql() -> str:
    return "SELECT GETDATE() AS NowLocal, GETUTCDATE() AS NowUtc"


def catalog_sql() -> str:
    return ("SELECT t.TagName, t.Description, t.TagType, a.MinEU, a.MaxEU, e.Unit FROM Tag t "
            "LEFT JOIN AnalogTag a ON a.TagName = t.TagName "
            "LEFT JOIN EngineeringUnit e ON e.EUKey = a.EUKey WHERE t.TagType IN (1, 2)")


def live_all_sql() -> str:
    return "SELECT TagName, DateTime, Value, Quality FROM Live WHERE Value IS NOT NULL"


_LIVE_HEAD = "SELECT TagName, DateTime, Value, Quality FROM Live WHERE TagName IN "


def live_sql(names: Sequence[str]) -> str:
    if isinstance(names, str) or not 0 < len(names) <= MAX_NAMES:
        raise ValueError(f"нужно от 1 до {MAX_NAMES} имён тегов")
    return _LIVE_HEAD + "(" + ", ".join(lit_name(n) for n in names) + ")"


# список имён в форме — ровно как его пишет live_sql: от 1 до MAX_NAMES литералов SAFE_NAME через ", "
_NAME_LITERAL = r"'[A-Za-z0-9_]{1,128}'"
_NAME_LIST = rf"\({_NAME_LITERAL}(?:, {_NAME_LITERAL}){{0,{MAX_NAMES - 1}}}\)"


# --- сводка за период (Д3б) ---

BASE_SUMMARY_COLUMNS = ("Minimum", "Maximum", "Average", "StdDev", "PercentGood")
LAST_COLUMNS = ("Last", "LastDateTime")
# значения ww-столбцов, которые ворота пускают литералом (закрытый набор)
WW_CONSTANTS = frozenset({"STAIRSTEP"})


@dataclass(frozen=True)
class SummaryShape:
    """Что в сводке зависит от пробы: формат даты, закреплённая интерполяция, столбцы Last."""

    date_fmt: str = DATE_FMT
    pin: str | None = PIN_INTERPOLATION
    has_last: bool = HAS_LAST

    @property
    def columns(self) -> tuple[str, ...]:
        return BASE_SUMMARY_COLUMNS + (LAST_COLUMNS if self.has_last else ())


SHAPE = SummaryShape()
SUMMARY_COLUMNS = SHAPE.columns


def lit_dt(dt: datetime, date_fmt: str = DATE_FMT) -> str:
    """Местное время историана литералом SQL по date_fmt; секунды без долей. Время с поясом — ValueError."""
    if not isinstance(dt, datetime) or dt.tzinfo is not None:
        raise ValueError("дата для SQL — местное время историана без пояса")
    return "'" + dt.replace(microsecond=0).strftime(date_fmt) + "'"


# части сводки — общие у summary_sql и summary_form: SQL и форма собираются по одним и тем же кускам
_SUMMARY_START = " AND StartDateTime >= "
_SUMMARY_END = " AND EndDateTime <= "
_SUMMARY_CYCLES = " AND wwCycleCount = 1"


def _summary_head(shape: SummaryShape) -> str:
    return "SELECT TagName, " + ", ".join(shape.columns) + " FROM AnalogSummaryHistory WHERE TagName IN "


def _summary_tail(shape: SummaryShape) -> str:
    # значение интерполяции — только из WW_CONSTANTS: иначе ворота не пустили бы ни одной сводки,
    # а кавычка в нём сломала бы литерал; для PIN_INTERPOLATION это ValueError уже при импорте
    if shape.pin is not None and shape.pin not in WW_CONSTANTS:
        raise ValueError("интерполяция сводки — только значение из WW_CONSTANTS")
    return _SUMMARY_CYCLES + ("" if shape.pin is None else f" AND wwInterpolationType = '{shape.pin}'")


def summary_sql(names: Sequence[str], start: datetime, end: datetime, shape: SummaryShape = SHAPE) -> str:
    """Сводка историана за [start, end] одной корзиной (wwCycleCount = 1); время — местное, без пояса."""
    if isinstance(names, str) or not 0 < len(names) <= MAX_NAMES:
        raise ValueError(f"нужно от 1 до {MAX_NAMES} имён тегов")
    return (_summary_head(shape) + "(" + ", ".join(lit_name(n) for n in names) + ")"
            + _SUMMARY_START + lit_dt(start, shape.date_fmt) + _SUMMARY_END + lit_dt(end, shape.date_fmt)
            + _summary_tail(shape))


# директивы формата даты и их регулярки; другая директива — ValueError (при импорте — для DATE_FMT).
# Только [0-9]: \d без re.ASCII берёт любые цифры Юникода («٢٠٢٦», «２０２６»)
_DATE_DIRECTIVES = {"%Y": "[0-9]{4}", "%m": "[0-9]{2}", "%d": "[0-9]{2}", "%H": "[0-9]{2}", "%M": "[0-9]{2}",
                    "%S": "[0-9]{2}"}


def _date_form(date_fmt: str) -> str:
    """Регулярка литерала даты по date_fmt, с кавычками."""
    parts, i = [], 0
    while i < len(date_fmt):
        if date_fmt[i] == "%":
            directive = date_fmt[i:i + 2]
            if directive not in _DATE_DIRECTIVES:
                raise ValueError(f"формат даты: директива {directive!r} не поддержана")
            parts.append(_DATE_DIRECTIVES[directive])
            i += 2
        else:
            parts.append(re.escape(date_fmt[i]))
            i += 1
    return "'" + "".join(parts) + "'"


def summary_form(shape: SummaryShape = SHAPE) -> re.Pattern[str]:
    """Форма сводки: та же строка, что пишет summary_sql, по тем же частям; список имён — как у live."""
    date = _date_form(shape.date_fmt)
    return re.compile(re.escape(_summary_head(shape)) + _NAME_LIST + re.escape(_SUMMARY_START) + date
                      + re.escape(_SUMMARY_END) + date + re.escape(_summary_tail(shape)), re.ASCII)


def allowed_literal(text: str, shape: SummaryShape = SHAPE) -> bool:
    """Литерал вне TagName IN (…), который ворота пускают: дата по shape.date_fmt или значение WW_CONSTANTS.

    Дата — только ровно та строка, что пишет lit_dt: strptime вольный (цифры Юникода в %Y и %S,
    час без нуля, лишние пробелы), поэтому разобранное печатается обратно и сверяется с текстом.
    """
    if text in WW_CONSTANTS:
        return True
    try:
        return datetime.strptime(text, shape.date_fmt).strftime(shape.date_fmt) == text
    except (TypeError, ValueError):
        return False


# формы всех инструкций службы; сверка — fullmatch
TEMPLATE_FORMS: dict[str, re.Pattern[str]] = {
    "clock": re.compile(re.escape(clock_sql())),
    "catalog": re.compile(re.escape(catalog_sql())),
    "live_all": re.compile(re.escape(live_all_sql())),
    "live": re.compile(re.escape(_LIVE_HEAD) + _NAME_LIST),
    "summary": summary_form(),
}


def template_of(sql: str) -> str | None:
    """Имя шаблона, форме которого инструкция соответствует целиком, иначе None."""
    for name, form in TEMPLATE_FORMS.items():
        if form.fullmatch(sql):
            return name
    return None


def _unquote(literal: str) -> str:
    return literal[1:-1].replace("''", "'")


def scan_literals(sql: str) -> tuple[tuple[str, bool], ...]:
    """Все литералы по порядку, с признаком «стоит в списке TagName IN (…)»."""
    out: list[tuple[str, bool]] = []
    for m in _TOKENS.finditer(sql):
        if m.group("names") is not None:
            out.extend((_unquote(lit), True) for lit in _LITERAL_RE.findall(m.group("names")))
        else:
            out.append((_unquote(m.group("literal")), False))
    return tuple(out)


def names_in(sql: str) -> tuple[str, ...]:
    """Литералы из каждого TagName IN (…) по порядку."""
    return tuple(text for text, in_names in scan_literals(sql) if in_names)


def literals(sql: str) -> tuple[str, ...]:
    """Все строковые литералы '…' по порядку, '' внутри — одна кавычка."""
    return tuple(text for text, _ in scan_literals(sql))


def plain(sql: str) -> bool:
    """Вне литералов нет того, что сбивает scan_literals: комментариев, двойных кавычек, [имён], «;»
    и незакрытой «'».

    Одну инструкцию и её форму это не гарантирует (вторая инструкция без «;», NOT, OR проходят) —
    их проверяет template_of.
    """
    return _LEX_BREAKERS.search(_LITERAL_RE.sub(" ", sql)) is None
