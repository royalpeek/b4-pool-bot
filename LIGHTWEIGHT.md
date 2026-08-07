# B4 Notify Bot — Lightweight Public Edition (V2 On-Chain + API)

## Goal

Detect new B4 V2 markets via **on-chain discovery** (same mechanism as the live
tracker bot) plus the official B4 API, then send **public** Telegram
notifications that are **intentionally delayed** a configurable 2–5 minutes
after the scheduled go-live (the live tracker notifies earliest; this bot
deliberately lags and verifies the market is still live before sending).
Optimized for small free hosts (Render free tier).

Each market gets **one Telegram message** that is **edited in place** as it
progresses through the V2 lifecycle (New Live → Graduated → 12h → 6h), then
**deleted automatically** when the market closes. Users only ever see active
markets.

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
Public Telegram notification (once) → single card per market
   ↓
V2 lifecycle (edit-in-place, one message per market):
   🎓 Graduated (immediate, once) → re-render card
   ⏰ 12 hours left (once)        → re-render card
   ⏰ 6 hours left (once)         → re-render card
   ↓
Market closed → delete every message + clear all state (as if never announced)
   ↓
Admin /cleanup → wipe every bot message + all stored state
```

## KEPT

| Component | Purpose |
|-----------|---------|
| V2 on-chain discovery | `getProgramAccounts` on B4 program, 464-byte account decode (confirmed layout) |
| B4 API polling | Supplementary discovery + `cover_image_status` for app-live check |
| Delayed public notify | Waits `NOTIFY_DELAY_SECONDS` after scheduled go-live (default 180s) |
| Still-live verification | Cover image HEAD 200 (cached) before sending |
| Single card per market | One message, edited across lifecycle stages (`edit_market_messages`) |
| V2 lifecycle stages | 🎓 Graduated (app signal), ⏰ 12h, ⏰ 6h — each fired once |
| Auto-cleanup on close | Deletes all messages for the closed market + clears state |
| Admin `/cleanup` | Deletes every bot message + clears all stored message IDs (rate-limited, never crashes) |
| Graduation detection | `GRADUATION_ENABLED` (default true); app-signal driven; on-chain placeholder documented |
| PostgreSQL tracking | `announced_markets`, `subscribed_chats`, `bot_state`, `market_messages` |
| Duplicate prevention | DB flags + claim/send/release + in-flight guard |
| Telegram broadcast | Simple sequential send; safe delete/edit helpers |
| Minimal commands | `/start`, `/help`, `/status` (admin), `/pause` (admin), `/resume` (admin), `/cleanup` (admin) |
| Flask keepalive | `GET /` / `/health` for Render web process |
| One monitor loop | Single background thread |

## REMOVED

| Removed | Why |
|---------|-----|
| Premium users / plans / wallets | Out of scope |
| Premium early alerts | Out of scope |
| Premium filters / digests | Out of scope |
| Featured wallets / Editor's Pick | Out of scope |
| Live Tracker integration | Separate bot |
| Vote / pool / liquidity tracking | Out of scope |
| Vote percentages / liquidity amounts | Out of scope — no analytics |
| Resolution notifications | Out of scope — markets are cleaned up on close |
| Market-closed notifications | Out of scope — auto cleanup instead |
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
- `ADMIN_ID` (optional but recommended for `/health` + `/cleanup`)

**Optional:**

- `MARKET_POLL_SECONDS` (default `5`)
- `NEW_MARKET_DELAY_SECONDS` (default `30`, legacy fallback window)
- `NOTIFY_DELAY_SECONDS` (default `180`; 120–300 = 2–5 min)
- `ONCHAIN_ENABLED` (default `true`)
- `SOLANA_RPC_URL` (default `https://api.mainnet-beta.solana.com`)
- `B4_PROGRAM_ID` (default B4 program address)
- `ONCHAIN_POLL_SECONDS` (default `5`)
- `ENABLE_REMINDERS` (default `true`)
- `REMINDER_12H_SECONDS` (default `43200`)
- `REMINDER_6H_SECONDS` (default `21600`)
- `GRADUATION_ENABLED` (default `true`)
- `DELETE_DELAY_SECONDS` (default `0.2`; pacing for cleanup deletes/edits)
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
