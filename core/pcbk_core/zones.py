"""Зоны адресов (Д4б-R23, Д5-R31, Ш25): куда пришло соединение и откуда пришёл токен места.

core слушает один uvicorn на 0.0.0.0:8000, и его видно на .2 каждой сети мест. uvicorn кладёт в
scope["server"] локальный адрес соединения (getsockname), в scope["client"] — адрес источника; оба —
адреса IP. Зона — по обоим адресам сразу (итоговое ревью Д5а, M3): и назначение, и источник — в сетях зоны.
Одного назначения мало: ядро принимает пакет на любой свой адрес с любого интерфейса, и клиент из сети места,
добравшийся до адреса core в pcbk-front, получил бы ручки шлюза.
- FRONT_NETS — pcbk-front (edge, сторож) и петля (проба здоровья, docker exec внутри core): ручки шлюза;
- ADMIN_NETS — ещё pcbk-egress (сам core и хост сервера — администратор): /health/llm полностью.

Закрыто при сомнении: адреса нет (scope["server"] или scope["client"] = None) или имя вместо адреса — чужая
зона. Имя бывает только у клиента тестов в процессе (TestClient: сервер «testserver», клиент «testclient»);
своим его считает лишь приложение с пометкой app.state.zone_names_ok = True — её ставят помощники тестов,
create_app — нет.

Токен места (student-NN) принимается только из сети своего места {STU_NET}.N.0/28 или из ADMIN_NETS
(caller_net_ok). STU_NET — app.state.pcbk_stu_net (ставит install шлюза), без него — 172.31, как в
compose.yaml. Вложенное приложение Starlette (транспорт MCP) заменяет scope["app"] собой, поэтому
mcp_auth кладёт уже найденный STU_NET в scope под тем же ключом до входа в него.

Только stdlib: модуль импортируют служба данных и LLM-прокси.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from typing import Any

FRONT_NETS = ("172.31.250.32/28", "127.0.0.0/8", "::1/128")
ADMIN_NETS = FRONT_NETS + ("172.31.250.80/28",)
DEFAULT_STU_NET = "172.31"
# ключ scope и имя атрибута app.state: первые два октета сетей мест
STU_NET_KEY = "pcbk_stu_net"
PLACE_NET_PREFIX = 28

STUDENT_PREFIX = "student-"
_STUDENT = re.compile(STUDENT_PREFIX + r"([0-9]{2})")

log = logging.getLogger("pcbk_core.zones")

_names_logged: set[str] = set()

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache(maxsize=64)
def _networks(nets: tuple[str, ...]) -> tuple[Network, ...]:
    return tuple(ipaddress.ip_network(n) for n in nets)


def _ip(value: object) -> Address | None:
    """Адрес IP из строки; не строка или не адрес (имя) — None. IPv4 в IPv6 (::ffff:a.b.c.d) — как IPv4."""
    if not isinstance(value, str):
        return None
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _in(addr: Address, nets: Iterable[str]) -> bool:
    return any(addr in net for net in _networks(tuple(nets)))


def _state(scope: Mapping[str, Any]) -> object:
    return getattr(scope.get("app"), "state", None)


_WHAT = {"server": "адрес назначения", "client": "адрес источника"}


def _log_name_once(key: str) -> None:
    if key not in _names_logged:
        _names_logged.add(key)
        log.warning("%s — не IP: считаю чужим", _WHAT[key])


def _addr_in(scope: Mapping[str, Any], key: str, nets: Iterable[str]) -> bool:
    """Адрес scope[key] (server — куда пришло соединение, client — откуда) в nets. Нет адреса — нет; имя —
    только при zone_names_ok = True."""
    pair = scope.get(key)
    if not pair:
        return False
    addr = _ip(pair[0])                 # у uvicorn — кортеж, у TestClient Starlette — список
    if addr is None:
        if getattr(_state(scope), "zone_names_ok", False) is True:
            return True
        _log_name_once(key)
        return False
    return _in(addr, nets)


def dest_in(scope: Mapping[str, Any], nets: Iterable[str]) -> bool:
    """Пришло ли соединение на адрес из nets."""
    return _addr_in(scope, "server", nets)


def client_in(scope: Mapping[str, Any], nets: Iterable[str]) -> bool:
    """Пришло ли соединение с адреса из nets."""
    return _addr_in(scope, "client", nets)


def is_front(scope: Mapping[str, Any]) -> bool:
    """Ручки шлюза: и назначение, и источник — в FRONT_NETS."""
    return dest_in(scope, FRONT_NETS) and client_in(scope, FRONT_NETS)


def is_admin(scope: Mapping[str, Any]) -> bool:
    """/health/llm полностью: и назначение, и источник — в ADMIN_NETS."""
    return dest_in(scope, ADMIN_NETS) and client_in(scope, ADMIN_NETS)


def stu_net(scope: Mapping[str, Any]) -> str:
    """Первые два октета сетей мест: ключ scope (его кладёт mcp_auth), иначе app.state, иначе 172.31."""
    value = scope.get(STU_NET_KEY)
    if not isinstance(value, str):
        value = getattr(_state(scope), STU_NET_KEY, None)
    return value if isinstance(value, str) else DEFAULT_STU_NET


def caller_net_ok(scope: Mapping[str, Any], caller: str) -> bool:
    """Токен student-NN — только из сети своего места или из ADMIN_NETS (Ш25); прочие вызывающие — всегда.

    id с префиксом student-, номер которого не разобрать (student-1, student-001, student-ab), — отказ из
    любой сети: закрыто при сомнении (ревью А4, M4). Адрес источника не IP или его нет (клиент тестов в
    процессе) — сверки нет: uvicorn кладёт адрес IP.
    """
    if not isinstance(caller, str):
        return True
    m = _STUDENT.fullmatch(caller)
    if m is None:
        return not caller.startswith(STUDENT_PREFIX)
    client = scope.get("client")
    addr = _ip(client[0]) if client else None
    if addr is None:
        return True
    if _in(addr, ADMIN_NETS):
        return True
    try:
        own = ipaddress.ip_network(f"{stu_net(scope)}.{int(m[1])}.0/{PLACE_NET_PREFIX}")
    except ValueError:                  # STU_NET не того вида — сеть места не построить: закрыто
        return False
    return addr in own
