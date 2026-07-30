"""
B4 Notify Bot — API-only lightweight public edition.

Polls the official B4 API → new market detection → public Telegram notification.
Optimized for free-tier hosts (Render): low memory, one monitor loop.
"""
print("B4 Notify Bot — API-ONLY public edition")

import html
import logging
import os
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
API_TIMEOUT = float(os.getenv("API_TIMEOUT_SECONDS", "8"))
ENABLE_REMINDERS = os.getenv("ENABLE_REMINDERS", "true").lower() == "true"
REMINDER_1H = float(os.getenv("REMINDER_1H_SECONDS", "3600"))
REMINDER_10M = float(os.getenv("REMINDER_10M_SECONDS", "600"))

_PAUSED = False

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
}


# ── Time / DB helpers ────────────────────────────────────────────────────────

def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
                            public_sent_at TIMESTAMP,
                            detected_at TIMESTAMP DEFAULT NOW(),
                            updated_at TIMESTAMP DEFAULT NOW()
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
                        "ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS public_sent_at TIMESTAMP",
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
        return existing, False

    title = str(market.get("title", "")).strip()
    end_unix = int(market.get("end_time") or 0)
    end_iso = datetime.fromtimestamp(end_unix, tz=timezone.utc).replace(tzinfo=None).isoformat()
    link = f"{MARKET_LINK_BASE}/{mid}"
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO announced_markets (
                        market_id, title, end_time, market_link, source
                    )
                    VALUES (%s, %s, %s, %s, 'api')
                    ON CONFLICT (market_id) DO NOTHING
                    RETURNING market_id
                    """,
                    (mid, title, end_iso, link),
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


def cleanup_market(market_id):
    """Delete all tracked messages, clear cache, mark DB ended."""
    logger.info("Market ended mid=%s: starting cleanup", market_id)
    messages = _get_market_messages(market_id)
    deleted = 0
    for row in messages:
        try:
            bot.delete_message(int(row["chat_id"]), int(row["message_id"]))
            deleted += 1
            time.sleep(SEND_DELAY_SECONDS)
        except Exception as e:
            logger.warning("cleanup delete msg mid=%s chat=%s: %s", market_id, row.get("chat_id"), e)
    logger.info("Market ended mid=%s: deleted %s Telegram messages", market_id, deleted)
    _delete_market_messages(market_id)
    logger.info("Market ended mid=%s: cleanup complete", market_id)


def is_admin(uid):
    return ADMIN_ID and int(uid) == ADMIN_ID


def market_link(market_id):
    return f"{MARKET_LINK_BASE}/{market_id}"


def fetch_api_markets():
    """Fetch active markets from the official B4 API."""
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
            })
        HEALTH["last_market_count"] = len(out)
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
    """Delay → verify → public notification (once)."""
    mid = str(market.get("market_id", "")).strip()
    if not mid or not row:
        return False
    if row.get("public_notified"):
        return False

    if not claim_flag(mid, "public_notified"):
        return False

    title = str(market.get("title") or row.get("title") or "").strip()
    end_unix = int(market.get("end_time") or 0)

    if NEW_MARKET_DELAY_SECONDS > 0:
        logger.debug("delaying announcement mid=%s %ss", mid, NEW_MARKET_DELAY_SECONDS)
        time.sleep(NEW_MARKET_DELAY_SECONDS)

    latest = get_market(mid)
    if latest and latest.get("notified_ended"):
        release_flag(mid, "public_notified")
        return False

    text = build_new_market_message(title, mid, end_unix)
    sent = broadcast(text, vote_keyboard(mid), track_market_id=mid, msg_type="new")
    if sent > 0:
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
                    cleanup_market(mid)
                continue

            if left <= REMINDER_10M and not row.get("notified_10m"):
                if claim_flag(mid, "notified_10m"):
                    sent = broadcast(
                        build_reminder_message(title, mid, "10 MINUTES LEFT", left),
                        vote_keyboard(mid),
                        track_market_id=mid,
                        msg_type="reminder_10m",
                    )
                    if not sent:
                        release_flag(mid, "notified_10m")
            elif left <= REMINDER_1H and not row.get("notified_1h"):
                if claim_flag(mid, "notified_1h"):
                    sent = broadcast(
                        build_reminder_message(title, mid, "1 HOUR LEFT", left),
                        vote_keyboard(mid),
                        track_market_id=mid,
                        msg_type="reminder_1h",
                    )
                    if not sent:
                        release_flag(mid, "notified_1h")
        except Exception as e:
            logger.error("reminder error: %s", e)


def monitor_loop():
    global _PAUSED
    logger.info("monitor loop started")
    HEALTH["status"] = "running"
    while True:
        try:
            loop_t = time.time()
            HEALTH["loop_count"] = int(HEALTH.get("loop_count", 0)) + 1

            if _PAUSED:
                HEALTH["last_eval_at"] = time.time()
                elapsed = time.time() - loop_t
                time.sleep(max(1.0, MARKET_POLL_SECONDS - elapsed))
                continue

            # API discovery (sole source of truth)
            for market in fetch_api_markets():
                try:
                    mid = str(market.get("market_id", "")).strip()
                    title = str(market.get("title", "")).strip()
                    end_unix = int(market.get("end_time") or 0)
                    if not mid or not title or end_unix <= int(time.time()):
                        continue

                    row, is_new = register_market(market)
                    if not row or row.get("public_notified") or row.get("notified_ended"):
                        continue

                    send_public_new_market(market, row)
                except Exception as e:
                    logger.error("process api market: %s", e)

            process_reminders()
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
        "/resume — resume notifications",
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
    active = len(get_all_markets())
    chats = len(get_all_chats())
    state = "paused" if _PAUSED else "running"
    bot.reply_to(
        message,
        f"📊 <b>B4 Notify</b>\n"
        f"State: <b>{state}</b>\n"
        f"Tracked markets: <b>{active}</b>\n"
        f"Subscribers: <b>{chats}</b>\n"
        f"Notifications sent: <b>{HEALTH.get('notifications_sent', 0)}</b>",
        parse_mode="HTML",
    )


@bot.message_handler(commands=["pause"])
def cmd_pause(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Admin only.")
        return
    global _PAUSED
    _PAUSED = True
    set_state("paused", "true")
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
