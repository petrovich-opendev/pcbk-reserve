"""Шаблоны SQL к историану — единственный источник SQL службы.

Провайдер INSQL ищет имена тегов в тексте WHERE ещё при подготовке запроса:
параметр драйвера он не видит («History queries must contain at least one valid
tagname»), а второй LIKE отвергает. Поэтому здесь нет ни LIKE, ни параметров:
имя попадает в SQL литералом через TagName IN (…), не больше 16 имён и только
из безопасного набора символов. IN и OR на ww-столбцы не ставятся.

Каждый шаблон регистрируется формой в TEMPLATE_FORMS: ворота пускают только
инструкцию, целиком совпавшую с одной из форм, — так NOT, OR, вторая
инструкция или комментарий не проходят даже с именами из белого списка.
"""
import re
from collections.abc import Sequence

MAX_NAMES = 16
SAFE_NAME = re.compile(r"[A-Za-z0-9_]{1,128}")
# представления провайдера INSQL: на них действуют ограничения выше
PROVIDER_VIEWS = ("Live", "AnalogHistory", "AnalogSummaryHistory", "StateSummaryHistory")

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
# формы всех инструкций службы (Д3б добавит свои); сверка — fullmatch
TEMPLATE_FORMS: dict[str, re.Pattern[str]] = {
    "clock": re.compile(re.escape(clock_sql())),
    "catalog": re.compile(re.escape(catalog_sql())),
    "live_all": re.compile(re.escape(live_all_sql())),
    "live": re.compile(re.escape(_LIVE_HEAD) + _NAME_LIST),
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
