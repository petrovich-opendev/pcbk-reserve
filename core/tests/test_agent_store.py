"""Учёт агентов студентов и их файлы (Д7-R56): атомарная запись, повтор и занятое имя, предел мест, очередь перезагрузок
с поколением, карантин и архив внутри корня, обход файлов места как у загрузчика OpenCode (вложенные, точечные,
символьные ссылки)."""
import os
import re
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from helpers import AGENT_DRAFT, AGENT_NOW, agent_text, make_agents_root, non_root, run_python
from pcbk_core.agents import store as store_module
from pcbk_core.agents.draft import MAX_FILE_BYTES
from pcbk_core.agents.store import (MAX_AGENTS, AgentMissing, AgentStore, DirUnavailable, NameTaken, PendingReload,
                                    StoreError, TooManyAgents, place_of_caller)

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
    seq = store.pending()[3].seq                                            # доводка р1, M5: seq растёт и при reset_since
    store.mark_reload(3, later); assert store.pending()[3].since == NOW
    store.mark_reload(3, later, reset_since=True); assert store.pending()[3].since == later
    assert store.pending()[3] == PendingReload(later, seq + 2)

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


# --- доводка р1: охранные ветки (I1) и Minor m2, m3, m4, m7 ---

def test_rows_without_file_hold_place(store, draft):                     # M1
    store.save(3, draft, TEXT, NOW); store.quarantine(3, "sushka-bdm2.md", NOW)
    assert list(store.place_dir(3).iterdir()) == [] and store.place_has_agents(3)
    store.save(4, draft, TEXT, NOW); store.set_state(4, "sushka-bdm2", "not_visible", NOW)
    (store.place_dir(4) / "sushka-bdm2.md").unlink()
    assert list(store.place_dir(4).iterdir()) == [] and store.place_has_agents(4)
    assert not store.place_has_agents(5)                                    # отрицательный контроль

def test_archive_does_not_walk_into_linked_dir(store):                   # M11
    (store.place_dir(4) / "x.md").write_text("4"); (store.place_dir(3) / "evil").symlink_to("../student-04")
    assert store.drift(3) == ((), (), ("evil",)) and store.archive(3, None, NOW) == ("evil",)
    assert (store.place_dir(4) / "x.md").read_text() == "4" and store.place_has_agents(4)
    assert next((store.root / "archive").glob("student-03-*/evil")).is_symlink()

def test_archive_name_cannot_leave_place(store):                          # M4: файл, который мутант унёс бы, есть
    (store.place_dir(4) / "x.md").write_text("4")
    for name in ("../student-04/x", "/x", "a/../x", "./x", "a//x"):
        with pytest.raises(ValueError):
            store.archive(3, name, NOW)
    assert (store.place_dir(4) / "x.md").read_text() == "4" and store.pending() == {}
    assert list((store.root / "archive").iterdir()) == []

def test_archive_by_name_queues_reload(store, draft):                     # M2 (Д7-R61)
    store.save(3, draft, TEXT, NOW); store.reload_done(3, store.pending()[3].seq)
    assert store.archive(3, "sushka-bdm2", NOW) == ("sushka-bdm2",) and 3 in store.pending()

def test_empty_archive_still_queues(store):                               # M3: «место в очередь» — всегда
    assert store.archive(3, None, NOW) == () and 3 in store.pending()

def test_limit_counts_nested_md(store, draft):                            # M6
    for i in range(19):
        store.save(3, replace(draft, name=f"a{i:02d}"), TEXT, NOW)
    (store.place_dir(3) / "sub" / ".h").mkdir(parents=True); (store.place_dir(3) / "sub" / ".h" / "m.md").write_text("x")
    with pytest.raises(TooManyAgents):
        store.save(3, draft, TEXT, NOW)
    (store.place_dir(3) / "sub" / ".h" / "m.md").unlink()
    assert store.save(3, draft, TEXT, NOW) == "saved"                       # положительный контроль: 20-й проходит

def test_set_state_does_not_revive_archived(store, draft):                # M7
    store.save(3, draft, TEXT, NOW); store.archive(3, "sushka-bdm2", NOW)
    for state in ("visible", "saved", "quarantined"):
        store.set_state(3, "sushka-bdm2", state, NOW)
    assert store.live_count(3) == 0 and store.list_place(3) == [] and store.expected(3) == ()
    assert store.save(3, draft, TEXT, NOW) == "saved"                       # имя свободно, строка новая

def test_place_dir_symlink_is_unavailable(store, draft, tmp_path):       # M9 и m4: по каталогу-ссылке — ни шагу
    elsewhere = tmp_path / "elsewhere"; elsewhere.mkdir(); (elsewhere / "x.md").write_text("чужой")
    store.place_dir(5).rmdir(); store.place_dir(5).symlink_to(elsewhere)
    calls = (lambda: store.place_has_agents(5), lambda: store.save(5, draft, TEXT, NOW),
             lambda: store.drift(5), lambda: store.archive(5, None, NOW), lambda: store.archive(5, "x", NOW),
             lambda: store.quarantine(5, "x.md", NOW))
    for call in calls:
        with pytest.raises(DirUnavailable):
            call()
    assert [p.name for p in elsewhere.iterdir()] == ["x.md"] and (elsewhere / "x.md").read_text() == "чужой"
    assert list((store.root / "archive").iterdir()) == [] and list((store.root / "quarantine").iterdir()) == []
    assert store.dir_problem() == "каталог места отсутствует или не пишется"

def test_side_dirs_are_not_created_by_store(store):                       # M8: их создаёт выкладка (Д7-R55)
    (store.place_dir(3) / "manual.md").write_text("x")
    (store.root / "archive").rmdir()
    with pytest.raises(DirUnavailable):
        store.archive(3, None, NOW)
    (store.root / "quarantine").rmdir()
    with pytest.raises(DirUnavailable):
        store.quarantine(3, "manual.md", NOW)
    assert not (store.root / "archive").exists() and not (store.root / "quarantine").exists()
    assert [p.name for p in store.place_dir(3).iterdir()] == ["manual.md"]          # файл остался на месте

def test_archive_skips_file_vanished_mid_move(store, monkeypatch):        # m2
    for n in ("a", "b", "c"):
        (store.place_dir(3) / f"{n}.md").write_text(n)
    real = store._scan
    def scan_then_vanish(place):
        out = real(place)
        (store.place_dir(place) / "b.md").unlink()                          # исчез после обхода, до переноса
        return out
    monkeypatch.setattr(store, "_scan", scan_then_vanish)
    assert store.archive(3, None, NOW) == ("a", "c") and 3 in store.pending()
    assert sorted(p.name for p in (store.root / "archive").glob("student-03-*/*")) == ["a.md", "c.md"]

def test_archive_takes_leftovers_of_interrupted_save(store):              # m3 (и M18)
    (store.place_dir(3) / ".sushka-bdm2.md.tmp-0a1b2c3d").write_text(TEXT)   # процесс упал между записью и os.link
    (store.place_dir(3) / "sub").mkdir(); (store.place_dir(3) / "sub" / "notes.txt").write_text("прежний студент")
    assert store.drift(3) == ((), (), ()) and store.place_has_agents(3)     # не агент, но место держит
    assert store.archive(3, None, NOW) == (".sushka-bdm2.md.tmp-0a1b2c3d", "sub/notes.txt")
    assert [p for p in store.place_dir(3).rglob("*") if not p.is_dir()] == [] and not store.place_has_agents(3)
    assert sorted(p.name for p in (store.root / "archive").glob("student-03-*/*")) == [
        ".sushka-bdm2.md.tmp-0a1b2c3d", "sub__notes.txt"]

def test_name_taken_lists_only_valid_names(store, draft):                 # m7: existing уходит в ответ модели
    store.save(3, replace(draft, name="davlenie"), TEXT, NOW)
    for odd in ("Bad Name.md", "x\ny.md", "ab.md", "manual.md", "sushka-bdm2.md"):
        (store.place_dir(3) / odd).write_text("x")
    fd = os.open(os.path.join(os.fsencode(store.place_dir(3)), b"\xff\xfe.md"), os.O_WRONLY | os.O_CREAT, 0o644)
    os.close(fd)
    with pytest.raises(NameTaken) as e:
        store.save(3, draft, TEXT, NOW)
    assert e.value.existing == ("davlenie", "manual", "sushka-bdm2")
    with pytest.raises(NameTaken) as e:
        store.save(3, replace(draft, name="davlenie", title="Другое"), TEXT, NOW)
    assert e.value.existing == ("davlenie",)


# --- итоговая доводка Д7а (А3, m2 — вторая сторона) ---

def test_archive_move_refused_keeps_file_and_row(store, draft, monkeypatch):    # PermissionError — не «исчез»
    store.save(3, draft, TEXT, NOW)
    real = os.replace

    def refuse(src, dst, *a, **k):
        if os.path.basename(src) == "sushka-bdm2.md":
            raise PermissionError(13, "Permission denied")
        return real(src, dst, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(store_module.os, "replace", refuse)
        with pytest.raises(DirUnavailable):
            store.archive(3, None, NOW)
    assert (store.place_dir(3) / "sushka-bdm2.md").read_text("utf-8") == TEXT
    assert store.list_place(3)[0].state == "saved"                          # строка не archived
    assert list((store.root / "archive").glob("student-03-*/sushka-bdm2*")) == []     # заглушка имени убрана


def test_archive_dir_vanished_keeps_files(store, draft, monkeypatch):       # каталог архива пропал после _side_dir
    store.save(3, draft, TEXT, NOW)
    (store.place_dir(3) / "manual.md").write_text("x")
    real = store._side_dir

    def side_then_vanish(side, name):
        path = real(side, name)
        path.rmdir()
        return path

    monkeypatch.setattr(store, "_side_dir", side_then_vanish)
    with pytest.raises(DirUnavailable):
        store.archive(3, None, NOW)
    assert sorted(p.name for p in store.place_dir(3).iterdir()) == ["manual.md", "sushka-bdm2.md"]
    assert store.list_place(3)[0].state == "saved" and list((store.root / "archive").iterdir()) == []


@non_root
def test_probe_write_sees_readonly_file(store):                           # I-1: проба записи учёта, без следа
    store.probe_write()
    assert store.pending() == {}                                            # проба откатывается
    db = store.db_path
    os.chmod(db, 0o444)
    try:
        assert store.open()                                                 # BEGIN IMMEDIATE на файле 0444 проходит
        with pytest.raises(StoreError, match="SQLITE_READONLY"):
            store.probe_write()
    finally:                                    # -wal и -shm, созданные при 0444, — тоже 0444
        for f in (db, db + "-wal", db + "-shm"):
            if os.path.exists(f):
                os.chmod(f, 0o644)
    store.probe_write()


# --- Д7б, задача А6 (предпроверка Д7б, П5) ---

def test_retry_after_quarantine_is_name_taken(store, draft):               # П5 а: не «already» про невидимого агента
    store.save(3, draft, TEXT, NOW); store.quarantine(3, "sushka-bdm2.md", NOW)
    with pytest.raises(NameTaken) as e:
        store.save(3, draft, TEXT, NOW + timedelta(seconds=5))             # тот же черновик, тот же digest
    assert e.value.existing == ("sushka-bdm2",)
    assert not (store.place_dir(3) / "sushka-bdm2.md").exists() and store.list_place(3)[0].state == "quarantined"
    store.set_state(3, "sushka-bdm2", "visible", NOW)                      # положительный контроль: живая видимая —
    assert store.save(3, draft, TEXT, NOW + timedelta(seconds=9)) == "already"   # already, как прежде


def test_mark_visible_keeps_quarantined(store, draft):                      # M12: место показывает имя — карантин
    store.save(3, draft, TEXT, NOW); store.quarantine(3, "sushka-bdm2.md", NOW)   # остаётся карантином
    store.save(3, replace(draft, name="davlenie"), TEXT, NOW)
    store.mark_visible(3, ["sushka-bdm2", "davlenie"], NOW + timedelta(seconds=7))
    rows = {r.name: (r.state, r.visible_at) for r in store.list_place(3)}
    assert rows == {"sushka-bdm2": ("quarantined", None), "davlenie": ("visible", "2026-10-07T09:00:07+00:00")}


# --- Д10а, задача А3: кабинет преподавателя читает учёт и файлы (Д10-R103, I-9, m-12) ---

def test_cards_read_file_and_live_counts(tmp_path):
    root = make_agents_root(tmp_path / "agents")
    s = AgentStore(str(tmp_path / "core.db"), root)
    assert s.open(), s.last_error
    other = replace(AGENT_DRAFT, name="sushka-bdm3", title="Сушка БДМ-3")
    for place, draft in ((3, AGENT_DRAFT), (3, other), (5, AGENT_DRAFT)):
        assert s.save(place, draft, agent_text(draft, f"student-{place:02d}"), AGENT_NOW) == "saved"
    s.archive(3, other.name, AGENT_NOW)
    assert [(c.name, c.node, c.tools, c.tags, c.state) for c in s.cards(3)] == \
           [(AGENT_DRAFT.name, AGENT_DRAFT.node, list(AGENT_DRAFT.tools), [], "saved")]
    assert s.live_counts() == {3: 1, 5: 1}
    assert s.read_file(3, AGENT_DRAFT.name) == agent_text(AGENT_DRAFT, "student-03")
    for place, name in ((3, other.name), (3, "net-takogo")):               # архивный и неизвестный
        with pytest.raises(AgentMissing):
            s.read_file(place, name)
    f = s.place_dir(5) / f"{AGENT_DRAFT.name}.md"
    for broken in (b"\xff\xfe" + b"x" * 10, b"x" * (MAX_FILE_BYTES + 1)):  # не UTF-8; больше 16 КиБ
        f.write_bytes(broken)
        with pytest.raises(AgentMissing):
            s.read_file(5, AGENT_DRAFT.name)
    f.unlink()
    f.mkdir()                                                                # каталог вместо файла
    with pytest.raises(AgentMissing):
        s.read_file(5, AGENT_DRAFT.name)
    f.rmdir()
    f.symlink_to(s.place_dir(3) / f"{AGENT_DRAFT.name}.md")                  # ссылка на файл соседа
    with pytest.raises(AgentMissing):
        s.read_file(5, AGENT_DRAFT.name)
    base = s.place_dir(5)                                                    # каталог места — ссылка (m-12)
    base.rename(tmp_path / "moved")
    base.symlink_to(tmp_path / "moved")
    with pytest.raises(DirUnavailable):
        s.read_file(5, AGENT_DRAFT.name)


def test_read_file_odd_marks_and_card_columns(store, draft):              # сверх брифа: odd — только у кривого файла
    store.save(3, draft, TEXT, NOW)
    f = store.place_dir(3) / f"{draft.name}.md"
    f.unlink()                                                              # строка есть, файла нет (как карантин)
    with pytest.raises(AgentMissing) as e:
        store.read_file(3, draft.name)
    assert e.value.odd is False and draft.name not in str(e.value)
    os.mkfifo(f)                                                            # FIFO не держит чтение
    with pytest.raises(AgentMissing) as e:
        store.read_file(3, draft.name)
    assert e.value.odd is True
    f.unlink()
    f.write_bytes(b"x" * MAX_FILE_BYTES)                                    # ровно предел — читается
    assert store.read_file(3, draft.name) == "x" * MAX_FILE_BYTES
    for bad in ("Bad_Name", "../x", "x" * 41, 3):                            # имя вне правил — как неизвестное
        with pytest.raises(AgentMissing):
            store.read_file(3, bad)
    with sqlite3.connect(store.db_path) as db:                              # правка мимо службы: столбцы не того вида
        db.execute("UPDATE agents SET tools = 'не json', tags = '[1, 2]' WHERE place = 3")
    assert [(c.tools, c.tags, c.created_at, c.visible_at) for c in store.cards(3)] == \
           [([], [], "2026-10-07T09:00:00+00:00", None)]
    assert store.cards(4) == [] and store.live_counts() == {3: 1}
