"""Разрешение терминов (Task 1; план §Task 1/Этап 1; мастер-промпт §4, §7 п.4, §5).

Отдельно от `retrieval.py`: термины — словарь корпуса (`terms.jsonl`: `id, preferred_ru, aliases, definition,
source_refs, ambiguous, machine_scope, knowledge_state, local_operating_authority, auto_merge_aliases`), а
`retrieval.py` — полнотекстовый/точный поиск по карточкам (`claims`/`mechanisms`/`processes`) и подписям тегов.
`preanalysis.py` (Task 3) вызывает этот модуль, чтобы превратить слово из вопроса технолога в `ResolvedTerm`
(`models.py`): «Существенная неоднозначность вызывает уточнение до оплачиваемого вызова и чтения данных»
(мастер-промпт §7 п.4) — отсюда `requires_confirmation` у каждого результата, а не только у спорных.

Точные подписи тегов (вид `НН<буквы><цифры>`, например в тестовой фикстуре `20FAKE_001_PV` — настоящие подписи
предприятия в этот модуль и его тесты не попадают, план, Global Constraints) ищутся отдельно от морфологической
нормализации русского текста (мастер-промпт §4: «unicode61 не решает русскую морфологию»): этот модуль не
трогает буквы/цифры подписи, только словарные слова вопроса. Нечёткое сходство — кандидат, не подтверждение
(мастер-промпт §4: «нечёткое сходство предлагает кандидата, но не подтверждает тег»): `resolve_term` всегда
возвращает список кандидатов, выбор одного — `selected_id`, который preanalysis.py решает оставить пустым при
неоднозначности.

Нормализация (`normalize_ru`) — наивный эвристический стеммер своими силами: снимает частые падежные/родовые
окончания существительных и прилагательных (список `_SUFFIXES`), без словарей и без новых зависимостей
(`pymorphy2` и подобные — новая зависимость, план Global Constraints: «новых зависимостей нет»; см. также
`task-0-report.md`, раздел «Вопросы/развилки», пункт Task 1). Это не полная морфология: он не обязан снимать
каждое окончание правильно — `resolve_term` не доверяет стеммеру молча, он только предлагает нормализованную
форму для сравнения со словарём, а решение «кандидат один и не спорный» принимает отдельно, по количеству
совпадений и флагу `ambiguous` корпуса. Сравнение терминов — по множеству нормализованных слов (`_word_set`),
не по точной строке целиком: вопрос может быть короче или длиннее словарной формулировки на служебное слово —
совпадение в любую сторону (вопрос ⊆ словарная форма или обратно) даёт кандидата, расходящиеся по смыслу слова
(например «отклонения» у Valmet COV и «вариации» у статистического коэффициента вариации) не совпадают никогда
— отсюда "COV на экранах Valmet IQ" (термин `cov`, корпус заказчика) и статистический коэффициент вариации
(термин `coefficient-variation-statistical`) остаются разными кандидатами даже при частично похожем вопросе
«коэффициент…» (который тогда отдаёт ОБА — не угадывает, какой из двух, а запрашивает уточнение).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import knowledge
from .models import ResolvedTerm

if TYPE_CHECKING:
    from .knowledge import CorpusHandle

MAX_CANDIDATES = 10  # models.ResolvedTerm.candidate_ids — Field(max_length=10)
_MIN_STEM = 3
_MIN_WORD = 2  # однобуквенные СЛОВА (предлоги «в», «к», «с»…) не участвуют в сравнении множеств слов — но не
# однозначные номера узлов/бассейнов (№7, №8, №9 — Особый фокус проверки плана): цифровой токен любой длины
# остаётся значимым, см. _keep_token

_WORD_RE = re.compile(r"[а-яa-z0-9]+(?:-[а-яa-z0-9]+)*")
# от длинных к коротким — длинный суффикс проверяется раньше короткого (иначе «ую» срежет лишнее у «-овую»)
_SUFFIXES: tuple[str, ...] = tuple(sorted({
    "иями", "ями", "иях", "ами", "ах", "ях",
    "ого", "его", "ому", "ему", "ыми", "ими", "ую", "юю",
    "ая", "яя", "ое", "ее", "ые", "ие", "ый", "ой", "им", "ым", "ем", "ом",
    "ов", "ев", "ей", "ий",
    "ы", "и", "а", "я", "о", "е", "у", "ю", "ь",
}, key=len, reverse=True))


def _stem(word: str) -> str:
    if len(word) <= _MIN_STEM:
        return word
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            return word[: -len(suffix)]
    return word


def normalize_ru(text: str) -> str:
    """Морфологическая нормализация русского слова/фразы для поиска по словарю (не для точных подписей тегов —
    их ищет `exact_tag_candidates`). Разбор конкретного приёма (лемматизация, список окончаний, словарь форм) —
    решение Task 1 (см. докстринг модуля); здесь только граница модуля, чтобы `preanalysis.py`/`retrieval.py`
    не знали о реализации. Пустой текст/текст без словарных символов — пустая строка, не ошибка."""
    lowered = text.strip().lower().replace("ё", "е")
    words = _WORD_RE.findall(lowered)
    return " ".join(_stem(word) for word in words)


def _keep_token(word: str) -> bool:
    """Длиной `>=2` ИЛИ чисто цифровой (однобуквенные предлоги отсеиваются, однозначный номер узла/бассейна
    №7/№8/№9 — нет: `retrieval.search` использует этот же приём для той же причины)."""
    return len(word) >= _MIN_WORD or word.isdigit()


def _word_set(text: str) -> frozenset[str]:
    return frozenset(word for word in normalize_ru(text).split() if _keep_token(word))


def _candidate_forms(row: dict) -> list[frozenset[str]]:
    forms = [_word_set(row.get("preferred_ru", ""))]
    forms.extend(_word_set(alias) for alias in (row.get("aliases") or ()))
    return [form for form in forms if form]


def _matches(query_words: frozenset[str], forms: list[frozenset[str]]) -> bool:
    if not query_words:
        return False
    return any(query_words <= form or form <= query_words for form in forms)


@dataclass(frozen=True)
class TermCandidate:
    """Один кандидат термина (`terms.jsonl` payload, подмножество): `id`, предпочтительное русское имя,
    псевдонимы, определение и признак неоднозначности — ровно то, что нужно показать технологу при уточнении."""

    id: str
    preferred_ru: str
    definition: str
    ambiguous: bool
    machine_scope: str


def resolve_term(handle: "CorpusHandle", original: str) -> "ResolvedTerm":
    """Слово/фраза из вопроса → `ResolvedTerm` (`models.py`): `candidate_ids` — до 10 кандидатов словаря по
    `normalize_ru` и псевдонимам, `selected_id` — единственный явный кандидат или `None` при неоднозначности,
    `requires_confirmation` — `True`, если кандидатов больше одного, термин помечен `ambiguous` в корпусе или
    кандидатов нет вовсе (не найдено — тоже требует уточнения, а не тихого пропуска).

    `original` — непустая строка (контракт `ResolvedTerm.original`, `models.py`: `min_length=1`); пустая строка
    `""` — `ValueError` (вызывающему, `preanalysis.py`, нечего резолвить — в вопросе не выделено ни слова, это
    его ошибка сегментации, не спорный термин). Строка из одних пробелов — НЕ та же ошибка: это осмысленный,
    хоть и бесполезный ввод (пользователь мог набрать пробел), контракт модели его принимает (`len("   ") >=
    1`), и этот модуль отвечает на него так же, как на нераспознанное слово — результатом с пустыми кандидатами
    и `requires_confirmation=True`, не исключением (чтобы не было двух противоречивых правил для «пусто» и
    «нечего искать»)."""
    if not original:
        raise ValueError("resolve_term: original не может быть пустой строкой")
    query_words = _word_set(original)
    candidate_ids: list[str] = []
    ambiguous_hit = False
    if query_words:
        term_ids = knowledge.all_ids(handle, "terms")
        rows = knowledge.get_rows(handle, "terms", term_ids)
        for term_id in term_ids:
            row = rows.get(term_id)
            if row is None or not _matches(query_words, _candidate_forms(row)):
                continue
            candidate_ids.append(term_id)
            if row.get("ambiguous"):
                ambiguous_hit = True
    candidate_ids = candidate_ids[:MAX_CANDIDATES]
    selected_id = candidate_ids[0] if len(candidate_ids) == 1 and not ambiguous_hit else None
    return ResolvedTerm(
        original=original[:120] if len(original) > 120 else original,
        candidate_ids=candidate_ids,
        selected_id=selected_id,
        requires_confirmation=selected_id is None,
    )


def exact_tag_candidates(handle: "CorpusHandle", text: str) -> list[str]:
    """Точные подписи тегов в тексте вопроса (вид `equipment_links.jsonl.display_label`/`processes.jsonl.
    source_label`) — по точному совпадению с известными подписями корпуса, без морфологии и без нечёткого
    сходства: регистр и знаки сохраняются (мастер-промпт §5: «подписи тегов сохраняют регистр и знаки»).
    Похожие символы (№7/№9, верх/нижний регистр буквы) — разные подписи, не варианты одной (ОСОБЫЙ ФОКУС
    ПРОВЕРКИ плана, мутация №7/№9): сравнение — только точное вхождение известной подписи целиком (границы —
    не буква/цифра/«_» по краям), ничего похожего, но неизвестного корпусу не возвращается — так синтаксически
    тег-подобная, но неизвестная строка не выдаётся как «похожий» тег (не угадывание)."""
    if not text:
        return []
    labels: set[str] = set()
    for row in knowledge.get_rows(handle, "processes", knowledge.all_ids(handle, "processes")).values():
        label = row.get("source_label")
        if label:
            labels.add(label)
    for row in knowledge.get_rows(handle, "equipment_links", knowledge.all_ids(handle, "equipment_links")).values():
        label = row.get("display_label")
        if label:
            labels.add(label)
    hits: list[tuple[int, str]] = []
    for label in labels:
        pattern = re.compile(r"(?<![0-9A-Za-zА-Яа-яЁё_])" + re.escape(label) + r"(?![0-9A-Za-zА-Яа-яЁё_])")
        match = pattern.search(text)
        if match:
            hits.append((match.start(), label))
    hits.sort()
    seen: set[str] = set()
    result: list[str] = []
    for _, label in hits:
        if label not in seen:
            seen.add(label)
            result.append(label)
    return result


def known_ambiguous_words(handle: "CorpusHandle") -> frozenset[str]:
    """Слова, про которые корпус уже знает, что они неоднозначны без контекста (мастер-промпт §7 п.4, пример
    «масса»: участок/единица/период решают смысл). Список — из корпуса (`terms.jsonl.ambiguous`), не хардкод
    этого модуля (`preferred_ru` + все `aliases` термина, помеченного `ambiguous=true`).

    ИЗМЕНЕНИЕ СИГНАТУРЫ `docs/kdm1/INTERFACES.md` (DONE_WITH_CONCERNS — см. `task-1-report.md`): интерфейс Task 0
    объявляет эту функцию без параметров (`() -> frozenset[str]`), но тот же докстринг Task 0 требует «список —
    из корпуса, не хардкод этого модуля». Без `handle` слова неоднозначности пришлось бы либо хардкодить в этом
    модуле (прямое нарушение собственного докстринга интерфейса), либо прятать за скрытым модульным состоянием
    (последний открытый корпус, не потокобезопасно и ломается при версии/откате Task 8). Добавлен параметр
    `handle: CorpusHandle` — вызывающие (`preanalysis.py`, Task 3) передают тот же хендл, что и остальным
    функциям этого модуля."""
    term_ids = knowledge.all_ids(handle, "terms")
    rows = knowledge.get_rows(handle, "terms", term_ids)
    words: set[str] = set()
    for row in rows.values():
        if not row.get("ambiguous"):
            continue
        preferred = row.get("preferred_ru")
        if preferred:
            words.add(preferred)
        words.update(alias for alias in (row.get("aliases") or ()) if alias)
    return frozenset(words)
