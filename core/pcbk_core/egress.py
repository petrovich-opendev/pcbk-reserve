"""Охрана выхода в процессе (Д3б-R6): растяжка по PEP 578, не песочница.

Аудит-хук ловит случайный выход нашим кодом и библиотеками: соединение не
туда и запуск процессов. От атакующего внутри процесса он не защищает — PEP
578 так и говорит. Настоящая граница — правила DOCKER-USER для моста
pcbk-egress (предложение владельцу на Д12).

Что делает хук:
- socket.connect (AF_INET, AF_INET6): адрес вне allowed и allowed_nets —
  EgressDenied до системного вызова. Имя хоста в кортеже разрешается через
  кэш, проверяется каждый его адрес; не разрешилось — отказ. Имена из allowed
  разрешаются так же, при первом соединении, а не при установке;
- socket.sendto и socket.sendmsg с адресом на потоковом сокете — отказ: так
  TCP не уйдёт через TCP Fast Open мимо connect. sendmsg без адреса — обычная
  запись asyncio 3.12 в уже подключённый сокет, её хук пропускает: без адреса
  Fast Open соединения не откроет;
- subprocess.Popen, os.system, os.exec, os.posix_spawn, ctypes.dlopen —
  PermissionError.

Прочие семейства (AF_UNIX — сокеты-пары asyncio) хук пропускает. Кэш имён:
имя разрешается не чаще раза в 60 с (RESOLVE_RETRY_S), результат — и неудача
тоже — живёт до следующего разрешения.

Ставится только в точках входа процесса — serve() службы и __main__
построителя: хук не снимается, а main(), build_roles и build_whitelist.main
тесты зовут в процессе pytest (F2, F3).

Обходы хука — названы, а не закрыты:
- уже загруженный ctypes и прямые системные вызовы через него;
- _posixsubprocess.fork_exec в обход subprocess;
- DNS через 127.0.0.11 и UDP (sendto на датаграммах);
- нативный модуль с тома /var/lib/pcbk-core;
- на уровне сети из pcbk-egress открыт любой адрес (факт Д3а);
- имя в кортеже connect разрешается дважды — хуком (из кэша до 60 с) и самим
  connect; если DNS между ними ответит иначе, проверен не тот адрес.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import sys
import threading
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .secrets import BdrvConfig

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

RESOLVE_RETRY_S = 60.0
PROCESS_TEXT = "запуск процессов через subprocess/os запрещён"
_PROCESS_EVENTS = frozenset({"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "ctypes.dlopen"})
_SEND_EVENTS = frozenset({"socket.sendto", "socket.sendmsg"})
_WATCHED = _PROCESS_EVENTS | _SEND_EVENTS | {"socket.connect"}
_INET = (socket.AF_INET, socket.AF_INET6)
# тип сокета без флагов Linux: у _socket.socket они видны в .type
_TYPE_FLAGS = getattr(socket, "SOCK_NONBLOCK", 0) | getattr(socket, "SOCK_CLOEXEC", 0)

log = logging.getLogger("pcbk_core.egress")

_install_lock = threading.Lock()
_installed = False


class EgressDenied(PermissionError):
    """Соединение вне разрешённых направлений: охрана выхода отказала до системного вызова."""


def _ip(text: str) -> IPAddress | None:
    """Адрес из строки; IPv4 внутри IPv6 (::ffff:a.b.c.d) — как IPv4; не адрес — None."""
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


class _Resolver:
    """Имя → адреса через getaddrinfo, не чаще раза в RESOLVE_RETRY_S на имя и семейство."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, int], tuple[float, frozenset[IPAddress]]] = {}

    def addresses(self, host: str, family: int) -> frozenset[IPAddress]:
        ip = _ip(host)
        if ip is not None:
            return frozenset({ip})
        key, now = (host, family), time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None and now - hit[0] < RESOLVE_RETRY_S:
            return hit[1]
        # замок не держится во время getaddrinfo: хук зовётся и изнутри неё (событие socket.getaddrinfo)
        try:
            infos = socket.getaddrinfo(host, None, family, socket.SOCK_STREAM)
            found = frozenset(ip for info in infos if (ip := _ip(str(info[4][0]))) is not None)
        except (OSError, UnicodeError, ValueError):
            found = frozenset()
        with self._lock:
            self._cache[key] = (now, found)
        return found


class _Guard:
    def __init__(self, allowed: Sequence[tuple[str, int | None]], nets: Sequence[str]):
        self.allowed = tuple(allowed)
        self.nets = tuple(ipaddress.ip_network(n) for n in nets)
        self.resolver = _Resolver()

    def permits(self, ip: IPAddress, port: int) -> bool:
        if any(ip.version == net.version and ip in net for net in self.nets):
            return True
        return any((want is None or want == port) and ip in self.resolver.addresses(host, socket.AF_UNSPEC)
                   for host, want in self.allowed)

    def check_connect(self, sock: object, address: object) -> None:
        if getattr(sock, "family", None) not in _INET:
            return
        try:
            host, port = address[0], int(address[1])         # type: ignore[index]
            if isinstance(host, (bytes, bytearray)):
                host = bytes(host).decode("idna")
            host = str(host)
        except Exception:                                  # кортеж не того вида: connect всё равно откажет
            raise EgressDenied(f"выход запрещён: {address!r}") from None
        found = self.resolver.addresses(host, sock.family)  # type: ignore[attr-defined]
        if not found:
            raise EgressDenied(f"выход запрещён: {host}:{port}")
        for ip in sorted(found, key=str):
            if not self.permits(ip, port):
                shown = f"[{ip}]" if ip.version == 6 else str(ip)
                raise EgressDenied(f"выход запрещён: {shown}:{port}")

    def hook(self, event: str, args: tuple) -> None:
        if event not in _WATCHED:
            return
        if event == "socket.connect":
            self.check_connect(args[0], args[1])
        elif event in _SEND_EVENTS:
            sock, address = args[0], args[1]
            if (address is not None and getattr(sock, "family", None) in _INET
                    and int(getattr(sock, "type", 0)) & ~_TYPE_FLAGS == socket.SOCK_STREAM):
                raise EgressDenied(f"выход запрещён: {event} на потоковом сокете (TCP Fast Open)")
        else:
            raise PermissionError(PROCESS_TEXT)


def install_egress_guard(allowed: Sequence[tuple[str, int | None]],
                         allowed_nets: Sequence[str] = ("127.0.0.0/8", "::1/128")) -> None:
    """Ставит аудит-хук; порт None — любой TCP-порт этого хоста (адрес с экземпляром, Д3а).

    Разрешено: направления allowed и сети allowed_nets на любом порту. Повторная
    установка — RuntimeError: хук не снимается. Ни DNS, ни файлов при установке.
    """
    global _installed
    for entry in allowed:
        host, port = entry
        if not isinstance(host, str):
            raise TypeError("хост направления — строка")
        if port is not None and (isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536):
            raise ValueError("порт направления — 1…65535 или None")
    guard = _Guard(allowed, allowed_nets)                   # неверная сеть — ValueError до установки
    with _install_lock:
        if _installed:
            raise RuntimeError("охрана выхода уже установлена")
        sys.addaudithook(guard.hook)
        _installed = True


def egress_guard_installed() -> bool:
    return _installed


def _directions(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "направление"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "направления"
    return "направлений"


def install_historian_guard(cfg: BdrvConfig | None) -> int:
    """Охрана с одним направлением — историан (BDRV_HOST без экземпляра, BDRV_PORT); cfg None — ни одного.

    Петля разрешена всегда и в счёт не идёт. Возвращает число направлений.
    """
    allowed: list[tuple[str, int | None]] = [] if cfg is None else [(cfg.host.split("\\")[0], cfg.port)]
    install_egress_guard(allowed)
    log.info("охрана выхода: разрешено %d %s", len(allowed), _directions(len(allowed)))
    return len(allowed)
