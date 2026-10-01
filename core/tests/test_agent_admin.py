"""Команда администратора агентов студентов (Д7-R61): list со сверкой файлов и учёта, archive только с --yes,
reload; коды выхода как у accounts (П6: ошибка — 1 и одна строка в stderr)."""
from dataclasses import replace

import pytest

from helpers import AGENT_DRAFT, AGENT_NOW, agent_text, make_agents_root
from pcbk_core.agents import admin
from pcbk_core.agents.store import AgentStore


@pytest.fixture
def env_store(tmp_path, monkeypatch):
    """Учёт и корень агентов в окружении команды (Settings.from_env: DB_PATH, AGENTS_ROOT), open() вызван."""
    root = make_agents_root(tmp_path / "agents")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "core.db"))
    monkeypatch.setenv("AGENTS_ROOT", str(root))
    s = AgentStore(str(tmp_path / "core.db"), str(root))
    assert s.open(), s.last_error
    return s


def test_archive_needs_yes(env_store, capsys):                              # П6: код 1, как у accounts
    assert admin.main(["archive", "--place", "3"]) == 1 and "--yes" in capsys.readouterr().err

def test_list_shows_drift(env_store, capsys):
    (env_store.place_dir(3) / "manual.md").write_text("x")
    assert admin.main(["list", "--place", "3"]) == 0 and "файлы без учёта: manual.md" in capsys.readouterr().out


# --- сверх брифа ---

def test_list_rows_flags_and_clean_drift(env_store, capsys):
    env_store.save(3, replace(AGENT_DRAFT, flags=("prompt_numeric_limit",)), agent_text(), AGENT_NOW)
    env_store.save(5, replace(AGENT_DRAFT, name="davlenie"), agent_text(), AGENT_NOW)
    assert admin.main(["list", "--place", "3"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["03\tsushka-bdm2\tСушка БДМ-2\tsaved\t2026-10-07T09:00:00+00:00\tprompt_numeric_limit",
                     "расхождений нет"]
    (env_store.place_dir(5) / "davlenie.md").unlink(); (env_store.place_dir(7) / "x.md").symlink_to("/nonexistent")
    assert admin.main(["list"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:2] == [lines[0], "05\tdavlenie\tСушка БДМ-2\tsaved\t2026-10-07T09:00:00+00:00\t-"]
    assert out[2:] == ["место 05: учёт без файлов: davlenie", "место 07: ссылки: x.md"]

def test_archive_and_reload(env_store, capsys):
    env_store.save(3, AGENT_DRAFT, agent_text(), AGENT_NOW)
    env_store.reload_done(3, env_store.pending()[3].seq)
    (env_store.place_dir(3) / "manual.md").write_text("x")
    assert admin.main(["archive", "--place", "3", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "в архив: manual, sushka-bdm2" in out and "в очереди перезагрузки" in out
    assert 3 in env_store.pending() and env_store.live_count(3) == 0 and not env_store.place_has_agents(3)
    assert admin.main(["archive", "--place", "3", "--yes"]) == 0 and "архивировать нечего" in capsys.readouterr().out
    assert admin.main(["reload", "--place", "4"]) == 0 and 4 in env_store.pending()

def test_errors_are_one_line_code_1(env_store, capsys):
    env_store.save(3, AGENT_DRAFT, agent_text(), AGENT_NOW)
    for argv in (["archive", "--place", "3", "--name", "nety", "--yes"], ["archive", "--place", "11", "--yes"],
                 ["reload"], ["list", "--place", "x"], ["archive", "--place", "3", "--name", "../x", "--yes"], []):
        assert admin.main(argv) == 1, argv
        err = capsys.readouterr()
        assert err.out == "" and len(err.err.strip().splitlines()) == 1, argv
    assert env_store.live_count(3) == 1                                       # ничего не ушло в архив
