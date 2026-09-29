"""Охрана выхода (Д3б-R6): хук снять нельзя, поэтому каждый случай — в подпроцессе; точки входа — F2, F3, F14."""
import os
import re
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
    out = guard_py(f"""
def unresolved(*args, **kwargs): raise socket.gaierror(socket.EAI_NONAME, 'нет имени')
socket.getaddrinfo = unresolved          # хук разрешает через socket.getaddrinfo, connect — сам, в C
s = socket.socket()
try: s.connect(('localhost', {a})); print('OPEN')
except EgressDenied as e: print(e)
""", a)
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
try: s.sendmsg([b'x'], [], 0x20000000, ('127.0.0.1', {b})); print('SENT')      # MSG_FASTOPEN с адресом
except EgressDenied: print('DENIED')
u = socket.socket(type=socket.SOCK_DGRAM)          # датаграммы хук не трогает (обход, названный в R6)
print(u.sendto(b'x', ('127.0.0.1', {b})))
""", a)
    assert out.split() == ["1", "DENIED", "1"]


# точки входа (F2, F3, F14): охрана только там; в процессе pytest хука нет
def test_pytest_process_has_no_guard():
    assert egress_guard_installed() is False     # main(), build_roles, build_whitelist.main её не ставят


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
    assert not re.search(r"EgressDenied|PermissionError|Traceback", err)      # стек под хуком поднялся (F14)
