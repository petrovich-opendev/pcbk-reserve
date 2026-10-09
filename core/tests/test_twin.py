"""Двойник историана стенда отбора и обёртка запуска core тестового стенда (Д8-R75)."""
import asyncio
import os
import re
import statistics
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from helpers import CORE_DIR, LLM_ENV, REPO, SETTINGS, TOKEN, make_app, make_service, wait_until
from pcbk_core.data.sql import (SUMMARY_COLUMNS, SummaryShape, catalog_sql, clock_sql, live_all_sql, live_sql,
                                series_sql, state_sql, summary_sql)
from pcbk_core.main import build_roles
from twin_core import install_twin
from twin_historian import BENCH_FIXTURE, TZ, TwinHistorian, analog_value, bench_whitelist, state_value


def test_twin_catalog_has_bench_cases():
    rows = {r[0]: r for r in TwinHistorian.from_fixture(BENCH_FIXTURE).catalog}
    group = {rows[n][1] for n in ("20FAKE_101_PV", "20FAKE_102_PV", "20FAKE_103_PV")}
    assert len(group) == 1 and len(next(iter(group))) == 50                       # A3: одно описание, 50 знаков
    assert "16FAKE_109_PV" in rows and "16FAKE_109_PV" not in bench_whitelist()    # C2
    assert all(n.startswith(("20FAKE_", "16FAKE_")) for n in rows)


def test_twin_answers_like_service(tmp_path):
    app, role = make_app(tmp_path, fake=TwinHistorian.from_fixture(BENCH_FIXTURE), whitelist=bench_whitelist())
    h = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as c:
        wait_until(lambda: role.catalog.loaded)
        now = c.post("/api/data/tag_now", json={"tags": ["20FAKE_104_PV"]}, headers=h).json()["items"][0]
        per = c.post("/api/data/tag_period", json={"tags": ["20FAKE_101_PV"], "period": "prev_shift"},
                     headers=h).json()["items"][0]
        none = c.post("/api/data/tag_period", json={"tags": ["20FAKE_107_PV"], "period": "prev_shift"},
                      headers=h).json()["items"][0]
    assert (now["status"], now["value"]) == ("ok", 3.42) and now["age_s"] < 60
    assert (per["avg"], per["min"], per["max"]) == (127.9, 125.1, 131.6)
    assert none["status"] == "no_data"


def test_install_twin_only_with_env(monkeypatch):                                # m-н1: запомнить до подмены
    import pcbk_core.data.historian as h
    monkeypatch.setattr(h, "tds_query", h.tds_query)
    real = h.tds_query
    assert install_twin({}) is False and h.tds_query is real
    assert install_twin({"PCBK_TWIN_HISTORIAN": " "}) is False and h.tds_query is real   # пустая — как нет
    assert install_twin({"PCBK_TWIN_HISTORIAN": str(BENCH_FIXTURE)}) is True
    assert isinstance(h.tds_query(None), TwinHistorian)


def test_install_twin_reaches_data_role(monkeypatch, tmp_path):                  # доводка р1, 4
    """install_twin, затем build_roles: роль «данные» ходит в двойник. Страховка от импорта tds_query на уровне
    модуля: pcbk_core.main импортирован раньше подмены (как у тестов), и подмена всё равно доходит до роли."""
    import pcbk_core.data.historian as h
    monkeypatch.setattr(h, "tds_query", h.tds_query)
    bdrv = tmp_path / "bdrv.env"
    bdrv.write_text("BDRV_HOST=192.0.2.10\nBDRV_USER=u\nBDRV_PW=<test-pw>\n", encoding="utf-8")   # заглушка
    settings = replace(SETTINGS, DB_PATH=str(tmp_path / "core.db"), BDRV_ENV_FILE=str(bdrv))
    assert install_twin({"PCBK_TWIN_HISTORIAN": str(BENCH_FIXTURE)}) is True
    data = {r.name: r for r in build_roles(settings, LLM_ENV)}["data"]
    result = asyncio.run(data.gate.run([clock_sql(), catalog_sql()], lane="background", deadline_s=5))
    names = {row[0] for row in result.rows[1]}
    assert names == {row[0] for row in TwinHistorian.from_fixture(BENCH_FIXTURE).catalog} and "20FAKE_104_PV" in names


def test_compose_test_twin_default_empty():                                      # доводка р1, 3
    """У core тестового стенда переменная двойника по умолчанию пуста — двойник включает только явное значение."""
    text = (REPO / "compose.test.yaml").read_text(encoding="utf-8")
    core = re.search(r"^  core:\n(.*?)(?=^  \S)", text, re.M | re.S).group(1)
    assert re.findall(r"^\s*PCBK_TWIN_HISTORIAN:.*$", text, re.M) == ["      PCBK_TWIN_HISTORIAN: ${PCBK_TWIN_HISTORIAN:-}"]
    assert "      PCBK_TWIN_HISTORIAN: ${PCBK_TWIN_HISTORIAN:-}" in core.splitlines()
    assert '    command: ["python", "-m", "twin_core"]' in core.splitlines()
    assert "      PYTHONPATH: /twin" in core.splitlines()
    assert re.search(r"source: \./core/tests, target: /twin, read_only: true", core)


def test_twin_core_runs_main_as_module(tmp_path):
    """python -m twin_core запускает pcbk_core.main как __main__ и отдаёт его код выхода (неверный аргумент — 1)."""
    env = {**os.environ, "PYTHONPATH": str(CORE_DIR / "tests"), "PCBK_TWIN_HISTORIAN": ""}
    plain = subprocess.run([sys.executable, "-m", "twin_core", "--bogus"], cwd=CORE_DIR, env=env,
                           capture_output=True, text=True, timeout=60)
    assert plain.returncode == 1 and "двойник" not in plain.stderr and plain.stderr.strip()
    env["PCBK_TWIN_HISTORIAN"] = str(BENCH_FIXTURE)
    twin = subprocess.run([sys.executable, "-m", "twin_core", "--bogus"], cwd=CORE_DIR, env=env,
                          capture_output=True, text=True, timeout=60)
    assert twin.returncode == 1 and "двойник историана тестового стенда" in twin.stderr
    assert plain.stderr.strip() in twin.stderr                                     # та же строка USAGE main


def test_prod_compose_has_no_twin():
    text = (REPO / "compose.yaml").read_text()
    assert "twin" not in text and "PCBK_TWIN_HISTORIAN" not in text and "PYTHONPATH" not in text


def test_core_image_context_has_no_twin():                                       # m22: образ — только pcbk_core
    ignore = [ln.strip() for ln in (CORE_DIR / ".dockerignore").read_text().splitlines()
              if ln.strip() and not ln.lstrip().startswith("#")]
    assert ignore[0] == "*" and {ln for ln in ignore if ln.startswith("!")} == {"!requirements.lock", "!pcbk_core/"}
    copies = re.findall(r"^COPY\s+(\S+)", (CORE_DIR / "Dockerfile").read_text(), re.M)
    assert copies == ["requirements.lock", "pcbk_core"]


def test_fake_bench_stdlib_only():                                                # m-н4
    imports = {n.split(".")[0] for n in re.findall(r"^\s*(?:from|import)\s+([\w.]+)",
                                                  (REPO / "core/tests/fake_bench.py").read_text(), re.M)}
    assert imports <= set(sys.stdlib_module_names)


# --- Д9-R86: сигналы по времени, теги состояний, ряд и наработка ---

NOW = datetime(2026, 10, 10, 7, 0, tzinfo=timezone.utc)                           # историан: 10.10 12:00, UTC+05:00
L = lambda *a: datetime(*a)                                                       # местное время без пояса


def twin():
    return TwinHistorian.from_fixture(now=lambda: NOW)


def test_twin_series_rows():
    t = twin()
    rows = t([series_sql(["20FAKE_201_PV"], L(2026, 10, 10, 4), L(2026, 10, 10, 12), 5)], 10.0)[0]
    assert [r[1] for r in rows] == [L(2026, 10, 10, h) for h in (4, 6, 8, 10, 12)]
    sig = t.signals["20FAKE_201_PV"]
    epoch = lambda d: d.replace(tzinfo=TZ).timestamp()
    assert [r[2] for r in rows] == [pytest.approx(analog_value(sig, epoch(r[1]))) for r in rows]
    assert {r[3] for r in rows} == {0}


def test_twin_summary_depends_on_period():
    t = twin()
    shift = t([summary_sql(["20FAKE_201_PV"], L(2026, 10, 9, 22), L(2026, 10, 10, 6))], 10.0)[0][0]
    day = t([summary_sql(["20FAKE_201_PV"], L(2026, 10, 9), L(2026, 10, 10))], 10.0)[0][0]
    assert shift != day
    cols = dict(zip(("TagName", *SUMMARY_COLUMNS), shift))
    assert cols["Minimum"] <= cols["Average"] <= cols["Maximum"] and cols["PercentGood"] == 100.0


def test_twin_state_rows():
    t = twin()
    rows = t([state_sql(["20FAKE_210_RUN", "20FAKE_211_CLS"], L(2026, 10, 9), L(2026, 10, 10))], 10.0)[0]
    run = {r[1]: r for r in rows if r[0] == "20FAKE_210_RUN"}
    assert set(run) == {0.0, 1.0} and not [r for r in rows if r[0] == "20FAKE_211_CLS"]
    assert sum(r[2] for r in run.values()) == 24 * 3_600_000                    # отсчёты [start, end)
    assert run[1.0][3] == pytest.approx(75.0) and run[1.0][4] == 8                 # цикл 3 ч — 8 переходов в «1» за сутки


def test_bench_tags_unchanged():                                                  # стенд отбора Д8 — как был
    t = twin()
    row = t([summary_sql(["20FAKE_104_PV"], L(2026, 10, 9), L(2026, 10, 10))], 10.0)[0][0]
    again = t([summary_sql(["20FAKE_104_PV"], L(2026, 10, 1), L(2026, 10, 2))], 10.0)[0][0]
    assert row == again and "16FAKE_109_PV" not in bench_whitelist()


@pytest.mark.anyio
async def test_twin_through_service(tmp_path):
    s, _ = make_service(tmp_path, fake=twin(), whitelist=bench_whitelist())
    r = await s.tag_series("ops", "http", ["20FAKE_201_PV"], period="last_8h", points=9)
    assert r["status"] == "ok" and len(r["items"][0]["points"]) == 9
    r = await s.calc_runtime("ops", "http", ["20FAKE_210_RUN", "20FAKE_211_CLS"], period="yesterday")
    a, b = r["items"]
    assert sum(st["hours"] for st in a["states"]) == pytest.approx(24.0) and b["status"] == "no_data"


# --- доводка р1 (ревью А3, I-1): числа сводки и живого значения по сигналу ---

def _signal_summary(sig, start: datetime, end: datetime) -> dict[str, float]:
    """Ожидание сводки по Д9-R86 своим счётом: отсчёты через 60 с на [start, end), время историана — через TZ."""
    t, t1, values = start.replace(tzinfo=TZ).timestamp(), end.replace(tzinfo=TZ).timestamp(), []
    while t < t1:
        values.append(analog_value(sig, t))
        t += 60
    return {"Minimum": min(values), "Maximum": max(values), "Average": statistics.fmean(values),
            "StdDev": statistics.pstdev(values)}


def test_twin_summary_matches_signal():
    t = TwinHistorian.from_fixture(now=lambda: NOW + timedelta(seconds=30))
    sig = t.signals["20FAKE_201_PV"]
    cols = {}
    # смена 06–14 09.10, сутки 09.10 и период, обрезанный «сейчас», с концом на :30
    for start, end in ((L(2026, 10, 9, 6), L(2026, 10, 9, 14)), (L(2026, 10, 9), L(2026, 10, 10)),
                       (L(2026, 10, 10), L(2026, 10, 10, 12, 0, 30))):
        row = t([summary_sql(["20FAKE_201_PV"], start, end)], 10.0)[0][0]
        got = cols[start, end] = dict(zip(("TagName", *SUMMARY_COLUMNS), row))
        want = _signal_summary(sig, start, end)
        assert {k: got[k] for k in want} == {k: pytest.approx(v) for k, v in want.items()}
        assert got["PercentGood"] == 100.0
    shift = cols[L(2026, 10, 9, 6), L(2026, 10, 9, 14)]["Average"]
    day = cols[L(2026, 10, 9), L(2026, 10, 10)]["Average"]
    assert abs(shift - day) > 1                                                    # 603,60 против 600,00


def test_twin_live_signal():
    t = twin()
    at = NOW.timestamp() - 20                                                     # «сейчас − 20 с»
    rows = {r[0]: r for r in t([live_sql(["20FAKE_201_PV", "20FAKE_210_RUN", "20FAKE_211_CLS"])], 10.0)[0]}
    assert set(rows) == {"20FAKE_201_PV", "20FAKE_210_RUN"}                       # у 211 сигнала нет — строки нет
    assert {r[1] for r in rows.values()} == {L(2026, 10, 10, 11, 59, 40)}
    assert rows["20FAKE_201_PV"][2] == pytest.approx(analog_value(t.signals["20FAKE_201_PV"], at))
    assert rows["20FAKE_210_RUN"][2] == state_value(t.signals["20FAKE_210_RUN"], at)
    assert {r[3] for r in rows.values()} == {0}
    names = {r[0] for r in t([live_all_sql()], 10.0)[0]}
    assert {"20FAKE_201_PV", "20FAKE_202_PV", "20FAKE_210_RUN"} <= names           # признак «живой» в каталоге
    assert not names & {"20FAKE_211_CLS", "20FAKE_107_PV"}


# --- итоговая доводка Д9а (ревью А3, m-2…m-5) ---

def test_twin_runtime_not_longer_than_period():                                  # m-2: конец не на целой минуте
    t = TwinHistorian.from_fixture(now=lambda: NOW + timedelta(seconds=30))
    rows = t([state_sql(["20FAKE_210_RUN"], L(2026, 10, 10), L(2026, 10, 10, 12, 0, 30))], 10.0)[0]
    assert sum(r[2] for r in rows) == (12 * 3600 + 30) * 1000                     # 12 ч 30 с, не 721 минута
    assert sum(r[3] for r in rows) == pytest.approx(100.0)
    day = t([state_sql(["20FAKE_210_RUN"], L(2026, 10, 9), L(2026, 10, 10))], 10.0)[0]
    assert {r[1]: r[2] for r in day} == {1.0: 18 * 3_600_000, 0.0: 6 * 3_600_000}  # целые минуты — как были


def test_twin_signals_checked_at_load():                                         # m-3: битая фикстура — сразу
    base = twin()
    bad_signals = ({"20FAKE_201_PV": {"base": 600, "amp": 20}},                          # не того вида
                   {"20FAKE_999_PV": {"base": 600, "amp": 20, "period_h": 6}},           # тег вне каталога
                   {"20FAKE_211_CLS": {"base": 1, "amp": 1, "period_h": 6}},             # аналоговый у тега состояния
                   {"20FAKE_201_PV": {"base": 600, "amp": 20, "period_h": 0}},           # период — ноль
                   {"20FAKE_210_RUN": {"cycle_h": 0, "duty": 0.75}},                      # цикл — ноль
                   {"20FAKE_210_RUN": {"cycle_h": 3, "duty": 1.5}})                       # доля вне 0…1
    for signals in bad_signals:
        with pytest.raises(ValueError):
            TwinHistorian(base.catalog, base.values, base.summary, signals=signals)
    assert TwinHistorian(base.catalog, base.values, base.summary, signals={"20FAKE_210_RUN": {"cycle_h": 3, "duty": 1}})


def test_twin_has_no_fakes_data():                                               # m-4: ряд и наработка fakes не тянутся
    t = twin()
    assert (t.live, t.clock, t.series, t.states) == ({}, [], {}, {})


def test_twin_summary_last_in_period():                                          # m-5: Last — в периоде, не «сейчас»
    t = twin()
    shape = SummaryShape(has_last=True)
    row = t([summary_sql(["20FAKE_201_PV"], L(2026, 10, 9), L(2026, 10, 10), shape)], 10.0)[0][0]
    cols = dict(zip(("TagName", *shape.columns), row))
    last = L(2026, 10, 9, 23, 59)                                                 # последний отсчёт [start, end)
    assert cols["LastDateTime"] == last
    assert cols["Last"] == pytest.approx(analog_value(t.signals["20FAKE_201_PV"], last.replace(tzinfo=TZ).timestamp()))
    plain = t([summary_sql(["20FAKE_104_PV"], L(2026, 10, 9), L(2026, 10, 10), shape)], 10.0)[0][0]
    assert dict(zip(("TagName", *shape.columns), plain))["Last"] == 3.42             # тег Д8 — живое, как было
