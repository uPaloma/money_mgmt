"""Read-only diagnostic: is entry_reference a safe dedup key for this bank?

Fetches all transaction pages and groups by entry_reference. For any reference
that appears more than once, it reports whether the records are IDENTICAL (true
duplicates - safe to merge) or DIFFER (distinct transactions sharing a
reference - merging them would lose data). No DB writes.

Run: ./.venv/bin/python diag_dupes.py
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

import eb_client

SESSIONS_FILE = Path(__file__).parent / "sessions.json"
MAX_PAGES = 50


def fetch_all(uid: str) -> list[dict]:
    out, cont = [], None
    for _ in range(MAX_PAGES):
        params = {"continuation_key": cont} if cont else {}
        resp = eb_client.account_transactions(uid, **params)
        out.extend(resp.get("transactions", []))
        cont = resp.get("continuation_key")
        if not cont:
            break
    return out


def main() -> None:
    sessions = json.loads(SESSIONS_FILE.read_text())
    for key, sess in sessions.items():
        for acct in sess["accounts"]:
            uid = acct["uid"]
            txns = fetch_all(uid)
            by_ref: dict = collections.defaultdict(list)
            for t in txns:
                by_ref[t.get("entry_reference")].append(t)

            repeated = {r: v for r, v in by_ref.items() if len(v) > 1}
            identical = differing = 0
            examples = []
            for ref, group in repeated.items():
                blobs = {json.dumps(t, sort_keys=True, ensure_ascii=False) for t in group}
                if len(blobs) == 1:
                    identical += 1
                else:
                    differing += 1
                    if len(examples) < 3:
                        examples.append((ref, group))

            print(f"\n### {key}  uid={uid[:8]}…")
            print(f"total fetched      : {len(txns)}")
            print(f"distinct refs      : {len(by_ref)}")
            print(f"repeated refs      : {len(repeated)}")
            print(f"  -> identical dups : {identical}  (safe to merge)")
            print(f"  -> DIFFERING dups : {differing}  (merging = DATA LOSS)")

            for ref, group in examples:
                print(f"\n  entry_reference={ref!r} has {len(group)} DIFFERING records:")
                for t in group:
                    amt = (t.get('transaction_amount') or {})
                    print(f"    amount={amt.get('amount')} {amt.get('currency')}  "
                          f"ind={t.get('credit_debit_indicator')}  "
                          f"book={t.get('booking_date')}  val={t.get('value_date')}  "
                          f"txid={t.get('transaction_id')}  "
                          f"remit={t.get('remittance_information')}")


if __name__ == "__main__":
    main()
