"""Обёртка запуска core тестового стенда (Д8-R75): python -m twin_core с PYTHONPATH=/twin (core/tests только на чтение).

С непустой PCBK_TWIN_HISTORIAN — путь к фикстуре — обёртка подменяет pcbk_core.data.historian.tds_query двойником
TwinHistorian (build_roles импортирует tds_query внутри функции, поэтому подмена до запуска действует), затем запускает
pcbk_core.main как __main__. Без переменной (или с пустой) — тот же запуск без подмены: настоящий tds_query.

Только compose.test.yaml: в compose.yaml и в образе core обёртки нет (test_prod_compose_has_no_twin,
test_core_wrapper_healthy_without_twin). Строка в stderr при включении — видно в журнале контейнера, что данные
ненастоящие.
"""
import os
import runpy
import sys
from collections.abc import Mapping
from pathlib import Path

ENV = "PCBK_TWIN_HISTORIAN"


def install_twin(env: Mapping[str, str]) -> bool:
    """Подменить tds_query двойником, если env[PCBK_TWIN_HISTORIAN] не пуст; True — подменён.

    Фикстура читается сразу: битый файл — отказ на старте, а не позже, в фоне каталога.
    """
    path = (env.get(ENV) or "").strip()
    if not path:
        return False
    import pcbk_core.data.historian as historian
    from twin_historian import TwinHistorian

    twin = TwinHistorian.from_fixture(Path(path))

    def twin_query(cfg, connect=None):          # подпись tds_query; учётные данные двойнику не нужны
        return twin

    historian.tds_query = twin_query
    return True


def main() -> None:
    if install_twin(os.environ):
        print(f"pcbk-core: двойник историана тестового стенда — данные из {os.environ[ENV].strip()}",
              file=sys.stderr, flush=True)
    runpy.run_module("pcbk_core.main", run_name="__main__")


if __name__ == "__main__":
    main()
