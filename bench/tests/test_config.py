"""Конфиг прогона отбора (Д8-R71, Д8-R74, m-р4-2, m4, m5): ссылки моделей мест, отказы, пояс у начала окна."""
import json
from datetime import timedelta

import pytest

from fakes import M1, M2, write_config
from helpers import REPO
from pcbk_bench.__main__ import main
from pcbk_bench.config import load_config, model_refs

OC = REPO / "student" / "config" / "opencode.json"


def test_config_refs_from_opencode_json():                                       # Review Focus 2
    refs = model_refs(json.loads(OC.read_text()))
    assert "pcbk/z-ai/glm-5.3" in refs and "pcbk-or/google/gemini-3.8-flash" in refs
    assert "pcbk/anthropic/claude-opus-5.5" not in refs


def test_model_refs_only_enabled_bench_providers():
    oc = {"enabled_providers": ["pcbk"],
          "provider": {"pcbk": {"models": {"a/b": {}}}, "pcbk-or": {"models": {"c/d": {}}},
                       "openrouter": {"models": {"e/f": {}}}}}
    assert model_refs(oc) == {"pcbk/a/b"}


@pytest.mark.parametrize("patch", [{"places": {"11": "pcbk/z-ai/glm-5.3"}}, {"places": {"7": "pcbk/z-ai/glm-5"}},
                                   {"places": {"7": "pcbk/z-ai/glm-5.3", "8": "pcbk/z-ai/glm-5.3"}},
                                   {"reps": {"create": 0, "number": 3, "trap": 3}}, {"cap_usd": 0}])
def test_config_refused(tmp_path, patch):
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, **patch), OC)


@pytest.mark.parametrize("patch", [{"places": {}}, {"places": {"0": M1}}, {"places": {"7": M1, "07": M2}},
                                   {"reps": {"create": 2, "number": 3}},
                                   {"reps": {"create": True, "number": 3, "trap": 3}},
                                   {"cap_usd": -1}, {"stop_margin_usd": 80.0}, {"stop_margin_usd": -0.1},
                                   {"run_id": "D8_Bad"}, {"run_id": ""}, {"turn_timeout_s": 0},
                                   {"run_timeout_s": {"create": 0}}, {"run_timeout_s": {"other": 5}},
                                   {"archive_reserve_s": 2700}, {"projection_ack": "yes"}, {"projection_ak": True},
                                   {"cards": None}])
def test_config_refused_more(tmp_path, patch):
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, **patch), OC)


def test_config_missing_key_refused(tmp_path):
    path = write_config(tmp_path)
    raw = json.loads(path.read_text())
    del raw["stop_margin_usd"]
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="stop_margin_usd"):
        load_config(path, OC)


def test_config_paths_relative_to_file(tmp_path):
    cfg = load_config(write_config(tmp_path, run_timeout_s={"number": 600}), OC)
    assert cfg.out_dir == (tmp_path / "out").resolve() and cfg.stand == (tmp_path / "stand.json").resolve()
    assert cfg.places == {7: M1, 8: M2} and cfg.run_timeout_s == {"create": 2700, "number": 600, "trap": 1200}
    assert (cfg.turn_timeout_s, cfg.heartbeat_s, cfg.archive_reserve_s, cfg.projection_ack) == (900.0, 30.0, 120.0,
                                                                                              False)


def test_twin_config_margin():                                                   # m-н10
    cfg = load_config(REPO / "bench" / "config.twin.json", OC)
    assert (cfg.cap_usd, cfg.stop_margin_usd) == (5.0, 0.5)


def test_twin_config_matches_integration():                                      # m4: А6 ждёт это дословно
    cfg = load_config(REPO / "bench" / "config.twin.json", OC)
    assert cfg.run_id == "d8-twin"
    assert cfg.places == {5: "pcbk/z-ai/glm-5.3", 6: "pcbk/deepseek/deepseek-v4.1-flash",
                          7: "pcbk/google/gemini-3.8-flash", 8: "pcbk/openai/gpt-6-sol"}
    assert cfg.reps == {"create": 2, "number": 3, "trap": 3}
    assert cfg.out_dir is None and cfg.window_start is None and cfg.stand is None      # out задаёт А6
    assert cfg.cards == (REPO / "bench" / "cards.twin.json").resolve()


def test_window_start_required_and_tz(tmp_path):                                 # m-р4-2
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, window_start="2026-10-08T10:00:00"), OC)    # без пояса
    cfg = load_config(write_config(tmp_path, window_start="2026-10-08T10:00:00+05:00"), OC)
    assert cfg.window_start.utcoffset() == timedelta(hours=5)
    assert main(["run", str(write_config(tmp_path, window_start=None))]) == 2           # RunRefused("window_start")


def test_cli_window_start_refused_before_connect(tmp_path, capsys):
    # stand.json нет: если бы main подключался до проверки window_start, был бы FileNotFoundError и код 1
    assert main(["run", str(write_config(tmp_path, window_start=None))]) == 2
    assert capsys.readouterr().err.strip() == "RunRefused: window_start"


def test_cli_bad_config_is_one_without_text(tmp_path, capsys):
    assert main(["run", str(write_config(tmp_path, places={"7": "pcbk/secret-model-name"}))]) == 1
    err = capsys.readouterr().err
    assert err.strip() == "ValueError" and "secret-model-name" not in err


def test_cli_override_prints_override(tmp_path, capsys):
    assert main(["override", str(write_config(tmp_path))]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert set(doc["services"]) == {"student-07", "student-08"}


def test_cli_usage_error_is_one(capsys):
    assert main(["nothing"]) == 1


def test_connect_reads_stand_json_and_secrets_without_network(tmp_path):        # П4
    from pcbk_bench.__main__ import connect
    from pcbk_bench.stand import ServerStand

    (tmp_path / "stand.json").write_text(json.dumps({
        "ssh": ["ssh", "-o", "BatchMode=yes", "dev@host"], "core_url": "http://127.0.0.1:18000",
        "edge_url": "https://127.0.0.1:28443", "edge_host": "stand.example:28443"}))
    (tmp_path / "secrets").mkdir()
    for n in (7, 8):
        (tmp_path / "secrets" / f"d8-probe-bench-{n:02d}.pw").write_text(f"pw-test-{n}\n")
    stand, chats = connect(load_config(write_config(tmp_path), OC))
    assert isinstance(stand, ServerStand) and stand.secrets == tmp_path / "secrets"
    assert (stand.core_url, stand.edge_url, stand.edge_host) == ("http://127.0.0.1:18000", "https://127.0.0.1:28443",
                                                                  "stand.example:28443")
    assert set(chats) == {7, 8} and chats[8]._login == "d8-probe-bench-08" and chats[8]._password == "pw-test-8"
    assert "pw-test" not in repr(vars(chats[8]))
    (tmp_path / "stand.json").write_text(json.dumps({"ssh": ["ssh"], "core_url": "x"}))
    with pytest.raises(ValueError):
        connect(load_config(write_config(tmp_path), OC))


def test_config_scenarios_filter(tmp_path):                                      # П14: сухой прогон Б4, слой 0 Б5
    assert load_config(write_config(tmp_path), OC).scenarios is None
    cfg = load_config(write_config(tmp_path, scenarios=["B3"]), OC)
    assert cfg.scenarios == ("B3",)
    for bad in ([], ["Z9"], ["B3", "B3"], "B3", [3]):
        with pytest.raises(ValueError, match="scenarios"):
            load_config(write_config(tmp_path, scenarios=bad), OC)


def test_load_twin_config_matches_integration():                                 # Д11б: Б2 ждёт это дословно
    cfg = load_config(REPO / "bench" / "config.load.twin.json", OC)
    assert (cfg.run_id, cfg.load, cfg.load_data) == ("d11-twin", True, "twin")
    assert cfg.places == {n: "pcbk/z-ai/glm-5.3" for n in range(1, 11)}
    assert cfg.reps == {"create": 1, "number": 1, "trap": 1} and cfg.scenarios == ("A1", "B4", "C3")
    assert (cfg.swap_base_mib, cfg.swap_grow_mib, cfg.dify_swap_base_mib, cfg.dify_swap_max_mib) == (0, 512, 0, 256)
    assert (cfg.mem_fail_mib, cfg.psi_full_max) == (None, None)                    # стенд: умолчания сторожа памяти
    assert (cfg.cap_usd, cfg.stop_margin_usd) == (5.0, 0.5)
    assert cfg.out_dir is None and cfg.window_start is None and cfg.stand is None


@pytest.mark.parametrize("patch", [
    {"load_data": "twin"}, {"swap_base_mib": 0}, {"psi_full_max": 10.0},           # поля нагрузки без load: true
    {"load": "yes", "load_data": "twin"}, {"load": True, "load_data": "server"},
    {"load": True, "load_data": "none"},                                            # все сценарии — и с bench
    {"load": True, "load_data": "twin", "swap_base_mib": -1}, {"load": True, "load_data": "twin", "swap_grow_mib": 0},
    {"load": True, "load_data": "twin", "mem_fail_mib": 1.5},
    {"load": True, "load_data": "twin", "dify_swap_max_mib": True},
    {"load": True, "load_data": "twin", "psi_full_max": 0}, {"load": True, "load_data": "twin", "psi_full_max": 101},
    {"load": True, "load_data": "twin", "dify_swap_base_mib": 1_048_577}])
def test_load_fields_refused(tmp_path, patch):
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, **patch), OC)


def test_load_fields_parsed(tmp_path):
    cfg = load_config(write_config(tmp_path, load=True, load_data="none", places={"1": M1, "2": M1}, mem_fail_mib=900,
                                   scenarios=["A1", "A2", "A3", "A4", "B3", "C2"], psi_full_max=7), OC)
    assert (cfg.load_data, cfg.mem_fail_mib, cfg.psi_full_max, cfg.swap_base_mib) == ("none", 900, 7.0, None)
    assert cfg.places == {1: M1, 2: M1}
    plain = load_config(write_config(tmp_path), OC)
    assert (plain.load, plain.load_data, plain.swap_base_mib) == (False, None, None)
