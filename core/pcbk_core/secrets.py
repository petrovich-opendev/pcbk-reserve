"""Учётные данные историана и хеши токенов вызывающих — только из файлов; ни пароль, ни строки файлов
не попадают в ошибки."""
import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

# KEY=VALUE, как в файле для `set -a; . файл` (Dify: /opt/dify/scripts/.bdrv.env)
_LINE = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)")
DEFAULT_PORT = 1433
DEFAULT_DATABASE = "Runtime"
# core-tokens: «<id> <sha256 hex>»; id попадает в события и журнал, поэтому набор узкий
TOKEN_ID = re.compile(r"[a-z0-9-]{1,32}")
_SHA256_HEX = re.compile(r"[0-9a-fA-F]{64}")


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


class TokenTable:
    """Вызывающие по токену: id → sha256 токена; самих токенов служба не хранит.

    Сверка — hmac.compare_digest со всеми записями, без раннего выхода. Пустая
    таблица (empty()) не пускает никого: всем 401 (F18).
    """

    def __init__(self, hashes: Mapping[str, str]):
        table: dict[str, bytes] = {}
        for caller_id, digest in hashes.items():
            if not (isinstance(caller_id, str) and TOKEN_ID.fullmatch(caller_id)):
                raise ValueError("id вызывающего — [a-z0-9-]{1,32}")
            if not (isinstance(digest, str) and _SHA256_HEX.fullmatch(digest)):
                raise ValueError("хеш токена — 64 шестнадцатеричных знака sha256")
            table[caller_id] = bytes.fromhex(digest)
        self._hashes = table
        self.ids: frozenset[str] = frozenset(table)

    @classmethod
    def empty(cls) -> "TokenTable":
        return cls({})

    @classmethod
    def from_file(cls, path: str) -> "TokenTable":
        """Строки «<id> <sha256 hex>»; `#` и пустые строки пропускаются.

        Ошибка — OSError или ValueError с именем файла и номером строки, без её содержимого.
        """
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except UnicodeDecodeError:
            raise ValueError(f"{path}: файл не в UTF-8") from None
        hashes: dict[str, str] = {}
        for n, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if not (len(parts) == 2 and TOKEN_ID.fullmatch(parts[0]) and _SHA256_HEX.fullmatch(parts[1])):
                raise ValueError(f"{path}: строка {n} — не «id sha256»")
            if parts[0] in hashes:
                raise ValueError(f"{path}: строка {n} — id повторяется")
            hashes[parts[0]] = parts[1]
        return cls(hashes)

    def caller(self, bearer: str | None) -> str | None:
        """id вызывающего по токену; нет токена или он чужой — None."""
        if not bearer:
            return None
        try:
            digest = hashlib.sha256(bearer.encode("utf-8")).digest()
        except UnicodeEncodeError:          # одиночный суррогат: такого токена нет ни у кого
            return None
        found = None
        for caller_id, expected in self._hashes.items():
            if hmac.compare_digest(digest, expected):
                found = caller_id
        return found

    def __repr__(self) -> str:
        return f"TokenTable(ids={sorted(self.ids)})"


# ключ OpenRouter длиннее не бывает; больше — не ключ (файл перепутан), читать его целиком незачем
KEY_FILE_MAX_BYTES = 4096


class KeyFileError(Exception):
    """Файл ключа есть, но не читается (права, каталог, не UTF-8): текст — имя файла и причина, без ключа."""


def read_key_file(path: str) -> str | None:
    """Ключ из файла: пробелы, переводы строк и BOM по краям срезаются; нет файла или пусто — None.

    Прочая ошибка чтения — KeyFileError с именем файла. Ни текста ключа, ни его байтов в исключении нет:
    исключения ввода-вывода и декодирования к нему не цепляются.
    """
    reason = None
    try:
        with open(path, "rb") as f:
            raw = f.read(KEY_FILE_MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        reason = exc.strerror or "ошибка чтения"
    else:
        if len(raw) > KEY_FILE_MAX_BYTES:
            reason = f"больше {KEY_FILE_MAX_BYTES} байт — это не ключ"
        else:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                reason = "файл не в UTF-8"
    if reason is not None:
        raise KeyFileError(f"{path}: {reason}")
    key = text.strip().strip("﻿").strip()
    return key or None


def key_fingerprint(key: str) -> str:
    """Первые 12 знаков sha256 ключа: точка отсчёта расхода в llm_meta и признак смены ключа (Д4а-R13)."""
    return hashlib.sha256(key.encode("utf-8", "surrogatepass")).hexdigest()[:12]
