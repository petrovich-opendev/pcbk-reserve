import re
from datetime import timedelta
from html import escape
from zoneinfo import ZoneInfo

from helpers import T0
from pcbk_watchdog.checks import Check, not_installed
from pcbk_watchdog.journal import Event
from pcbk_watchdog.page import Snapshot, drill_titles, overall, render_html, status_json


def test_overall_ignores_absent_and_takes_worst():
    s = Snapshot(T0, (Check("a", "A", "ok", ""), Check("b", "B", "absent", "ещё не установлен"),
                      Check("c", "C", "warn", "")))
    assert overall(s) == "warn"


def test_overall_order_fail_unknown_warn_ok():
    def worst(*states):
        return overall(Snapshot(T0, tuple(Check(str(n), "x", st, "") for n, st in enumerate(states))))
    assert worst("ok", "warn", "unknown") == "unknown"
    assert worst("unknown", "fail", "warn") == "fail"
    assert worst("ok") == "ok"
    assert worst("absent") == "ok" and worst() == "ok"   # нечего проверять — сбоев нет


def test_status_json_marks_stale_and_missing_snapshot():
    s = Snapshot(T0, (Check("a", "A", "ok", ""),))
    assert status_json(s, T0 + timedelta(seconds=29), 30)["stale"] is False
    assert status_json(s, T0 + timedelta(seconds=31), 30)["stale"] is True
    assert status_json(None, T0, 30)["stale"] is True and status_json(None, T0, 30)["overall"] == "unknown"


def test_status_json_shape():
    s = Snapshot(T0, (Check("edge", "Входной прокси", "fail", "не отвечает"),))
    data = status_json(s, T0 + timedelta(seconds=4), 30)
    assert data["checked_at"] == "2026-09-29T12:00:00+00:00" and data["stale_after_s"] == 30
    assert data["overall"] == "fail" and data["age_s"] == 4
    assert data["checks"] == [{"component": "edge", "title": "Входной прокси", "state": "fail",
                               "detail": "не отвечает", "note": ""}]
    assert status_json(None, T0, 30)["checked_at"] is None and status_json(None, T0, 30)["checks"] == []


def banner(html):
    return re.search(r'<[^>]*id="silence"[^>]*>', html).group(0)


def test_html_stale_snapshot_shows_banner():
    s = Snapshot(T0, (Check("a", "A", "ok", ""),))
    assert "hidden" in banner(render_html(s, [], T0 + timedelta(seconds=29), 30, ZoneInfo("UTC")))
    assert "hidden" not in banner(render_html(s, [], T0 + timedelta(seconds=31), 30, ZoneInfo("UTC")))
    assert "hidden" not in banner(render_html(None, [], T0, 30, ZoneInfo("UTC")))


def test_html_stale_snapshot_greys_content():
    s = Snapshot(T0, (Check("a", "A", "ok", ""),))

    def main_tag(html):
        return re.search(r"<main[^>]*>", html).group(0)
    assert "data-stale" not in main_tag(render_html(s, [], T0 + timedelta(seconds=29), 30, ZoneInfo("UTC")))
    assert "data-stale" in main_tag(render_html(s, [], T0 + timedelta(seconds=31), 30, ZoneInfo("UTC")))
    assert "data-stale" in main_tag(render_html(None, [], T0, 30, ZoneInfo("UTC")))
    assert re.search(r"main\[data-stale\]\s*\{[^}]*opacity", render_html(s, [], T0, 30, ZoneInfo("UTC")))


def test_html_banner_text_and_age():
    html = render_html(Snapshot(T0, ()), [], T0 + timedelta(seconds=7), 30, ZoneInfo("UTC"))
    assert "Сторож не отвечает — состояние неизвестно" in html
    assert re.search(r'id="age"[^>]*>обновлено 7 с назад<', html)


def test_html_shows_absent_as_not_installed_and_offset():
    html = render_html(Snapshot(T0, (not_installed("core", "Серверный слой"),)), [], T0, 30,
                       ZoneInfo("Asia/Yekaterinburg"))
    assert "Серверный слой" in html and "ещё не установлен" in html and "+05:00" in html


def test_html_loads_local_module_only():
    html = render_html(Snapshot(T0, ()), [], T0, 30, ZoneInfo("UTC"))
    assert 'src="/status.js"' in html and "http://" not in html and "https://" not in html


def test_html_passes_stale_after_to_module_without_inline_script():
    html = render_html(Snapshot(T0, ()), [], T0, 45, ZoneInfo("UTC"))
    assert 'data-stale-after-s="45"' in html
    assert re.findall(r"<script[^>]*>", html) == ['<script type="module" src="/status.js">']


def test_html_marks_states_and_escapes_detail():
    s = Snapshot(T0, (Check("x", "X", "unknown", "нет связи с прокси сокета"),
                      Check("y", "Y", "fail", '<script>alert(1)</script> & "q"')))
    html = render_html(s, [], T0, 30, ZoneInfo("UTC"))
    assert "неизвестно" in html and "s-unknown" in html and "s-fail" in html
    assert "<script>alert" not in html and "&lt;script&gt;alert(1)&lt;/script&gt; &amp;" in html


def test_html_lists_recent_events_in_zone():
    events = [Event(T0, "edge", "fail", "не отвечает"), Event(T0, "watchdog", "ok", "сторож запущен")]
    s = Snapshot(T0, (Check("edge", "Входной прокси", "fail", "не отвечает"),))
    html = render_html(s, events, T0, 30, ZoneInfo("Asia/Yekaterinburg"))
    assert "29.09.2026 17:00:00 UTC+05:00" in html
    assert "Входной прокси" in html and "сторож запущен" in html


def test_note_in_json_and_html():
    s = Snapshot(T0, (Check("llm", "LLM-прокси и бюджет", "ok", "OpenRouter отвечает", note="расход $1.00 из $1000"),))
    assert status_json(s, T0, 30)["checks"][0]["note"] == "расход $1.00 из $1000"
    assert "OpenRouter отвечает · расход $1.00 из $1000" in render_html(s, [], T0, 30, ZoneInfo("UTC"))


def test_note_alone_when_detail_equals_label():
    # detail совпал с подписью — не повторяется, note идёт один, без « · »
    s = Snapshot(T0, (Check("llm", "LLM-прокси и бюджет", "ok", "норма", note="расход $0.00 из $1000"),
                      Check("edge", "Входной прокси", "ok", "отвечает")))
    html = render_html(s, [], T0, 30, ZoneInfo("UTC"))
    assert '<span class="detail">расход $0.00 из $1000</span>' in html
    assert '<span class="detail">отвечает</span>' in html                         # без note — без « · »


def test_long_note_not_cut():                                                    # П20: note свободен от 80
    note = ("расход $999.99 из $1000 · идёт на оценку $2.00 · по оценке $0.4900 · по ключу OpenRouter $999.99"
            " · отбор и проверки $12.35 · доля больше 80 %: 10 · доля исчерпана: 10 · на счёте $19.99")
    s = Snapshot(T0, (Check("llm", "LLM-прокси и бюджет", "warn", "израсходовано больше 95 % бюджета", note=note),))
    html = render_html(s, [], T0, 30, ZoneInfo("UTC"))
    assert status_json(s, T0, 30)["checks"][0]["note"] == note and escape(note) in html
    assert "nowrap" not in html                                                   # строка переносится


def test_note_is_escaped():
    s = Snapshot(T0, (Check("x", "X", "ok", "a", note='<b>&"</b>'),))
    html = render_html(s, [], T0, 30, ZoneInfo("UTC"))
    assert "<b>" not in html and "a · &lt;b&gt;&amp;&quot;&lt;/b&gt;" in html


def test_page_lists_rows_under_drills():                                       # Д11-R116
    rows = (Check("llm", "LLM-прокси и бюджет", "fail", "На счёте…", "учения DRILL_LLM=402"),
            Check("gateway", "Шлюз и вход", "ok", "принимает вход", "учения: freeze_reaper; работает мест: 0"),
            Check("memory", "Память сервера", "ok", "свободно 8000 МиБ", ""))
    assert drill_titles(rows) == ["LLM-прокси и бюджет", "Шлюз и вход"]
    html = render_html(Snapshot(T0, rows), [], T0, 30, ZoneInfo("UTC"))
    assert '<p id="drills" class="drills">Идут учения: LLM-прокси и бюджет, Шлюз и вход</p>' in html
    assert '<p id="drills" class="drills" hidden></p>' in render_html(Snapshot(T0, rows[2:]), [], T0, 30, ZoneInfo("UTC"))
