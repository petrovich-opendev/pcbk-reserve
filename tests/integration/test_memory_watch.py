"""Память сервера на тестовом стенде (Д11а, R114–R116, R123а): строка «Память сервера» по двойникам /proc сторожа,
настоящее переполнение места с причиной перезапуска, сбой места не по памяти, абзац «Идут учения».

По алфавиту — после test_llm, до test_socket_proxy; модуль оставляет строки в норме (последний тест). Место 10 —
только здесь и в test_agents (до этого модуля); после каждого теста оно без переопределения и остановлено.
"""
import json
from datetime import datetime, timedelta, timezone

from conftest import STAND, datetime_now, row_of, shot
from pcbk_watchdog.memory import MEM_DRILL_NOTE, MEM_OOM_OUTSIDE_DETAIL, MEM_PSI_DETAIL, MEM_SWAP_DETAIL

TICK_S, GRACE_S = 10, 20                                                    # сторож стенда: TICK_S 10, grace 2 × TICK_S
# давление стоит выше swap: «внимание» по давлению включается и снимается только 3 такта подряд (PSI_HOLD_TICKS) —
# переход строки занимает до 3 тактов и ещё такт на снимок (отчёт А1, сомнение 2; решение контроллера: 45–60 с)
HOLD_WAIT_S = 60
# строка страницы состояния с этим заголовком — в середину экрана (для снимка; %s — JSON)
ROW_INTO_VIEW_JS = ("(() => { const e = [...document.querySelectorAll('#checks li .name')]"
                    ".find((x) => x.textContent.trim() === %s); if (e) e.scrollIntoView({block: 'center'}); "
                    "return !!e; })()")


def into_view(page, title: str) -> None:
    assert page.eval(ROW_INTO_VIEW_JS % json.dumps(title, ensure_ascii=False)), f"строки «{title}» нет на странице"


def wait_hidden(page, css_id: str, timeout: float) -> None:
    """Элемент #id есть и скрыт (hidden): status.js сам обновляет страницу раз в 5 с, без перезагрузки."""
    js = f"document.getElementById({json.dumps(css_id.removeprefix('#'))})?.hidden === true"
    page.wait(lambda: page.eval(js), timeout, f"{css_id} скрыт")


def test_memory_rows_drills(stack, page):
    try:
        stack.recreate("watchdog", {"MEM_WARN_MIB": "1000000"})
        s = stack.wait_status(lambda d: row_of(d, "memory")["state"] == "warn", 30)
        assert row_of(s, "memory")["note"].endswith(MEM_DRILL_NOTE)
        stack.recreate("watchdog", {})
        stack.set_psi("watchdog", 12.0)
        stack.wait_status(lambda d: row_of(d, "memory")["detail"] == MEM_PSI_DETAIL, HOLD_WAIT_S)
        stack.set_psi("watchdog", 0.0)
        stack.set_memory("watchdog", 8000, swap_used_mib=900)
        stack.wait_status(lambda d: row_of(d, "memory")["detail"] == MEM_SWAP_DETAIL, HOLD_WAIT_S)
        page.goto(f"{STAND}/status"); page.wait_text("сервер вытесняет память в swap", 20, css="#checks")
        into_view(page, "Память сервера")
        shot(page, "d11a-01-memory-row.png")
        stack.set_memory("watchdog", 8000, swap_used_mib=0)
        stack.set_oom_kill("watchdog", 1)
        stack.wait_status(lambda d: row_of(d, "memory")["detail"] == MEM_OOM_OUTSIDE_DETAIL, 60)
    finally:                                                                # и при сбое: двойники — в норме (m-4)
        stack.set_psi("watchdog", 0.0)
        stack.set_memory("watchdog", 8000, swap_used_mib=0)
        stack.recreate("watchdog", {})                                      # новая история: база счётчика — как есть
    stack.wait_status(lambda d: row_of(d, "memory")["state"] == "ok", 30)


def test_place_oom_restart_reason(stack, page):                             # настоящий /proc/vmstat — только здесь
    stack.recreate("watchdog", {"VMSTAT_PATH": "/proc/vmstat"})
    try:
        with stack.place_override(10, "[ -e /work/.oom-drill ] && exec pcbk-entrypoint; sleep 20; "
                                      "touch /work/.oom-drill; exec tail /dev/zero"):
            stack.wait_status(lambda d: row_of(d, "student-10")["detail"].startswith("перезапущен: убит по памяти"), 120)
            seen = datetime.now(timezone.utc)
            s = stack.wait_snapshot_after(seen + timedelta(seconds=GRACE_S + TICK_S))   # I-5 р2: после grace
            assert row_of(s, "memory")["state"] == "ok"                     # без объяснения — было бы «вне мест» (m-5)
            page.goto(f"{STAND}/status"); page.wait_text("убит по памяти", 20, css="#checks")
            into_view(page, "Рабочее место 10")
            shot(page, "d11a-02-oom-restart.png")
    finally:                                                                # место уже остановлено — метка из тома (m-4)
        stack.place_work_rm(10, ".oom-drill")
        stack.recreate("watchdog", {})


def test_place_crash_restart_reason(stack, page):
    try:
        stack.start("pcbk-student-10"); stack.wait_ready("pcbk-student-10")
        stack.wait_snapshot_after(datetime_now())
        stack.sh("pcbk-student-10", f"kill -KILL {stack.opencode_pid('pcbk-student-10')}")
        stack.wait_status(lambda d: row_of(d, "student-10")["detail"].startswith("перезапущен: сбой не по памяти"), 90)
        page.goto(f"{STAND}/status"); page.wait_text("сбой не по памяти", 20, css="#checks")
        into_view(page, "Рабочее место 10")
        shot(page, "d11a-03-crash-restart.png")
    finally:                                                                # и при сбое: место — без перезапусков (m-4)
        stack.compose("create", "--force-recreate", "--no-build", "student-10")


def test_drills_line_on_page(stack, page):
    try:
        stack.recreate("core", {"DRILL_LLM": "402"})
        page.goto(f"{STAND}/status"); page.wait_text("Идут учения: LLM-прокси и бюджет", 30, css="#drills")
        shot(page, "d11a-05-drills-line.png")
    finally:                                                                # и при сбое: core без учений (m-4)
        stack.recreate("core", {})
    wait_hidden(page, "#drills", 30)


def test_memory_watch_leaves_rows_ok(stack):
    stack.wait_status(lambda d: all(c["state"] == "ok" for c in d["checks"] if c["component"] != "historian"), 60)
