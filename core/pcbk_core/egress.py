"""Охрана выхода в процессе (Д3б-R6): растяжка по PEP 578, не песочница.

Аудит-хук ловит случайный выход нашим кодом и библиотеками: соединение не
туда и запуск процессов. От атакующего внутри процесса он не защищает — PEP
578 так и говорит. Ограничение на уровне сети для моста pcbk-egress — это
межсетевой экран хоста (правила DOCKER-USER), предложение владельцу на Д12.

Что делает хук (AF_INET и AF_INET6, сокет любого типа):
- socket.connect: адрес вне allowed и allowed_nets — EgressDenied до
  системного вызова. Имя хоста в самом кортеже: в потоке цикла событий —
  отказ без DNS (asyncio передаёт в connect уже разрешённый адрес); в прочих
  потоках оно разрешается через кэш на RESOLVE_RETRY_S = 60 с на любую
  запись, неудача тоже, и проверяется каждый его адрес; не разрешилось —
  отказ. Имена из allowed — по кэшу имён, ниже;
- socket.sendto и socket.sendmsg с адресом: на потоковом сокете — отказ
  всегда (так TCP не уйдёт через TCP Fast Open мимо connect), на прочих
  (UDP, ICMP ping, SCTP SEQPACKET, RAW) — та же проверка адреса, что у
  connect. Опрос SQL Browser у pytds (UDP 1434 на хост историана) проходит:
  порт None — любой порт этого хоста. sendmsg без адреса — обычная запись
  asyncio 3.12 в уже подключённый сокет, её хук пропускает: адрес проверил
  connect;
- subprocess.Popen, os.system, os.exec, os.posix_spawn, ctypes.dlopen —
  PermissionError.

Прочие семейства (AF_UNIX — сокеты-пары asyncio) хук пропускает.

Кэш имён (Д4а-R18). Имена из allowed, которые не IP-литералы, хук сверяет с
кэшем «имя → {адрес: monotonic последней встречи}»; адреса имени — все ключи.
Удачное разрешение добавляет найденные адреса со свежей меткой и выкидывает
адреса старше NAME_KEEP_S = 1800 с; неудача не меняет ничего (stale-if-error:
пока DNS не отвечает, прежние адреса живут). Так смена адресов CDN не даёт
ложного отказа, но адрес, который имя отдавало за последние 30 мин, пускается
и после того, как имя на него указывать перестало. Кэш наполняет поток-демон
pcbk-egress-refresh (start_refresh): сразу и дальше раз в NAME_REFRESH_S =
30 с. В потоке, где запущен цикл событий asyncio, хук DNS не делает никогда:
промах — отказ и внеочередной круг обновления (не чаще раза в секунду). В
прочих потоках (ворота историана, to_thread) промах, как в Д3б, разрешается
синхронно, не чаще раза в RESOLVE_RETRY_S на имя, и найденное входит в кэш.
Поток разрешает только имена из allowed; при установке — ни DNS, ни файлов,
ни потоков.

Значениям уровня Python хук не верит: семейство и тип читает из полей
_socket.socket (подкласс со своим .family их не подменит), хост принимает
только точного типа str, bytes или bytearray, порт — только int, кортеж —
только tuple; иначе — отказ. getaddrinfo, asyncio._get_running_loop, набор
событий, сроки NAME_* и всё прочее, чем пользуются хук и поток обновления,
связываются при установке: подмена глобалов этого модуля, socket.getaddrinfo,
ipaddress или asyncio после установки его не выключает.

Ставится только в точках входа процесса — serve() службы (там же, после
установки, start_refresh) и __main__ построителя: хук не снимается, а main(),
build_roles и build_whitelist.main тесты зовут в процессе pytest (F2, F3).

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
- связанное при установке можно подменить через __closure__ хука или тела
  потока обновления (до start_refresh — _refresher, после — поток) или gc;
- вне потока цикла событий имя в кортеже connect/sendto разрешается дважды —
  хуком (из кэша до 60 с) и самим вызовом; если DNS между ними ответит иначе,
  проверен не тот адрес. В потоке цикла событий имя в кортеже — отказ;
- на уровне сети из pcbk-egress открыт любой адрес (факт Д3а) — это закроет
  только межсетевой экран хоста на Д12.
"""
from __future__ import annotations

import _socket
import asyncio
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
NAME_KEEP_S = 1800.0        # адрес разрешённого имени живёт 30 мин с последней встречи (Д4а-R18)
NAME_REFRESH_S = 30.0       # круг потока обновления
FIRST_WAIT_S = 5.0          # start_refresh ждёт первый круг не дольше
_REFRESH_GAP_S = 1.0        # внеочередные круги — не чаще раза в min(это, NAME_REFRESH_S)
PROCESS_TEXT = "запуск процессов через subprocess/os запрещён"
_PROCESS_EVENTS = frozenset({"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "ctypes.dlopen"})
_SEND_EVENTS = frozenset({"socket.sendto", "socket.sendmsg"})
_WATCHED = _PROCESS_EVENTS | _SEND_EVENTS | {"socket.connect"}
# тип сокета без флагов Linux
_TYPE_FLAGS = getattr(socket, "SOCK_NONBLOCK", 0) | getattr(socket, "SOCK_CLOEXEC", 0)

log = logging.getLogger("pcbk_core.egress")

_install_lock = threading.Lock()
_installed = False
_refresher: Callable[[threading.Event], None] | None = None   # тело потока обновления: от установки до start_refresh
_refreshing = False


class EgressDenied(PermissionError):
    """Выход вне разрешённых направлений: охрана отказала до системного вызова."""


def _make_hook(allowed: tuple[tuple[str, int | None], ...], nets: tuple[IPNetwork, ...],
               ) -> tuple[Callable[[str, tuple], None], Callable[[threading.Event], None]]:
    """Хук и тело потока обновления; всё, чем они пользуются, — локальные имена этого вызова, то есть связаны
    при установке. Кэш имён живёт только здесь."""
    getaddrinfo = _socket.getaddrinfo
    running_loop = asyncio._get_running_loop
    family_of = _socket.socket.family.__get__
    type_of = _socket.socket.type.__get__
    ip_address = ipaddress.ip_address
    ipv6_type = ipaddress.IPv6Address
    monotonic, sleep, warn = time.monotonic, time.sleep, log.warning
    watched, send_events = _WATCHED, _SEND_EVENTS
    inet = frozenset({int(socket.AF_INET), int(socket.AF_INET6)})
    af_unspec, sock_stream = int(socket.AF_UNSPEC), int(socket.SOCK_STREAM)
    type_mask = ~_TYPE_FLAGS
    retry_s, process_text, denied = RESOLVE_RETRY_S, PROCESS_TEXT, EgressDenied
    keep_s, refresh_s = NAME_KEEP_S, NAME_REFRESH_S
    gap_s = min(_REFRESH_GAP_S, refresh_s)
    host_types = (str, bytes)
    lock = threading.Lock()
    wake = threading.Event()                # промах в потоке цикла: круг обновления вне очереди
    cache: dict[tuple[str | bytes, int], tuple[float, frozenset[IPAddress]]] = {}   # имена в кортеже адреса
    seen: dict[str, dict[IPAddress, float]] = {}    # разрешённые имена: адрес → monotonic последней встречи
    tried: dict[str, float] = {}            # разрешённые имена: последнее синхронное разрешение вне цикла

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

    def resolve(host: str | bytes, family: int) -> frozenset[IPAddress]:
        """Адреса имени; не разрешилось — пусто. Замок не держится: хук зовётся и изнутри getaddrinfo
        (событие socket.getaddrinfo)."""
        try:
            infos = getaddrinfo(host, None, family, sock_stream)
            return frozenset(ip for info in infos if (ip := as_ip(str(info[4][0]))) is not None)
        except (OSError, UnicodeError, ValueError, TypeError):
            return frozenset()

    def addresses(host: str | bytes, family: int) -> frozenset[IPAddress]:
        """Хост из кортежа адреса: литерал — сам, имя — через кэш на retry_s, неудача тоже кэшируется."""
        ip = as_ip(host)
        if ip is not None:
            return frozenset({ip})
        key, now = (host, family), monotonic()
        with lock:
            hit = cache.get(key)
        if hit is not None and now - hit[0] < retry_s:
            return hit[1]
        found = resolve(host, family)
        with lock:
            cache[key] = (now, found)
        return found

    def learn(name: str, found: frozenset[IPAddress]) -> None:
        """Удача — найденные адреса со свежей меткой, старше keep_s — вон; неудача не меняет ничего."""
        if not found:
            return
        now = monotonic()
        with lock:
            book = seen[name]
            for ip in found:
                book[ip] = now
            for ip in [ip for ip, at in book.items() if now - at > keep_s]:
                del book[ip]

    def knows(name: str, ip: IPAddress) -> bool:
        with lock:
            return ip in seen[name]

    def resolve_now(name: str) -> None:
        """Промах вне потока цикла — как в Д3б: синхронно, не чаще раза в retry_s на имя."""
        now = monotonic()
        with lock:
            last = tried.get(name)
            if last is not None and now - last < retry_s:
                return
            tried[name] = now
        learn(name, resolve(name, af_unspec))

    def permits(ip: IPAddress, port: int) -> bool:
        if any(ip.version == net.version and ip in net for net in nets):
            return True
        in_loop = running_loop() is not None
        for host, want, literal in entries:
            if want is not None and want != port:
                continue
            if literal is not None:
                if ip == literal:
                    return True
                continue
            if knows(host, ip):
                return True
            if in_loop:                     # DNS в потоке цикла нет никогда: отказ, круг — в фоне вне очереди
                wake.set()
                continue
            resolve_now(host)
            if knows(host, ip):
                return True
        return False

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
        if running_loop() is not None and as_ip(host) is None:
            raise denied(f"выход запрещён: {shown}:{port} — имя в адресе в потоке цикла событий")
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

    def refresher(first: threading.Event) -> None:
        """Тело потока pcbk-egress-refresh: круг сразу, дальше раз в refresh_s или по сигналу wake."""
        failing: str | None = None
        while True:
            begun = monotonic()
            try:
                for name in names:
                    learn(name, resolve(name, af_unspec))
            except Exception as e:          # поток не умирает; в журнал — только класс, раз за серию
                if type(e).__name__ != failing:
                    failing = type(e).__name__
                    warn("охрана выхода: круг обновления адресов не удался (%s)", failing)
            else:
                failing = None
            first.set()
            wake.wait(refresh_s)
            wake.clear()                    # сигнал после clear даст ещё круг: этот начнётся уже после него
            rest = begun + gap_s - monotonic()
            if rest > 0:
                sleep(rest)

    entries = tuple((host, want, as_ip(host)) for host, want in allowed)
    for host, _, literal in entries:
        if literal is None:
            seen.setdefault(host, {})
    names = tuple(seen)
    return hook, refresher


def install_egress_guard(allowed: Sequence[tuple[str, int | None]],
                         allowed_nets: Sequence[str] = ("127.0.0.0/8", "::1/128")) -> None:
    """Ставит аудит-хук; порт None — любой порт этого хоста (адрес с экземпляром, Д3а).

    Разрешено: направления allowed и сети allowed_nets на любом порту. Повторная
    установка — RuntimeError: хук не снимается. Ни DNS, ни файлов, ни потоков при
    установке; адреса имён из allowed держит поток start_refresh (Д4а-R18).
    """
    global _installed, _refresher
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
    hook, refresher = _make_hook(tuple(entries), nets)
    with _install_lock:
        if _installed:
            raise RuntimeError("охрана выхода уже установлена")
        sys.addaudithook(hook)
        _installed = True
        _refresher = refresher


def egress_guard_installed() -> bool:
    return _installed


def start_refresh(first_wait_s: float = FIRST_WAIT_S) -> threading.Thread:
    """Поток-демон pcbk-egress-refresh: разрешает имена из allowed установленного хука сразу и дальше раз в
    NAME_REFRESH_S или по промаху в потоке цикла событий (Д4а-R18).

    Ждёт первый круг не дольше first_wait_s и отдаёт поток. Без установленной охраны и при повторном
    вызове — RuntimeError.
    """
    global _refresher, _refreshing
    with _install_lock:
        if _refreshing:
            raise RuntimeError("поток обновления адресов уже запущен")
        if _refresher is None:
            raise RuntimeError("охрана выхода не установлена")
        body, _refresher, _refreshing = _refresher, None, True   # дальше тело держит только поток
    first = threading.Event()
    thread = threading.Thread(target=body, args=(first,), name="pcbk-egress-refresh", daemon=True)
    thread.start()
    first.wait(first_wait_s)
    return thread


def _directions(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "направление"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "направления"
    return "направлений"


def install_guard(targets: Sequence[tuple[str, int | None]]) -> int:
    """install_egress_guard(targets) и строка журнала «охрана выхода: разрешено N направлений»; возвращает N.

    Петля разрешена всегда и в счёт не идёт.
    """
    targets = tuple(targets)
    install_egress_guard(targets)
    log.info("охрана выхода: разрешено %d %s", len(targets), _directions(len(targets)))
    return len(targets)


def install_historian_guard(cfg: BdrvConfig | None) -> int:
    """Охрана с одним направлением — историан (BDRV_HOST без экземпляра, BDRV_PORT); cfg None — ни одного.

    Так её ставит построитель белого списка (guard=True). Возвращает число направлений.
    """
    return install_guard([] if cfg is None else [(cfg.host.split("\\")[0], cfg.port)])
