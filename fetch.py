"""Fetch balances + transactions for accounts saved in sessions.json.

WHY THIS EXISTS (for now): before designing the SQLite schema we need to see the
real shape of a transaction record - which field(s) uniquely identify a
transaction - so the poller can dedup correctly. This prints raw JSON for
inspection. It later becomes the read half of the poller.

Usage:
  ./.venv/bin/python fetch.py                 # all sessions, first page of txns
  ./.venv/bin/python fetch.py --days 30       # limit transactions by date
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import eb_client

SESSIONS_FILE = Path(__file__).parent / "sessions.json"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=None,
                   help="Only fetch transactions from the last N days.")
    args = p.parse_args()

    sessions = json.loads(SESSIONS_FILE.read_text())
    params = {}
    if args.days:
        params["date_from"] = (date.today() - timedelta(days=args.days)).isoformat()

    for key, sess in sessions.items():
        print(f"\n########## {key} ##########")
        for acct in sess["accounts"]:
            uid = acct["uid"]
            print(f"\n--- account uid={uid}  ({acct.get('currency')}) ---")

            balances = eb_client.account_balances(uid)
            print("\nBALANCES:")
            print(json.dumps(balances, indent=2, ensure_ascii=False))

            txns = eb_client.account_transactions(uid, **params)
            tx_list = txns.get("transactions", [])
            print(f"\nTRANSACTIONS: {len(tx_list)} on first page"
                  f"  (continuation_key={txns.get('continuation_key')})")
            # Print up to 3 full transactions so we can see every field.
            for t in tx_list[:3]:
                print(json.dumps(t, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
