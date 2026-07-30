# B4 V2 Market Investigation Report

**Date**: July 20, 2026
**Status**: Paused — awaiting APK analysis for deeper investigation

---

## 1. Summary

B4 is rolling out "Time-Value Mechanics" (V2) that fundamentally change how staking rewards work. The B4 Android app displays V2 markets, but these markets are **not accessible via any publicly documented API endpoint**. This report documents all findings.

---

## 2. What V2 Changes (from Whitepaper)

| Feature | V1 (Current) | V2 (Rolling Out) |
|---------|-------------|-------------------|
| Time weighting | None | 2x for first hour, decays to 0x in final 10 min |
| Display | Vote counts visible | Odds shown, votes hidden until resolve |
| Downside | Up to 50-80% loss | Capped at ~12% with early exit |
| Final window | Open to all | Gold+ reputation, $25 minimum, 0x weight |
| Reputation tiers | None | Gold, Platinum, Diamond |
| Creator access | Not available | Diamond-only market creation |
| Switches | Not mentioned | One per poll, gets current time weight |
| Fees | 1-2% | 1% first hour, 2% late |

**Key insight**: V2 is a mechanics upgrade to the staking system, not a separate market type. The whitepaper says "v2 · rolling out soon."

---

## 3. Public API Investigation

### Endpoint: `https://www.b4app.xyz/api/markets`

**Total markets returned**: 15 (all active)
**All markets have**: `mechanics_version: 1`

### Fields Tested

| Query Parameter | Result |
|----------------|--------|
| `?mechanics_version=2` | Ignored — returns same V1 data |
| `?mechanics_version=1` | Same as default |
| `?resolved=true` | Returns resolved markets (still V1) |
| `?limit=5&page=2` | Pagination works, all V1 |
| `/api/markets/all` | 404 |
| `/api/v2/markets` | 404 |
| `/api/markets/v2` | 404 |
| `/api/v1/markets` | 404 |
| `/api/auth/session` | 404 |
| `/api/supabase/health` | 404 |
| `/api/stats` | 404 |
| `/api/games` | 404 |
| `/api/feed` | 404 |
| `/api/creators` | 404 |
| `/api/me` | 404 |
| `/api/leaderboard` | 404 |
| `/graphql` | 404 |

### Market Data Schema (V1 Response)

```json
{
  "market_id": "1784502669600491",
  "market_pubkey": "B2G8Qv6Ftq9Lcc9joeJTsx4Wy7KoqUD15eYVPTRG1rsT",
  "title": "Should political leaders present the WC trophy to the winners?",
  "description": "",
  "creator": "65tXKySPoqeM49egfwUA5WUUvMRJ2Uaewx8dh4ZnfoUS",
  "end_time": 1784589343,
  "yes_pool": 20790000,
  "no_pool": 4950000,
  "yes_votes": 10,
  "no_votes": 1,
  "resolved": false,
  "outcome": 0,
  "created_at": "2026-07-19T23:11:54.580152+00:00",
  "updated_at": "2026-07-20T10:20:17.188+00:00",
  "hidden": false,
  "theme": "sports",
  "is_private": false,
  "go_live_at": "2026-07-19T23:15:42.753518+00:00",
  "yes_weighted_pool": 41580000,
  "no_weighted_pool": 9900000,
  "yes_tracked_raw": 20790000,
  "no_tracked_raw": 4950000,
  "tw_snapshot_at": "2026-07-20T10:20:17.188+00:00",
  "mechanics_version": 1,
  "yes_display_weight": null,
  "no_display_weight": null,
  "sponsor_match_count": 1,
  "first_staker_promo_available": false,
  "first_staker_match_usdc": null,
  "first_staker_min_stake_usdc": null
}
```

---

## 4. Critical Discovery: V2 Fields Already Active

The V1 market response already contains V2-related fields:

### Weighted Pool Fields (ACTIVE)

| Field | Value Example | Meaning |
|-------|--------------|---------|
| `yes_weighted_pool` | 41580000 | Time-weighted YES pool |
| `no_weighted_pool` | 9900000 | Time-weighted NO pool |
| `yes_tracked_raw` | 20790000 | Raw YES pool (no weighting) |
| `no_tracked_raw` | 4950000 | Raw NO pool (no weighting) |
| `tw_snapshot_at` | timestamp | When weighted values were last calculated |

**Ratio analysis**: `yes_weighted_pool / yes_pool = 41580000 / 20790000 ≈ 2.0x`

This matches the V2 whitepaper's "2× for first hour" time-weighting exactly. **V2 weighting mechanics are already being applied to V1 markets.**

### Null Fields (NOT YET ACTIVE)

| Field | Current Value | Expected V2 Use |
|-------|--------------|-----------------|
| `yes_display_weight` | `null` | Display weight for UI |
| `no_display_weight` | `null` | Display weight for UI |

### Sponsor/First Staker Fields

| Field | Value | Notes |
|-------|-------|-------|
| `sponsor_match_count` | 1 | Sponsor boost active |
| `first_staker_promo_available` | false | All markets have first staker used |
| `first_staker_match_usdc` | `null` | Not exposed in API |
| `first_staker_min_stake_usdc` | `null` | Not exposed in API |

---

## 5. On-Chain Investigation

### Program Address
`9XQDD38sy1qJ57DqAQvADuLRTjcYUXD48H7deyNuaehH`

### RPC Queries Performed

| Filter | RPC | Result |
|--------|-----|--------|
| `dataSize: 300` | mainnet-beta | 0 accounts |
| `dataSize: 300` | mainnet-beta (retry) | 0 accounts |
| Multiple sizes (150-1000) | mainnet-beta | Rate limited (429) |
| `dataSize: 300` | Helius (demo key) | 401 Unauthorized |

**Assessment**: The Solana program likely uses account structures different from what we queried, or the public RPC is not returning results for `getProgramAccounts`. A paid RPC (Helius/QuickNode) is needed for reliable on-chain queries.

---

## 6. Website Analysis

### Technology Stack
- **Framework**: Next.js (Turbopack)
- **Hosting**: Vercel (deployment ID: `dpl_C4mKNe6dwtD19txhJCX3SZxrJjeq`)
- **Analytics**: PostHog (PostHogProvider detected)
- **No Supabase/Firebase config** found in page source
- **No API keys exposed** in client-side code

### Website Statement
> "No browser, no automation: There's deliberately no web app"

This confirms B4 intentionally has no web interface — the mobile app is the primary (only) user interface.

---

## 7. What the Android App Likely Uses

Based on all evidence, the B4 Android app almost certainly uses one or more of:

1. **Authenticated REST API** — requires wallet session/JWT, not publicly documented
2. **WebSocket/Socket.IO** — for real-time market updates (V2 may use streaming)
3. **Direct on-chain reads** — for V2 market state via Solana RPC with proper API key
4. **GraphQL endpoint** — not publicly accessible
5. **Separate V2 backend** — dedicated service not exposed to public

### Why We Can't Access It
- No wallet session/auth token available
- No API key for paid RPC
- Mobile app uses proprietary authentication (wallet-based)
- Backend may use IP/device fingerprinting

---

## 8. Assumptions

1. **V2 markets may not exist as a separate type** — V2 is a mechanics upgrade applied to all markets. The "V2 markets" visible in the app are likely V1 markets displayed with V2 weighting mechanics.

2. **The public API is intentionally limited** — B4 likely restricts the public API to prevent scraping/automation, per their "no browser" policy.

3. **The Android app has access to richer data** — fields like `first_staker_match_usdc`, `first_staker_min_stake_usdc`, `yes_display_weight`, `no_display_weight` are exposed in the schema but null, suggesting the app populates them from a different source.

4. **V2 rollout is incremental** — The whitepaper says "rolling out soon," and the presence of weighted pool fields in V1 markets suggests the backend is already calculating V2 metrics, just not exposing them fully.

5. **Our bot currently works fine** — The public API provides enough data for our notification system. V2 fields are additive, not replacing existing fields.

---

## 9. Recommendations

1. **Do not chase hidden endpoints** — Diminishing returns without auth tokens.

2. **APK analysis is the priority** — Decompiling the Android app will reveal:
   - All API endpoints used
   - Authentication flow
   - WebSocket/GraphQL endpoints
   - Protobuf schemas
   - Feature flags

3. **Our V1-based bot is stable** — Continue development on current architecture.

4. **Market Provider abstraction** — Design the bot to accept V2 data when available without major refactoring.

5. **Monitor the public API** — V2 fields may be enabled in the public API as rollout progresses.

---

## 10. Files Referenced

- B4 Whitepaper: `https://www.b4app.xyz/whitepaper`
- B4 API: `https://www.b4app.xyz/api/markets`
- Solana Program: `9XQDD38sy1qJ57DqAQvADuLRTjcYUXD48H7deyNuaehH`
- B4 Android: `com.b4app.b4` (Google Play)
- B4 iOS: `id6760046133`
