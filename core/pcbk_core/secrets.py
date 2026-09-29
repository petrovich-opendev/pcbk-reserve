"""Учётные данные историана — только из файла; ни пароль, ни строки файла не попадают в ошибки."""
import re
from dataclasses import dataclass, field

# KEY=VALUE, как в файле для `set -a; . файл` (Dify: /opt/dify/scripts/.bdrv.env)
_LINE = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)")
DEFAULT_PORT = 1433
DEFAULT_DATABASE = "Runtime"


def read_env_file(path: str) -> dict[str, str]:
    """Пары файла: `#` и пустые строки пропускаются, `export ` и одна пара кавычек снимаются.

    Ошибка называет файл и номер строки, но не её содержимое: там может быть пароль.
    """
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except UnicodeDecodeError:
        raise ValueError(f"{path}: файл не в UTF-8") from None
    values = {}
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.fullmatch(line)
        if m is None:
            raise ValueError(f"{path}: строка {n} — не KEY=VALUE")
        key, value = m.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


@dataclass(frozen=True)
class BdrvConfig:
    host: str
    port: int | None          # None — в адресе экземпляр (хост\ЭКЗЕМПЛЯР), порт находит pytds
    database: str
    user: str
    password: str = field(repr=False)

    @classmethod
    def from_env_file(cls, path: str) -> "BdrvConfig":
        env = read_env_file(path)
        missing = [k for k in ("BDRV_HOST", "BDRV_USER", "BDRV_PW") if not env.get(k)]
        if missing:
            raise ValueError(f"{path}: не заданы {', '.join(missing)}")
        host = env["BDRV_HOST"]
        raw_port = env.get("BDRV_PORT", "")
        if raw_port:
            try:
                port = int(raw_port)
            except ValueError:
                port = 0
            if not 0 < port < 65536:
                raise ValueError(f"{path}: BDRV_PORT — не номер порта")
        elif "\\" in host:
            port = None
        else:
            port = DEFAULT_PORT
        return cls(host, port, env.get("BDRV_DB") or DEFAULT_DATABASE, env["BDRV_USER"], env["BDRV_PW"])
