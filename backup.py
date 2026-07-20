"""WAL-safe backup of money.sqlite with rotation.

Uses SQLite's online backup API for a transactionally-consistent snapshot (a
plain file copy is unsafe in WAL mode), then gzips it and keeps the newest KEEP
backups. Intended to run on a schedule right after poll.py.

Run: ./.venv/bin/python backup.py
"""
from __future__ import annotations

import gzip
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import db

BACKUP_DIR = Path(__file__).parent / "backups"
KEEP = 14  # newest N gzipped snapshots to retain


def make_backup() -> Path:
    BACKUP_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snap = BACKUP_DIR / f"money-{ts}.sqlite"

    src = sqlite3.connect(db.DB_PATH)
    dst = sqlite3.connect(snap)
    try:
        with dst:
            src.backup(dst)  # consistent even while the DB is in use
    finally:
        dst.close()
        src.close()

    gz = snap.with_name(snap.name + ".gz")
    with open(snap, "rb") as f_in, gzip.open(gz, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    snap.unlink()
    return gz


def rotate() -> list[Path]:
    backups = sorted(BACKUP_DIR.glob("money-*.sqlite.gz"))
    removed = backups[:-KEEP] if len(backups) > KEEP else []
    for old in removed:
        old.unlink()
    return removed


def main() -> None:
    gz = make_backup()
    size_kb = gz.stat().st_size / 1024
    removed = rotate()
    remaining = len(sorted(BACKUP_DIR.glob("money-*.sqlite.gz")))
    print(f"backup: {gz.name}  ({size_kb:.1f} KB)")
    if removed:
        print(f"rotated out {len(removed)} old backup(s)")
    print(f"{remaining} backup(s) retained in {BACKUP_DIR}")


if __name__ == "__main__":
    main()
