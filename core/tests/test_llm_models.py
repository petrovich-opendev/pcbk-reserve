"""Образ места и прокси знают одни и те же модели: иначе выбор модели в чате — 403 pcbk_model_refused."""
import json
from pathlib import Path

from pcbk_core.llm.models import select_models

WORKPLACE_CONFIG = Path(__file__).resolve().parents[2] / "student" / "config" / "opencode.json"


def test_workplace_config_lists_same_models():                                  # Review Focus 4
    cfg = json.loads(WORKPLACE_CONFIG.read_text())
    assert list(cfg["provider"]["pcbk"]["models"]) == list(select_models(""))
    assert set(cfg["provider"]["pcbk-or"]["models"]) <= set(select_models(""))
    assert cfg["model"].split("/", 1)[1] in select_models("")
    assert cfg["permission"]["task"] == "deny"                                  # подагентов нет (Д4б-R20)
