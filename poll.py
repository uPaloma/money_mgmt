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
# Ask for a wide window by default. With no date_from most ASPSPs return only a
# short recent slice (often 90 days), which silently truncates history for some
# accounts while others look complete.
DEFAULT_DAYS = 730


def _txn_date(t: dict) -> str:
    return t.get("booking_date") or t.get("value_date") or t.get("transaction_date") or ""


def poll_account(conn, aspsp: dict, acct: dict, base_params: dict) -> dict:
    ak = db.upsert_account(conn, aspsp, acct)
    uid = acct["uid"]

    balances = eb_client.account_balances(uid).get("balances", [])
    n_bal = db.store_balances(conn, ak, balances)

    all_txns = []
    cont = None
    pages = 0
    for _ in range(MAX_PAGES):
        params = dict(base_params)
        if cont:
            params["continuation_key"] = cont
        resp = eb_client.account_transactions(uid, **params)
        all_txns.extend(resp.get("transactions", []))
        pages += 1
        cont = resp.get("continuation_key")
        if not cont:
            break
    new, updated = db.store_transactions(conn, ak, all_txns)
    conn.commit()
    dates = sorted(d for d in (_txn_date(t) for t in all_txns) if d)
    return {"balances": n_bal, "new": new, "updated": updated,
            "pages": pages, "truncated": bool(cont),
            "lo": dates[0] if dates else None, "hi": dates[-1] if dates else None}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=DEFAULT_DAYS,
                   help=f"Fetch transactions from the last N days (default {DEFAULT_DAYS}). "
                        "0 sends no date_from and lets the bank pick -- which is "
                        "how accounts end up with only the last ~90 days.")
    p.add_argument("--since", help="Explicit start date YYYY-MM-DD (overrides --days).")
    args = p.parse_args()

    base_params = {}
    if args.since:
        base_params["date_from"] = args.since
    elif args.days:
        base_params["date_from"] = (date.today() - timedelta(days=args.days)).isoformat()
    print(f"date_from={base_params.get('date_from', '(bank default)')}\n")

    sessions = json.loads(SESSIONS_FILE.read_text())
    conn = db.connect()
    try:
        for key, sess in sessions.items():
            aspsp = sess.get("aspsp", {})
            for acct in sess["accounts"]:
                r = poll_account(conn, aspsp, acct, base_params)
                # Print the span the bank actually returned: if it starts well
                # after date_from, that account is capped and the dashboard
                # totals for it will never reconcile with its balance.
                span = f"{r['lo']} -> {r['hi']}" if r["lo"] else "no transactions"
                warn = ""
                want = base_params.get("date_from")
                if want and r["lo"] and r["lo"] > want:
                    warn = f"  <-- bank returned nothing before {r['lo']} (asked {want})"
                if r["truncated"]:
                    warn += f"  <-- hit MAX_PAGES={MAX_PAGES}, history INCOMPLETE"
                print(f"{key}  uid={acct['uid'][:8]}…  "
                      f"+{r['balances']} balances | {r['new']} new txns, "
                      f"{r['updated']} updated | {span}{warn}")
        result = categorize.run(conn)
        print(f"categorized {result['categorized']} transactions "
              f"into {len(result['distribution'])} categories")
    finally:
        conn.close()
    print(f"\nDB: {db.DB_PATH}")


if __name__ == "__main__":
    main()
