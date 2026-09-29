"""Охрана выхода в процессе (Д3б-R6): растяжка по PEP 578, не песочница.

Аудит-хук ловит случайный выход нашим кодом и библиотеками: соединение не
туда и запуск процессов. От атакующего внутри процесса он не защищает — PEP
578 так и говорит. Ограничение на уровне сети для моста pcbk-egress — это
межсетевой экран хоста (правила DOCKER-USER), предложение владельцу на Д12.

Что делает хук (AF_INET и AF_INET6, сокет любого типа):
- socket.connect: адрес вне allowed и allowed_nets — EgressDenied до
  системного вызова. Имя хоста в кортеже разрешается через кэш, проверяется
  каждый его адрес; не разрешилось — отказ. Имена из allowed разрешаются так
  же, при первом соединении, а не при установке;
- socket.sendto и socket.sendmsg с адресом: на потоковом сокете — отказ
  всегда (так TCP не уйдёт через TCP Fast Open мимо connect), на прочих
  (UDP, ICMP ping, SCTP SEQPACKET, RAW) — та же проверка адреса, что у
  connect. Опрос SQL Browser у pytds (UDP 1434 на хост историана) проходит:
  порт None — любой порт этого хоста. sendmsg без адреса — обычная запись
  asyncio 3.12 в уже подключённый сокет, её хук пропускает: адрес проверил
  connect;
- subprocess.Popen, os.system, os.exec, os.posix_spawn, ctypes.dlopen —
  PermissionError.

Прочие семейства (AF_UNIX — сокеты-пары asyncio) хук пропускает. Кэш имён:
имя разрешается не чаще раза в 60 с (RESOLVE_RETRY_S), результат — и неудача
тоже — живёт до следующего разрешения.

Значениям уровня Python хук не верит: семейство и тип читает из полей
_socket.socket (подкласс со своим .family их не подменит), хост принимает
только точного типа str, bytes или bytearray, порт — только int, кортеж —
только tuple; иначе — отказ. getaddrinfo, набор событий и всё прочее, чем он
пользуется, связываются при установке: подмена глобалов этого модуля,
socket.getaddrinfo или ipaddress после установки его не выключает.

Ставится только в точках входа процесса — serve() службы и __main__
построителя: хук не снимается, а main(), build_roles и build_whitelist.main
тесты зовут в процессе pytest (F2, F3).

Обходы хука — названы, а не закрыты:
- уже загруженный ctypes и прямые системные вызовы через него;
- _posixsubprocess.fork_exec в обход subprocess; им же multiprocessing в
  режимах spawn и forkserver запускает новый интерпретатор — в нём хука нет;
- cffi (ffi.dlopen) грузит библиотеки мимо события ctypes.dlopen;
- sock.send(b, MSG_FASTOPEN) аудит-события не поднимает; без connect ядро
  отвечает EINVAL, но это заслуга ядра, не хука;
- setsockopt SCTP_SOCKOPT_CONNECTX открывает ассоциацию SCTP мимо connect;
- DNS glibc (в контейнере — через 127.0.0.11) идёт из C, событий не
  поднимает;
- нативный модуль с тома /var/lib/pcbk-core;
- связанное при установке можно подменить через __closure__ хука или gc;
- имя в кортеже connect/sendto разрешается дважды — хуком (из кэша до 60 с)
  и самим вызовом; если DNS между ними ответит иначе, проверен не тот адрес;
- на уровне сети из pcbk-egress открыт любой адрес (факт Д3а) — это закроет
  только межсетевой экран хоста на Д12.
"""
from __future__ import annotations

import _socket
import ipaddress
import logging
import socket
import sys
import threading
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .secrets import BdrvConfig

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

RESOLVE_RETRY_S = 60.0
PROCESS_TEXT = "запуск процессов через subprocess/os запрещён"
_PROCESS_EVENTS = frozenset({"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "ctypes.dlopen"})
_SEND_EVENTS = frozenset({"socket.sendto", "socket.sendmsg"})
_WATCHED = _PROCESS_EVENTS | _SEND_EVENTS | {"socket.connect"}
# тип сокета без флагов Linux
_TYPE_FLAGS = getattr(socket, "SOCK_NONBLOCK", 0) | getattr(socket, "SOCK_CLOEXEC", 0)

log = logging.getLogger("pcbk_core.egress")

_install_lock = threading.Lock()
_installed = False


class EgressDenied(PermissionError):
    """Выход вне разрешённых направлений: охрана отказала до системного вызова."""


def _make_hook(allowed: tuple[tuple[str, int | None], ...],
               nets: tuple[IPNetwork, ...]) -> Callable[[str, tuple], None]:
    """Хук и всё, чем он пользуется, — локальные имена этого вызова, то есть связаны при установке."""
    getaddrinfo = _socket.getaddrinfo
    family_of = _socket.socket.family.__get__
    type_of = _socket.socket.type.__get__
    ip_address = ipaddress.ip_address
    ipv6_type = ipaddress.IPv6Address
    monotonic = time.monotonic
    watched, send_events = _WATCHED, _SEND_EVENTS
    inet = frozenset({int(socket.AF_INET), int(socket.AF_INET6)})
    af_unspec, sock_stream = int(socket.AF_UNSPEC), int(socket.SOCK_STREAM)
    type_mask = ~_TYPE_FLAGS
    retry_s, process_text, denied = RESOLVE_RETRY_S, PROCESS_TEXT, EgressDenied
    host_types = (str, bytes)
    lock = threading.Lock()
    cache: dict[tuple[str | bytes, int], tuple[float, frozenset[IPAddress]]] = {}

    def as_ip(host: str | bytes) -> IPAddress | None:
        """Адрес-литерал; IPv4 внутри IPv6 (::ffff:a.b.c.d) — как IPv4; имя — None."""
        if type(host) is bytes:
            try:
                host = host.decode("ascii")
            except UnicodeDecodeError:
                return None
        try:
            ip = ip_address(host)
        except ValueError:
            return None
        if type(ip) is ipv6_type and ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        return ip

    def addresses(host: str | bytes, family: int) -> frozenset[IPAddress]:
        ip = as_ip(host)
        if ip is not None:
            return frozenset({ip})
        key, now = (host, family), monotonic()
        with lock:
            hit = cache.get(key)
        if hit is not None and now - hit[0] < retry_s:
            return hit[1]
        # замок не держится во время getaddrinfo: хук зовётся и изнутри неё (событие socket.getaddrinfo)
        try:
            infos = getaddrinfo(host, None, family, sock_stream)
            found = frozenset(ip for info in infos if (ip := as_ip(str(info[4][0]))) is not None)
        except (OSError, UnicodeError, ValueError, TypeError):
            found = frozenset()
        with lock:
            cache[key] = (now, found)
        return found

    def permits(ip: IPAddress, port: int) -> bool:
        if any(ip.version == net.version and ip in net for net in nets):
            return True
        return any((want is None or want == port) and ip in addresses(host, af_unspec) for host, want in allowed)

    def check(family: int, address: object) -> None:
        """Каждый адрес назначения разрешён — иначе EgressDenied."""
        if type(address) is not tuple or len(address) < 2:
            raise denied("выход запрещён: адрес не кортеж (хост, порт)")
        host, port = address[0], address[1]
        if type(host) is bytearray:
            host = bytes(host)
        if type(host) not in host_types or type(port) is not int:
            raise denied("выход запрещён: хост — не str/bytes или порт — не int")
        shown = host if type(host) is str else host.decode("ascii", "replace")
        found = addresses(host, family)
        if not found:
            raise denied(f"выход запрещён: {shown}:{port}")
        for ip in sorted(found, key=str):
            if not permits(ip, port):
                raise denied(f"выход запрещён: {f'[{ip}]' if ip.version == 6 else ip}:{port}")

    def hook(event: str, args: tuple) -> None:
        if event not in watched:
            return
        if event == "socket.connect" or event in send_events:
            sock, address = args[0], args[1]
            if address is None:             # sendmsg без адреса: сокет подключён, адрес проверил connect
                return
            try:
                family, kind = family_of(sock), type_of(sock) & type_mask
            except TypeError:
                raise denied("выход запрещён: объект события — не сокет") from None
            if family not in inet:
                return
            if event != "socket.connect" and kind == sock_stream:
                raise denied(f"выход запрещён: {event} на потоковом сокете (TCP Fast Open)")
            check(family, address)
            return
        raise PermissionError(process_text)

    return hook


def install_egress_guard(allowed: Sequence[tuple[str, int | None]],
                         allowed_nets: Sequence[str] = ("127.0.0.0/8", "::1/128")) -> None:
    """Ставит аудит-хук; порт None — любой порт этого хоста (адрес с экземпляром, Д3а).

    Разрешено: направления allowed и сети allowed_nets на любом порту. Повторная
    установка — RuntimeError: хук не снимается. Ни DNS, ни файлов при установке.
    """
    global _installed
    allowed = tuple(allowed)                # генератор иначе пустел бы после проверки
    entries: list[tuple[str, int | None]] = []
    for entry in allowed:
        host, port = entry
        if type(host) is not str:
            raise TypeError("хост направления — строка")
        if port is not None and (type(port) is not int or not 0 < port < 65536):
            raise ValueError("порт направления — 1…65535 или None")
        entries.append((host, port))
    nets = tuple(ipaddress.ip_network(n) for n in tuple(allowed_nets))   # неверная сеть — ValueError до установки
    hook = _make_hook(tuple(entries), nets)
    with _install_lock:
        if _installed:
            raise RuntimeError("охрана выхода уже установлена")
        sys.addaudithook(hook)
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
