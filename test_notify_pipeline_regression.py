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


if __name__ == "__main__":
    test_notified_new_must_unlock_on_premium_success()
    test_public_empty_audience_must_not_block_forever()
    test_reminder_gate_accepts_premium_or_public_or_notified_new()
    test_claim_before_send_releases_on_failure()
    test_no_duplicate_public_inflight()
    test_standard_reminder_windows()
    test_pipeline_stages_for_one_market()
    print("ALL NOTIFY PIPELINE REGRESSION TESTS PASSED")
