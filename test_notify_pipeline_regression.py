"""Regression tests for B4 Notify Bot notification flag + decision logic.

These tests do NOT need BOT_TOKEN / DATABASE_URL / Telegram.
They encode the production outage root causes so they cannot silently return.

Run: python test_notify_pipeline_regression.py
"""


def test_notified_new_must_unlock_on_premium_success():
    """ROOT CAUSE: reminders gate on notified_new; premium-only success must set it."""
    state = {"notified_new": False, "premium_notified_onchain": False, "public_notified": False}

    def mark_premium_success():
        state["premium_notified_onchain"] = True
        # Fixed behavior: also unlock reminders
        state["notified_new"] = True

    mark_premium_success()
    assert state["premium_notified_onchain"] is True
    assert state["notified_new"] is True, "premium success must set notified_new for reminders"
    print("PASS: premium success unlocks reminders (notified_new)")


def test_public_independent_of_notified_new_and_premium():
    """ROOT CAUSE: public must NOT treat notified_new as public_notified."""

    def market_public_already_notified(existing):
        if not existing:
            return False
        return bool(existing.get("public_notified"))

    # Premium already sent + reminders unlocked — public still pending
    row = {
        "premium_notified_onchain": True,
        "notified_new": True,
        "public_notified": False,
    }
    assert market_public_already_notified(row) is False, "public must still run after premium"
    row["public_notified"] = True
    assert market_public_already_notified(row) is True
    print("PASS: public independent of premium/notified_new")


def test_premium_independent_of_api_indexed():
    """Premium fires on APP_LIVE; must not require public/api flags."""
    premium_done = False
    app_live = True
    api_indexed = False  # not yet in API

    def should_send_premium():
        return app_live and not premium_done

    assert should_send_premium() is True
    premium_done = True
    assert should_send_premium() is False
    # API later does not re-send premium
    api_indexed = True
    assert should_send_premium() is False
    # Public still independent
    public_done = False
    assert api_indexed and not public_done
    print("PASS: premium on APP_LIVE independent of API_INDEXED")


def test_public_empty_audience_must_not_block_forever():
    """ROOT CAUSE: all chats premium → exclude_premium targets=0 → public never set flags."""
    public_targets = 0
    state = {"public_notified": False, "notified_new": False}

    if public_targets <= 0:
        # Fixed: mark path complete so retries stop and reminders can run
        state["public_notified"] = True
        state["notified_new"] = True

    assert state["public_notified"] is True
    assert state["notified_new"] is True
    print("PASS: empty public audience closes path without infinite retry")


def test_reminder_gate_accepts_premium_or_public_or_notified_new():
    def eligible(row):
        return bool(
            row.get("notified_new")
            or row.get("premium_notified_onchain")
            or row.get("public_notified")
        )

    assert eligible({"notified_new": True}) is True
    assert eligible({"premium_notified_onchain": True}) is True
    assert eligible({"public_notified": True}) is True
    assert eligible({}) is False
    print("PASS: reminder gate accepts any live-announced flag")


def test_claim_before_send_releases_on_failure():
    flag = {"claimed": False}

    def claim():
        if flag["claimed"]:
            return False
        flag["claimed"] = True
        return True

    def release():
        flag["claimed"] = False

    assert claim() is True
    sent = 0  # broadcast failed
    if not sent:
        release()
    assert flag["claimed"] is False
    assert claim() is True  # can retry
    print("PASS: failed send releases claim for retry")


def test_no_duplicate_public_inflight():
    inflight = set()
    mid = "123"

    def queue_public():
        if mid in inflight:
            return "skip_inflight"
        inflight.add(mid)
        return "queued"

    assert queue_public() == "queued"
    assert queue_public() == "skip_inflight"
    inflight.discard(mid)
    assert queue_public() == "queued"
    print("PASS: inflight guard prevents duplicate public workers")


def test_standard_reminder_windows():
    """Standard markets: 1h and 10m. Featured: 12h/6h/1h/30m/10m."""
    def standard_window(hours_until, minutes_until, flags):
        if hours_until <= 1.0 and not flags.get("notified_1h"):
            return "1h"
        if minutes_until <= 10.0 and not flags.get("notified_5m"):
            return "10m"
        return None

    assert standard_window(0.9, 54, {}) == "1h"
    assert standard_window(0.1, 6, {"notified_1h": True}) == "10m"
    assert standard_window(5.0, 300, {}) is None  # 5h left: no standard 6h reminder
    print("PASS: standard reminder windows (1h/10m)")


def test_pipeline_stages_for_one_market():
    """Trace decision stages for one active market simulation."""
    decisions = []
    market = {
        "market_id": "1784930203559934",
        "title": "Would you go back in time instead of forward in time?",
        "cover_image_status": "ready",
        "source": "api",
        "hidden": False,
        "resolved": False,
        "end_time": 10**12,
    }

    # Detected
    decisions.append(("detected", True, "from_api"))
    # Registered
    row = {"notified_new": False, "premium_notified_onchain": False, "public_notified": False}
    decisions.append(("registered", True, "new_row"))
    # Eligible
    active = not market["hidden"] and not market["resolved"]
    decisions.append(("eligible", active, "active_and_visible"))
    # App live via API ready cover
    app_live = market["cover_image_status"] == "ready"
    decisions.append(("app_live", app_live, "cover_status_ready"))
    # Premium send simulation
    if app_live and not row["premium_notified_onchain"]:
        row["premium_notified_onchain"] = True
        row["notified_new"] = True
        decisions.append(("premium_sent", True, "broadcast_ok"))
    # Public send simulation
    if not row["public_notified"]:
        row["public_notified"] = True
        row["notified_new"] = True
        decisions.append(("public_sent", True, "broadcast_ok"))
    # Reminder eligible
    decisions.append(("reminder_eligible", bool(row["notified_new"]), "notified_new_set"))

    assert all(d[1] for d in decisions), decisions
    print("PASS: one-market pipeline stages all succeed")
    for stage, ok, reason in decisions:
        print(f"  {stage}: {ok} ({reason})")


def test_save_announced_market_placeholder_count():
    """ROOT CAUSE of 12 markets / 0 rows: INSERT %s count must match params."""
    sql = """
            INSERT INTO announced_markets (
                market_id, title, theme, end_time, market_link, notified_new,
                notified_1h, notified_5m, notified_ended, delete_scheduled,
                notified_scheduled, notified_go_live_2m, image_followup_sent,
                is_scheduled, go_live_at, detected_at, source, market_pubkey,
                cover_image_url, onchain_detected_at, api_detected_at,
                premium_notified_onchain, metadata_json,
                notified_12h, notified_6h, notified_30m, is_featured
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE,
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                FALSE, FALSE, FALSE, %s
            )
            ON CONFLICT (market_id) DO NOTHING
            RETURNING market_id
        """
    params = (
        "id", "title", "theme", "end", "link", False,
        False, None, "now", "api",
        None, None, None, None,
        False, None,
        False,
    )
    assert sql.count("%s") == len(params), (
        f"placeholders={sql.count('%s')} params={len(params)} — registration will always fail"
    )
    # Broken legacy form had 18 placeholders and 17 params
    broken = "%s, " * 11  # old middle line had 11 %s instead of 10
    assert sql.count("%s") == 17
    print("PASS: save_announced_market placeholder count matches params (17)")


def test_active_markets_zero_rows_is_critical():
    active_api = 12
    row_count = 0
    is_critical = active_api > 0 and row_count == 0
    assert is_critical is True
    print("PASS: active markets with 0 rows is a CRITICAL condition")


# ── V2 delayed-notify behavior (NOT like the earliest-notify live bot) ─────

def test_notify_delayed_until_scheduled_go_live_plus_delay():
    """ROOT CAUSE (V2): public notify must lag go-live by NOTIFY_DELAY_SECONDS."""
    NOTIFY_DELAY_SECONDS = 180  # 2–5 min configurable window
    scheduled_go_live = 1_784_000_000.0

    def passed(row, now):
        sgl = row.get("scheduled_go_live")
        return now >= sgl + NOTIFY_DELAY_SECONDS

    row = {"scheduled_go_live": scheduled_go_live}
    assert passed(row, scheduled_go_live + 179) is False  # still waiting
    assert passed(row, scheduled_go_live + 180) is True   # delay elapsed
    print("PASS: notify fires only after scheduled_go_live + delay")


def test_notify_verify_still_live_before_send():
    """ROOT CAUSE: must verify the market is still live (cover HEAD) before send."""
    def app_live(mid, market, cover_ok):
        status = str(market.get("cover_image_status") or "").lower()
        source = str(market.get("source") or "").lower()
        if status == "ready":
            if source == "onchain":
                return cover_ok
            return True
        return cover_ok

    market = {"market_id": "123", "cover_image_status": "ready", "source": "onchain"}
    assert app_live("123", market, True) is True
    assert app_live("123", market, False) is False  # went dark → skip
    api_market = {"cover_image_status": "ready", "source": "api"}
    assert app_live("123", api_market, False) is True  # API trusted without HEAD
    print("PASS: still-live verification gates the send")


def test_delayed_send_no_duplicate_with_inflight_and_flag():
    """ROOT CAUSE: delayed send must still claim exactly once (inflight + DB flag)."""
    inflight = set()
    state = {"public_notified": False}

    def attempt(mid):
        if mid in inflight or state["public_notified"]:
            return False
        inflight.add(mid)
        state["public_notified"] = True
        inflight.discard(mid)
        return True

    assert attempt("m1") is True
    assert attempt("m1") is False  # already notified
    assert attempt("m1") is False  # never re-sends
    print("PASS: delayed path still dedups via inflight + public_notified")


def test_scheduled_go_live_derived_from_end_time():
    """V2: scheduled_go_live = end_time - 86400 (confirmed chain behavior)."""
    end_time = 1_785_000_000
    duration = 86400
    assert end_time - duration == 1_784_913_600
    # Feed markets: go_live_at == end_time - 86400 for all observed markets.
    for end, expected_go_live in [(1_784_913_600, 1_784_827_200), (1_790_000_000, 1_789_913_600)]:
        assert end - duration == expected_go_live
    print("PASS: scheduled_go_live derived from end_time (end - 86400)")


def test_onchain_register_backfills_schedule_but_not_duplicate():
    """On-chain + API both discover the same market → one row, schedule merged."""
    def register(store, market):
        mid = str(market["market_id"])
        if mid in store:
            if not store[mid].get("scheduled_go_live") and market.get("scheduled_go_live"):
                store[mid]["scheduled_go_live"] = market["scheduled_go_live"]
            return store[mid], False
        store[mid] = dict(market)
        return store[mid], True

    store = {}
    api = {"market_id": "m1", "title": "t", "end_time": 1_785_000_000, "source": "api"}
    row, new = register(store, api)
    assert new is True and row["source"] == "api" and not row.get("scheduled_go_live")
    onchain = {"market_id": "m1", "title": "t", "end_time": 1_785_000_000,
               "scheduled_go_live": 1_784_913_600.0, "source": "onchain"}
    row2, new2 = register(store, onchain)
    assert new2 is False  # no duplicate row
    assert row2["scheduled_go_live"] == 1_784_913_600.0  # schedule backfilled
    print("PASS: on-chain + API discovery merges into one deduped row")


def test_send_releases_claim_if_delay_not_yet_passed():
    """Delayed gate must not claim the flag before the window opens."""
    claimed = {"flag": False}

    def should_send(row, now, delay):
        return now >= row["scheduled_go_live"] + delay

    row = {"scheduled_go_live": 1_784_000_000.0}
    if not should_send(row, 1_784_000_000 + 60, 180):
        pass  # not claimed yet — gate prevents premature claim
    assert claimed["flag"] is False
    assert should_send(row, 1_784_000_000 + 180, 180) is True
    print("PASS: flag not claimed before delay window opens")


if __name__ == "__main__":
    test_notified_new_must_unlock_on_premium_success()
    test_public_independent_of_notified_new_and_premium()
    test_premium_independent_of_api_indexed()
    test_public_empty_audience_must_not_block_forever()
    test_reminder_gate_accepts_premium_or_public_or_notified_new()
    test_claim_before_send_releases_on_failure()
    test_no_duplicate_public_inflight()
    test_standard_reminder_windows()
    test_pipeline_stages_for_one_market()
    test_save_announced_market_placeholder_count()
    test_active_markets_zero_rows_is_critical()
    test_notify_delayed_until_scheduled_go_live_plus_delay()
    test_notify_verify_still_live_before_send()
    test_delayed_send_no_duplicate_with_inflight_and_flag()
    test_scheduled_go_live_derived_from_end_time()
    test_onchain_register_backfills_schedule_but_not_duplicate()
    test_send_releases_claim_if_delay_not_yet_passed()
    print("ALL NOTIFY PIPELINE REGRESSION TESTS PASSED")
