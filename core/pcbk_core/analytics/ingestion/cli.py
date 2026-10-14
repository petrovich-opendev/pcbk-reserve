"""Командная строка офлайн-загрузки/версий корпуса (план КДМ1, Task 8, Этап 8; мастер-промпт §9 последний
абзац, §10 последний абзац). Решение контроллера: это ОТДЕЛЬНЫЙ офлайн-командный этап оператора, не маршрут
API и не код, исполняемый во время обычного вопроса — `python -m pcbk_core.analytics.ingestion <подкоманда>`.
Сеть не используется ни одной подкомандой."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from collections.abc import Sequence

from . import quarantine, versions


def _default_corpus_dir() -> str | None:
    return os.environ.get("ANALYTICS_CORPUS_DIR") or None


def _require_corpus_dir(value: str | None) -> str:
    corpus_dir = value or _default_corpus_dir()
    if not corpus_dir:
        raise SystemExit("нужен --corpus-dir или переменная окружения ANALYTICS_CORPUS_DIR")
    return corpus_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pcbk_core.analytics.ingestion", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("quarantine", help="поставить файл/архив в карантин, получить отчёт по каждому элементу")
    p.add_argument("source", help="путь к файлу/архиву")
    p.add_argument("--quarantine-dir", required=True)
    p.add_argument("--id", dest="item_id", default=None, help="идентификатор элемента (по умолчанию — по хешу)")
    p.add_argument("--scope", default=quarantine.DEFAULT_SCOPE_PREFIX)

    p = sub.add_parser("report", help="показать отчёт карантина по идентификатору")
    p.add_argument("item_id")
    p.add_argument("--quarantine-dir", required=True)

    p = sub.add_parser("build", help="собрать новую версию корпуса из каталога knowledge/ (не публикует)")
    p.add_argument("version_id")
    p.add_argument("--knowledge-dir", required=True, help="каталог с *.jsonl + seed_manifest.json")
    p.add_argument("--importer", required=True, help="путь к tools/import_seed.py пакета заказчика")
    p.add_argument("--corpus-dir", default=None)

    p = sub.add_parser("publish", help="опубликовать собранную версию (атомарная смена указателя текущей версии)")
    p.add_argument("version_id")
    p.add_argument("--approved-by", required=True, help="логин утвердившего публикацию человека")
    p.add_argument("--corpus-dir", default=None)

    p = sub.add_parser("rollback", help="откатить указатель текущей версии на уже существующую версию")
    p.add_argument("version_id")
    p.add_argument("--approved-by", required=True, help="логин утвердившего откат человека")
    p.add_argument("--corpus-dir", default=None)

    p = sub.add_parser("list", help="список версий корпуса в каталоге версий и текущая")
    p.add_argument("--corpus-dir", default=None)
    return parser


def _print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "quarantine":
            report = quarantine.quarantine_item(args.source, args.quarantine_dir, item_id=args.item_id,
                                                scope=args.scope)
            _print(report)
            return 0 if report["verdict"] == "accepted" else 1
        if args.command == "report":
            _print(quarantine.read_report(args.quarantine_dir, args.item_id))
            return 0
        if args.command == "build":
            corpus_dir = _require_corpus_dir(args.corpus_dir)
            result = versions.build_version(corpus_dir, args.version_id, args.knowledge_dir, args.importer)
            _print(dataclasses.asdict(result))
            return 0
        if args.command == "publish":
            corpus_dir = _require_corpus_dir(args.corpus_dir)
            _print(versions.publish_version(corpus_dir, args.version_id, approved_by=args.approved_by))
            return 0
        if args.command == "rollback":
            corpus_dir = _require_corpus_dir(args.corpus_dir)
            _print(versions.rollback_version(corpus_dir, args.version_id, approved_by=args.approved_by))
            return 0
        if args.command == "list":
            corpus_dir = _require_corpus_dir(args.corpus_dir)
            _print(versions.list_versions(corpus_dir))
            return 0
    except (quarantine.QuarantineError, versions.VersionError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — правка, раунд 1: любая непредвиденная ошибка — документированный
        # JSON-конверт на стдерр, не голый traceback (оператор командной строки не должен получать питоновский
        # трейсбек как единственный диагностический вывод). SystemExit (например, из _require_corpus_dir) сюда
        # не попадает — он не наследует Exception, проходит как обычный код завершения argparse-стиля.
        print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False),
              file=sys.stderr)
        return 1
    parser.error("неизвестная команда")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
