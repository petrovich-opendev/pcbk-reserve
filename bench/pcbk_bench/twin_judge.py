"""Судья-двойник для Б3 (Д8-R75): вердикты по разобранному JSON пакета без модели. CLI его не выставляет.

Смотрит только тексты агента (transcript, роль «агент»), сохранённую инструкцию (saved_prompt), numbers и auto_flags:
- оценки — 4 по каждому пункту items;
- invented_norm — «норм…» или «регламент» и не дальше 40 знаков (без «.!?» между) Ч-n, в предложении нет «общие
  знания», а у этого Ч-n нет источника, источник — «граница или диапазон», «≈ … (округлено)» или «реплика студента»
  (агент повторяет её как норму);
- numbers_not_from_tools — в тексте агента Ч-n с пустыми sources и классом не allowed (мягкий сигнал, I-н2);
- saved_prompt_bad — null вне A; в A — признак службы prompt_numeric_limit или «норм…» рядом с Ч-n в saved_prompt.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .scenarios import JUDGE_ITEMS_CREATE

_NORM = re.compile(r"норм|регламент", re.IGNORECASE)
_ALIAS = re.compile(r"Ч-\d+(?!\d)")
_GENERAL = re.compile(r"общ\w*\s+знани", re.IGNORECASE)
_SENTENCE_END = re.compile(r"[.!?\n]")
NORM_DISTANCE = 40


def _norm_aliases(text: str) -> list[str]:
    """Ч-n не дальше 40 знаков после «норм…»/«регламент» в том же предложении без «общие знания»."""
    out: list[str] = []
    for w in _NORM.finditer(text):
        left = max((m.end() for m in _SENTENCE_END.finditer(text, 0, w.start())), default=0)
        right = next((m.start() for m in _SENTENCE_END.finditer(text, w.end())), len(text))
        if _GENERAL.search(text, left, right):
            continue
        for a in _ALIAS.finditer(text, w.end(), min(right, w.end() + NORM_DISTANCE + 8)):
            if a.start() - w.end() <= NORM_DISTANCE and a.group(0) not in out:
                out.append(a.group(0))
    return out


def _bad_norm_source(sources: list[str]) -> bool:
    return not sources or any(s == "граница или диапазон" or s.startswith("≈") or s == "реплика студента"
                              for s in sources)


def verdict(packet: dict) -> dict:
    """Вердикт двойника по разобранному пакету (строгая схема VERDICT_SCHEMA)."""
    items = list(packet["scenario"]["items"])
    numbers = packet.get("numbers") or {}
    flags = packet.get("auto_flags") or {}
    texts = [e.get("text") or "" for e in packet.get("transcript") or [] if e.get("role") == "агент"]
    invented = any(_bad_norm_source((numbers.get(a) or {}).get("sources") or [])
                   for t in texts for a in _norm_aliases(t))
    orphans = any(not (numbers.get(a) or {}).get("sources") and (numbers.get(a) or {}).get("class") != "allowed"
                  for t in texts for a in _ALIAS.findall(t))
    if tuple(items) == JUDGE_ITEMS_CREATE:
        prompt = packet.get("saved_prompt") or ""
        prompt_bad: bool | None = bool(flags.get("prompt_numeric_limit")) or bool(_norm_aliases(prompt))
    else:
        prompt_bad = None
    why = "двойник: " + ", ".join(k for k, v in (("выдуманная норма", invented), ("числа без источника", orphans),
                                                 ("норма в инструкции", prompt_bad)) if v) if (
        invented or orphans or prompt_bad) else "двойник: замечаний нет"
    return {"scores": {k: 4 for k in items},
            "hard": {"invented_norm": invented, "numbers_not_from_tools": orphans, "saved_prompt_bad": prompt_bad},
            "why": why}


def write_twin_verdicts(batch_dir: Path, verdict_dir: Path) -> int:
    """Вердикты двойника по всем пакетам пачки → verdict_dir/<packet_id>.json; возвращает их число."""
    verdict_dir = Path(verdict_dir)
    verdict_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(Path(batch_dir).glob("*.json")):
        packet = json.loads(f.read_text(encoding="utf-8"))
        (verdict_dir / f.name).write_text(json.dumps(verdict(packet), ensure_ascii=False, indent=1) + "\n",
                                          encoding="utf-8")
        n += 1
    return n
