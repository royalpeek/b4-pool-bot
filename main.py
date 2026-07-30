"""
B4 Notify Bot — Lightweight public edition.

On-chain discovery → APP_LIVE (cover HEAD 200) → public Telegram notification.
Optimized for free-tier hosts (Render): low memory, one monitor loop, no AI/premium.
"""
print("B4 Notify Bot — LIGHTWEIGHT public edition")

import base64
import html
import logging
import os
import struct
import time
from datetime import datetime, timezone, timedelta
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
DATABASE_URL = os.getenv("DATABASE_URL")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required")

MARKET_LINK_BASE = os.getenv("MARKET_LINK_BASE", "https://www.b4app.xyz/m").rstrip("/")
B4_API_URL = os.getenv("B4_API_URL", "https://www.b4app.xyz/api/markets")

SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
B4_PROGRAM_ID = os.getenv("B4_SOLANA_PROGRAM_ID", "9XQDD38sy1qJ57DqAQvADuLRTjcYUXD48H7deyNuaehH")
ONCHAIN_ENABLED = os.getenv("ONCHAIN_PROVIDER_ENABLED", "true").lower() == "true"
ONCHAIN_POLL_SECONDS = float(os.getenv("ONCHAIN_POLL_SECONDS", "8"))
ONCHAIN_ACCOUNT_SIZE = int(os.getenv("ONCHAIN_MARKET_ACCOUNT_SIZE", "464"))
ONCHAIN_ID_OFFSET = int(os.getenv("ONCHAIN_MARKET_ID_OFFSET", "8"))
ONCHAIN_TITLE_LEN_OFFSET = int(os.getenv("ONCHAIN_TITLE_LENGTH_OFFSET", "48"))
ONCHAIN_TITLE_OFFSET = int(os.getenv("ONCHAIN_TITLE_OFFSET", "52"))
ONCHAIN_DURATION = int(os.getenv("ONCHAIN_MARKET_DURATION_SECONDS", "86400"))

MARKET_POLL_SECONDS = float(os.getenv("MARKET_POLL_SECONDS", "5"))
PUBLIC_ALERT_DELAY_SECONDS = float(os.getenv("PUBLIC_ALERT_DELAY_SECONDS", "0"))
SEND_DELAY_SECONDS = float(os.getenv("SEND_DELAY_SECONDS", "0.08"))
API_TIMEOUT = float(os.getenv("API_TIMEOUT_SECONDS", "8"))
COVER_CACHE_SECONDS = float(os.getenv("COVER_LIVE_CACHE_SECONDS", "60"))
ENABLE_REMINDERS = os.getenv("ENABLE_REMINDERS", "true").lower() == "true"
REMINDER_1H = float(os.getenv("REMINDER_1H_SECONDS", "3600"))
REMINDER_10M = float(os.getenv("REMINDER_10M_SECONDS", "600"))

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

# Process health (in-memory)
HEALTH = {
    "started_at": time.time(),
    "status": "starting",
    "last_scan_at": 0.0,
    "last_onchain_at": 0.0,
    "last_eval_at": 0.0,
    "last_notification_at": 0.0,
    "last_notification_title": "",
    "markets_onchain_last": 0,
    "markets_api_last": 0,
    "markets_registered": 0,
    "notifications_sent": 0,
    "loop_count": 0,
    "last_error": "",
}

_cover_cache: dict = {}  # mid -> (ok, expires_mono)
_last_onchain_poll = 0.0


# ── Time / DB helpers ────────────────────────────────────────────────────────

def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def escape(text):
    return html.escape(str(text or ""), quote=False)


def get_db():
    return psycopg.connect(DATABASE_URL)


def init_db():
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
                    source TEXT DEFAULT 'onchain',
                    lifecycle_state TEXT DEFAULT 'discovered_onchain',
                    public_notified BOOLEAN DEFAULT FALSE,
                    notified_1h BOOLEAN DEFAULT FALSE,
                    notified_10m BOOLEAN DEFAULT FALSE,
                    notified_ended BOOLEAN DEFAULT FALSE,
                    app_live_at TIMESTAMP,
                    public_sent_at TIMESTAMP,
                    detected_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
                """
            )
            # Compat with older full-bot schema
            for stmt in (
                "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS public_notified BOOLEAN DEFAULT FALSE",
                "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS notified_1h BOOLEAN DEFAULT FALSE",
                "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS notified_10m BOOLEAN DEFAULT FALSE",
                "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS notified_ended BOOLEAN DEFAULT FALSE",
                "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS lifecycle_state TEXT DEFAULT 'discovered_onchain'",
                "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS app_live_at TIMESTAMP",
                "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS public_sent_at TIMESTAMP",
                "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'onchain'",
                "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS market_link TEXT",
                "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS end_time TEXT",
                "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS title TEXT",
                "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()",
                "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS detected_at TIMESTAMP DEFAULT NOW()",
            ):
                try:
                    cur.execute(stmt)
                except Exception:
                    pass
    logger.info("database ready")


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
        return existing, False

    title = str(market.get("title", "")).strip()
    end_unix = int(market.get("end_time") or 0)
    end_iso = datetime.fromtimestamp(end_unix, tz=timezone.utc).replace(tzinfo=None).isoformat()
    link = f"{MARKET_LINK_BASE}/{mid}"
    source = str(market.get("source") or "onchain")
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO announced_markets (
                        market_id, title, end_time, market_link, source,
                        lifecycle_state, public_notified, notified_1h, notified_10m, notified_ended
                    )
                    VALUES (%s, %s, %s, %s, %s, 'discovered_onchain', FALSE, FALSE, FALSE, FALSE)
                    ON CONFLICT (market_id) DO NOTHING
                    RETURNING market_id
                    """,
                    (mid, title, end_iso, link, source),
                )
                inserted = cur.fetchone() is not None
        if inserted:
            HEALTH["markets_registered"] = int(HEALTH.get("markets_registered", 0)) + 1
            logger.info("REGISTERED market=%s title=%s", mid, title[:50])
        return get_market(mid), inserted
    except Exception as e:
        logger.critical("REGISTER failed market=%s error=%s", mid, e)
        HEALTH["last_error"] = f"register:{e}"
        return get_market(mid), False


def claim_flag(market_id, flag):
    allowed = {
        "public_notified", "notified_1h", "notified_10m", "notified_ended",
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
    allowed = {"public_notified", "notified_1h", "notified_10m", "notified_ended"}
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


def set_lifecycle(market_id, state, app_live=False, public_sent=False):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if app_live:
                    cur.execute(
                        """
                        UPDATE announced_markets
                        SET lifecycle_state = %s,
                            app_live_at = COALESCE(app_live_at, NOW()),
                            updated_at = NOW()
                        WHERE market_id = %s
                        """,
                        (state, str(market_id)),
                    )
                elif public_sent:
                    cur.execute(
                        """
                        UPDATE announced_markets
                        SET lifecycle_state = %s,
                            public_sent_at = COALESCE(public_sent_at, NOW()),
                            updated_at = NOW()
                        WHERE market_id = %s
                        """,
                        (state, str(market_id)),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE announced_markets
                        SET lifecycle_state = %s, updated_at = NOW()
                        WHERE market_id = %s
                        """,
                        (state, str(market_id)),
                    )
    except Exception as e:
        logger.error("set_lifecycle %s: %s", market_id, e)


# ── On-chain + APP_LIVE ──────────────────────────────────────────────────────

def cover_url(market_id):
    return f"https://www.b4app.xyz/api/assets/market-cover/{market_id}.png"


def market_link(market_id):
    return f"{MARKET_LINK_BASE}/{market_id}"


def is_app_live(market_id):
    """APP_LIVE = cover image HEAD returns 200 (cached)."""
    mid = str(market_id).strip()
    if not mid:
        return False
    now_m = time.monotonic()
    cached = _cover_cache.get(mid)
    if cached and cached[1] > now_m:
        return cached[0]
    ok = False
    try:
        r = requests.head(cover_url(mid), timeout=min(API_TIMEOUT, 6), allow_redirects=True)
        ok = r.status_code == 200
    except Exception as e:
        logger.debug("cover HEAD %s: %s", mid, e)
    _cover_cache[mid] = (ok, now_m + COVER_CACHE_SECONDS)
    if len(_cover_cache) > 1500:
        dead = [k for k, v in _cover_cache.items() if v[1] <= now_m]
        for k in dead[:400]:
            _cover_cache.pop(k, None)
    return ok


def read_u64_le(data, offset):
    if offset < 0 or offset + 8 > len(data):
        return None
    return struct.unpack_from("<Q", data, offset)[0]


def read_u32_le(data, offset):
    if offset < 0 or offset + 4 > len(data):
        return None
    return struct.unpack_from("<I", data, offset)[0]


def decode_onchain_account(pubkey, encoded_data):
    try:
        raw = encoded_data[0] if isinstance(encoded_data, list) else encoded_data
        data = base64.b64decode(raw)
        market_id = read_u64_le(data, ONCHAIN_ID_OFFSET)
        title_len = read_u32_le(data, ONCHAIN_TITLE_LEN_OFFSET)
        if not market_id or not title_len:
            return None
        if market_id < 1_700_000_000_000_000 or market_id > 1_900_000_000_000_000:
            return None
        if title_len < 6 or title_len > 180:
            return None
        end = ONCHAIN_TITLE_OFFSET + title_len
        if end > len(data):
            return None
        title = data[ONCHAIN_TITLE_OFFSET:end].decode("utf-8", errors="strict").strip("\x00").strip()
        if len(title) < 6:
            return None
        created = int(market_id // 1_000_000)
        return {
            "market_id": str(market_id),
            "market_pubkey": str(pubkey),
            "title": title,
            "end_time": created + ONCHAIN_DURATION,
            "source": "onchain",
        }
    except Exception:
        return None


def fetch_onchain_markets():
    if not ONCHAIN_ENABLED:
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
                    "filters": [{"dataSize": ONCHAIN_ACCOUNT_SIZE}],
                    "dataSlice": {"offset": 0, "length": ONCHAIN_ACCOUNT_SIZE},
                },
            ],
        }
        r = requests.post(SOLANA_RPC_URL, json=payload, timeout=max(API_TIMEOUT, 15))
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            logger.error("solana rpc: %s", data["error"])
            return []
        now_ts = int(time.time())
        markets = []
        for acc in data.get("result") or []:
            m = decode_onchain_account(acc.get("pubkey"), (acc.get("account") or {}).get("data"))
            if m and int(m["end_time"]) > now_ts:
                markets.append(m)
        HEALTH["last_onchain_at"] = time.time()
        HEALTH["markets_onchain_last"] = len(markets)
        set_state("last_onchain_check", now_utc().isoformat())
        logger.info("on-chain live markets: %s", len(markets))
        return markets
    except Exception as e:
        logger.error("fetch_onchain_markets: %s", e)
        HEALTH["last_error"] = f"onchain:{e}"
        return []


def fetch_api_markets():
    """Optional secondary discovery — does not gate APP_LIVE public notify."""
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
            out.append({
                "market_id": mid,
                "title": title,
                "end_time": end,
                "source": "api",
                "cover_image_status": m.get("cover_image_status"),
            })
        HEALTH["markets_api_last"] = len(out)
        HEALTH["last_scan_at"] = time.time()
        set_state("last_api_check", now_utc().isoformat())
        return out
    except Exception as e:
        logger.error("fetch_api_markets: %s", e)
        HEALTH["last_error"] = f"api:{e}"
        return []


# ── Telegram broadcast ───────────────────────────────────────────────────────

def send_to_chat(chat_id, text, keyboard=None):
    try:
        bot.send_message(
            int(chat_id),
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=False,
        )
        time.sleep(SEND_DELAY_SECONDS)
        return True
    except Exception as e:
        logger.warning("send_to_chat %s: %s", chat_id, e)
        return False


def broadcast(text, keyboard=None):
    chats = get_all_chats()
    sent = 0
    for chat_id in chats:
        if send_to_chat(chat_id, text, keyboard):
            sent += 1
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


def build_new_market_message(title, market_id, end_unix):
    end_dt = datetime.fromtimestamp(int(end_unix), tz=timezone.utc)
    end_str = end_dt.strftime("%b %d, %Y %H:%M UTC")
    return (
        f"🟢 <b>NEW MARKET LIVE</b>\n\n"
        f"🔥 <b>{escape(title)}</b>\n\n"
        f"⏰ Closes: {escape(end_str)}\n"
        f"🔗 <a href=\"{escape(market_link(market_id))}\">Open in b4</a>"
    )


def build_reminder_message(title, market_id, label, time_left_sec):
    mins = max(1, int(time_left_sec // 60))
    return (
        f"⏰ <b>{escape(label)}</b>\n\n"
        f"<b>{escape(title)}</b>\n\n"
        f"Time left: <b>{mins} min</b>\n"
        f"🔗 <a href=\"{escape(market_link(market_id))}\">Vote now</a>"
    )


# ── Notification pipeline ────────────────────────────────────────────────────

def send_public_new_market(market, row):
    """APP_LIVE → public notification (once)."""
    mid = str(market.get("market_id", "")).strip()
    if not mid or not row:
        return False
    if row.get("public_notified"):
        return False
    if not is_app_live(mid):
        return False

    if not claim_flag(mid, "public_notified"):
        return False

    set_lifecycle(mid, "app_live", app_live=True)
    title = str(market.get("title") or row.get("title") or "").strip()
    end_unix = int(market.get("end_time") or 0)

    if PUBLIC_ALERT_DELAY_SECONDS > 0:
        time.sleep(PUBLIC_ALERT_DELAY_SECONDS)

    # Re-check ended
    latest = get_market(mid)
    if latest and latest.get("notified_ended"):
        release_flag(mid, "public_notified")
        return False

    text = build_new_market_message(title, mid, end_unix)
    sent = broadcast(text, vote_keyboard(mid))
    if sent > 0:
        set_lifecycle(mid, "public_notified", public_sent=True)
        HEALTH["last_notification_title"] = title[:80]
        set_state("last_market_detected", f"{mid} | {title}")
        logger.info("PUBLIC NEW MARKET sent mid=%s title=%s sent=%s", mid, title[:40], sent)
        return True

    release_flag(mid, "public_notified")
    logger.warning("PUBLIC NEW MARKET zero sends mid=%s (will retry)", mid)
    return False


def process_reminders():
    if not ENABLE_REMINDERS:
        return
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
            title = row.get("title") or ""

            if left <= 0:
                if not row.get("notified_ended") and claim_flag(mid, "notified_ended"):
                    broadcast(
                        f"⬜ <b>MARKET ENDED</b>\n\n<b>{escape(title)}</b>",
                        None,
                    )
                continue

            if left <= REMINDER_10M and not row.get("notified_10m"):
                if claim_flag(mid, "notified_10m"):
                    sent = broadcast(
                        build_reminder_message(title, mid, "10 MINUTES LEFT", left),
                        vote_keyboard(mid),
                    )
                    if not sent:
                        release_flag(mid, "notified_10m")
            elif left <= REMINDER_1H and not row.get("notified_1h"):
                if claim_flag(mid, "notified_1h"):
                    sent = broadcast(
                        build_reminder_message(title, mid, "1 HOUR LEFT", left),
                        vote_keyboard(mid),
                    )
                    if not sent:
                        release_flag(mid, "notified_1h")
        except Exception as e:
            logger.error("reminder error: %s", e)


def process_market(market):
    """Discovery → register → APP_LIVE public notify."""
    mid = str(market.get("market_id", "")).strip()
    title = str(market.get("title", "")).strip()
    end_unix = int(market.get("end_time") or 0)
    if not mid or not title or end_unix <= int(time.time()):
        return

    row, is_new = register_market(market)
    if not row:
        return

    if row.get("public_notified") or row.get("notified_ended"):
        return

    if is_app_live(mid):
        send_public_new_market(market, row)
    else:
        set_lifecycle(mid, "discovered_onchain")


def monitor_loop():
    global _last_onchain_poll
    logger.info("monitor loop started")
    HEALTH["status"] = "running"
    while True:
        try:
            loop_t = time.time()
            HEALTH["loop_count"] = int(HEALTH.get("loop_count", 0)) + 1

            # On-chain discovery (primary)
            if ONCHAIN_ENABLED and (time.time() - _last_onchain_poll) >= ONCHAIN_POLL_SECONDS:
                _last_onchain_poll = time.time()
                for market in fetch_onchain_markets():
                    try:
                        process_market(market)
                    except Exception as e:
                        logger.error("process onchain market: %s", e)

            # API discovery (secondary — catches anything on-chain missed)
            for market in fetch_api_markets():
                try:
                    process_market(market)
                except Exception as e:
                    logger.error("process api market: %s", e)

            # Pending APP_LIVE for registered but not yet public
            for row in get_all_markets():
                try:
                    if row.get("public_notified") or row.get("notified_ended"):
                        continue
                    mid = str(row.get("market_id", "")).strip()
                    if not mid or not is_app_live(mid):
                        continue
                    et = row.get("end_time")
                    if isinstance(et, datetime):
                        end_unix = int(et.replace(tzinfo=timezone.utc).timestamp()) if et.tzinfo is None else int(et.timestamp())
                    else:
                        end_unix = int(datetime.fromisoformat(str(et).replace("Z", "+00:00")).timestamp())
                    process_market({
                        "market_id": mid,
                        "title": row.get("title") or "",
                        "end_time": end_unix,
                        "source": row.get("source") or "onchain",
                    })
                except Exception as e:
                    logger.error("pending app_live: %s", e)

            process_reminders()
            HEALTH["last_eval_at"] = time.time()
            HEALTH["status"] = "running"

            if HEALTH["loop_count"] % 12 == 0:
                logger.info(
                    "HEARTBEAT loops=%s onchain=%s api=%s chats=%s sends=%s",
                    HEALTH["loop_count"],
                    HEALTH.get("markets_onchain_last"),
                    HEALTH.get("markets_api_last"),
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
        "/stop — unsubscribe",
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
        "status", "loop_count", "markets_onchain_last", "notifications_sent", "last_error"
    )}}


def _run_bot_polling():
    """Manual long-poll loop with 409 conflict resilience."""
    bot.remove_webhook()
    try:
        bot.set_my_commands([
            BotCommand("start", "Subscribe to market alerts"),
            BotCommand("stop", "Unsubscribe"),
        ])
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
