"""Двойник историана стенда отбора и обёртка запуска core тестового стенда (Д8-R75)."""
import asyncio
import os
import re
import subprocess
import sys
from dataclasses import replace

from fastapi.testclient import TestClient

from helpers import CORE_DIR, LLM_ENV, REPO, SETTINGS, TOKEN, make_app, wait_until
from pcbk_core.data.sql import catalog_sql, clock_sql
from pcbk_core.main import build_roles
from twin_core import install_twin
from twin_historian import BENCH_FIXTURE, TwinHistorian, bench_whitelist


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
    bdrv.write_text("BDRV_HOST=192.0.2.10\nBDRV_USER=u\nBDRV_PW=p\n", encoding="utf-8")
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
