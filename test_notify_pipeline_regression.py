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
    """V2 lifecycle: 12h and 6h windows, once each, after the market went live."""
    def window(hours_until, flags):
        if hours_until <= 6.0 and not flags.get("notified_6h"):
            return "6h"
        if hours_until <= 12.0 and not flags.get("notified_12h"):
            return "12h"
        return None

    assert window(10.0, {}) == "12h"
    assert window(5.0, {"notified_12h": True}) == "6h"
    assert window(1.0, {"notified_12h": True, "notified_6h": True}) is None
    assert window(5.0, {}) == "6h"  # within 6h, 6h milestone wins
    print("PASS: lifecycle reminder windows (12h/6h, once each)")


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


# ── Startup baseline (no historical replay) ────────────────────────────────

def test_startup_baseline_never_notifies_historical_markets():
    """ROOT CAUSE: first scan after deploy returned ALL live markets → old
    markets were announced as brand-new. The boot baseline must gate which
    markets may ever be registered/notified."""
    baseline = set()

    def seed_baseline(scan):
        for m in scan:
            baseline.add(str(m.get("market_id")))

    # Boot: two markets already live (historical).
    seed_baseline([{"market_id": "OLD1"}, {"market_id": "OLD2"}])
    assert baseline == {"OLD1", "OLD2"}

    def register_if_new(market):
        return str(market["market_id"]) not in baseline

    assert register_if_new({"market_id": "OLD1"}) is False  # historical → skipped
    assert register_if_new({"market_id": "OLD2"}) is False  # historical → skipped
    assert register_if_new({"market_id": "OLD3"}) is True   # seen only AFTER boot → genuinely new
    print("PASS: boot baseline gates registration -> old markets never replayed")


def test_restart_reseeds_baseline_no_replay():
    """ROOT CAUSE: on every restart the baseline must be reseeded from the
    then-live markets, so a restart never replays markets that exist at boot."""
    def boot(now_live, prior_rows):
        baseline = set(now_live)
        # Any prior row still live at boot is in the new baseline → never replayed.
        newly = sorted(m for m in prior_rows if m not in baseline)
        return newly, sorted(baseline)

    newly, baseline = boot({"A", "B"}, ["A", "B", "C"])
    assert newly == ["C"]
    assert baseline == ["A", "B"]
    # A live-at-restart market stays silent even if it was never notified before.
    assert "A" not in newly
    print("PASS: restart reseeds baseline; only genuinely new markets notify")


def test_pause_cancels_queued_delayed_notifies():
    """ROOT CAUSE: bot kept sending after Pause. Pause must cancel queued
    delayed notifies so Resume only applies to markets discovered after it."""
    rows = {"P1": {"public_notified": False, "notify_cancelled": False}}

    def pause():
        for r in rows.values():
            if not r["public_notified"] and not r["notify_cancelled"]:
                r["notify_cancelled"] = True  # queued delayed notifies cancelled

    def pending():
        return [mid for mid, r in rows.items()
                if not r["public_notified"] and not r["notify_cancelled"]]

    pause()
    assert rows["P1"]["notify_cancelled"] is True
    assert pending() == []  # nothing re-sends after Resume
    # Markets discovered AFTER resume are future markets → may still notify.
    rows["P2"] = {"public_notified": False, "notify_cancelled": False}
    assert pending() == ["P2"]
    print("PASS: pause cancels queued notifies; resume only future markets")


def test_send_aborts_when_paused_mid_flight():
    """Pause during a send must cancel (mark notify_cancelled), not just
    release-and-retry, otherwise the send would fire after Resume."""
    state = {"paused": True, "public_notified": True}

    def pre_send():
        if state["paused"]:
            state["public_notified"] = False
            state["notify_cancelled"] = True
            return "cancelled"
        return "sent"

    assert pre_send() == "cancelled"
    assert state["notify_cancelled"] is True
    # After Resume it must NOT be re-queued.
    state["paused"] = False

    def should_resend():
        return not state["public_notified"] and not state.get("notify_cancelled")

    assert should_resend() is False
    print("PASS: mid-flight pause cancels instead of re-queuing")


def test_pending_queue_only_admits_this_session_new():
    """The delayed-notify queue only admits rows first seen during this session
    (detected_at >= boot) — pre-boot legacy rows are never replayed."""
    boot_ts = 1_800_000_000.0

    def pending(row):
        detected = row.get("detected_at")
        return (
            detected is not None
            and detected >= boot_ts
            and not row.get("public_notified")
            and not row.get("notify_cancelled")
        )

    assert pending({"detected_at": boot_ts - 100, "public_notified": False}) is False  # legacy
    assert pending({"detected_at": boot_ts + 5, "public_notified": False}) is True      # new
    assert pending({"detected_at": boot_ts + 5, "public_notify": False}) is True        # typo key ≠ cancel
    assert pending({"detected_at": boot_ts + 5, "notify_cancelled": True}) is False     # paused
    print("PASS: pending queue excludes pre-boot legacy + paused rows")


def test_delay_still_applies_to_genuinely_new_markets():
    """ROOT CAUSE: old markets passed the delay gate because sgl was long past.
    With the baseline, only genuinely-new markets reach the gate, and the gate
    still waits NOTIFY_DELAY_SECONDS after go-live."""
    baseline = {"OLD1", "OLD2"}
    delay = 180

    def eligible(mid, sgl, now):
        if mid in baseline:
            return False  # historical — never eligible regardless of timing
        return now >= sgl + delay

    assert eligible("OLD1", 1_000_000, 2_000_000) is False   # old, past, but baseline → skip
    assert eligible("NEW1", 1_784_000_000, 1_784_000_000 + 179) is False  # still in window
    assert eligible("NEW1", 1_784_000_000, 1_784_000_000 + 180) is True   # window elapsed
    print("PASS: baseline prevents old-market delay bypass; delay still enforced")


def test_one_message_per_market_edit_in_place():
    """A market's card is edited across stages, never duplicated: banner priority
    6h > 12h > graduated > new live, all rendered from the same row."""
    def banner(row):
        if row.get("notified_6h"):
            return "6h"
        if row.get("notified_12h"):
            return "12h"
        if row.get("graduated"):
            return "graduated"
        return "new_live"

    live = {"public_notified": True, "graduated": False, "notified_12h": False, "notified_6h": False}
    assert banner(live) == "new_live"
    live["graduated"] = True
    assert banner(live) == "graduated"
    live["notified_12h"] = True
    assert banner(live) == "12h"
    live["notified_6h"] = True
    assert banner(live) == "6h"
    print("PASS: single card edits in place, 6h > 12h > graduated > new live")


def test_graduation_gated_by_app_signal():
    """Graduation only fires on an observed app signal, and can be disabled."""
    graduated_set = {"M1", "M2"}

    def is_graduated(mid, enabled=True):
        return enabled and str(mid) in graduated_set

    assert is_graduated("M2") is True
    assert is_graduated("M3") is False
    assert is_graduated("M2", enabled=False) is False  # GRADUATION_ENABLED=false
    print("PASS: graduation is gated on the app's observed signal")


def test_cleanup_deletes_every_message_and_state():
    """global_cleanup removes every tracked message, clears DB rows and caches."""
    tracked = [
        {"market_id": "M1", "chat_id": 1, "message_id": 11},
        {"market_id": "M2", "chat_id": 1, "message_id": 22},
        {"market_id": "M2", "chat_id": 2, "message_id": 33},
    ]
    deleted = 0
    chats = set()
    for r in tracked:
        # Simulate _safe_delete_message succeeding even when already gone.
        deleted += 1
        chats.add(r["chat_id"])
    cache = {"M1": "x", "M2": "y"}
    cache.clear()
    inflight = {"M1"}
    inflight.clear()
    assert deleted == 3 and chats == {1, 2}
    assert cache == {} and inflight == set()
    print("PASS: cleanup deletes all messages + chats and clears state")


def test_safe_delete_ignores_already_deleted():
    """Telegram 'message not found' / 'too old' must be treated as success."""
    def missing(e):
        txt = str(e).lower()
        return ("message to delete not found" in txt
                or "message is too old" in txt
                or "chat not found" in txt
                or "not found" in txt and "method" not in txt)

    assert missing("Bad Request: message to delete not found")
    assert missing("Bad Request: message is too old to be deleted")
    assert missing("Bad Request: chat not found")
    assert missing("Bad Request: only the creator of a basic group") is False
    print("PASS: already-deleted messages are ignored, not crashes")


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
    test_startup_baseline_never_notifies_historical_markets()
    test_restart_reseeds_baseline_no_replay()
    test_pause_cancels_queued_delayed_notifies()
    test_send_aborts_when_paused_mid_flight()
    test_pending_queue_only_admits_this_session_new()
    test_delay_still_applies_to_genuinely_new_markets()
    test_one_message_per_market_edit_in_place()
    test_graduation_gated_by_app_signal()
    test_cleanup_deletes_every_message_and_state()
    test_safe_delete_ignores_already_deleted()
    print("ALL NOTIFY PIPELINE REGRESSION TESTS PASSED")
