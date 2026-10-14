"""19 защищённых файлов (план КДМ1, Task 0, Global Constraints: `core/pcbk_core/data/` целиком и
`gateway/calc_api.py`) не меняются ни одной задачей волны КДМ-1. База — `docs/kdm1/PROTECTED_DATA_BASELINE.
current.json` (пересчёт от рабочего дерева, не архивный файл пакета заказчика — расхождение и причина см.
`docs/kdm1/DRIFT.md`): падает при изменении любого из 19 файлов относительно неё, независимо от уже учтённого
расхождения архива."""
import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_PATH = REPO_ROOT / "docs" / "kdm1" / "PROTECTED_DATA_BASELINE.current.json"


def _baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_baseline_file_has_19_entries():
    files = _baseline()["files"]
    assert len(files) == 19
    assert len({f["path"] for f in files}) == 19           # пути не повторяются


def test_protected_files_match_current_baseline():
    """Каждый из 19 файлов — ровно тот размер и хеш, что зафиксирован в PROTECTED_DATA_BASELINE.current.json.
    Падает (а не молча проходит) при любой правке защищённого файла — в том числе косметической."""
    mismatches = []
    for entry in _baseline()["files"]:
        path = REPO_ROOT / entry["path"]
        if not path.is_file():
            mismatches.append((entry["path"], "файла нет"))
            continue
        data = path.read_bytes()
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            mismatches.append((entry["path"], "изменён"))
    assert mismatches == []


def test_protected_files_are_exactly_data_dir_plus_calc_api():
    """Список — весь core/pcbk_core/data/*.py (без подкаталогов данных, если появятся — тест заметит разницу
    числа) плюс ровно gateway/calc_api.py: защищённая граница не уже и не шире плана."""
    data_dir = REPO_ROOT / "core" / "pcbk_core" / "data"
    actual_data_files = {f"core/pcbk_core/data/{p.name}" for p in data_dir.glob("*.py")}
    baseline_paths = {e["path"] for e in _baseline()["files"]}
    baseline_data_files = {p for p in baseline_paths if p.startswith("core/pcbk_core/data/")}
    assert actual_data_files == baseline_data_files
    assert baseline_paths - baseline_data_files == {"core/pcbk_core/gateway/calc_api.py"}
