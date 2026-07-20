"""Minimal Enable Banking API client.

Auth model: you sign a short-lived RS256 JWT with your RSA private key; the
`kid` header is your Application ID (registered in the Enable Banking Control
Panel, where the matching public key is stored). Every request carries this JWT
as a Bearer token.

Docs: https://enablebanking.com/docs/api/
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import jwt
import requests

BASE_URL = "https://api.enablebanking.com"

# Config comes from environment (see .env.example). Kept out of source.
APP_ID = os.environ.get("EB_APP_ID", "").strip()
PRIVATE_KEY_PATH = os.environ.get(
    "EB_PRIVATE_KEY", str(Path(__file__).parent / "secrets" / "eb_private.pem")
)


def _private_key() -> str:
    return Path(PRIVATE_KEY_PATH).read_text()


def build_jwt(valid_seconds: int = 3600) -> str:
    """Create a signed JWT for API auth. Valid one hour by default."""
    if not APP_ID:
        raise RuntimeError("EB_APP_ID is not set (see .env.example)")
    now = int(time.time())
    body = {
        "iss": "enablebanking.com",
        "aud": "api.enablebanking.com",
        "iat": now,
        "exp": now + valid_seconds,
    }
    return jwt.encode(
        body,
        _private_key(),
        algorithm="RS256",
        headers={"typ": "JWT", "alg": "RS256", "kid": APP_ID},
    )


def _headers() -> dict:
    return {"Authorization": f"Bearer {build_jwt()}"}


def get(path: str, **params) -> dict:
    r = requests.get(f"{BASE_URL}{path}", headers=_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def post(path: str, json: dict) -> dict:
    r = requests.post(f"{BASE_URL}{path}", headers=_headers(), json=json, timeout=30)
    r.raise_for_status()
    return r.json()


# --- Convenience wrappers -------------------------------------------------

def list_aspsps(country: str) -> list[dict]:
    """List available banks (ASPSPs) for a country code, e.g. 'NO', 'AT'."""
    return get("/aspsps", country=country).get("aspsps", [])


def start_auth(bank_name: str, country: str, redirect_url: str,
               valid_until_iso: str, psu_type: str = "personal") -> dict:
    """Begin bank authorization. Returns {'url': ..., 'authorization_id': ...}.
    Open the returned url in a browser, log in at the bank, then copy the
    `code` query param from the redirect."""
    return post("/auth", {
        "access": {"valid_until": valid_until_iso},
        "aspsp": {"name": bank_name, "country": country},
        "psu_type": psu_type,
        "redirect_url": redirect_url,
        "state": str(uuid.uuid4()),
    })


def create_session(code: str) -> dict:
    """Exchange the auth `code` for a session. Returns session_id + accounts."""
    return post("/sessions", {"code": code})


def account_balances(account_uid: str) -> dict:
    return get(f"/accounts/{account_uid}/balances")


def account_transactions(account_uid: str, **params) -> dict:
    return get(f"/accounts/{account_uid}/transactions", **params)
