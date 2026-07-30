# B4 Notify Bot — Lightweight Edition

## Goal

Detect new B4 markets and send **public** Telegram notifications only.
Optimized for small free hosts (Render free tier).

## Architecture

```
On-chain discovery
        ↓
Register market (DB)
        ↓
APP_LIVE (cover HEAD 200)
        ↓
Public new-market notification
        ↓
Public reminders (1h / 10m) while active
```

## KEPT

| Component | Purpose |
|-----------|---------|
| On-chain discovery | Solana `getProgramAccounts` + account decode |
| APP_LIVE detection | Cover image `HEAD …/market-cover/{id}.png` → 200 |
| Public new-market notify | Broadcast to all subscribed chats |
| Public reminders | 1 hour left, 10 minutes left |
| PostgreSQL tracking | `announced_markets`, `subscribed_chats`, `bot_state` |
| Duplicate prevention | DB flags + claim/send/release |
| Telegram broadcast | Simple sequential send |
| Minimal commands | `/start`, `/help`, `/health`, `/status` (admin) |
| Flask keepalive | `GET /` for Render web process option |
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

- `SOLANA_RPC_URL`
- `ONCHAIN_POLL_SECONDS` (default `8` — gentler on free RPC)
- `MARKET_POLL_SECONDS` (default `5`)
- `PUBLIC_ALERT_DELAY_SECONDS` (default `0` for lightweight: send at APP_LIVE)
- `ENABLE_REMINDERS` (default `true`)
- `PORT` (Render sets this)

**Process:**

```
web: python main.py
```

or worker-only if you use an external health check.

## Memory profile

- One process
- No OpenAI client
- No concurrent broadcast pool by default
- Cover HEAD results cached ~60s
- On-chain poll every 8s (not 1s)
