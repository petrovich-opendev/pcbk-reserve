# Фикстуры тестов аналитики КДМ-1

`kdm1_corpus.py` строит синтетический корпус (SQLite, схема `kdm1.seed.sqlite3` пакета заказчика) с полностью
выдуманными данными — тегами вида `20FAKE_001_PV`, выдуманными терминами/карточками/фрагментами. Это
единственный корпус, который видят автоматические тесты и CI: в репозитории нет и не должно быть текстов
настоящего корпуса КДМ-1 (`/home/dev/incoming/kdm1-package/` — вне git, с данными предприятия).

```python
from fixtures.kdm1_corpus import build_corpus

path = build_corpus(tmp_path / "fake.seed.sqlite3")   # путь — новый файл, как tools/import_seed.py
```

## Подключение настоящего корпуса локально

Тесты, которым нужен настоящий корпус заказчика (например, сверка реального покрытия знаний, а не формы
схемы), читают путь к пакету из переменной окружения `KDM1_PACKAGE_DIR` — она указывает на каталог вида
`/home/dev/incoming/kdm1-package/` (там `knowledge/kdm1.seed.sqlite3`, `MASTER_PROMPT_KDM1_CLAUDE_CODE.md` и
прочее). Без переменной такие тесты пропускаются, а не падают:

```python
import os
import pytest

KDM1_PACKAGE_DIR = os.environ.get("KDM1_PACKAGE_DIR", "")

@pytest.mark.skipif(not KDM1_PACKAGE_DIR, reason="нужен KDM1_PACKAGE_DIR — пакет заказчика вне репозитория")
def test_against_real_corpus():
    corpus_path = os.path.join(KDM1_PACKAGE_DIR, "knowledge", "kdm1.seed.sqlite3")
    ...
```

Запуск локально с настоящим корпусом:

```sh
cd core
KDM1_PACKAGE_DIR=/home/dev/incoming/kdm1-package \
  uv run --python 3.12 --with-requirements requirements.lock --with pytest pytest -q tests/test_analytics_knowledge.py
```

Эти тесты никогда не коммитят в репозиторий ни пути, ни содержимое пакета заказчика — только факт, что переменная
задана. CI и обычный `pytest` без `KDM1_PACKAGE_DIR` видят только синтетический корпус выше.
