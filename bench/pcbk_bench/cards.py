"""Карточки сценариев (Д8-R72, Д8-R73, Д8-R75): что говорит бот или скрипт и на каких тегах проверка.

Файл карточек — JSON: {"bench_node": str, "bench_tags": [str], "cards": {"<id>": {поля Card без scenario}}}. Карточки
двойника — bench/cards.twin.json (в git, только 20FAKE_*/16FAKE_*); живые — $JOB/pcbk-d8/live/cards.json, их пишет
отдельный субагент (Д8-R76), в git их нет.

Поля по сценариям:
- A: opening (у A4 — с узлом целиком и целью), node — ответ на вопрос об узле, goal — на вопрос о цели,
  on_draft — только правки (согласие бот добавляет сам; П8 и I-4: любое произносимое поле под CONSENT_RE — ошибка
  формы), requests —
  просьбы A4, pick — выбор из списка («первый» или тег, A3), bridge — первый вопрос новому агенту (мост),
  expect_tag — тег моста (не обязателен);
- B1, B2, B4, C2, C4: tag — имя тега, оно же стоит в opening; у C4 — period {"start", "end"} (ISO без пояса —
  местное время историана, как у службы);
- B3: expect_tag — тег, к которому вопрос без имени должен привести, clarify — одно уточнение словами;
- C1, C3: opening.
load_cards сверяет только вид (типы, ключи, согласие в on_draft); полноту и коды формы — check_cards (Б3). Тексты
ValueError — без значений карточек: в них только id сценария и имя поля.

Card.bench_tags (сверх брифа) load_cards заполняет у всех карточек тегами заготовленного агента: check_run берёт
единицы этих тегов в правило чисел у сценариев агента bench (I-р4-4), а сигнатура check_run(rec, card, tag_units)
CardSet не получает. CardSet.aliases — постоянные псевдонимы тегов и узлов карточек (ТЕГ-n, УЗЕЛ-n) по порядку
сценариев; пакеты судьи Б1 строят свои по пакету, это — справочно.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from datetime import datetime
from pathlib import Path

from .numbers import TAG_TOKEN_RE
from .scenarios import BENCH_AGENT, SCENARIO_IDS
from .student import CONSENT, CONSENT_RE

# AGENT_TOOLS Д7 (core/pcbk_core/agents/draft.py) — значением, не импортом: bench только на stdlib
AGENT_TOOLS = ("catalog_search", "tag_now", "tag_period")
# пределы полей save_agent (draft.py TEXT_LIMITS, PROMPT_MIN/MAX, MAX_TAGS) — значением
NODE_LIMITS = (2, 100)
MAX_TAGS = 16

BENCH_TITLE = "Узел стенда отбора"
BENCH_PURPOSE = "Отвечать технологу о текущих значениях и сводке за период по тегам узла"
# инструкция заготовленного агента: по плану konstruktor.md, без цифр, без норм, без SQL и обхода (m15)
BENCH_PROMPT = (
    "Ты — помощник технолога по узлу «{node}». Ты отвечаешь на вопросы «что сейчас» и «что было за период» по "
    "тегам этого узла. Теги узла с описаниями и единицами служба перечислила ниже, в разделе «Теги узла»; бери "
    "имена тегов оттуда или из ответа pcbk_catalog_search.\n"
    "Как отвечать на типовые вопросы:\n"
    "- «Что сейчас по тегу …», «какое сейчас …» → pcbk_tag_now по этому тегу; назови значение, единицу, время "
    "замера и качество.\n"
    "- «Среднее, минимум или максимум за смену, за сутки или за свой период» → pcbk_tag_period: готовый период из "
    "описания инструмента или начало и конец периода; назови среднее, минимум, максимум и границы периода.\n"
    "- Если в вопросе сравнение — сначала получи оба числа инструментами, потом сравни и покажи, как считал.\n"
    "- Вопрос про параметр без имени тега → pcbk_catalog_search по словам вопроса; если подходящих тегов "
    "несколько — покажи их списком «тег — описание — единица» и спроси, какой нужен.\n"
    "- Служба ответила отказом, «нет данных» или тег не входит в белый список — скажи об этом как есть, число не "
    "подставляй.\n"
    "Формат ответа: сначала число с единицей, потом тег и время или период; коротко, по-русски.")


@dataclass(frozen=True)
class Card:
    scenario: str
    opening: str
    node: str = ""
    goal: str = ""
    on_draft: tuple[str, ...] = ()
    requests: tuple[str, ...] = ()
    pick: str = "первый"
    expect_tag: str | None = None
    bridge: str = ""
    tag: str | None = None
    period: dict | None = None
    clarify: str = ""
    bench_tags: tuple[str, ...] = ()        # теги bench-uzel; заполняет load_cards (см. шапку модуля)


@dataclass(frozen=True)
class CardSet:
    cards: dict[str, Card]
    bench_node: str
    bench_tags: tuple[str, ...]
    aliases: dict[str, str]


_STR_FIELDS = ("opening", "node", "goal", "pick", "bridge", "clarify")
_LIST_FIELDS = ("on_draft", "requests")
_TAG_FIELDS = ("expect_tag", "tag")
# поля, которые произносит бот или скрипт: ни одно не может звучать как согласие
_SPOKEN = ("opening", "node", "goal", "pick", "bridge", "clarify", "on_draft", "requests")
_CARD_KEYS = frozenset(f.name for f in fields(Card)) - {"scenario", "bench_tags"}


def _card(sid: str, raw: object, bench_tags: tuple[str, ...]) -> Card:
    if not isinstance(raw, dict):
        raise ValueError(f"карточка {sid}: нужен объект")
    extra = set(raw) - _CARD_KEYS
    if extra:
        raise ValueError(f"карточка {sid}: неизвестные поля ({len(extra)})")
    values: dict[str, object] = {}
    for name in _STR_FIELDS:
        if name in raw:
            if not isinstance(raw[name], str):
                raise ValueError(f"карточка {sid}: поле {name} — строка")
            values[name] = raw[name]
    if not isinstance(raw.get("opening"), str) or not raw["opening"].strip():
        raise ValueError(f"карточка {sid}: нет opening")
    for name in _LIST_FIELDS:
        if name in raw:
            items = raw[name]
            if not isinstance(items, list) or not all(isinstance(i, str) and i.strip() for i in items):
                raise ValueError(f"карточка {sid}: поле {name} — список непустых строк")
            values[name] = tuple(items)
    for name in _TAG_FIELDS:
        if name in raw:
            if raw[name] is not None and not isinstance(raw[name], str):
                raise ValueError(f"карточка {sid}: поле {name} — строка или null")
            values[name] = raw[name]
    if "period" in raw and raw["period"] is not None:
        period = raw["period"]
        if not isinstance(period, dict) or set(period) != {"start", "end"} or not all(
                isinstance(v, str) for v in period.values()):
            raise ValueError(f"карточка {sid}: period — {{start, end}}")
        try:
            start, end = datetime.fromisoformat(period["start"]), datetime.fromisoformat(period["end"])
        except ValueError:
            raise ValueError(f"карточка {sid}: period — ISO 8601") from None
        if (start.tzinfo is None) != (end.tzinfo is None) or start >= end:
            raise ValueError(f"карточка {sid}: period — начало раньше конца, пояс у обоих или ни у одного")
        values["period"] = dict(period)
    for name in _SPOKEN:                     # П8, I-4 доводки р1: согласие бот добавляет сам и только на черновик
        said = values.get(name, ())
        if any(CONSENT_RE.match(line) for line in ((said,) if isinstance(said, str) else said)):
            raise ValueError(f"карточка {sid}: поле {name} похоже на согласие — его произносит бот")
    return Card(scenario=sid, bench_tags=bench_tags, **values)


def _aliases(bench_node: str, bench_tags: tuple[str, ...], cards: dict[str, Card]) -> dict[str, str]:
    tags: list[str] = list(bench_tags)
    nodes: list[str] = [bench_node]
    for sid in SCENARIO_IDS:
        c = cards.get(sid)
        if c is None:
            continue
        tags += [t for t in (c.tag, c.expect_tag, c.pick) if t and TAG_TOKEN_RE.fullmatch(t)]
        if c.node:
            nodes.append(c.node)
    out: dict[str, str] = {}
    for i, t in enumerate(dict.fromkeys(tags), 1):
        out[t] = f"ТЕГ-{i}"
    for i, n in enumerate(dict.fromkeys(n for n in nodes if n and n not in out), 1):
        out[n] = f"УЗЕЛ-{i}"
    return out


def load_cards(path: Path) -> CardSet:
    """Карточки из JSON; неверный вид — ValueError без значений карточек."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"карточки: не JSON (строка {exc.lineno})") from None
    if not isinstance(raw, dict) or set(raw) != {"bench_node", "bench_tags", "cards"}:
        raise ValueError("карточки: ключи верхнего уровня — bench_node, bench_tags, cards")
    node, tags, cards = raw["bench_node"], raw["bench_tags"], raw["cards"]
    if not isinstance(node, str) or not node.strip():
        raise ValueError("карточки: bench_node — непустая строка")
    if not isinstance(tags, list) or not all(isinstance(t, str) and t for t in tags) or len(set(tags)) != len(tags):
        raise ValueError("карточки: bench_tags — список разных непустых строк")
    if not isinstance(cards, dict):
        raise ValueError("карточки: cards — объект")
    unknown = [sid for sid in cards if sid not in SCENARIO_IDS]
    if unknown:
        raise ValueError(f"карточки: неизвестные сценарии ({len(unknown)})")
    bench_tags = tuple(tags)
    out = {sid: _card(sid, cards[sid], bench_tags) for sid in SCENARIO_IDS if sid in cards}
    return CardSet(cards=out, bench_node=node, bench_tags=bench_tags, aliases=_aliases(node, bench_tags, out))


def with_bench_tags(card: Card, bench_tags: tuple[str, ...]) -> Card:
    """Карточка с тегами заготовленного агента (для карточек, собранных не через load_cards)."""
    return replace(card, bench_tags=tuple(bench_tags))


def bench_agent_args(cs: CardSet) -> dict:
    """Аргументы save_agent заготовленного агента BENCH_AGENT (B1, B2, B4, C1, C3, C4): один файл для всех моделей.
    Узел и теги — из карточек; инструкция — постоянная, без цифр (признака prompt_numeric_limit у неё не бывает)."""
    low, high = NODE_LIMITS
    if not low <= len(cs.bench_node.strip()) <= high:
        raise ValueError("карточки: bench_node — от 2 до 100 знаков")
    if not cs.bench_tags or len(cs.bench_tags) > MAX_TAGS:
        raise ValueError("карточки: bench_tags — от 1 до 16 тегов")
    return {"name": BENCH_AGENT, "title": BENCH_TITLE, "node": cs.bench_node.strip(), "purpose": BENCH_PURPOSE,
            "tools": list(AGENT_TOOLS), "tags": list(cs.bench_tags),
            "prompt": BENCH_PROMPT.format(node=cs.bench_node.strip()), "student_reply": CONSENT}
