"""Списки имён тегов в файлах: белый список, whitelist.extra, прежний список для --base.

Одно имя в строке; строки на `#` и пустые пропускаются, пробелы по краям и BOM
снимаются. Имя вне SAFE_NAME — ошибка с номером строки, но без самой строки:
в журнал службы имена тегов не идут.
"""
from .sql import SAFE_NAME


def load_names(path: str) -> frozenset[str]:
    """Имена файла; нет файла — FileNotFoundError, недопустимое имя — ValueError."""
    with open(path, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    names = set()
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if SAFE_NAME.fullmatch(line) is None:
            raise ValueError(f"строка {n}: недопустимое имя")
        names.add(line)
    return frozenset(names)
