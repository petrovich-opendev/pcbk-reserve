"""Счётчики агентов студентов за час (Д7-R60): строка сторожа «Агенты студентов» и GET /health/agents.

Один объект на процесс (Д7-R53): его создаёт main.build_roles и отдаёт обеим ролям — «данные» пишет saved, refused,
unavailable (save_agent, Д7-R57), «шлюз» — виды перезагрузки и карантина (Д7-R58, Д7-R59) и отдаёт /health/agents.
Роль без переданного объекта (тесты) создаёт свой.

Кроме счётчиков — три состояния, без чисел и путей:
- store_error — сбой учёта агентов у роли «данные» (store_failed / store_ok);
- worker_store_error — то же у воркера перезагрузки (worker_store_failed / worker_store_ok, раунд 3, I-р3-1):
  удача одной роли флаг другой не стирает, и в /health/agents store_ok истинно, только когда оба None;
- dir_error — текст проверки каталога агентов (ставит воркер, Д7-R58; тексты Д7-R60, не длиннее 80 знаков) или None.

Только stdlib (Д7-R53, предпроверка П1): окно часа — pcbk_core.hourly, не gateway.stats.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from ..hourly import HourlyCounts

# saved — ok без already, refused, unavailable — save_agent (Д7-R57); прочие — перезагрузка и карантин (Д7-R58, Д7-R59)
AGENT_KINDS = frozenset({"saved", "refused", "unavailable", "visible", "not_visible", "quarantined", "reload_failed",
                         "image_config", "reload_delayed"})


class AgentStats(HourlyCounts):
    """event(kind) и count_1h(kind) по AGENT_KINDS (окно часа); флаги учёта и каталога — см. модуль."""

    def __init__(self, monotonic: Callable[[], float] = time.monotonic):
        super().__init__(AGENT_KINDS, monotonic, what="агентов")
        self.store_error: str | None = None
        self.worker_store_error: str | None = None
        self.dir_error: str | None = None

    def store_failed(self, text: str) -> None:
        """Роль «данные»: учёт агентов не открылся или не записал; text — класс ошибки, без пути и значений."""
        self.store_error = text

    def store_ok(self) -> None:
        """Роль «данные»: учёт агентов снова пишется."""
        self.store_error = None

    def worker_store_failed(self, text: str) -> None:
        """Воркер перезагрузки: очередь или строки агентов не читаются (StoreError)."""
        self.worker_store_error = text

    def worker_store_ok(self) -> None:
        """Воркер перезагрузки: проход по очереди удался."""
        self.worker_store_error = None
