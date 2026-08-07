"""
B4 Notify Bot — lightweight public edition.

On-chain V2 discovery (getProgramAccounts) + official B4 API → new market
detection → intentionally delayed public Telegram notification (waits
NOTIFY_DELAY_SECONDS after go-live, verifies the market is still live, then
sends once). Optimized for free-tier hosts (Render): low memory, one loop.
"""
print("B4 Notify Bot — lightweight public edition (V2 on-chain + API, delayed notify)")

import base64
import html
import logging
import os
import struct
import time
import urllib.parse
from datetime import datetime, timezone
from threading import Thread

import psycopg
import requests
import telebot
from flask import Flask
from telebot import types
from telebot.types import BotCommand

# ── Config ──────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("b4-notify-lite")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")

_raw_db_url = os.getenv("DATABASE_URL")
if not _raw_db_url:
    raise RuntimeError("DATABASE_URL is required")

# Railway external PostgreSQL requires sslmode=require
_parsed = urllib.parse.urlparse(_raw_db_url)
if "sslmode" not in _parsed.query:
    _sep = "&" if _parsed.query else "?"
    DATABASE_URL = f"{_raw_db_url}{_sep}sslmode=require"
else:
    DATABASE_URL = _raw_db_url

_db_parsed = urllib.parse.urlparse(DATABASE_URL)
logger.info(
    "database: host=%s port=%s ssl=%s",
    _db_parsed.hostname or "?",
    _db_parsed.port or 5432,
    any(q.startswith("sslmode=") for q in _db_parsed.query.split("&")),
)

ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)

MARKET_LINK_BASE = os.getenv("MARKET_LINK_BASE", "https://www.b4app.xyz/m").rstrip("/")
B4_API_URL = os.getenv("B4_API_URL", "https://www.b4app.xyz/api/markets")

MARKET_POLL_SECONDS = float(os.getenv("MARKET_POLL_SECONDS", "5"))
NEW_MARKET_DELAY_SECONDS = float(os.getenv("NEW_MARKET_DELAY_SECONDS", "30"))
SEND_DELAY_SECONDS = float(os.getenv("SEND_DELAY_SECONDS", "0.08"))
DELETE_DELAY_SECONDS = float(os.getenv("DELETE_DELAY_SECONDS", "0.2"))
API_TIMEOUT = float(os.getenv("API_TIMEOUT_SECONDS", "8"))
ENABLE_REMINDERS = os.getenv("ENABLE_REMINDERS", "true").lower() == "true"
REMINDER_12H = float(os.getenv("REMINDER_12H_SECONDS", "43200"))
REMINDER_6H = float(os.getenv("REMINDER_6H_SECONDS", "21600"))
# Graduation (V2 lifecycle): the app signals a market has graduated from its
# bonding curve. The notify bot re-renders the market's single message when the
# signal is observed. Detection uses whatever the app itself exposes (API
# `graduated`/`status`); the on-chain 81-byte graduation account exists but its
# flag offset is not yet confirmed, so on-chain detection is a documented
# placeholder (see refresh_graduation). Safe to leave enabled — it only fires on
# a real observed signal.
GRADUATION_ENABLED = os.getenv("GRADUATION_ENABLED", "true").lower() == "true"

# ── V2 on-chain discovery + delayed notify ─────────────────────────────────
# Detection uses the same mechanism as the live bot: getProgramAccounts on the
# B4 program with a 464-byte account filter, decoded with the confirmed layout
# (market_id u64 @ 0x08, title_byte_len u32 @ 0x30, title @ 0x34,
# desc_len @ title_end, end_time u32 @ desc_end+32).
ONCHAIN_ENABLED = os.getenv("ONCHAIN_ENABLED", "true").lower() == "true"
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
B4_PROGRAM_ID = os.getenv("B4_PROGRAM_ID", "9XQDD38sy1qJ57DqAQvADuLRTjcYUXD48H7deyNuaehH")
ONCHAIN_POLL_SECONDS = float(os.getenv("ONCHAIN_POLL_SECONDS", "5"))
ONCHAIN_MARKET_ACCOUNT_SIZE = int(os.getenv("ONCHAIN_MARKET_ACCOUNT_SIZE", "464"))
ONCHAIN_MARKET_ID_OFFSET = int(os.getenv("ONCHAIN_MARKET_ID_OFFSET", "8"))
ONCHAIN_TITLE_LENGTH_OFFSET = int(os.getenv("ONCHAIN_TITLE_LENGTH_OFFSET", "48"))
ONCHAIN_TITLE_OFFSET = int(os.getenv("ONCHAIN_TITLE_OFFSET", "52"))
ONCHAIN_MARKET_DURATION_SECONDS = int(os.getenv("ONCHAIN_MARKET_DURATION_SECONDS", "86400"))
# Intentional delay after the scheduled go-live before the public notify fires.
# The live bot notifies earliest; this bot deliberately lags (2–5 min default).
NOTIFY_DELAY_SECONDS = float(os.getenv("NOTIFY_DELAY_SECONDS", "180"))
COVER_LIVE_CACHE_SECONDS = float(os.getenv("COVER_LIVE_CACHE_SECONDS", "45"))

_PAUSED = False
_cover_live_cache: dict = {}
_inflight_public: set = set()
last_onchain_poll_at = 0.0

# V2 lifecycle: markets the app has reported as graduated (from the market's
# own API signal). A market in this set gets its single message re-rendered
# with a "🎓 MARKET GRADUATED" banner.
_graduated_markets: set = set()

# Startup baseline (no historical replay): market_ids that were already live when
# this process booted. They are historical from the bot's perspective and must
# NEVER be registered as new / notified. Only markets first seen AFTER the
# baseline may be delayed-notified. Reseeded from then-live markets on every boot.
_baseline_markets: set = set()
_baseline_seeded = False
_boot_ts = time.time()
_last_onchain_fetch_ok = False
_last_api_fetch_ok = False

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

# Process health (in-memory)
HEALTH = {
    "started_at": time.time(),
    "status": "starting",
    "last_scan_at": 0.0,
    "last_eval_at": 0.0,
    "last_notification_at": 0.0,
    "last_notification_title": "",
    "last_market_count": 0,
    "markets_registered": 0,
    "notifications_sent": 0,
    "loop_count": 0,
    "last_error": "",
    "onchain_loops": 0,
    "onchain_markets": 0,
    "last_onchain_at": 0.0,
    "graduated_markets": 0,
}


# ── Time / DB helpers ────────────────────────────────────────────────────────

def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _end_unix(value):
    """Parse an end_time value (unix number, ISO string or datetime) to epoch seconds."""
    if not value:
        return 0
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp())
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(float(value))
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return 0


def escape(text):
    return html.escape(str(text or ""), quote=False)


def get_db():
    return psycopg.connect(DATABASE_URL)


def init_db():
    last_err = None
    for attempt in range(1, 6):
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS subscribed_chats (
                            chat_id TEXT PRIMARY KEY,
                            chat_name TEXT,
                            joined_at TIMESTAMP DEFAULT NOW()
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS bot_state (
                            key TEXT PRIMARY KEY,
                            value TEXT
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS announced_markets (
                            market_id TEXT PRIMARY KEY,
                            title TEXT,
                            end_time TEXT,
                            market_link TEXT,
                            source TEXT DEFAULT 'api',
                            public_notified BOOLEAN DEFAULT FALSE,
                            notified_1h BOOLEAN DEFAULT FALSE,
                            notified_10m BOOLEAN DEFAULT FALSE,
                            notified_ended BOOLEAN DEFAULT FALSE,
                            notify_cancelled BOOLEAN DEFAULT FALSE,
                            graduated BOOLEAN DEFAULT FALSE,
                            notified_12h BOOLEAN DEFAULT FALSE,
                            notified_6h BOOLEAN DEFAULT FALSE,
                            public_sent_at TIMESTAMP,
                            detected_at TIMESTAMP DEFAULT NOW(),
                            updated_at TIMESTAMP DEFAULT NOW(),
                            scheduled_go_live TEXT,
                            app_live_at TIMESTAMP
                        )
                        """
                    )
                    # Track sent message IDs for cleanup
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS market_messages (
                            id SERIAL PRIMARY KEY,
                            market_id TEXT NOT NULL,
                            chat_id TEXT NOT NULL,
                            message_id BIGINT NOT NULL,
                            msg_type TEXT DEFAULT 'new',
                            sent_at TIMESTAMP DEFAULT NOW()
                        )
                        """
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_market_messages_market ON market_messages (market_id)"
                    )
                    # Compat columns for existing databases
                    for stmt in (
                        "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS public_notified BOOLEAN DEFAULT FALSE",
                        "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS notified_1h BOOLEAN DEFAULT FALSE",
                        "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS notified_10m BOOLEAN DEFAULT FALSE",
                        "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS notified_ended BOOLEAN DEFAULT FALSE",
                        "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS notify_cancelled BOOLEAN DEFAULT FALSE",
                        "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS graduated BOOLEAN DEFAULT FALSE",
                        "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS notified_12h BOOLEAN DEFAULT FALSE",
                        "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS notified_6h BOOLEAN DEFAULT FALSE",
                        "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS public_sent_at TIMESTAMP",
                        "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS market_link TEXT",
                        "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS end_time TEXT",
                        "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS title TEXT",
                        "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()",
                        "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS detected_at TIMESTAMP DEFAULT NOW()",
                        "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS scheduled_go_live TEXT",
                        "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS app_live_at TIMESTAMP",
                    ):
                        try:
                            cur.execute(stmt)
                        except Exception:
                            pass
            logger.info("database ready")
            return
        except Exception as e:
            last_err = e
            if attempt < 5:
                wait = 2 ** attempt
                logger.warning("DB connection attempt %d/5 failed, retrying in %ss: %s", attempt, wait, e)
                time.sleep(wait)
    logger.critical("DB connection failed after 5 attempts: %s", last_err)
    raise last_err


def set_state(key, value):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO bot_state (key, value) VALUES (%s, %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                    """,
                    (key, str(value)),
                )
    except Exception as e:
        logger.error("set_state %s: %s", key, e)


def get_state(key, default=""):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM bot_state WHERE key = %s", (key,))
                row = cur.fetchone()
                return row[0] if row else default
    except Exception:
        return default


def add_chat(chat_id, chat_name=""):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO subscribed_chats (chat_id, chat_name)
                    VALUES (%s, %s)
                    ON CONFLICT (chat_id) DO UPDATE SET chat_name = EXCLUDED.chat_name
                    """,
                    (str(chat_id), chat_name or str(chat_id)),
                )
        return True
    except Exception as e:
        logger.error("add_chat: %s", e)
        return False


def get_all_chats():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT chat_id FROM subscribed_chats")
                return [r[0] for r in cur.fetchall()]
    except Exception as e:
        logger.error("get_all_chats: %s", e)
        return []


def get_market(market_id):
    try:
        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("SELECT * FROM announced_markets WHERE market_id = %s", (str(market_id),))
                return cur.fetchone()
    except Exception as e:
        logger.error("get_market %s: %s", market_id, e)
        return None


def get_all_markets():
    try:
        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("SELECT * FROM announced_markets WHERE COALESCE(notified_ended, FALSE) = FALSE")
                return cur.fetchall()
    except Exception as e:
        logger.error("get_all_markets: %s", e)
        return []


def register_market(market):
    """Insert discovery row if new. Returns (row, is_new)."""
    mid = str(market.get("market_id", "")).strip()
    if not mid:
        return None, False
    existing = get_market(mid)
    if existing:
        # Backfill schedule / source when first learned (on-chain → API merge).
        update_schedule = False
        if not existing.get("scheduled_go_live"):
            sgl = market.get("scheduled_go_live")
            if sgl:
                existing["scheduled_go_live"] = sgl
                update_schedule = True
        if existing.get("source") in (None, "", "api") and market.get("source") == "onchain":
            existing["source"] = "onchain"
            update_schedule = True
        if update_schedule:
            try:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE announced_markets SET scheduled_go_live = %s, source = %s, updated_at = NOW() WHERE market_id = %s",
                            (existing.get("scheduled_go_live"), existing.get("source"), mid),
                        )
            except Exception as e:
                logger.error("update schedule %s: %s", mid, e)
        return existing, False

    title = str(market.get("title", "")).strip()
    end_unix = int(market.get("end_time") or 0)
    end_iso = datetime.fromtimestamp(end_unix, tz=timezone.utc).replace(tzinfo=None).isoformat()
    link = f"{MARKET_LINK_BASE}/{mid}"
    source = str(market.get("source") or "api").strip() or "api"
    sgl = market.get("scheduled_go_live")
    sgl_iso = None
    if sgl:
        sgl_ts = int(float(sgl))
        if sgl_ts > 0:
            sgl_iso = datetime.fromtimestamp(sgl_ts, tz=timezone.utc).replace(tzinfo=None).isoformat()
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO announced_markets (
                        market_id, title, end_time, market_link, source, scheduled_go_live
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (market_id) DO NOTHING
                    RETURNING market_id
                    """,
                    (mid, title, end_iso, link, source, sgl_iso),
                )
                inserted = cur.fetchone() is not None
        if inserted:
            HEALTH["markets_registered"] = int(HEALTH.get("markets_registered", 0)) + 1
            logger.info("REGISTERED market=%s title=%s source=%s sgl=%s", mid, title[:50], source, sgl_iso)
        return get_market(mid), inserted
    except Exception as e:
        logger.critical("REGISTER failed market=%s error=%s", mid, e)
        HEALTH["last_error"] = f"register:{e}"
        return get_market(mid), False


def claim_flag(market_id, flag):
    allowed = {
        "public_notified", "notified_1h", "notified_10m", "notified_ended",
        "graduated", "notified_12h", "notified_6h",
    }
    if flag not in allowed:
        return False
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE announced_markets
                    SET {flag} = TRUE, updated_at = NOW()
                    WHERE market_id = %s AND COALESCE({flag}, FALSE) = FALSE
                    RETURNING market_id
                    """,
                    (str(market_id),),
                )
                return cur.fetchone() is not None
    except Exception as e:
        logger.error("claim_flag %s %s: %s", market_id, flag, e)
        return False


def release_flag(market_id, flag):
    allowed = {"public_notified", "notified_1h", "notified_10m", "notified_ended", "graduated", "notified_12h", "notified_6h"}
    if flag not in allowed:
        return
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE announced_markets SET {flag} = FALSE WHERE market_id = %s",
                    (str(market_id),),
                )
    except Exception as e:
        logger.error("release_flag %s: %s", market_id, e)


def _track_message(market_id, chat_id, message_id, msg_type="new"):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO market_messages (market_id, chat_id, message_id, msg_type) VALUES (%s, %s, %s, %s)",
                    (str(market_id), str(chat_id), int(message_id), msg_type),
                )
    except Exception as e:
        logger.warning("track_message %s: %s", market_id, e)


def _get_market_messages(market_id):
    try:
        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute(
                    "SELECT chat_id, message_id FROM market_messages WHERE market_id = %s",
                    (str(market_id),),
                )
                return cur.fetchall()
    except Exception as e:
        logger.warning("get_market_messages %s: %s", market_id, e)
        return []


def _delete_market_messages(market_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM market_messages WHERE market_id = %s", (str(market_id),))
    except Exception as e:
        logger.warning("delete_market_messages %s: %s", market_id, e)


def _delete_all_market_messages():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM market_messages")
    except Exception as e:
        logger.error("clear market_messages: %s", e)


def _telegram_missing(e):
    """True when Telegram says the message is already gone (safe to ignore)."""
    txt = str(getattr(e, "text", "") or e)
    low = txt.lower()
    return (
        "not found" in low
        or "message to delete not found" in low
        or "message to edit not found" in low
        or ("bad request: message" in low and "can't be deleted" not in low)
    )


def _safe_delete_message(chat_id, message_id, retries=3):
    """Delete a message, tolerating already-deleted messages and transient
    errors (retried with backoff). Never raises. Returns True when gone."""
    for attempt in range(retries):
        try:
            bot.delete_message(int(chat_id), int(message_id))
            return True
        except Exception as e:
            if _telegram_missing(e):
                return True  # already deleted — treat as success
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 8))
            else:
                logger.warning("delete msg chat=%s mid=%s: %s", chat_id, message_id, e)
    return False


def _safe_edit_message(chat_id, message_id, text, keyboard=None, retries=3):
    """Edit a message, tolerating already-deleted messages and transient errors.
    Never raises. Returns True when the edit succeeded."""
    for attempt in range(retries):
        try:
            bot.edit_message_text(
                text,
                chat_id=int(chat_id),
                message_id=int(message_id),
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_web_page_preview=False,
            )
            return True
        except Exception as e:
            if _telegram_missing(e):
                return False  # message gone — nothing left to edit
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 8))
            else:
                logger.warning("edit msg chat=%s mid=%s: %s", chat_id, message_id, e)
    return False


def cleanup_market(market_id):
    """Market closed: delete every notification, clear all state.

    After cleanup the market is as if it was never announced — user chats,
    groups, the message DB, the cover cache, and in-memory state are all clean.
    Never raises; per-message failures (already deleted) are ignored.
    """
    market_id = str(market_id)
    logger.info("Market ended mid=%s: starting cleanup", market_id)
    messages = _get_market_messages(market_id)
    deleted = 0
    for row in messages:
        if _safe_delete_message(row.get("chat_id"), row.get("message_id")):
            deleted += 1
        time.sleep(DELETE_DELAY_SECONDS)
    logger.info("Market ended mid=%s: deleted %s Telegram messages", market_id, deleted)
    _delete_market_messages(market_id)
    _cover_live_cache.pop(market_id, None)
    _inflight_public.discard(market_id)
    _graduated_markets.discard(market_id)
    logger.info("Market ended mid=%s: cleanup complete", market_id)


def is_admin(uid):
    return ADMIN_ID and int(uid) == ADMIN_ID


def market_link(market_id):
    return f"{MARKET_LINK_BASE}/{market_id}"


def fetch_api_markets():
    """Fetch active markets from the official B4 API."""
    global _last_api_fetch_ok
    try:
        r = requests.get(
            B4_API_URL,
            params={"page": 1, "limit": 50, "_": int(time.time())},
            headers={"Cache-Control": "no-cache"},
            timeout=API_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        markets = data.get("markets", data) if isinstance(data, dict) else data
        if not isinstance(markets, list):
            _last_api_fetch_ok = True
            return []
        out = []
        now_ts = int(time.time())
        for m in markets:
            mid = str(m.get("market_id", "")).strip()
            title = str(m.get("title", "")).strip()
            end = int(m.get("end_time") or 0)
            if not mid or not title or end <= now_ts:
                continue
            if m.get("hidden") or m.get("resolved"):
                continue
            # Prefer the API's go_live_at; else derive end_time - 86400 (confirmed).
            sgl = m.get("go_live_at") or m.get("go_live_at_scheduled")
            if not sgl:
                sgl = end - ONCHAIN_MARKET_DURATION_SECONDS
            try:
                sgl_float = float(sgl)
            except Exception:
                sgl_float = float(end - ONCHAIN_MARKET_DURATION_SECONDS)
            out.append({
                "market_id": mid,
                "title": title,
                "end_time": end,
                "scheduled_go_live": sgl_float,
                "source": "api",
                "cover_image_status": str(m.get("cover_image_status") or "").lower(),
                "go_live_at": m.get("go_live_at") or m.get("go_live_at_scheduled"),
            })
        HEALTH["last_market_count"] = len(out)
        HEALTH["last_scan_at"] = time.time()
        set_state("last_api_check", now_utc().isoformat())
        _last_api_fetch_ok = True
        return out
    except Exception as e:
        logger.error("fetch_api_markets: %s", e)
        HEALTH["last_error"] = f"api:{e}"
        _last_api_fetch_ok = False
        return []


# ── V2 on-chain discovery (confirmed 464-byte layout) ──────────────────────

def read_u64_le(data, offset):
    if offset < 0 or offset + 8 > len(data):
        return None
    return struct.unpack_from("<Q", data, offset)[0]


def read_u32_le(data, offset):
    if offset < 0 or offset + 4 > len(data):
        return None
    return struct.unpack_from("<I", data, offset)[0]


def decode_onchain_market_account(pubkey, encoded_data):
    """Decode a 464-byte B4 market account (confirmed layout).

    market_id u64 @ 0x08, title_byte_len u32 @ 0x30, title @ 0x34,
    desc_byte_len u32 @ title_end, end_time u32 @ desc_end+32.
    """
    try:
        raw_data = encoded_data[0] if isinstance(encoded_data, list) else encoded_data
        data = base64.b64decode(raw_data)
        market_id = read_u64_le(data, ONCHAIN_MARKET_ID_OFFSET)
        title_len = read_u32_le(data, ONCHAIN_TITLE_LENGTH_OFFSET)
        if not market_id or not title_len:
            return None
        if market_id < 1_700_000_000_000_000 or market_id > 1_900_000_000_000_000:
            return None
        if title_len < 6 or title_len > 180:
            return None
        title_end = ONCHAIN_TITLE_OFFSET + title_len
        if title_end > len(data):
            return None
        title = data[ONCHAIN_TITLE_OFFSET:title_end].decode("utf-8", errors="strict").strip("\x00").strip()
        if len(title) < 6:
            return None
        desc_len = read_u32_le(data, title_end) or 0
        desc_end = title_end + 4 + desc_len
        if desc_end + 36 > len(data):
            return None
        end_time = read_u32_le(data, desc_end + 32)
        if not end_time:
            return None
        scheduled_go_live = end_time - ONCHAIN_MARKET_DURATION_SECONDS
        return {
            "market_id": str(market_id),
            "title": title,
            "end_time": end_time,
            "scheduled_go_live": float(scheduled_go_live),
            "source": "onchain",
        }
    except Exception as e:
        logger.debug("could not decode on-chain market account %s: %s", pubkey, e)
        return None


def fetch_onchain_markets():
    """Discover live V2 markets on-chain (same mechanism as the live bot)."""
    global _last_onchain_fetch_ok
    if not ONCHAIN_ENABLED:
        _last_onchain_fetch_ok = True
        return []
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getProgramAccounts",
            "params": [
                B4_PROGRAM_ID,
                {
                    "encoding": "base64",
                    "commitment": "confirmed",
                    "filters": [{"dataSize": ONCHAIN_MARKET_ACCOUNT_SIZE}],
                    "dataSlice": {"offset": 0, "length": ONCHAIN_MARKET_ACCOUNT_SIZE},
                },
            ],
        }
        response = requests.post(SOLANA_RPC_URL, json=payload, timeout=max(API_TIMEOUT, 12))
        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            logger.error("solana rpc error: %s", data["error"])
            _last_onchain_fetch_ok = False
            return []
        markets = []
        now_ts = int(time.time())
        for account in data.get("result", []):
            market = decode_onchain_market_account(
                account.get("pubkey"),
                account.get("account", {}).get("data"),
            )
            if not market:
                continue
            if int(market["end_time"]) <= now_ts:
                continue
            markets.append(market)
        HEALTH["last_onchain_at"] = time.time()
        HEALTH["onchain_markets"] = len(markets)
        logger.info("on-chain discovery: %s live markets", len(markets))
        _last_onchain_fetch_ok = True
        return markets
    except Exception as e:
        logger.error("fetch_onchain_markets: %s", e)
        HEALTH["last_error"] = f"onchain:{e}"
        _last_onchain_fetch_ok = False
        return []


def _discover_graduation_accounts():
    """Best-effort on-chain graduation state.

    V2 (2026) added an `InitGraduation` instruction that creates an 81-byte
    account owned by the B4 program (`discriminator 0x44aea24530fa12ed`); the
    payload updates as users bet (bonding progress). The exact "graduated" flag
    offset is not yet confirmed, so this only reports the count (for /status).
    Once the flag offset is verified, return the graduated market_ids here and
    graduation fires from on-chain too. Never raises.
    """
    if not ONCHAIN_ENABLED:
        return 0
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getProgramAccounts",
            "params": [
                B4_PROGRAM_ID,
                {
                    "encoding": "base64",
                    "commitment": "confirmed",
                    "filters": [{"dataSize": 81}],
                    "dataSlice": {"offset": 0, "length": 8},
                },
            ],
        }
        response = requests.post(SOLANA_RPC_URL, json=payload, timeout=max(API_TIMEOUT, 12))
        response.raise_for_status()
        return len(response.json().get("result", []))
    except Exception as e:
        logger.debug("graduation account discovery: %s", e)
        return 0


def refresh_graduation(api_markets):
    """Refresh the set of graduated market_ids from the app's own signals.

    Signal 1 (authoritative today): the B4 API reports graduation for a market
    (a `graduated` flag / `graduated` status). This is whatever the app itself
    exposes — when the product reports graduation, the bot sees it here.
    Signal 2 (placeholder): on-chain 81-byte graduation accounts, pending the
    confirmed flag offset. Only observed signals ever mark a market graduated,
    so this can never fire a false announcement.
    """
    global _graduated_markets
    if not GRADUATION_ENABLED:
        return set()
    ids: set = set()
    for m in api_markets or []:
        mid = str(m.get("market_id") or "").strip()
        if not mid:
            continue
        raw_grad = m.get("graduated")
        if raw_grad is True or str(raw_grad).strip().lower() in ("true", "1", "yes"):
            ids.add(mid)
        elif "graduated" in str(m.get("status") or "").strip().lower():
            ids.add(mid)
        elif str(m.get("bonding_status") or "").strip().lower() == "graduated":
            ids.add(mid)
    _graduated_markets = ids
    HEALTH["graduated_markets"] = len(ids)
    return ids


def is_graduated(market_id):
    return GRADUATION_ENABLED and str(market_id) in _graduated_markets


def _seed_baseline():
    """Snapshot the markets live at boot (no historical replay).

    Every market already on-chain/in the API when this process starts is
    historical from the bot's perspective and must NEVER be notified. Only
    market_ids first seen after the baseline may be registered and delayed-
    notified. Returns True once a baseline is recorded (retry if both sources
    failed, so an outage at boot cannot turn into an empty baseline).
    """
    global _baseline_markets, _baseline_seeded
    if _baseline_seeded:
        return True
    ids: set = set()
    onchain = fetch_onchain_markets()
    for m in onchain:
        ids.add(str(m.get("market_id") or "").strip())
    api = fetch_api_markets()
    for m in api:
        ids.add(str(m.get("market_id") or "").strip())
    ids.discard("")
    if not _last_onchain_fetch_ok and not _last_api_fetch_ok:
        logger.warning("baseline sources unavailable at boot, retrying")
        return False
    _baseline_markets = ids
    _baseline_seeded = True
    logger.info("BASELINE seeded: %s live markets at boot (never replayed)", len(ids))
    return True


def _mark_notify_cancelled(market_id):
    """Mark a single market's delayed notify as cancelled (pause aborts)."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE announced_markets SET notify_cancelled = TRUE, updated_at = NOW() WHERE market_id = %s",
                    (str(market_id),),
                )
    except Exception as e:
        logger.error("mark notify_cancelled %s: %s", market_id, e)


def cancel_pending_delayed_notifies():
    """Pause cancels already-queued delayed notifies.

    The delayed-notify 'queue' is the set of rows with public_notified = FALSE.
    On pause these are cancelled (notify_cancelled = TRUE) so Resume never sends
    them; Resume applies only to markets discovered after it.
    """
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE announced_markets
                    SET notify_cancelled = TRUE, updated_at = NOW()
                    WHERE COALESCE(public_notified, FALSE) = FALSE
                      AND COALESCE(notified_ended, FALSE) = FALSE
                      AND COALESCE(notify_cancelled, FALSE) = FALSE
                    """
                )
                logger.warning("PAUSE cancelled %s queued delayed notifies", cur.rowcount)
    except Exception as e:
        logger.error("cancel_pending_delayed_notifies: %s", e)


# ── APP_LIVE / still-live verification (cover HEAD, cached) ────────────────

def check_cover_image_published(market_id, use_cache=True):
    """True when the cover image is published (APP_LIVE signal), cached."""
    market_id = str(market_id or "").strip()
    if not market_id:
        return False, 0
    now_m = time.monotonic()
    if use_cache:
        cached = _cover_live_cache.get(market_id)
        if cached and cached[2] > now_m:
            return cached[0], cached[1]
    url = f"https://www.b4app.xyz/api/assets/market-cover/{market_id}.png"
    try:
        resp = requests.head(url, timeout=min(API_TIMEOUT, 6), allow_redirects=True)
        ok, code = resp.status_code == 200, resp.status_code
    except Exception as e:
        logger.debug("cover HEAD failed for %s: %s", market_id, e)
        ok, code = False, 0
    _cover_live_cache[market_id] = (ok, code, now_m + COVER_LIVE_CACHE_SECONDS)
    if len(_cover_live_cache) > 2000:
        expired = [k for k, v in _cover_live_cache.items() if v[2] <= now_m]
        for k in expired[:500]:
            _cover_live_cache.pop(k, None)
    return ok, code


def is_app_live(market_id, market=None):
    """True when the market is live in the app (cover published).

    Trust API cover_image_status=ready (no network). On-chain uses cached HEAD.
    """
    market_id = str(market_id or (market or {}).get("market_id", "")).strip()
    if market:
        status = str(market.get("cover_image_status") or "").strip().lower()
        source = str(market.get("source") or "").strip().lower()
        if status == "ready":
            if source == "onchain":
                ok, _ = check_cover_image_published(market_id)
                return ok
            return True
    ok, _ = check_cover_image_published(market_id)
    return ok


def scheduled_go_live_passed(row):
    """True once the market's scheduled go-live + notify delay have elapsed.

    V2 on-chain markets carry scheduled_go_live (derived from end_time), so the
    notify deliberately lags go-live by NOTIFY_DELAY_SECONDS. V1 API-only rows
    fall back to the legacy detected_at + NEW_MARKET_DELAY_SECONDS window.
    """
    now_ts = time.time()
    sgl = row.get("scheduled_go_live")
    if sgl:
        sgl_ts = 0.0
        try:
            sgl_ts = float(sgl)
        except Exception:
            pass
        if isinstance(sgl, str) and not sgl.replace(".", "", 1).isdigit():
            try:
                dt = datetime.fromisoformat(sgl)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                sgl_ts = dt.timestamp()
            except Exception:
                sgl_ts = 0.0
        if sgl_ts > 0:
            return now_ts >= sgl_ts + NOTIFY_DELAY_SECONDS
    # Legacy fallback: delay measured from detection time.
    detected = row.get("detected_at")
    if detected:
        try:
            if isinstance(detected, datetime):
                dt = detected
            else:
                dt = datetime.fromisoformat(str(detected).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return now_ts >= dt.timestamp() + NEW_MARKET_DELAY_SECONDS
        except Exception:
            pass
    return True


def get_markets_pending_public():
    """Markets discovered but not yet publicly notified (and not ended).

    Only rows first seen during THIS session (detected_at >= boot) are eligible,
    so pre-boot legacy rows are never replayed after a restart, and paused
    (notify_cancelled) rows are never re-queued after Resume.
    """
    boot_iso = datetime.fromtimestamp(_boot_ts, tz=timezone.utc).replace(tzinfo=None).isoformat()
    try:
        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT * FROM announced_markets
                    WHERE COALESCE(public_notified, FALSE) = FALSE
                      AND COALESCE(notified_ended, FALSE) = FALSE
                      AND COALESCE(notify_cancelled, FALSE) = FALSE
                      AND detected_at >= %s
                    ORDER BY detected_at
                    """,
                    (boot_iso,),
                )
                return cur.fetchall()
    except Exception as e:
        logger.error("get_markets_pending_public: %s", e)
        return []


# ── Telegram broadcast ───────────────────────────────────────────────────────

def send_to_chat(chat_id, text, keyboard=None):
    try:
        msg = bot.send_message(
            int(chat_id),
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=False,
        )
        time.sleep(SEND_DELAY_SECONDS)
        return msg.message_id
    except Exception as e:
        logger.warning("send_to_chat %s: %s", chat_id, e)
        return None


def broadcast(text, keyboard=None, track_market_id=None, msg_type="new"):
    chats = get_all_chats()
    sent = 0
    for chat_id in chats:
        if _PAUSED:
            logger.info("broadcast aborted mid-way: paused")
            break
        msg_id = send_to_chat(chat_id, text, keyboard)
        if msg_id:
            sent += 1
            if track_market_id:
                _track_message(track_market_id, chat_id, msg_id, msg_type)
    if sent:
        HEALTH["last_notification_at"] = time.time()
        HEALTH["notifications_sent"] = int(HEALTH.get("notifications_sent", 0)) + sent
        set_state("last_notification_sent", now_utc().isoformat())
    logger.info("broadcast sent=%s chats=%s", sent, len(chats))
    return sent


def vote_keyboard(market_id):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Vote Now", url=market_link(market_id)))
    return kb


def _banner_for_row(row):
    """Most recent lifecycle milestone wins (6h > 12h > graduated > new live)."""
    if row.get("notified_6h"):
        return "⏰ <b>6 HOURS LEFT</b>"
    if row.get("notified_12h"):
        return "⏰ <b>12 HOURS LEFT</b>"
    if row.get("graduated"):
        return "🎓 <b>MARKET GRADUATED</b>"
    return "🟢 <b>NEW MARKET LIVE</b>"


def build_market_message(row):
    """Build the single market card. One message per market is sent and then
    EDITED as the market progresses (graduated → 12h → 6h) — never a new
    message per stage."""
    mid = str(row.get("market_id", "") or "").strip()
    title = str(row.get("title") or "").strip()
    end_unix = int(_end_unix(row.get("end_time")) or 0)
    end_str = datetime.fromtimestamp(end_unix, tz=timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    return (
        f"{_banner_for_row(row)}\n\n"
        f"🔥 <b>{escape(title)}</b>\n\n"
        f"⏰ Closes: {escape(end_str)}\n"
        f"🔗 <a href=\"{escape(market_link(mid))}\">Open in b4</a>"
    )


def edit_market_messages(market_id):
    """Re-render the market's single card in every chat (graduation / 12h / 6h).

    Edits the existing tracked message instead of sending a new one, so users
    only ever see one message per market. Safe: already-deleted messages are
    ignored and transient errors are retried. Returns the number edited.
    """
    market_id = str(market_id)
    row = get_market(market_id)
    if not row:
        return 0
    text = build_market_message(row)
    kb = vote_keyboard(market_id)
    edited = 0
    for m in _get_market_messages(market_id):
        if _safe_edit_message(m.get("chat_id"), m.get("message_id"), text, kb):
            edited += 1
        time.sleep(DELETE_DELAY_SECONDS)
    return edited


# ── Notification pipeline ────────────────────────────────────────────────────

def send_public_new_market(market, row):
    """Intentionally delayed → verify still live → public notification (once)."""
    mid = str(market.get("market_id", "")).strip()
    if not mid or not row:
        return False
    if row.get("public_notified"):
        return False
    if mid in _inflight_public:
        return False
    # Pause aborts immediately — queued delayed notifies are cancelled, never sent.
    if _PAUSED:
        return False
    # Historical markets live at boot must never be announced as new.
    if mid in _baseline_markets:
        return False
    if row.get("notify_cancelled"):
        return False

    # Intentional lag: never fire before scheduled go-live + notify delay.
    if not scheduled_go_live_passed(row):
        return False

    # Verify the market is actually live (cover published) before sending.
    if not is_app_live(mid, market):
        logger.debug("mid=%s not app-live yet, waiting", mid)
        return False

    if not claim_flag(mid, "public_notified"):
        return False
    _inflight_public.add(mid)
    try:
        title = str(market.get("title") or row.get("title") or "").strip()
        end_unix = int(market.get("end_time") or 0)

        latest = get_market(mid)
        if latest and latest.get("notified_ended"):
            release_flag(mid, "public_notified")
            return False

        # Re-verify after claim so a market that went dark is not announced.
        if not is_app_live(mid, market):
            release_flag(mid, "public_notified")
            logger.info("mid=%s went dark before notify, releasing claim", mid)
            return False

        # Pause during a mid-flight send must cancel, not release-and-retry.
        if _PAUSED:
            release_flag(mid, "public_notified")
            _mark_notify_cancelled(mid)
            logger.info("mid=%s paused before send, cancelled", mid)
            return False

        text = build_market_message({
            "market_id": mid,
            "title": title,
            "end_time": end_unix,
        })
        sent = broadcast(text, vote_keyboard(mid), track_market_id=mid, msg_type="new")
        if sent > 0:
            try:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE announced_markets SET public_sent_at = NOW(), updated_at = NOW() WHERE market_id = %s",
                            (mid,),
                        )
            except Exception as e:
                logger.error("stamp public_sent_at %s: %s", mid, e)
            HEALTH["last_notification_title"] = title[:80]
            set_state("last_market_detected", f"{mid} | {title}")
            logger.info("PUBLIC NEW MARKET sent mid=%s title=%s sent=%s", mid, title[:40], sent)
            return True

        release_flag(mid, "public_notified")
        logger.warning("PUBLIC NEW MARKET zero sends mid=%s (will retry)", mid)
        return False
    finally:
        _inflight_public.discard(mid)


def process_lifecycle():
    """Drive the V2 lifecycle for announced markets (edit-in-place, once each).

    - 🎓 Graduated: when the app reports the market graduated, re-render the card.
    - ⏰ 12 hours / 6 hours remaining: re-render the card, once each.
    - 🧹 Market closed: delete every message, clear all state (as if never sent).
    """
    now = now_utc()
    for row in get_all_markets():
        try:
            mid = str(row.get("market_id", "")).strip()
            if not mid or not row.get("public_notified"):
                continue
            et = row.get("end_time")
            if isinstance(et, datetime):
                end_dt = et.replace(tzinfo=None) if et.tzinfo else et
            else:
                end_dt = datetime.fromisoformat(str(et).replace("Z", "+00:00")).replace(tzinfo=None)
            left = (end_dt - now).total_seconds()

            if left <= 0:
                if not row.get("notified_ended") and claim_flag(mid, "notified_ended"):
                    cleanup_market(mid)
                continue

            changed = False
            # Graduated (immediate, once) — gate on the app's observed signal.
            if GRADUATION_ENABLED and not row.get("graduated") and is_graduated(mid):
                if claim_flag(mid, "graduated"):
                    logger.info("GRADUATED mid=%s title=%s", mid, (row.get("title") or "")[:40])
                    changed = True
            # 12h / 6h remaining (once each).
            if ENABLE_REMINDERS and not row.get("notified_12h") and left <= REMINDER_12H:
                if claim_flag(mid, "notified_12h"):
                    logger.info("12H LEFT mid=%s title=%s", mid, (row.get("title") or "")[:40])
                    changed = True
            if ENABLE_REMINDERS and not row.get("notified_6h") and left <= REMINDER_6H:
                if claim_flag(mid, "notified_6h"):
                    logger.info("6H LEFT mid=%s title=%s", mid, (row.get("title") or "")[:40])
                    changed = True

            if changed:
                edit_market_messages(mid)
        except Exception as e:
            logger.error("lifecycle error mid=%s: %s", row.get("market_id"), e)


def global_cleanup():
    """Admin wipe: delete every bot message across private chats, groups and
    channels, then clear every stored message ID. Returns (deleted, chats)."""
    try:
        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("SELECT market_id, chat_id, message_id FROM market_messages")
                rows = cur.fetchall()
    except Exception as e:
        logger.error("global_cleanup read: %s", e)
        return 0, 0
    deleted = 0
    chats: set = set()
    for r in rows:
        if _safe_delete_message(r.get("chat_id"), r.get("message_id")):
            deleted += 1
        chats.add(str(r.get("chat_id")))
        time.sleep(DELETE_DELAY_SECONDS)
    _delete_all_market_messages()
    _cover_live_cache.clear()
    _inflight_public.clear()
    _graduated_markets.clear()
    logger.warning("GLOBAL CLEANUP deleted=%s chats=%s", deleted, len(chats))
    return deleted, len(chats)


def monitor_loop():
    global _PAUSED, last_onchain_poll_at
    logger.info("monitor loop started")
    HEALTH["status"] = "running"
    while True:
        try:
            loop_t = time.time()
            HEALTH["loop_count"] = int(HEALTH.get("loop_count", 0)) + 1

            # Seed the boot baseline before anything can notify (no replay).
            if not _baseline_seeded:
                if not _seed_baseline():
                    time.sleep(max(1.0, MARKET_POLL_SECONDS))
                    continue

            if _PAUSED:
                HEALTH["last_eval_at"] = time.time()
                elapsed = time.time() - loop_t
                time.sleep(max(1.0, MARKET_POLL_SECONDS - elapsed))
                continue

            # V2 on-chain discovery (throttled)
            if time.time() - last_onchain_poll_at >= ONCHAIN_POLL_SECONDS:
                last_onchain_poll_at = time.time()
                HEALTH["onchain_loops"] = int(HEALTH.get("onchain_loops", 0)) + 1
                for market in fetch_onchain_markets():
                    try:
                        mid = str(market.get("market_id", "")).strip()
                        title = str(market.get("title", "")).strip()
                        end_unix = int(market.get("end_time") or 0)
                        if not mid or not title or end_unix <= int(time.time()):
                            continue
                        if mid in _baseline_markets:
                            continue
                        register_market(market)
                    except Exception as e:
                        logger.error("process onchain market: %s", e)

            # API discovery (supplementary source; V1 compatibility)
            api_markets = list(fetch_api_markets())
            refresh_graduation(api_markets)
            for market in api_markets:
                try:
                    mid = str(market.get("market_id", "")).strip()
                    title = str(market.get("title", "")).strip()
                    end_unix = int(market.get("end_time") or 0)
                    if not mid or not title or end_unix <= int(time.time()):
                        continue
                    if mid in _baseline_markets:
                        continue

                    row, is_new = register_market(market)
                    if not row or row.get("public_notified") or row.get("notified_ended") or row.get("notify_cancelled"):
                        continue

                    # API markets carry cover_image_status; synthesize for app-live check.
                    merged = dict(market)
                    merged["cover_image_status"] = market.get("cover_image_status") or "ready"
                    merged["source"] = row.get("source") or "api"
                    send_public_new_market(merged, row)
                except Exception as e:
                    logger.error("process api market: %s", e)

            # Pending public markets registered on-chain (delayed notify).
            for row in get_markets_pending_public():
                try:
                    mid = str(row.get("market_id", "")).strip()
                    if not mid or mid in _inflight_public:
                        continue
                    if row.get("public_notified") or row.get("notified_ended"):
                        continue
                    market = {
                        "market_id": mid,
                        "title": row.get("title") or "",
                        "end_time": _end_unix(row.get("end_time")),
                        "source": row.get("source") or "onchain",
                    }
                    send_public_new_market(market, row)
                except Exception as e:
                    logger.error("process pending public market: %s", e)

            process_lifecycle()
            HEALTH["last_eval_at"] = time.time()
            HEALTH["status"] = "running"

            if HEALTH["loop_count"] % 12 == 0:
                logger.info(
                    "HEARTBEAT loops=%s markets=%s chats=%s sends=%s",
                    HEALTH["loop_count"],
                    HEALTH.get("last_market_count"),
                    len(get_all_chats()),
                    HEALTH.get("notifications_sent"),
                )

            elapsed = time.time() - loop_t
            time.sleep(max(1.0, MARKET_POLL_SECONDS - elapsed))
        except Exception as e:
            logger.error("monitor_loop: %s", e)
            HEALTH["last_error"] = str(e)
            HEALTH["status"] = "error"
            time.sleep(15)


# ── Bot commands ─────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def cmd_start(message):
    add_chat(
        message.chat.id,
        message.chat.title if message.chat.type != "private" else (
            f"user_{message.from_user.username or message.from_user.id}"
        ),
    )
    bot.reply_to(
        message,
        "🔔 <b>B4 Notify Bot</b>\n\n"
        "You will receive alerts when new B4 markets go live.\n\n"
        "/start — subscribe\n"
        "/help — show commands\n"
        "/stop — unsubscribe",
        parse_mode="HTML",
    )


@bot.message_handler(commands=["help"])
def cmd_help(message):
    bot.reply_to(
        message,
        "<b>B4 Notify Bot — Commands</b>\n\n"
        "/start — subscribe to market alerts\n"
        "/stop — unsubscribe\n"
        "/help — this message\n\n"
        "<i>Admin commands (if configured):</i>\n"
        "/status — bot status\n"
        "/pause — pause notifications\n"
        "/resume — resume notifications\n"
        "/cleanup — delete all bot messages",
        parse_mode="HTML",
    )


@bot.message_handler(commands=["stop"])
def cmd_stop(message):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM subscribed_chats WHERE chat_id = %s", (str(message.chat.id),))
        bot.reply_to(message, "Unsubscribed. Use /start to subscribe again.")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")


@bot.message_handler(commands=["status"])
def cmd_status(message):
    add_chat(message.chat.id, "")
    bot.reply_to(message, health_text(), parse_mode="HTML")


def health_text():
    active = len(get_all_markets())
    chats = len(get_all_chats())
    state = "paused" if _PAUSED else "running"
    return (
        f"📊 <b>B4 Notify</b>\n"
        f"State: <b>{state}</b>\n"
        f"Tracked markets: <b>{active}</b>\n"
        f"Subscribers: <b>{chats}</b>\n"
        f"Notifications sent: <b>{HEALTH.get('notifications_sent', 0)}</b>\n"
        f"On-chain loops: <b>{HEALTH.get('onchain_loops', 0)}</b>\n"
        f"On-chain markets: <b>{HEALTH.get('onchain_markets', 0)}</b>\n"
        f"Graduated markets: <b>{HEALTH.get('graduated_markets', 0)}</b>\n"
        f"Baseline (no-replay): <b>{len(_baseline_markets)}</b>\n"
        f"Notify delay: <b>{int(NOTIFY_DELAY_SECONDS)}s</b>"
    )


@bot.message_handler(commands=["pause"])
def cmd_pause(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Admin only.")
        return
    global _PAUSED
    _PAUSED = True
    set_state("paused", "true")
    cancel_pending_delayed_notifies()
    logger.warning("ADMIN PAUSE by user=%s", message.from_user.id)
    bot.reply_to(message, "⏸ Notifications paused. Use /resume to resume.")


@bot.message_handler(commands=["resume"])
def cmd_resume(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Admin only.")
        return
    global _PAUSED
    _PAUSED = False
    set_state("paused", "false")
    logger.warning("ADMIN RESUME by user=%s", message.from_user.id)
    bot.reply_to(message, "▶ Notifications resumed.")


@bot.message_handler(commands=["cleanup"])
def cmd_cleanup(message):
    """Admin wipe: delete every bot message and clear all notification state."""
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Admin only.")
        return
    logger.warning("ADMIN CLEANUP by user=%s", message.from_user.id)
    bot.reply_to(message, "🧹 Cleaning up all bot messages…")
    try:
        deleted, chats = global_cleanup()
    except Exception as e:
        logger.error("cleanup failed: %s", e)
        bot.reply_to(message, f"⚠️ Cleanup failed: {e}")
        return
    bot.reply_to(
        message,
        "<b>🧹 Cleanup complete</b>\n\n"
        f"Deleted:\n"
        f"• <b>{deleted}</b> messages\n"
        f"• <b>{chats}</b> chats\n"
        f"• <b>Database cleaned successfully.</b>",
        parse_mode="HTML",
    )


# ── Flask ────────────────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/")
def root():
    return {
        "bot": "b4-notify-lite",
        "status": HEALTH.get("status"),
        "loops": HEALTH.get("loop_count"),
        "last_eval_ago_s": int(time.time() - HEALTH["last_eval_at"]) if HEALTH.get("last_eval_at") else None,
    }


@app.route("/health")
def http_health():
    return {"ok": HEALTH.get("status") == "running", **{k: HEALTH[k] for k in (
        "status", "loop_count", "last_market_count", "notifications_sent", "last_error"
    )}}


def _run_bot_polling():
    """Manual long-poll loop with 409 conflict resilience."""
    bot.remove_webhook()
    try:
        public_commands = [
            BotCommand("start", "Subscribe to market alerts"),
            BotCommand("help", "Show available commands"),
        ]
        bot.set_my_commands(public_commands)
        bot.set_my_commands(public_commands, scope=telebot.types.BotCommandScopeAllPrivateChats())
        bot.set_my_commands(public_commands, scope=telebot.types.BotCommandScopeAllGroupChats())
        if ADMIN_ID:
            try:
                admin_scope = telebot.types.BotCommandScopeChat(chat_id=ADMIN_ID)
                bot.set_my_commands([
                    BotCommand("pause", "Pause all notifications"),
                    BotCommand("resume", "Resume notifications"),
                    BotCommand("status", "Bot status"),
                    BotCommand("cleanup", "Delete all bot messages"),
                ], scope=admin_scope)
            except Exception:
                pass
    except Exception:
        pass
    try:
        bot.get_updates(offset=-1, timeout=0)
    except Exception:
        pass

    retry = 1
    while True:
        try:
            updates = bot.get_updates(
                offset=(bot.last_update_id or 0) + 1,
                timeout=20,
                long_polling_timeout=20,
            )
            retry = 1
            if updates:
                bot.process_new_updates(updates)
        except Exception as e:
            code = getattr(e, 'status_code', 0)
            if code == 409:
                logger.warning("409 conflict (another instance active), retrying in %ss", retry)
                time.sleep(retry)
                retry = min(retry * 2, 120)
            else:
                logger.critical("polling failed (code %s): %s", code, e)
                time.sleep(5)
                retry = 1


# ── Entry point ──────────────────────────────────────────────────────────────

init_db()
Thread(target=monitor_loop, daemon=True).start()

if __name__ == "__main__":
    Thread(target=_run_bot_polling, daemon=True).start()
    port = int(os.getenv("PORT", "5000"))
    logger.info("Bot ready (dev server)")
    app.run(host="0.0.0.0", port=port, use_reloader=False)
else:
    Thread(target=_run_bot_polling, daemon=True).start()
    logger.info("Bot ready (lightweight public notify, Gunicorn managed)")
