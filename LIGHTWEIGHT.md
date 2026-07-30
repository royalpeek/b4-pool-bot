# B4 Notify Bot — API-Only Edition

## Goal

Detect new B4 markets via the official B4 API and send **public** Telegram notifications.
Optimized for small free hosts (Render free tier).

## Architecture

```
B4 API
   ↓
Market polling
   ↓
New market detection
   ↓
Public Telegram notification
   ↓
Public reminders (1h / 10m) while active
   ↓
Database tracking
```

## KEPT

| Component | Purpose |
|-----------|---------|
| B4 API polling | Sole source of truth for market discovery |
| Public new-market notify | Broadcast to all subscribed chats |
| Public reminders | 1 hour left, 10 minutes left |
| PostgreSQL tracking | `announced_markets`, `subscribed_chats`, `bot_state`, `market_messages` |
| Duplicate prevention | DB flags + claim/send/release |
| Telegram broadcast | Simple sequential send |
| Minimal commands | `/start`, `/help`, `/status` (admin), `/pause` (admin), `/resume` (admin) |
| Flask keepalive | `GET /` / `/health` for Render web process |
| One monitor loop | Single background thread |

## REMOVED

| Removed | Why |
|---------|-----|
| Solana on-chain RPC discovery | Unreliable on free RPC; API is the source of truth |
| `getProgramAccounts` + account decoding | No longer needed |
| APP_LIVE / cover image HEAD detection | No longer needed |
| Lifecycle state machine | Simplified to notified flags |
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
- `NEW_MARKET_DELAY_SECONDS` (default `30`)
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
- No on-chain RPC calls
- No cover image cache
