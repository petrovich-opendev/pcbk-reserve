"""Файл переопределения мест (Д8-R71): только OPENCODE_CONFIG_CONTENT = {"model": …}."""
import json

import pytest

from pcbk_bench.override import render_override


def test_override_render_only_model():                                           # Review Focus 2
    doc = json.loads(render_override({7: "pcbk/z-ai/glm-5.3", 10: "pcbk-or/google/gemini-3.8-flash"}))
    assert set(doc["services"]) == {"student-07", "student-10"}
    for svc in doc["services"].values():
        assert set(svc) == {"environment"} and set(svc["environment"]) == {"OPENCODE_CONFIG_CONTENT"}
    assert json.loads(doc["services"]["student-10"]["environment"]["OPENCODE_CONFIG_CONTENT"]) == \
           {"model": "pcbk-or/google/gemini-3.8-flash"}


@pytest.mark.parametrize("places", [{}, {0: "pcbk/z-ai/glm-5.3"}, {12: "pcbk/z-ai/glm-5.3"}, {7: "glm"}, {7: ""}])
def test_override_refuses_bad_places(places):
    with pytest.raises(ValueError):
        render_override(places)
