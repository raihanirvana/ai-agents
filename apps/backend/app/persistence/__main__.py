"""Local database commands: python -m app.persistence {upgrade,current,check} [--db PATH]."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import migrate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.persistence", description=__doc__)
    parser.add_argument("command", choices=("upgrade", "current", "check"))
    parser.add_argument("--db", type=Path, help="database file (default: DATABASE_PATH from the environment)")
    args = parser.parse_args(argv)
    if args.db is None:
        from app.config import DATABASE_PATH
        args.db = DATABASE_PATH

    if args.command == "upgrade":
        migrate.upgrade(args.db)
        print(f"{args.db}: at revision {migrate.current_revision(args.db)}")
        return 0
    if args.command == "current":
        print(migrate.current_revision(args.db) or "(no migrations applied)")
        return 0
    if not args.db.exists():
        print(f"{args.db}: database does not exist; run 'upgrade' first", file=sys.stderr)
        return 1
    problems = []
    current, head = migrate.current_revision(args.db), migrate.head_revision()
    if current != head:
        problems.append(f"revision {current} is not the head {head}")
    problems += [f"schema drift: {diff}" for diff in migrate.schema_drift(args.db)]
    problems += migrate.integrity_problems(args.db)
    for problem in problems:
        print(problem, file=sys.stderr)
    print("database is healthy" if not problems else f"{len(problems)} problem(s) found")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
