"""Охрана выхода (Д3б-R6): хук снять нельзя, поэтому каждый случай — в подпроцессе; точки входа — F2, F3, F14."""
import os
import re
import socket
import subprocess
import sys

import pytest

from helpers import CORE_DIR, free_port, run_python, sha, wait_http, write
from pcbk_core.egress import egress_guard_installed


def guard_py(body, a, allowed_nets="()"):
    return run_python(f"import socket, subprocess, time\n"
                      f"from pcbk_core.egress import install_egress_guard, EgressDenied\n"
                      f"install_egress_guard([('localhost', {a})], allowed_nets={allowed_nets})\n" + body)


def test_guard_blocks_other_destinations_fast(listeners):
    a, b = listeners
    out = guard_py(f"""
socket.create_connection(('127.0.0.1', {a}), 1).close(); print('OK')
for dest in (('127.0.0.1', {b}), ('192.0.2.1', 443)):
    t = time.monotonic()
    try: socket.create_connection(dest, 5); print('OPEN')
    except EgressDenied: print('DENIED', time.monotonic() - t < 0.5)
""", a)
    assert out.split() == ["OK", "DENIED", "True", "DENIED", "True"]


def test_guard_checks_hostname_tuple_in_plain_connect(listeners):   # имя приходит в хук неразрешённым
    a, b = listeners
    # имя, которого нет, CPython разрешает в getsockaddrarg до события socket.connect: gaierror раньше хука,
    # соединения нет
    out = guard_py(f"""
s = socket.socket(); s.connect(('localhost', {a})); s.close(); print('OK')
for dest in (('localhost', {b}), ('nonexistent.invalid', 443)):
    s = socket.socket()
    try: s.connect(dest); print('OPEN')
    except (EgressDenied, socket.gaierror) as e: print(type(e).__name__)
""", a)
    assert out.split() == ["OK", "EgressDenied", "gaierror"]


def test_guard_refuses_name_it_cannot_resolve(listeners):   # connect имя разрешил, хук — нет: тоже отказ
    a, _ = listeners
    # хук связывает _socket.getaddrinfo при установке — подмена до неё; connect разрешает имя сам, в C
    out = run_python(f"""
import _socket, socket
def unresolved(*args, **kwargs): raise socket.gaierror(socket.EAI_NONAME, 'нет имени')
_socket.getaddrinfo = unresolved
from pcbk_core.egress import install_egress_guard, EgressDenied
install_egress_guard([('localhost', {a})], allowed_nets=())
s = socket.socket()
try: s.connect(('localhost', {a})); print('OPEN')
except EgressDenied as e: print(e)
""")
    assert out.strip() == f"выход запрещён: localhost:{a}"


def test_guard_blocks_processes_and_fastopen(listeners):
    a, b = listeners
    out = guard_py(f"""
try: subprocess.run(['true']); print('RAN')
except PermissionError: print('BLOCKED')
s = socket.socket()
try: s.sendto(b'x', 0x20000000, ('127.0.0.1', {b})); print('SENT')   # MSG_FASTOPEN
except EgressDenied: print('DENIED')
""", a)
    assert out.split() == ["BLOCKED", "DENIED"]


def test_guard_blocks_every_process_event(listeners):
    a, _ = listeners
    out = guard_py("""
import ctypes, os                        # import ctypes под хуком на 3.12 проходит (pythonapi = None)
for name, run in (('system', lambda: os.system('true')), ('exec', lambda: os.execv('/bin/true', ['true'])),
                  ('spawn', lambda: os.posix_spawn('/bin/true', ['true'], {})),
                  ('dlopen', lambda: ctypes.CDLL('libc.so.6'))):
    try: run(); print(name, 'RAN')
    except PermissionError as e: print(name, str(e) == 'запуск процессов через subprocess/os запрещён')
""", a)
    assert out.split() == ["system", "True", "exec", "True", "spawn", "True", "dlopen", "True"]


def test_guard_lets_addressless_sendmsg_through(listeners):   # так пишет asyncio 3.12 в подключённый сокет
    a, b = listeners
    out = guard_py(f"""
s = socket.create_connection(('127.0.0.1', {a}), 1); print(s.sendmsg([b'x'])); s.close()
s = socket.socket()
try: s.sendmsg([b'x'], [], 0x20000000, ('127.0.0.1', {a})); print('SENT')      # MSG_FASTOPEN, даже куда можно
except EgressDenied: print('DENIED')
""", a)
    assert out.split() == ["1", "DENIED"]


def test_guard_checks_datagram_destinations(listeners):     # UDP: та же проверка адреса, что у connect
    a, b = listeners
    out = guard_py(f"""
u = socket.socket(type=socket.SOCK_DGRAM)
print(u.sendto(b'x', ('127.0.0.1', {a})))                                   # разрешено: localhost:a
for send in (lambda: u.sendto(b'x', ('127.0.0.1', {b})), lambda: u.sendto(b'x', ('192.0.2.1', 53)),
             lambda: u.sendmsg([b'x'], [], 0, ('192.0.2.1', 53)), lambda: u.connect(('192.0.2.1', 53))):
    try: send(); print('SENT')
    except EgressDenied: print('DENIED')
""", a)
    assert out.split() == ["1", "DENIED", "DENIED", "DENIED", "DENIED"]


@pytest.mark.parametrize("kind, proto", [
    (socket.SOCK_SEQPACKET, getattr(socket, "IPPROTO_SCTP", -1)),    # SCTP «один ко многим»
    (socket.SOCK_STREAM, getattr(socket, "IPPROTO_SCTP", -1)),       # SCTP «один к одному» — потоковый
    (socket.SOCK_DGRAM, socket.IPPROTO_ICMP),                        # ping-сокет
    (socket.SOCK_RAW, socket.IPPROTO_UDP),
])
def test_guard_checks_other_socket_types(listeners, kind, proto):
    try:
        socket.socket(socket.AF_INET, kind, proto).close()
    except OSError as e:                   # нет модуля ядра или прав (ping_group_range, CAP_NET_RAW)
        pytest.skip(f"сокет {kind!r}/{proto} недоступен: {e.strerror}")
    a, _ = listeners
    out = guard_py(f"""
s = socket.socket(socket.AF_INET, {int(kind)}, {proto}); s.settimeout(2)   # без охраны SCTP ждал бы INIT
try: s.sendto(b'x', ('192.0.2.1', 9)); print('SENT')
except EgressDenied: print('DENIED')
""", a)
    assert out.split() == ["DENIED"]


def test_pytds_browser_and_glibc_dns_under_guard():
    # SQL Browser pytds — UDP 1434 на хост историана: порт None пускает любой порт этого хоста;
    # DNS glibc идёт из C и событий сокетов не поднимает
    out = run_python("""
import socket, sys
from pytds import instance_browser_client as browser
from pcbk_core.egress import install_egress_guard, EgressDenied
install_egress_guard([('127.0.0.1', None)], allowed_nets=())
seen = []
sys.addaudithook(lambda event, args: seen.append(event) if event.startswith('socket.') else None)
for host in ('127.0.0.1', '192.0.2.1'):
    try: browser.tds7_get_instances(host, timeout=0.5); print('ANSWER')
    except EgressDenied: print('DENIED')
    except OSError: print('SENT')              # 1434 никто не слушает: отказ порта или срок
seen.clear()
try: socket.getaddrinfo('nonexistent.invalid', 443); print('RESOLVED')
except socket.gaierror: print('GAI')
print(sorted(set(seen)))
""")
    assert out.split() == ["SENT", "DENIED", "GAI", "['socket.getaddrinfo']"]


def test_guard_ignores_spoofed_python_values(listeners):     # семейство и тип — из полей _socket.socket
    a, b = listeners
    out = guard_py(f"""
class Sneaky(socket.socket):
    family = property(lambda self: socket.AF_UNIX)
    type = property(lambda self: socket.SOCK_DGRAM)
class S(str): pass
class I(int): pass
class T(tuple): pass
cases = (lambda: Sneaky().connect(('127.0.0.1', {b})),
         lambda: Sneaky().sendto(b'x', 0x20000000, ('127.0.0.1', {a})),       # потоковый, хоть и «DGRAM»
         lambda: socket.socket().connect((S('127.0.0.1'), {a})),              # куда можно, но хост — подкласс
         lambda: socket.socket().connect(('127.0.0.1', I({a}))),
         lambda: socket.socket().connect(T(('127.0.0.1', {a}))))
for case in cases:
    try: case(); print('OPEN')
    except EgressDenied: print('DENIED')
""", a)
    assert out.split() == ["DENIED"] * 5


def test_guard_survives_module_state_reassigned(listeners):   # глобалы модуля хук не выключают
    a, b = listeners
    out = guard_py(f"""
import _socket, ipaddress
import pcbk_core.egress as egress
egress._WATCHED = egress._PROCESS_EVENTS = egress._SEND_EVENTS = frozenset()
egress.EgressDenied, egress.RESOLVE_RETRY_S = Exception, 0
fake = lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 0))]
socket.getaddrinfo = _socket.getaddrinfo = fake          # «localhost» стал бы 192.0.2.1
ipaddress.ip_address = lambda text: ipaddress.IPv4Address('127.0.0.1')
for dest in (('127.0.0.1', {b}), ('192.0.2.1', {a})):   # не create_connection: он сам зовёт подменённое
    s = socket.socket(); s.settimeout(1)
    try: s.connect(dest); print('OPEN')
    except EgressDenied: print('DENIED')
    except OSError: print('TRIED')
try: subprocess.run(['true']); print('RAN')
except PermissionError: print('BLOCKED')
""", a)
    assert out.split() == ["DENIED", "DENIED", "BLOCKED"]


def test_install_takes_generator_and_refuses_second_time(listeners):
    a, _ = listeners
    out = run_python(f"""
import socket
from pcbk_core.egress import install_egress_guard, egress_guard_installed
install_egress_guard((d for d in [('localhost', {a})]), allowed_nets=(n for n in ()))
socket.create_connection(('127.0.0.1', {a}), 1).close(); print('OK')
try: install_egress_guard([])
except RuntimeError: print('AGAIN', egress_guard_installed())
""")
    assert out.split() == ["OK", "AGAIN", "True"]


# точки входа (F2, F3, F14): охрана только там; в процессе pytest хука нет
def test_pytest_process_has_no_guard():
    assert egress_guard_installed() is False     # main(), build_roles, build_whitelist.main её не ставят


# сбой в stderr: трасса без журнала или исключение, записанное журналом одной строкой (logs.OneLine)
CRASH = re.compile(r"EgressDenied|PermissionError|Traceback| \| [A-Z]\w*(?:Error|Exception|Denied)\b")


@pytest.mark.parametrize("bdrv_text, code, line", [
    ("BDRV_HOST=127.0.0.1\nBDRV_PORT={closed}\nBDRV_USER=u\nBDRV_PW=<p>\n", 200, "разрешено 1 направление"),
    (None, 503, "разрешено 0 направлений"),              # нет bdrv.env → config_error, процесс жив (I2)
])
def test_serve_installs_guard_and_stays_up(tmp_path, bdrv_text, code, line):
    port, closed = free_port(), free_port()             # на closed никто не слушает
    env = {**os.environ, "CORE_PORT": str(port), "FRESH_POLL_S": "3600",
           "BDRV_ENV_FILE": write(tmp_path, bdrv_text.format(closed=closed)) if bdrv_text else str(tmp_path / "нет.env"),
           "WHITELIST_PATH": write(tmp_path, "20FAKE_001_PV\n"),
           "TOKENS_FILE": write(tmp_path, f"ops {sha('tok')}\n"), "DB_PATH": str(tmp_path / "core.db")}
    p = subprocess.Popen([sys.executable, "-m", "pcbk_core.main"], cwd=CORE_DIR, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        got, _ = wait_http(f"http://127.0.0.1:{port}/healthz/data")   # (код, тело); ждёт слушателя до 10 с
    finally:
        p.kill()             # не terminate: поток ворот досыпает login_timeout pytds, выход ждал бы его
        err = p.communicate(timeout=10)[1]
    assert got == code and f"охрана выхода: {line}" in err
    assert not CRASH.search(err)                                         # стек под хуком поднялся (F14)


def test_crash_signal_matches_one_line_exception():
    assert CRASH.search("… ERROR pcbk_core.app: сбой | ImportError: no module @ /app/pcbk_core/x.py:3")
    assert CRASH.search("… ERROR x: сбой | EgressDenied @ /app/pcbk_core/egress.py:9")
    assert not CRASH.search("… WARNING pcbk_core.data: свежесть: нет связи с историаном (connect)")


# кэш адресов разрешённых имён (Д4а-R18): подмена _socket.getaddrinfo — до установки, хук и поток обновления
# связывают её при установке; «or.test» — разрешённое имя, allowed_nets=() — петля сама не пускается;
# слушатель — на 127.0.0.1, у 127.0.0.2 и 127.0.0.3 соединение отвергает ядро, а не охрана
ATTEMPT = """
def attempt(ip):
    s = socket.socket(); s.settimeout(2)
    try:
        s.connect((ip, {a})); return 'PASSED'
    except egress.EgressDenied:
        return 'DENIED'
    except (ConnectionRefusedError, TimeoutError):                   # охрана пустила, отказало ядро
        return 'PASSED'                                              # (или очередь слушателя полна)
    finally:
        s.close()
"""


def test_loop_thread_never_resolves(listeners):                                 # Д4а-R18
    a, _ = listeners
    out = run_python(f"""
import _socket, asyncio, socket, threading
calls = []                                                           # имя потока каждого вызова getaddrinfo
real = _socket.getaddrinfo
def fake(host, *rest, **kw):
    calls.append(threading.current_thread().name)
    return real('127.0.0.1' if host == 'or.test' else host, *rest, **kw)
_socket.getaddrinfo = fake                                           # до установки: хук связывает её при установке
from pcbk_core import egress
egress.install_egress_guard([('or.test', {a})], allowed_nets=())
egress.start_refresh()                                               # первый круг — в потоке pcbk-egress-refresh
async def main():
    s = socket.socket()
    s.connect(('127.0.0.1', {a})); s.close(); print('PASSED')        # тёплый кэш: пропуск без DNS
    try:
        socket.socket().connect(('127.0.0.2', {a}))                  # промах в потоке цикла
    except egress.EgressDenied:
        print('DENIED')
    try:                                                             # имя в кортеже в потоке цикла: C разрешил его
        socket.socket().connect(('localhost', {a}))                  # до хука, хук второй раз не разрешает — отказ
    except egress.EgressDenied:
        print('NAME-DENIED')
asyncio.run(main())
print(calls.count('pcbk-egress-refresh') >= 1, calls.count('MainThread'))
""")
    assert out.split() == ["PASSED", "DENIED", "NAME-DENIED", "True", "0"]   # в потоке цикла DNS нет


def test_address_change_keeps_old_and_takes_new(listeners):                     # смена адресов CDN
    a, _ = listeners
    out = run_python(f"""
import _socket, asyncio, socket, threading, time
calls, answer = [], ['127.0.0.1']
real = _socket.getaddrinfo
def fake(host, *rest, **kw):
    if host == 'or.test':
        calls.append(threading.current_thread().name)
        host = answer[0]
    return real(host, *rest, **kw)
_socket.getaddrinfo = fake
from pcbk_core import egress
egress.NAME_REFRESH_S, egress.NAME_KEEP_S = 0.1, 1.0                 # до установки: связываются при ней
egress.install_egress_guard([('or.test', {a})], allowed_nets=())
egress.start_refresh()
{ATTEMPT.format(a=a)}
async def until(ip, want, pause):                                    # ждёт итога want не дольше 5 с
    end = time.monotonic() + 5
    while (got := attempt(ip)) != want and time.monotonic() < end:
        await asyncio.sleep(pause)
    return got
async def main():                        # слушатель не принимает: на 127.0.0.1 — редко, очередь в 8 не забить
    print(attempt('127.0.0.2'))                                      # B ещё не видели — отказ
    answer[0] = '127.0.0.2'                                          # CDN сменил адрес: A → B
    switched = time.monotonic()
    print(await until('127.0.0.2', 'PASSED', 0.02))                  # после круга обновления B пускается
    await asyncio.sleep(max(0, switched + 0.5 - time.monotonic()))
    print(attempt('127.0.0.1'))                                      # A — до истечения NAME_KEEP_S пускается
    await asyncio.sleep(max(0, switched + 1.5 - time.monotonic()))
    print(await until('127.0.0.1', 'DENIED', 0.5), time.monotonic() - switched > 0.8)   # и уходит после
asyncio.run(main())
print(calls.count('MainThread'))
""")
    assert out.split() == ["DENIED", "PASSED", "PASSED", "DENIED", "True", "0"]


def test_dns_failure_keeps_addresses(listeners):                                # stale-if-error
    a, _ = listeners
    code = f"""
import _socket, asyncio, socket, threading
from pcbk_core.logs import setup_logging
setup_logging()
calls, broken = [], [None]
real = _socket.getaddrinfo
def fake(host, *rest, **kw):
    if host == 'or.test':
        calls.append(threading.current_thread().name)
        if broken[0] is not None:
            raise broken[0]
        host = '127.0.0.1'
    return real(host, *rest, **kw)
_socket.getaddrinfo = fake
from pcbk_core import egress
try: egress.start_refresh()
except RuntimeError: print('NOT-INSTALLED')
egress.NAME_KEEP_S, egress.NAME_REFRESH_S = 0.2, 0.05
egress.install_egress_guard([('or.test', {a})], allowed_nets=())
refresher = egress.start_refresh()                                   # первый круг — 127.0.0.1
try: egress.start_refresh()
except RuntimeError: print('AGAIN')
{ATTEMPT.format(a=a)}
async def main():
    before = len(calls)
    broken[0] = socket.gaierror(socket.EAI_AGAIN, 'сбой DNS')        # DNS не отвечает
    await asyncio.sleep(0.3)
    broken[0] = RuntimeError('секрет-в-тексте')                      # непредвиденное в круге — поток жив
    await asyncio.sleep(0.3)
    print(len(calls) - before > 5, refresher.is_alive(), refresher.name)
    print(attempt('127.0.0.1'), attempt('127.0.0.2'))                # прежний адрес живёт дольше NAME_KEEP_S
asyncio.run(main())
print(calls.count('MainThread'))
"""
    p = subprocess.run([sys.executable, "-c", code], cwd=CORE_DIR, capture_output=True, text=True, check=True)
    assert p.stdout.split() == ["NOT-INSTALLED", "AGAIN", "True", "True", "pcbk-egress-refresh",
                                "PASSED", "DENIED", "0"]
    assert p.stderr.count("RuntimeError") == 1 and "секрет" not in p.stderr   # только класс, один раз за серию


def test_worker_thread_miss_resolves_like_d3b(listeners):                        # ворота историана — как в Д3б
    a, _ = listeners
    out = run_python(f"""
import _socket, asyncio, socket, threading, time
calls, answer = [], ['127.0.0.1']
real = _socket.getaddrinfo
def fake(host, *rest, **kw):
    if host == 'or.test':
        calls.append(threading.current_thread().name)
        host = answer[0]
    return real(host, *rest, **kw)
_socket.getaddrinfo = fake
from pcbk_core import egress
egress.install_egress_guard([('or.test', {a})], allowed_nets=())
egress.start_refresh()                                               # NAME_REFRESH_S = 30: очередного круга за тест нет
{ATTEMPT.format(a=a)}
workers = lambda: sum(name.startswith('asyncio_') for name in calls)   # потоки asyncio.to_thread
async def main():
    answer[0] = '127.0.0.2'
    print(await asyncio.to_thread(attempt, '127.0.0.2'), workers())   # промах в потоке: DNS, новый адрес пущен
    print(attempt('127.0.0.2'))                                       # в множестве имени: и цикл его пускает
    print(await asyncio.to_thread(attempt, '127.0.0.3'), workers())   # повторный промах за RESOLVE_RETRY_S — без DNS
    answer[0] = '127.0.0.3'
    print(attempt('127.0.0.3'))                       # промах в потоке цикла: отказ и внеочередной круг в фоне
    end = time.monotonic() + 5                        # очередной круг — только через 30 с
    while attempt('127.0.0.3') != 'PASSED' and time.monotonic() < end:
        await asyncio.sleep(0.05)
    print(attempt('127.0.0.3'), workers())
asyncio.run(main())
print(calls.count('MainThread'))
""")
    assert out.split() == ["PASSED", "1", "PASSED", "DENIED", "1", "DENIED", "PASSED", "1", "0"]


def test_install_guard_counts_and_logs():                                        # П3
    code = ("from pcbk_core.logs import setup_logging; from pcbk_core.egress import install_guard; "
            "setup_logging(); print(install_guard([('h', 1), ('or.test', None)]))")
    p = subprocess.run([sys.executable, "-c", code], cwd=CORE_DIR, capture_output=True, text=True, check=True)
    assert p.stdout.split() == ["2"] and "охрана выхода: разрешено 2 направления" in p.stderr


def test_hits_do_not_wake_refresher(listeners):      # промах одной записи при попадании другой — не промах
    a, _ = listeners
    out = run_python(f"""
import _socket, asyncio, socket, threading, time
calls, answers = [], {{'hist.test': '127.0.0.5', 'or.test': '127.0.0.2'}}
real = _socket.getaddrinfo
def fake(host, *rest, **kw):
    if host in answers:
        calls.append(threading.current_thread().name)
        host = answers[host]
    return real(host, *rest, **kw)
_socket.getaddrinfo = fake
from pcbk_core import egress
egress.install_egress_guard([('hist.test', None), ('or.test', {a})], allowed_nets=())   # историан по имени — первым
egress.start_refresh()                                               # круг: по вызову на имя, очередной — через 30 с
{ATTEMPT.format(a=a)}
async def main():
    print(sorted({{attempt('127.0.0.2') for _ in range(50)}}))       # OpenRouter: запись историана промахивается
    await asyncio.sleep(1.5)                                         # внеочередной круг шёл бы через 1 с
    print(len(calls))
    print(attempt('127.0.0.3'))                                      # настоящий промах будит поток
    end = time.monotonic() + 5
    while len(calls) == 2 and time.monotonic() < end:
        await asyncio.sleep(0.05)
    print(len(calls) > 2)
asyncio.run(main())
print(calls.count('MainThread'))
""")
    assert out.split() == ["['PASSED']", "2", "DENIED", "True", "0"]


def test_start_refresh_retries_after_failed_start():  # нет потоков (pids_limit) — повторный вызов возможен
    out = run_python("""
import threading
from pcbk_core import egress
egress.install_egress_guard([('127.0.0.1', None)], allowed_nets=())
start = threading.Thread.start
def broken(self): raise RuntimeError('нет потоков')
threading.Thread.start = broken
try: egress.start_refresh()
except RuntimeError as e: print(str(e) == 'нет потоков')
threading.Thread.start = start
thread = egress.start_refresh()
print(thread.is_alive(), thread.name)
try: egress.start_refresh()
except RuntimeError as e: print('AGAIN')
""")
    assert out.split() == ["True", "True", "pcbk-egress-refresh", "AGAIN"]
