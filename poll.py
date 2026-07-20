"""Poll all authorized sessions and store balances + transactions into SQLite.

For each account in sessions.json it: upserts the account, stores current balances, then pages
through transactions (following continuation_key) and upserts them.

Rate limits: real banks may cap calls (~4/account/day). Each account here costs
1 balances call + a few transaction pages, so a couple of polls per day is safe;
don't run this on a tight loop against production.

Usage:
  ./.venv/bin/python poll.py                 # everything, full history the bank offers
  ./.venv/bin/python poll.py --days 90       # limit transactions by date
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import categorize
import db
import eb_client

SESSIONS_FILE = Path(__file__).parent / "sessions.json"
MAX_PAGES = 50  # safety cap against a misbehaving continuation_key


def poll_account(conn, aspsp: dict, acct: dict, base_params: dict) -> tuple[int, int, int]:
    ak = db.upsert_account(conn, aspsp, acct)
    uid = acct["uid"]

    balances = eb_client.account_balances(uid).get("balances", [])
    n_bal = db.store_balances(conn, ak, balances)

    all_txns = []
    cont = None
    for _ in range(MAX_PAGES):
        params = dict(base_params)
        if cont:
            params["continuation_key"] = cont
        resp = eb_client.account_transactions(uid, **params)
        all_txns.extend(resp.get("transactions", []))
        cont = resp.get("continuation_key")
        if not cont:
            break
    new, updated = db.store_transactions(conn, ak, all_txns)
    conn.commit()
    return n_bal, new, updated


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=None,
                   help="Only fetch transactions from the last N days.")
    args = p.parse_args()

    base_params = {}
    if args.days:
        base_params["date_from"] = (date.today() - timedelta(days=args.days)).isoformat()

    sessions = json.loads(SESSIONS_FILE.read_text())
    conn = db.connect()
    try:
        for key, sess in sessions.items():
            aspsp = sess.get("aspsp", {})
            for acct in sess["accounts"]:
                n_bal, new, updated = poll_account(conn, aspsp, acct, base_params)
                print(f"{key}  uid={acct['uid'][:8]}…  "
                      f"+{n_bal} balances | {new} new txns, {updated} updated")
        result = categorize.run(conn)
        print(f"categorized {result['categorized']} transactions "
              f"into {len(result['distribution'])} categories")
    finally:
        conn.close()
    print(f"\nDB: {db.DB_PATH}")


if __name__ == "__main__":
    main()
