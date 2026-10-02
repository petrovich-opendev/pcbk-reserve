"""Файл переопределения мест на время отбора (Д8-R71): только OPENCODE_CONFIG_CONTENT = {"model": <ссылка>}.

JSON — он же годный YAML для compose: docker compose -f compose.yaml -f <файл> up -d --no-build --force-recreate
student-NN…. OpenCode 1.18.33 читает переменную последней и сливает только model; заголовок разговора идёт на ту же
модель (сверка с кодом, п. 2). На сервере файл лежит в ~/pcbk-d8/compose.bench.yaml, в git — нигде.
"""
from __future__ import annotations

import json
from collections.abc import Mapping

from .config import PLACES


def render_override(places: Mapping[int, str]) -> str:
    """JSON-текст {"services": {"student-NN": {"environment": {"OPENCODE_CONFIG_CONTENT": "<JSON>"}}}}."""
    services: dict[str, dict] = {}
    for n, ref in sorted(places.items()):
        if isinstance(n, bool) or not isinstance(n, int) or n not in PLACES:
            raise ValueError("переопределение: места от 1 до 10")
        if not isinstance(ref, str) or "/" not in ref:
            raise ValueError("переопределение: модель — «провайдер/модель»")
        content = json.dumps({"model": ref}, ensure_ascii=False, separators=(",", ":"))
        services[f"student-{n:02d}"] = {"environment": {"OPENCODE_CONFIG_CONTENT": content}}
    if not services:
        raise ValueError("переопределение: нет мест")
    return json.dumps({"services": services}, ensure_ascii=False, indent=2) + "\n"


__all__ = ["render_override"]
