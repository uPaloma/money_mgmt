# Money Management — Effort Estimate

Personal project to aggregate all monetary actions from multiple bank accounts into one central app: poll regularly, store in a local DB file, back it up, and eventually view on iPhone.

## The key finding that shapes everything

You do **not** want to integrate with each bank's raw PSD2 API directly. Doing that requires you to be a **licensed TPP (Third-Party Provider)** with an eIDAS certificate — a regulatory burden that's completely impractical for a personal project. Instead you use an **aggregator** that holds the license and lets you connect *your own* accounts.

The famous free option (Nordigen → GoCardless Bank Account Data) **closed to new signups** in 2025. The current self-serve replacement is **Enable Banking**: free for personal use on your own accounts ("Restricted Production"), self-serve signup, no TPP license needed on your side. That's your foundation.

## Effort per data source

| Source | Path | Effort | Reliability |
|---|---|---|---|
| **Bank Norwegian** | Enable Banking (Nordic bank, well covered; also 6 aggregators support it) | ~0.5–1 day once Enable Banking is wired up | High — official PSD2 |
| **Erste Bank Austria** | Enable Banking (Erste has full PSD2 APIs) | ~0.5 day — same code path as above, just another connection | High — official PSD2 |
| **Trade Republic** | ⚠️ No official API. It's a *broker*, so it's **out of PSD2 scope** — aggregators don't cover it. Only unofficial reverse-engineered libs (`pytr`, `tr-api`) | ~1–3 days + ongoing maintenance | **Low/fragile** |

**The Trade Republic caveat is important.** Its private API broke mid-2026 (WAF changes), and the current workaround needs Playwright to fetch a token plus a push-approval you tap in the TR app on every login. It violates their ToS, can break any time, and can't be fully automated headlessly.

Recommendation: build phases 1–2 without it, and treat TR as an optional, best-effort add-on later (or just import its monthly PDF/CSV statements instead).

## Overall phased estimate

- **Phase 0 — Enable Banking setup + one connection:** ~1 day. Sign up, get JWT/keys, OAuth-style consent flow, pull balances + transactions for Bank Norwegian.
- **Phase 1 — DB + polling + backup:** ~1–2 days. SQLite file, schema (accounts / transactions / balances with dedup on transaction IDs), a scheduled poller (respecting bank rate limits — some cap at ~4 calls/account/day), and a backup job (e.g. nightly copy + rotate, optionally to cloud).
- **Phase 2 — Add Erste:** ~0.5 day (reuses Phase 0 code).
- **Phase 3 — Trade Republic (optional):** ~1–3 days, fragile.
- **Phase 4 — iPhone access:** see below.

**Realistic total for a solid Norwegian + Erste setup with polling and backups: ~3–5 focused days.** Add TR only if you accept the fragility.

## The iPhone piece

You do **not** need special rights from Apple for the sensible approach:

- **Best fit: a PWA (Progressive Web App).** Build a small web dashboard over the SQLite DB, "Add to Home Screen" from Safari — it looks and launches like an app, no App Store, no review, no $99. Least friction for a personal tool.
- **Native app via Xcode sideload:** free, but the build expires every 7 days unless you re-install. Annoying for daily use.
- **Native app via App Store/TestFlight:** requires the **Apple Developer Program ($99/year)**. Only worth it if you specifically want a real native app or widgets.

Architecture note: for the phone to reach the data, the app/DB needs to live somewhere reachable (a small always-on box, a cheap VPS, or a home server with a VPN like Tailscale). A DB file sitting only on this machine won't be phone-accessible unless this machine is always on and reachable.

## Recommended stack

Python (Enable Banking has a clean REST/JWT API and SDKs) + SQLite + a scheduler (cron or APScheduler) + a lightweight web UI (FastAPI/Flask serving a PWA). All lightweight, all runnable on a small always-on machine.

## Open decisions before building

1. **Where will this run?** (This machine only, a home server, or a small VPS — determines how the phone reaches it and how backups go.)
2. **Include Trade Republic from the start, or defer it** given the fragility?

## Sources

- [Free & Indie Open Banking APIs (2026) — what's actually free](https://www.openbankingtracker.com/guides/free-open-banking-apis)
- [Enable Banking FAQ / docs](https://enablebanking.com/docs/faq/)
- [Bank Norwegian — Open Banking / aggregator coverage](https://www.openbankingtracker.com/provider/bank-norwegian-no)
- [Erste Group developer portal](https://developers.erstegroup.com/)
- [GoCardless (Nordigen) Bank Account Data — new signups disabled](https://developer.gocardless.com/bank-account-data/overview)
- [pytr — unofficial Trade Republic API](https://github.com/pytr-org/pytr)
- [tr-api (2026 WAF workaround)](https://github.com/cdamken/tr-api)
