"""Снимок проверок, его JSON и HTML страницы состояния."""
from dataclasses import dataclass
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from .checks import Check, State
from .journal import Event

# худшее — первым; absent в общий итог не входит
SEVERITY: tuple[State, ...] = ("fail", "unknown", "warn", "ok")

# подписи состояний; те же — в status.js
LABELS: dict[str, str] = {"ok": "норма", "warn": "внимание", "fail": "сбой",
                          "unknown": "неизвестно", "absent": "ещё не установлен"}

# названия компонентов, которых нет в снимке, — для журнала
EXTRA_TITLES = {"watchdog": "Сторож", "journal": "Журнал сторожа"}

SILENCE_TEXT = "Сторож не отвечает — состояние неизвестно"


@dataclass(frozen=True)
class Snapshot:
    checked_at: datetime
    checks: tuple[Check, ...]


def overall(snapshot: Snapshot) -> State:
    states = {c.state for c in snapshot.checks}
    return next((s for s in SEVERITY if s in states), "ok")


def age_s(snapshot: Snapshot, now: datetime) -> int:
    return int((now - snapshot.checked_at).total_seconds())


def is_stale(snapshot: Snapshot | None, now: datetime, stale_after_s: int) -> bool:
    return snapshot is None or (now - snapshot.checked_at).total_seconds() > stale_after_s


def status_json(snapshot: Snapshot | None, now: datetime, stale_after_s: int) -> dict:
    """Ответ /status.json; stale считается в момент запроса."""
    return {
        "checked_at": snapshot.checked_at.isoformat() if snapshot else None,
        "stale": is_stale(snapshot, now, stale_after_s),
        "stale_after_s": stale_after_s,
        "age_s": age_s(snapshot, now) if snapshot else None,
        "overall": overall(snapshot) if snapshot else "unknown",
        "checks": [{"component": c.component, "title": c.title, "state": c.state,
                    "detail": c.detail} for c in (snapshot.checks if snapshot else ())],
    }


def fmt_time(ts: datetime, tz: ZoneInfo) -> str:
    """Время с явным смещением от UTC; тот же вид — в status.js."""
    return ts.astimezone(tz).strftime("%d.%m.%Y %H:%M:%S UTC%:z")


STYLE = """
:root { color-scheme: light dark; --fg: #1f2328; --bg: #f6f8fa; --card: #fff; --muted: #6b7280;
  --ok: #1a7f37; --warn: #b35900; --fail: #c62828; --unknown: #8a6d00; --unknown-bg: #fff4c2; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #e6edf3; --bg: #0d1117; --card: #161b22; --muted: #8b949e;
    --ok: #3fb950; --warn: #f0883e; --fail: #ff6b6b; --unknown: #e3b341; --unknown-bg: #3a2f00; } }
body { margin: 0; font: 16px/1.45 system-ui, sans-serif; color: var(--fg); background: var(--bg); }
main { max-width: 56rem; margin: 0 auto; padding: 1rem; }
.silence { position: sticky; top: 0; z-index: 1; padding: .9rem 1rem; text-align: center;
  font-weight: 700; font-size: 1.15rem; color: #fff; background: var(--fail); }
h1 { font-size: 1.4rem; margin: .5rem 0; }
h2 { font-size: 1.1rem; margin: 1.5rem 0 .5rem; }
.meta { color: var(--muted); margin: 0 0 1rem; }
.summary { font-size: 1.1rem; margin: .5rem 0; }
.checks, .events { list-style: none; padding: 0; margin: 0; }
.check { display: grid; grid-template-columns: minmax(12rem, 1fr) 10.5rem 2fr; gap: .75rem;
  align-items: baseline; padding: .5rem .75rem; margin: 0 0 .25rem; background: var(--card);
  border-left: .35rem solid var(--muted); border-radius: .25rem; }
.check .state { font-weight: 600; }
.s-ok { border-color: var(--ok); } .s-ok .state { color: var(--ok); }
.s-warn { border-color: var(--warn); } .s-warn .state { color: var(--warn); }
.s-fail { border-color: var(--fail); } .s-fail .state, .s-fail .name { color: var(--fail); }
.s-unknown { border-color: var(--unknown); background: var(--unknown-bg); }
.s-unknown .state { color: var(--unknown); }
.s-absent { color: var(--muted); padding-block: .25rem; } .s-absent .state { font-weight: 400; }
#overall { font-weight: 700; } #overall.s-ok { color: var(--ok); } #overall.s-warn { color: var(--warn); }
#overall.s-fail { color: var(--fail); } #overall.s-unknown { color: var(--unknown); }
.detail:empty { display: none; }
.events li { padding: .2rem 0; } .events time { color: var(--muted); margin-right: .5rem; }
@media (max-width: 40rem) { .check { grid-template-columns: 1fr auto; } .check .detail { grid-column: 1 / -1; } }
"""


def _check_row(c: Check) -> str:
    label = LABELS.get(c.state, c.state)
    detail = "" if c.detail == label else c.detail   # «ещё не установлен» — один раз
    return (f'<li class="check s-{escape(c.state)}"><span class="name">{escape(c.title)}</span>'
            f'<span class="state">{escape(label)}</span>'
            f'<span class="detail">{escape(detail)}</span></li>')


def _event_row(e: Event, titles: dict[str, str], tz: ZoneInfo) -> str:
    title = titles.get(e.component, e.component)
    text = f"{title} — {LABELS.get(e.state, e.state)}" + (f": {e.detail}" if e.detail else "")
    return (f'<li><time datetime="{escape(e.ts.isoformat())}">{escape(fmt_time(e.ts, tz))}</time>'
            f"{escape(text)}</li>")


def render_html(snapshot: Snapshot | None, events: list[Event], now: datetime, stale_after_s: int,
                tz: ZoneInfo) -> str:
    """Страница состояния; без внешних ресурсов и без встроенного JS."""
    stale = is_stale(snapshot, now, stale_after_s)
    checks = snapshot.checks if snapshot else ()
    if snapshot:
        state = overall(snapshot)
        summary = f'<span id="overall" class="s-{state}">{escape(LABELS[state])}</span>'
        at = (f'<time id="checked-at" datetime="{escape(snapshot.checked_at.isoformat())}">'
              f"{escape(fmt_time(snapshot.checked_at, tz))}</time>")
        age = age_s(snapshot, now)
        age_text = f'<span id="age" data-age-s="{age}">обновлено {age} с назад</span>'
    else:
        summary = '<span id="overall" class="s-unknown">неизвестно</span>'
        at = '<time id="checked-at">проверок ещё не было</time>'
        age_text = '<span id="age">данных нет</span>'
    titles = {**EXTRA_TITLES, **{c.component: c.title for c in checks}}
    rows = "\n".join(_check_row(c) for c in checks)
    event_rows = ("\n".join(_event_row(e, titles, tz) for e in events)
                  or "<li>Событий пока нет</li>")
    hidden = "" if stale else " hidden"
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Состояние резервного стенда</title>
<style>{STYLE}</style>
<script type="module" src="/status.js"></script>
</head>
<body data-stale-after-s="{int(stale_after_s)}">
<div id="silence" class="silence" role="alert"{hidden}>{SILENCE_TEXT}</div>
<main>
<h1>Состояние резервного стенда</h1>
<p class="summary">Общее состояние: {summary}</p>
<p class="meta">Проверено: {at} · {age_text}. Страница обновляется сама каждые 5 с.</p>
<ul id="checks" class="checks">
{rows}
</ul>
<h2>Последние события</h2>
<p class="meta">Журнал сторожа — на момент открытия страницы, время с поясом.</p>
<ol class="events">
{event_rows}
</ol>
</main>
</body>
</html>
"""
