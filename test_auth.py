"""Smoke test: proves your JWT auth works, then lists the banks Enable Banking
can reach for Norway and Austria so you can find the exact ASPSP names to use.

Run:  ./.venv/bin/python test_auth.py
(after setting EB_APP_ID in .env and exporting it, or `set -a; . ./.env`)
"""
import eb_client


def main() -> None:
    for country in ("AT","DE"):
        banks = eb_client.list_aspsps(country)
        print(f"\n=== {country}: {len(banks)} banks reachable ===")
        for b in banks:
            print(f"  {b.get('name')!r:40}  auth={b.get('auth_methods') or b.get('psu_types')}")


if __name__ == "__main__":
    main()
