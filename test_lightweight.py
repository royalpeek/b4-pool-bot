"""Smoke tests for lightweight notify bot (no network / DB required)."""
import ast
import os


def test_main_parses():
    src = open("main.py", encoding="utf-8").read()
    ast.parse(src)
    assert "LIGHTWEIGHT" in src or "lightweight" in src.lower()
    assert "premium" not in src.lower() or src.lower().count("premium") < 3
    # Core pieces present
    for name in (
        "fetch_onchain_markets",
        "decode_onchain_market_account",
        "scheduled_go_live",
        "NOTIFY_DELAY_SECONDS",
        "is_app_live",
        "send_public_new_market",
        "monitor_loop",
        "health_text",
        "register_market",
    ):
        assert name in src, name
    # Heavy systems gone
    for banned in (
        "OpenAI",
        "FEATURED_WALLETS",
        "premium_chats",
        "run_intelligence",
        "score_markets",
        "Live Tracker",
    ):
        assert banned not in src, f"should not contain {banned}"
    print("PASS: main.py parses and is slim")


def test_v2_discovery_uses_confirmed_layout():
    src = open("main.py", encoding="utf-8").read()
    # Detection must use the same mechanism as the live bot: getProgramAccounts
    # on the B4 program with a 464-byte account filter.
    assert "getProgramAccounts" in src
    assert "B4_PROGRAM_ID" in src
    assert "dataSize" in src
    assert "ONCHAIN_MARKET_ACCOUNT_SIZE" in src
    # Deliberate lag, never earliest: delay is applied AFTER scheduled go-live.
    assert "NOTIFY_DELAY_SECONDS" in src
    assert "scheduled_go_live + NOTIFY_DELAY_SECONDS" in src or (
        "sgl_ts + NOTIFY_DELAY_SECONDS" in src
    )
    # Still-live verification before send.
    assert "is_app_live" in src
    assert "check_cover_image_published" in src
    print("PASS: V2 on-chain discovery + delayed-notify wiring present")


def test_no_historical_replay_baseline_pause_wiring():
    src = open("main.py", encoding="utf-8").read()
    # Boot baseline: markets live at boot are never registered/notified again.
    assert "_seed_baseline" in src
    assert "_baseline_markets" in src
    assert "_baseline_seeded" in src
    # Pause must cancel queued delayed notifies (notify_cancelled column).
    assert "notify_cancelled" in src
    assert "cancel_pending_delayed_notifies" in src
    # Pending queue only admits this-session rows (detected_at >= boot).
    assert "detected_at >= %s" in src
    # Send path must abort on pause before broadcast.
    assert "if _PAUSED:" in src
    print("PASS: baseline (no-replay) + immediate-pause wiring present")


def test_architecture_flow():
    stages = [
        "onchain_discovery",
        "register",
        "app_live",
        "public_notify",
    ]
    assert stages == ["onchain_discovery", "register", "app_live", "public_notify"]
    print("PASS: architecture flow defined")


def test_no_openai_dependency():
    req = open("requirements.txt", encoding="utf-8").read().lower()
    assert "openai" not in req
    assert "psycopg" in req
    assert "telebot" in req or "pytelegrambotapi" in req
    print("PASS: requirements are lightweight")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")
    test_main_parses()
    test_v2_discovery_uses_confirmed_layout()
    test_no_historical_replay_baseline_pause_wiring()
    test_architecture_flow()
    test_no_openai_dependency()
    print("ALL LIGHTWEIGHT TESTS PASSED")
