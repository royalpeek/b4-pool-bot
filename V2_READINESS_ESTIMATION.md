# V2 Readiness Estimation

**Purpose**: Estimate the work required to support B4 V2 markets once endpoints become available.

---

## 1. Current State

### What We Have
- **Public API provider** — fetching 15 active V1 markets
- **Market Provider abstraction** — clean interface for swapping data sources
- **V2 fields in V1 response** — `yes_weighted_pool`, `no_weighted_pool` already present
- **Intelligence pipeline** — analytics, scoring, badges, creator tracking
- **Notification engine** — premium/public layers, reminders, cover images

### What We Need for V2
1. Authenticated API access (from APK analysis)
2. V2-specific field parsing
3. Reputation tier integration
4. Time-weighted display logic
5. Real-time updates (WebSocket?)

---

## 2. Work Breakdown

### Phase 1: Data Layer (2-3 days)

| Task | Description | Estimate |
|------|-------------|----------|
| V2Provider implementation | Implement authenticated API calls, token management | 1 day |
| V2 market schema | Parse new fields: `display_weight`, reputation, tier data | 0.5 day |
| Weighted pool display | Show weighted vs raw pools in notifications | 0.5 day |
| Reputation data fetch | Get user reputation tiers for leaderboard features | 0.5 day |
| WebSocket integration | Real-time market updates (if needed) | 1 day |

### Phase 2: Notification Enhancements (1-2 days)

| Task | Description | Estimate |
|------|-------------|----------|
| Time-weighted alerts | "2x weight for 47 more minutes" notifications | 0.5 day |
| Final window alerts | "Gold+ only in 10 minutes" for Premium users | 0.5 day |
| Reputation badges | Show user tier in notifications | 0.5 day |
| Switch tracking | Alert when creator switches positions | 0.5 day |

### Phase 3: Intelligence Pipeline (1-2 days)

| Task | Description | Estimate |
|------|-------------|----------|
| Weighted analytics | Analytics using weighted pools instead of raw | 0.5 day |
| Creator scoring v2 | Include reputation tier in creator rankings | 0.5 day |
| Early conviction tracker | Track and rank early stakers (2x weight window) | 0.5 day |
| V2 market classification | Classify markets by time-weight patterns | 0.5 day |

### Phase 4: Premium Features (1 day)

| Task | Description | Estimate |
|------|-------------|----------|
| Reputation leaderboard | Premium-only reputation rankings | 0.5 day |
| Diamond market alerts | Alerts for Diamond-only markets | 0.5 day |

---

## 3. Total Estimate

| Phase | Days |
|-------|------|
| Phase 1: Data Layer | 2-3 |
| Phase 2: Notifications | 1-2 |
| Phase 3: Intelligence | 1-2 |
| Phase 4: Premium | 1 |
| **Total** | **5-8 days** |

---

## 4. Prerequisites

Before starting V2 work, we need:

1. **APK analysis complete** — know the auth flow and API endpoints
2. **Auth token available** — wallet signature or JWT for API access
3. **V2 API documented** — endpoints, request/response schemas
4. **Test environment** — ability to test V2 API calls without affecting production

---

## 5. Risk Factors

| Risk | Impact | Mitigation |
|------|--------|------------|
| Auth token expires quickly | High | Implement auto-refresh, cache tokens |
| V2 API rate limits | Medium | Batch requests, cache responses |
| WebSocket reliability | Medium | Fallback to polling |
| Reputation data changes | Low | Cache with TTL, refresh periodically |
| V2 fields change during rollout | Medium | Version API responses, handle missing fields |

---

## 6. Architecture Impact

### Current Flow
```
PublicAPIProvider
    ↓
fetch_b4_markets()
    ↓
monitor_b4_markets()
    ↓
Notification Engine
```

### V2 Flow (Future)
```
V2Provider (with auth)
    ↓
fetch_b4_markets()
    ↓
Market Normalization Layer (V1 ↔ V2 field mapping)
    ↓
monitor_b4_markets()
    ↓
Notification Engine (with V2-aware builders)
```

### Key Principle
**The notification engine consumes normalized market data, not raw API responses.** This means:
- V2 fields are parsed and normalized in the provider
- Notification builders use normalized fields (with V1 fallbacks)
- Intelligence pipeline works with both V1 and V2 data

---

## 7. Field Mapping (V1 → V2)

| V1 Field | V2 Equivalent | Notes |
|----------|---------------|-------|
| `yes_pool` | `yes_tracked_raw` | Raw pool (no weighting) |
| `no_pool` | `no_tracked_raw` | Raw pool (no weighting) |
| — | `yes_weighted_pool` | Time-weighted pool |
| — | `no_weighted_pool` | Time-weighted pool |
| — | `yes_display_weight` | Display weight for UI |
| — | `no_display_weight` | Display weight for UI |
| — | `reputation_tier` | User's reputation level |
| — | `time_weight` | Current time weight for staker |

---

## 8. Migration Strategy

### Step 1: Shadow Mode
- Run V2Provider alongside PublicAPIProvider
- Log V2 data but don't display it
- Compare V1 vs V2 data for consistency

### Step 2: Opt-In
- Enable V2 features for Premium users only
- Keep V1 as default for Public users
- Gather feedback and fix issues

### Step 3: Full Rollout
- Enable V2 for all users
- Deprecate V1-only code paths
- Remove legacy field handling

---

## 9. Success Criteria

V2 support is complete when:

- [ ] V2Provider successfully fetches authenticated market data
- [ ] Time-weighted pools displayed correctly in notifications
- [ ] Reputation tiers visible in Premium features
- [ ] Final window alerts work for Gold+ users
- [ ] Intelligence pipeline uses weighted data for scoring
- [ ] No regression in V1 notification delivery
- [ ] All existing tests pass
- [ ] Premium conversion maintained or improved

---

## 10. Estimated Timeline

| Milestone | Target |
|-----------|--------|
| APK analysis complete | Week 1 |
| V2Provider implemented | Week 2 |
| V2 notifications live | Week 3 |
| V2 intelligence pipeline | Week 4 |
| Full V2 rollout | Week 5 |

**Total**: 5 weeks from APK analysis to full V2 support.
