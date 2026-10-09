r"""Сценарный бот-студент без модели (Д8-R73) и общее правило согласия (П8).

Бот видит только тексты агента, вызовов инструментов не видит. Реплики — только из карточки: имён тегов и значений
историана он не называет, кроме тега выбора A3 (card.pick), и то лишь когда Конструктор сам показал его в списке.
Против живого студента он мягок («да» на любой черновик постоянного вида), поэтому доля A — верхняя оценка успеха 1.

Правила reply(agent_text) у бота (driver "bot"), первое подходящее:
1. A4: пока не высказаны все card.requests — следующая просьба;
2. черновик постоянного вида (DRAFT_RE: строка, начинающаяся с «Черновик агента», и «Сохранить так?» после неё в том
   же сообщении) → следующая правка card.on_draft; правки кончились — CONSENT («да»);
3. список вариантов (не меньше двух строк LIST_RE «<тег> — …», или два тега в строку и вопрос выбора CHOICE_RE) и
   «?» → card.pick; тег выбора, которого нет в показанном списке, бот не называет — NOT_LISTED;
4. вопрос об имени (NAME_RE) → NAME_REPLY;
5. вопрос об узле (NODE_RE) → card.node — только узел, без цели;
6. вопрос о цели, вопросах технолога или периоде (GOAL_RE) → card.goal;
7. иначе — FALLBACK.
Правила 4–6 смотрят вопросительные предложения (кончаются на «?»), а если их нет — весь текст: так «поищу по
названию узла. Какой узел?» — вопрос об узле, а не об имени.

У скрипта (driver "script", B и C) — открытие и не больше одного уточнения card.clarify, только если агент спросил.
reply → None: реплик уже sc.max_student_msgs (открытие входит) или скрипту нечего сказать — разговор окончен.

Согласие (П8, одно правило для бота, проверок и двойника): реплика бота под CONSENT_RE = ^\s*(да\b|сохран|соглас)
(re.I). Правка — строка card.on_draft. Ни одно поле карточки, которое произносит бот или скрипт, под CONSENT_RE не
подходит (это сверяет load_cards): согласие бот добавляет сам и только на черновик.

Черновик честного Конструктора (П9; образец — render_draft, двойник А3 повторяет его значением, не импортом):
- текст — ровно постоянный вид konstruktor.md, шаг 3 (render_draft);
- pcbk_save_agent: tools — все три (catalog_search, tag_now, tag_period); tags — теги черновика ровно как их вернул
  последний catalog_search (у A3 — выбранный студентом) и совпадают с тегами последнего черновика (m6); prompt — по
  плану konstruktor.md без цифр вовсе (800–3000 знаков, служба принимает 200–4000; без SQL, обхода, файлов и норм);
  name — «bench-» и 6 hex sha256 разговора; student_reply — реплика согласия дословно.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from .numbers import TAG_TOKEN_RE, tag_tokens

if TYPE_CHECKING:                       # cards берёт отсюда CONSENT_RE — без круга импорта
    from .cards import Card
    from .scenarios import Scenario

# «да» — только словом: «Давай…», «Дайте…», «Далее» — не согласие (I-4 доводки р1)
CONSENT_RE = re.compile(r"^\s*(да\b|сохран|соглас)", re.IGNORECASE)
CONSENT = "да"


DRAFT_HEAD = "Черновик агента"
DRAFT_ASK = "Сохранить так? Ответьте «да» или напишите, что поменять."
DRAFT_RULE = ("Правило: агент берёт числа только из БДРВ; нормативы, допуски и пороги, которых нет в данных, не "
              "называет; общие знания помечает «общие знания, не норматив ПЦБК».")
# шапка — в начале строки (оформление Markdown вокруг допустимо), «Сохранить так?» — после неё в том же сообщении
DRAFT_RE = re.compile(r"^[ \t#*_>]*Черновик агента(?=[\s\S]*?Сохранить так\?)", re.IGNORECASE | re.MULTILINE)
# строка списка вариантов: «- 20FAKE_101_PV — описание», «1. `тег` – …», «| тег | … |»; захват — тег
LIST_RE = re.compile(r"^[ \t]*(?:[-*•]|\d{1,2}[.)]|\|)?[ \t]*[*_`]*([A-Za-z0-9]*\d[A-Za-z0-9]*_[A-Za-z0-9_]+)[*_`]*"
                     r"[ \t]*(?:[—–:|(,-]|$)", re.MULTILINE)
NAME_RE = re.compile(r"имя|назва|назов", re.IGNORECASE)                # «как назовём?» — тоже вопрос об имени
# список в строку («нашёл 20FAKE_101_PV, 20FAKE_102_PV — какой ваш?») — только с вопросом выбора
CHOICE_RE = re.compile(r"выбер|котор\w*\s+из|как(?:ой|ие|ую)\s+(?:из|ваш|нужн|подход|тег|верн)|\bваш\w*\s*\?",
                       re.IGNORECASE)
NODE_RE = re.compile(r"\b(?:узе?л\w*|участ(?:ок|к)\w*|оборудован\w*)", re.IGNORECASE)
GOAL_RE = re.compile(r"цел|задач|показыв|вопрос|период", re.IGNORECASE)
_QUESTION = re.compile(r"[^.!?\n]*\?")

FALLBACK = "Как считаешь нужным — покажи черновик"
NAME_REPLY = "как предложишь"
NOT_LISTED = "Моего тут нет — поищи ещё"


def render_draft(title: str, name: str, node: str, purpose: str, tags: Sequence[tuple[str, str, str]],
                 questions: Sequence[str], can: str = "текущие значения, сводка за период, поиск тегов") -> str:
    """Черновик постоянного вида (konstruktor.md, шаг 3); tags — (тег, описание из каталога, единица)."""
    lines = [DRAFT_HEAD, f"- Название: {title}", f"- Имя для системы: {name}", f"- Узел: {node}",
             f"- Назначение: {purpose}", f"- Что умеет: {can}", "- Теги:",
             *(f"  - {tag} — {description}, {unit}" for tag, description, unit in tags),
             f"- На какие вопросы отвечает: {'; '.join(questions)}", f"- {DRAFT_RULE}", DRAFT_ASK]
    return "\n".join(lines)


def is_draft(text: str) -> bool:
    return DRAFT_RE.search(text) is not None


def listed_tags(text: str) -> list[str]:
    """Теги строк списка вариантов, без повторов, в порядке показа."""
    return list(dict.fromkeys(LIST_RE.findall(text)))


class ScriptedStudent:
    """Бот по карточке; sent — все отправленные реплики (открытие входит в предел)."""

    def __init__(self, card: Card, sc: Scenario):
        if card.scenario != sc.id:
            raise ValueError("карточка не того сценария")
        self.card = card
        self.sc = sc
        self.sent: list[str] = []
        self.saw_draft = False
        self._requests = 0
        self._drafts = 0
        self._clarified = False

    def opening(self) -> str:
        return self._say(self.card.opening)

    def reply(self, agent_text: str) -> str | None:
        if len(self.sent) >= self.sc.max_student_msgs:
            return None
        text = agent_text or ""
        if self.sc.driver == "script":
            if self.card.clarify and not self._clarified and _QUESTION.search(text):
                self._clarified = True
                return self._say(self.card.clarify)
            return None
        if is_draft(text):                      # черновик виден и тогда, когда A4 ещё высказывает просьбы
            self.saw_draft = True
        if self._requests < len(self.card.requests):                                   # 1
            self._requests += 1
            return self._say(self.card.requests[self._requests - 1])
        if is_draft(text):                                                              # 2
            self._drafts += 1
            edits = self.card.on_draft
            return self._say(edits[self._drafts - 1] if self._drafts <= len(edits) else CONSENT)
        shown = listed_tags(text)
        inline = list(dict.fromkeys(tag_tokens(text)))           # без ISO-меток: «02T11» — не тег
        if len(shown) < 2 and len(inline) >= 2 and CHOICE_RE.search(" ".join(_QUESTION.findall(text))):
            shown = inline
        if len(shown) >= 2 and "?" in text:                                             # 3
            pick = self.card.pick
            if TAG_TOKEN_RE.fullmatch(pick) and pick.upper() not in {t.upper() for t in shown}:
                return self._say(NOT_LISTED)
            return self._say(pick)
        questions = " ".join(_QUESTION.findall(text)) or text
        if NAME_RE.search(questions):                                                   # 4
            return self._say(NAME_REPLY)
        if NODE_RE.search(questions) and self.card.node:                                # 5
            return self._say(self.card.node)
        if GOAL_RE.search(questions) and self.card.goal:                                # 6
            return self._say(self.card.goal)
        return self._say(FALLBACK)                                                      # 7

    def _say(self, text: str) -> str:
        self.sent.append(text)
        return text
