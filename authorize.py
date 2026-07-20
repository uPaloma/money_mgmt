"""Authorize a bank connection and save the resulting session.

Enable Banking's data endpoints (balances, transactions) all
require a `session_id`, and the only way to obtain one is the PSD2 consent flow
below. Linking accounts in the Control Panel activates the app and whitelists
accounts, but does NOT create a session. You run this once per consent window
(~90-180 days); the poller then reuses the saved session_id until it expires.

Manual (no-open-ports) flow, suited to an SSH-only server:

  1. Run this with the bank's ASPSP name + country (from test_auth.py output).
  2. It prints a URL. Open that in a browser on your laptop and log in / consent.
  3. The bank redirects to your whitelisted redirect URL with a `?code=...`
     (the page itself won't load - that's fine). Copy the whole redirected URL
     or just the code value.
  4. Paste it back here. The session id + linked accounts are saved to
     sessions.json for the poller to use.

Usage:
  ./.venv/bin/python authorize.py "Bank Norwegian" NO
  ./.venv/bin/python authorize.py "Erste Bank und Sparkasse" AT --days 180
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import eb_client

SESSIONS_FILE = Path(__file__).parent / "sessions.json"


def _extract_code(pasted: str) -> str:
    pasted = pasted.strip()
    if pasted.startswith("http"):
        qs = parse_qs(urlparse(pasted).query)
        if "code" not in qs:
            raise SystemExit("No `code` parameter found in that URL.")
        return qs["code"][0]
    return pasted  # assume they pasted the bare code


def _account_ids(session: dict) -> list[str]:
    """Stable per-account identities (survive re-auth). Sorted for a stable key."""
    ids = [a.get("identification_hash") or a.get("uid") or ""
           for a in session.get("accounts", [])]
    return sorted(i for i in ids if i)


def _save_session(session: dict, aspsp: dict) -> None:
    """Key sessions by the accounts they contain, not by bank name, so two
    separate logins at the same bank both persist. A new consent supersedes any
    stored session that shares an account (a refresh), leaving others intact."""
    sessions = {}
    if SESSIONS_FILE.exists():
        sessions = json.loads(SESSIONS_FILE.read_text())

    ids = _account_ids(session)
    new_ids = set(ids)
    # Drop any prior session overlapping these accounts (supersede on refresh).
    sessions = {k: v for k, v in sessions.items()
                if not (new_ids & set(_account_ids(v)))}

    short = hashlib.sha256("|".join(ids).encode()).hexdigest()[:6] if ids else "noacct"
    key = f"{aspsp['name']} ({aspsp['country']}) · {short}"
    sessions[key] = {
        "session_id": session.get("session_id"),
        "accounts": session.get("accounts", []),
        "aspsp": aspsp,
        "authorized_at": datetime.now(timezone.utc).isoformat(),
    }
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2))
    print(f"\nSaved session for {key} -> {SESSIONS_FILE}")


def main() -> None:
    p = argparse.ArgumentParser(description="Authorize a bank via Enable Banking.")
    p.add_argument("bank_name", help="Exact ASPSP name from test_auth.py")
    p.add_argument("country", help="Two-letter country code, e.g. NO, AT")
    p.add_argument("--days", type=int, default=90,
                   help="Consent validity in days (bank max often 90-180).")
    args = p.parse_args()

    redirect_url = os.environ.get("EB_REDIRECT_URL", "https://localhost/callback")
    valid_until = (datetime.now(timezone.utc) + timedelta(days=args.days)) \
        .replace(microsecond=0).isoformat()

    print(f"redirect: {redirect_url} | valid until: {valid_until}")

    auth = eb_client.start_auth(
        bank_name=args.bank_name,
        country=args.country,
        redirect_url=redirect_url,
        valid_until_iso=valid_until,
    )
    print("\n1) Open this URL in your browser and complete the bank login/consent:\n")
    print("   " + auth["url"])
    print("\n2) After consent the bank redirects to your redirect_url with ?code=...")
    pasted = input("\nPaste the full redirected URL (or just the code): ")

    code = _extract_code(pasted)
    session = eb_client.create_session(code)

    accounts = session.get("accounts", [])
    print(f"\nSession created: {session.get('session_id')}")
    print(f"Linked {len(accounts)} account(s):")
    for a in accounts:
        # account shape varies by bank; print whatever identifiers exist
        ident = a.get("identification_hash") or a.get("uid") or a.get("id") or a
        print(f"  - {ident}")

    _save_session(session, {"name": args.bank_name, "country": args.country})


if __name__ == "__main__":
    main()
