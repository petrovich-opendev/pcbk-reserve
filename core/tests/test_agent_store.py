"""Учёт агентов студентов и их файлы (Д7-R56): атомарная запись, повтор и занятое имя, предел мест, очередь перезагрузок
с поколением, карантин и архив внутри корня, обход файлов места как у загрузчика OpenCode (вложенные, точечные,
символьные ссылки)."""
import re
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from helpers import AGENT_DRAFT, agent_text, make_agents_root, non_root, run_python
from pcbk_core.agents.store import (MAX_AGENTS, AgentStore, DirUnavailable, NameTaken, PendingReload, StoreError,
                                    TooManyAgents, place_of_caller)

NOW = datetime(2026, 10, 7, 9, tzinfo=timezone.utc)
TEXT = agent_text()


@pytest.fixture
def store(tmp_path):
    """AgentStore на tmp_path/core.db; корень tmp_path/agents — student-01…10 (0755), quarantine и archive (0700)."""
    s = AgentStore(str(tmp_path / "core.db"), str(make_agents_root(tmp_path / "agents")))
    assert s.open(), s.last_error
    yield s
    for p in s.root.rglob("*"):                                         # tmp_path чистится и после chmod 0
        if p.is_dir() and not p.is_symlink():
            p.chmod(0o755)


@pytest.fixture
def draft():
    return AGENT_DRAFT


def test_save_writes_file_row_and_queue(store, draft):
    assert store.save(3, draft, TEXT, NOW) == "saved"
    f = store.place_dir(3) / "sushka-bdm2.md"
    assert f.read_text("utf-8") == TEXT and stat.S_IMODE(f.stat().st_mode) == 0o644
    assert [p.name for p in store.place_dir(3).iterdir()] == ["sushka-bdm2.md"]           # временного файла нет
    assert (store.list_place(3)[0].state, store.pending()[3].since) == ("saved", NOW)

def test_retry_is_already(store, draft):
    store.save(3, draft, TEXT, NOW); mtime = (store.place_dir(3) / "sushka-bdm2.md").stat().st_mtime_ns
    assert store.save(3, draft, TEXT, NOW + timedelta(seconds=5)) == "already"
    assert (store.place_dir(3) / "sushka-bdm2.md").stat().st_mtime_ns == mtime and store.pending()[3].since == NOW

def test_other_draft_same_name_taken(store, draft):
    store.save(3, draft, TEXT, NOW)
    with pytest.raises(NameTaken) as e:
        store.save(3, replace(draft, title="Сушка БДМ-3"), TEXT, NOW)
    assert e.value.existing == ("sushka-bdm2",)

def test_file_without_row_is_taken(store, draft):
    (store.place_dir(3) / "sushka-bdm2.md").write_text("ручной")
    with pytest.raises(NameTaken):
        store.save(3, draft, TEXT, NOW)

def test_limit_counts_rows_and_files(store, draft):
    for i in range(19):
        store.save(3, replace(draft, name=f"a{i:02d}"), TEXT, NOW)
    (store.place_dir(3) / "manual.md").write_text("x")
    with pytest.raises(TooManyAgents):
        store.save(3, draft, TEXT, NOW)

def _try(fn, *args):
    try:
        return fn(*args)
    except Exception as exc:            # noqa: BLE001 — тест сравнивает вид исхода
        return exc

def test_concurrent_same_name(store, draft):
    with ThreadPoolExecutor(2) as ex:
        outs = list(ex.map(lambda t: _try(store.save, 3, replace(draft, title=t), TEXT, NOW), ["Сушка А", "Сушка Б"]))
    assert sorted(type(o).__name__ for o in outs) == ["NameTaken", "str"]
    assert len(list(store.place_dir(3).glob("*.md"))) == 1

def test_insert_failure_removes_file(store, draft, monkeypatch):
    monkeypatch.setattr(store, "_insert", lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("disk")))
    with pytest.raises(StoreError):
        store.save(3, draft, TEXT, NOW)
    assert list(store.place_dir(3).iterdir()) == []

@non_root                                                                   # m5: от root chmod не отнимает права
def test_dir_unavailable(store, draft):
    store.place_dir(4).chmod(0o555)
    with pytest.raises(DirUnavailable):
        store.save(4, draft, TEXT, NOW)
    assert store.dir_problem() == "каталог места отсутствует или не пишется"

def test_quarantine_and_archive_stay_inside_root(store, draft):
    store.save(3, draft, TEXT, NOW)
    assert store.quarantine(3, "sushka-bdm2.md", NOW) == "sushka-bdm2"
    q = list((store.root / "quarantine" / "student-03").iterdir())
    assert len(q) == 1 and re.fullmatch(r"sushka-bdm2\.md\.20261007T090000Z-[0-9a-f]{6}", q[0].name)   # m14
    assert store.list_place(3)[0].state == "quarantined"
    assert store.quarantine(3, "sushka-bdm2.md", NOW) is None                                   # файла уже нет
    store.save(3, replace(draft, name="davlenie"), TEXT, NOW)
    assert store.live_count(3) == 2                                                             # карантинный — живой
    assert store.archive(3, "davlenie", NOW) == ("davlenie",) and store.live_count(3) == 1
    assert store.archive(3, None, NOW) == ("sushka-bdm2",) and store.live_count(3) == 0   # файл уже в карантине
    assert list(store.place_dir(3).iterdir()) == []
    assert [p.name for p in (store.root / "archive").glob("student-03-*/*")] == ["davlenie.md"]

def test_quarantine_same_second_keeps_both(store):                      # m14
    for _ in range(2):
        (store.place_dir(3) / "x.md").write_text("x"); store.quarantine(3, "x.md", NOW)
    assert len(list((store.root / "quarantine" / "student-03").iterdir())) == 2

def test_place_has_agents_counts_stray_files(store):                    # I2
    assert not store.place_has_agents(3)
    (store.place_dir(3) / "manual.md").write_text("x")
    assert store.place_has_agents(3) and store.live_count(3) == 0
    assert store.archive(3, None, NOW) == ("manual",) and not store.place_has_agents(3)

def test_flags_and_has_saved(store, draft):
    store.save(3, replace(draft, flags=("prompt_numeric_limit",)), TEXT, NOW)
    assert store.has_saved(3) and store.flags_of(3, "sushka-bdm2") == ("prompt_numeric_limit",)

def test_reload_done_keeps_newer_mark(store, draft):                    # I-н2б
    store.save(3, draft, TEXT, NOW); seq = store.pending()[3].seq
    store.mark_reload(3, NOW + timedelta(seconds=1))                       # пришла во время перезагрузки
    assert store.reload_done(3, seq) is False and 3 in store.pending()
    assert store.reload_done(3, store.pending()[3].seq) is True and store.pending() == {}

def test_reset_since(store, draft):                                        # m-н6
    store.save(3, draft, TEXT, NOW); later = NOW + timedelta(hours=2)
    store.mark_reload(3, later); assert store.pending()[3].since == NOW
    store.mark_reload(3, later, reset_since=True); assert store.pending()[3].since == later

def test_nested_and_dot_files_count(store):                                # m-н3
    (store.place_dir(3) / "sub" / ".hidden").mkdir(parents=True)
    (store.place_dir(3) / "sub" / ".hidden" / "x.md").write_text("x")
    (store.place_dir(3) / ".y.md").write_text("y")
    assert store.place_has_agents(3) and store.drift(3)[0] == (".y.md", "sub/.hidden/x.md")
    assert store.archive(3, None, NOW) == (".y", "sub/.hidden/x") and not store.place_has_agents(3)

def test_symlink_is_drift_and_archived_as_link(store, tmp_path):          # m-р3-2
    outside = tmp_path / "outside.md"; outside.write_text("чужой")
    (store.place_dir(3) / "link.md").symlink_to(outside)
    assert store.place_has_agents(3) and store.drift(3)[2] == ("link.md",)
    store.archive(3, None, NOW)
    moved = next((store.root / "archive").glob("student-03-*/link.md"))
    assert moved.is_symlink() and outside.read_text() == "чужой"             # перенесена ссылка, цель цела

def test_archive_keeps_nested_paths(store):                                # m-р3-3
    (store.place_dir(3) / "sub").mkdir()
    (store.place_dir(3) / "sub" / "x.md").write_text("1"); (store.place_dir(3) / "sub__x.md").write_text("2")
    store.archive(3, None, NOW)
    names = sorted(p.name for p in (store.root / "archive").glob("student-03-*/*"))
    assert len(names) == 2 and names[0] == "sub__x.md" and names[1].startswith("sub__x.md-")
    assert sorted(p.read_text() for p in (store.root / "archive").glob("student-03-*/*")) == ["1", "2"]

def test_save_bumps_seq(store, draft):                                     # m-р3-7
    store.mark_reload(3, NOW); seq = store.pending()[3].seq
    store.save(3, draft, TEXT, NOW)
    assert store.pending()[3].seq == seq + 1

@non_root                                                                   # m5
def test_unreadable_subdir_is_refusal(store):                              # m-н3
    (store.place_dir(3) / "sub").mkdir(); (store.place_dir(3) / "sub").chmod(0)
    with pytest.raises(DirUnavailable):
        store.place_has_agents(3)

def test_place_of_caller():
    assert [place_of_caller(c) for c in ("student-01", "student-10", "student-11", "ops", "student-1")] == [1, 10, None, None, None]


# --- сверх брифа: имя перед путём (заметка А2), договор Role, ссылки в пути, сверка, каталоги ---

@pytest.mark.parametrize("name", ["a/b", "../x", "..", "a\\b", "sub/../x", "Sushka", "build", "pcbk-x", "ab"])
def test_save_rechecks_name_before_path(store, draft, name):              # Draft собирается и в обход check_draft
    with pytest.raises(ValueError):
        store.save(3, replace(draft, name=name), TEXT, NOW)
    assert list(store.root.rglob("*.md")) == [] and store.live_count(3) == 0 and store.pending() == {}
    assert store.save(3, draft, TEXT, NOW) == "saved"                       # положительный контроль

def test_link_never_overwrites_file_born_after_scan(store, draft, monkeypatch):     # гонка: os.link, не replace
    real = store._scan
    def scan_then_manual_file(place):
        out = real(place)
        (store.place_dir(place) / "sushka-bdm2.md").write_text("ручной")
        return out
    monkeypatch.setattr(store, "_scan", scan_then_manual_file)
    with pytest.raises(NameTaken) as e:
        store.save(3, draft, TEXT, NOW)
    assert e.value.existing == ("sushka-bdm2",) and store.live_count(3) == 0 and store.pending() == {}
    assert [p.name for p in store.place_dir(3).iterdir()] == ["sushka-bdm2.md"]           # временный удалён
    assert (store.place_dir(3) / "sushka-bdm2.md").read_text() == "ручной"              # чужой файл цел

def test_constructor_does_no_io(tmp_path):                                 # договор Role
    s = AgentStore(str(tmp_path / "нет" / "core.db"), str(tmp_path / "нет-корня"))
    assert not (tmp_path / "нет").exists() and not (tmp_path / "нет-корня").exists()
    assert s.open() is False and s.last_error                                 # каталога базы нет — не бросает
    with pytest.raises(StoreError) as e:
        s.pending()
    assert "core.db" not in str(e.value) and str(tmp_path) not in str(e.value)

def test_ops_without_open_do_not_create_db(tmp_path):                      # mode=rw: файл создаёт только open
    s = AgentStore(str(tmp_path / "core.db"), str(make_agents_root(tmp_path / "agents")))
    with pytest.raises(StoreError):
        s.live_count(3)
    assert not (tmp_path / "core.db").exists()

def test_quarantine_does_not_follow_symlinked_dir(store, tmp_path):       # m-р3-2: перенос — внутри корня
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "x.md").write_text("чужой")
    (store.place_dir(3) / "sub").symlink_to(outside)
    assert store.quarantine(3, "sub/x.md", NOW) == "sub/x"
    moved = list((store.root / "quarantine" / "student-03").iterdir())
    assert len(moved) == 1 and moved[0].is_symlink() and moved[0].name.startswith("sub.20261007T090000Z-")
    assert (outside / "x.md").read_text() == "чужой" and not (store.place_dir(3) / "sub").exists()

@pytest.mark.parametrize("rel", ["../x.md", "/etc/x.md", "a//b.md", "./x.md", "x.txt", "a b.md", ".md/..", ""])
def test_quarantine_rejects_paths_outside_template(store, rel):
    with pytest.raises(ValueError):
        store.quarantine(3, rel, NOW)

def test_drift_rows_without_file_and_expected(store, draft):
    store.save(3, draft, TEXT, NOW); store.save(3, replace(draft, name="davlenie"), TEXT, NOW)
    assert store.drift(3) == ((), (), ()) and store.expected(3) == ("davlenie", "sushka-bdm2")
    (store.place_dir(3) / "davlenie.md").unlink()
    assert store.drift(3) == ((), ("davlenie",), ())
    store.mark_visible(3, ["sushka-bdm2"], NOW + timedelta(seconds=7))
    store.set_state(3, "davlenie", "not_visible", NOW + timedelta(seconds=7))
    rows = {r.name: r for r in store.list_place(3)}
    assert (rows["sushka-bdm2"].state, rows["sushka-bdm2"].visible_at) == ("visible", "2026-10-07T09:00:07+00:00")
    assert rows["davlenie"].state == "not_visible" and store.expected(3) == ("sushka-bdm2",)
    assert not store.has_saved(3) and store.pending()[3] == PendingReload(NOW, store.pending()[3].seq)
    with pytest.raises(ValueError):
        store.set_state(3, "davlenie", "gone", NOW)

def test_archive_by_name_absent_changes_nothing(store, draft):
    store.save(3, draft, TEXT, NOW); store.reload_done(3, store.pending()[3].seq)
    assert store.archive(3, "davlenie", NOW) == () and store.pending() == {}
    assert list((store.root / "archive").iterdir()) == [] and store.live_count(3) == 1

def test_archive_same_name_twice_same_second_keeps_both(store, draft):    # m19: один каталог архива на секунду
    for _ in range(2):
        store.save(3, draft, TEXT, NOW); store.archive(3, "sushka-bdm2", NOW)
    files = sorted(p.name for p in (store.root / "archive").glob("student-03-*/*"))
    assert len(files) == 2 and files[0] == "sushka-bdm2.md" and files[1].startswith("sushka-bdm2.md-")

def test_dir_problem_texts(store, tmp_path):
    assert store.dir_problem() is None
    (store.root / "archive").rmdir()
    assert store.dir_problem() == "нет каталога карантина или архива"
    assert AgentStore(str(tmp_path / "core.db"), str(tmp_path / "нет")).dir_problem() == "нет каталога агентов"
    with pytest.raises(DirUnavailable):
        AgentStore(str(tmp_path / "core.db"), str(tmp_path / "нет")).place_has_agents(3)

def test_place_numbers_checked(store, draft):
    for place in (0, 11, "3", True):
        with pytest.raises(ValueError):
            store.save(place, draft, TEXT, NOW)
    assert MAX_AGENTS == 20

def test_store_and_admin_are_stdlib_only():                               # Д7-R53, П1
    code = ("import sys, pcbk_core.agents.store, pcbk_core.agents.admin; "
            "print(sorted(m for m in ('fastapi', 'starlette', 'httpx', 'pydantic', 'pcbk_core.gateway', "
            "'pcbk_core.data', 'pcbk_core.llm') if m in sys.modules))")
    assert run_python(code).strip() == "[]"
    assert run_python("import sys, pcbk_core.agents; print('pcbk_core.agents.admin' in sys.modules)").strip() == "False"   # m20
