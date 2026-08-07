# B4 Notify Bot — Lightweight Public Edition (V2 On-Chain + API)

## Goal

Detect new B4 V2 markets via **on-chain discovery** (same mechanism as the live
tracker bot) plus the official B4 API, then send **public** Telegram
notifications that are **intentionally delayed** a configurable 2–5 minutes
after the scheduled go-live (the live tracker notifies earliest; this bot
deliberately lags and verifies the market is still live before sending).
Optimized for small free hosts (Render free tier).

## Architecture

```
B4 Program (Solana RPC getProgramAccounts)
   ↓   + B4 API
On-chain + API market discovery
   ↓
Register (scheduled_go_live = end_time - 86400)
   ↓
Wait NOTIFY_DELAY_SECONDS after scheduled go-live
   ↓
Verify still live (cover HEAD / API cover ready)
   ↓
Public Telegram notification (once, dedup via DB flag + inflight guard)
   ↓
Public reminders (1h / 10m) while active
   ↓
Database tracking
```

## KEPT

| Component | Purpose |
|-----------|---------|
| V2 on-chain discovery | `getProgramAccounts` on B4 program, 464-byte account decode (confirmed layout) |
| B4 API polling | Supplementary discovery + `cover_image_status` for app-live check |
| Delayed public notify | Waits `NOTIFY_DELAY_SECONDS` after scheduled go-live (default 180s) |
| Still-live verification | Cover image HEAD 200 (cached) before sending |
| Public reminders | 1 hour left, 10 minutes left |
| PostgreSQL tracking | `announced_markets`, `subscribed_chats`, `bot_state`, `market_messages` |
| Duplicate prevention | DB flags + claim/send/release + in-flight guard |
| Telegram broadcast | Simple sequential send |
| Minimal commands | `/start`, `/help`, `/status` (admin), `/pause` (admin), `/resume` (admin) |
| Flask keepalive | `GET /` / `/health` for Render web process |
| One monitor loop | Single background thread |

## REMOVED

| Removed | Why |
|---------|-----|
| Premium users / plans / wallets | Out of scope |
| Premium early alerts | Out of scope |
| Premium filters / digests | Out of scope |
| Featured wallets / Editor's Pick | Out of scope |
| Featured 12h/6h/30m reminders | Out of scope |
| Live Tracker integration | Separate bot |
| Vote / pool tracking | Out of scope |
| Intelligence / analytics / scoring | Memory + CPU heavy |
| AI notification copy (OpenAI/Groq) | Optional cost + dependency |
| Studio / templates / custom buttons | Out of scope |
| Admin broadcast studio | Out of scope |
| V2 decoder package / APK research docs | Not needed for notify |
| Pipeline audit complexity | Simplified to `/health` |
| Multi-worker ThreadPool broadcasts | Lower memory, simpler |
| Daily summaries | Out of scope |

## Deploy (Render)

**Env vars (required):**

- `BOT_TOKEN`
- `DATABASE_URL`
- `ADMIN_ID` (optional but recommended for `/health`)

**Optional:**

- `MARKET_POLL_SECONDS` (default `5`)
- `NEW_MARKET_DELAY_SECONDS` (default `30`, legacy fallback window)
- `NOTIFY_DELAY_SECONDS` (default `180`; 120–300 = 2–5 min)
- `ONCHAIN_ENABLED` (default `true`)
- `SOLANA_RPC_URL` (default `https://api.mainnet-beta.solana.com`)
- `B4_PROGRAM_ID` (default B4 program address)
- `ONCHAIN_POLL_SECONDS` (default `5`)
- `ENABLE_REMINDERS` (default `true`)
- `PORT` (Render sets this)

**Process:**

```
web: gunicorn main:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120
```

## Memory profile

- One process
- No OpenAI client
- No concurrent broadcast pool
- No on-chain RPC calls beyond the throttled discovery poll
- No persistent cover image cache (bounded in-memory)
