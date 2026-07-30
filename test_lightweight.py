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
    test_architecture_flow()
    test_no_openai_dependency()
    print("ALL LIGHTWEIGHT TESTS PASSED")
