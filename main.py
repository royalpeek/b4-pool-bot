print("NEW DEPLOY VERSION - WITH CONFIGURABLE AI")
import telebot
from telebot import types
import json
import os
import time
import logging
import psycopg
import html
from threading import Thread
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import requests
from openai import OpenAI

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

bot_token = os.getenv("BOT_TOKEN")
if not bot_token:
    raise RuntimeError("BOT_TOKEN environment variable is required")

if not os.getenv("DATABASE_URL"):
    raise RuntimeError("DATABASE_URL environment variable is required")

bot = telebot.TeleBot(bot_token)
TELEGRAM_API_URL = f"https://api.telegram.org/bot{bot_token}"

B4_API_URL = os.getenv("B4_API_URL", "https://www.b4app.xyz/api/markets")
MARKET_LINK_BASE = os.getenv("MARKET_LINK_BASE", "https://www.b4app.xyz/m").rstrip("/")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
DATABASE_URL = os.getenv("DATABASE_URL")

# OpenAI-compatible AI client. Supports Groq, OpenRouter, OpenAI-compatible
# gateways, and the older FREEMODEL_* env names as a fallback.
ai_api_key = os.getenv("AI_API_KEY") or os.getenv("GROQ_API_KEY") or os.getenv("FREEMODEL_API_KEY")
ai_base_url = (
    os.getenv("AI_BASE_URL")
    or os.getenv("GROQ_BASE_URL")
    or os.getenv("FREEMODEL_BASE_URL")
    or ("https://api.groq.com/openai/v1" if os.getenv("GROQ_API_KEY") else None)
)
ai_model = os.getenv("AI_MODEL", "llama-3.1-8b-instant")
NOTIFICATION_COOLDOWN_SECONDS = int(os.getenv("NOTIFICATION_COOLDOWN_SECONDS", "2"))
SEND_DELAY_SECONDS = float(os.getenv("SEND_DELAY_SECONDS", "0.1"))
BROADCAST_WORKERS = max(1, int(os.getenv("BROADCAST_WORKERS", "4")))
MARKET_POLL_SECONDS = float(os.getenv("MARKET_POLL_SECONDS", "5"))
COVER_IMAGE_WAIT_SECONDS = float(os.getenv("COVER_IMAGE_WAIT_SECONDS", "8"))
COVER_IMAGE_RETRY_SECONDS = float(os.getenv("COVER_IMAGE_RETRY_SECONDS", "1"))
IMAGE_FOLLOWUP_WAIT_SECONDS = float(os.getenv("IMAGE_FOLLOWUP_WAIT_SECONDS", "45"))
PREMIUM_GO_LIVE_REMINDER_SECONDS = int(os.getenv("PREMIUM_GO_LIVE_REMINDER_SECONDS", "120"))
TEMP_RESPONSE_DELETE_SECONDS = int(os.getenv("TEMP_RESPONSE_DELETE_SECONDS", "180"))
DAILY_SUMMARY_UTC_HOUR = int(os.getenv("DAILY_SUMMARY_UTC_HOUR", "9"))
VALID_THEMES = ["all", "crypto", "politics", "entertainment", "sports", "travel", "current_events", "other"]
VALID_TONES = ["casual", "urgent", "premium", "degen", "professional"]

ai_client = None
if ai_api_key and ai_base_url:
    ai_client = OpenAI(api_key=ai_api_key, base_url=ai_base_url)
    logger.info("ai client initialized with model %s", ai_model)
else:
    logger.warning("ai not configured, using template notifications")


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def build_market_link(market_id):
    return f"{MARKET_LINK_BASE}/{market_id}"


def parse_api_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def get_market_go_live_at(market):
    go_live_at = parse_api_datetime(market.get("go_live_at"))
    if go_live_at:
        return go_live_at
    created_at = parse_api_datetime(market.get("created_at"))
    return created_at


def get_db():
    conn = psycopg.connect(DATABASE_URL)
    return conn


def init_db():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS subscribed_chats (
                        chat_id TEXT PRIMARY KEY,
                        chat_name TEXT,
                        themes TEXT DEFAULT 'all',
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    ALTER TABLE subscribed_chats
                    ADD COLUMN IF NOT EXISTS themes TEXT DEFAULT 'all'
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id TEXT PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        join_date TEXT,
                        is_admin BOOLEAN DEFAULT FALSE
                    )
                """)
                
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS announced_markets (
                        market_id TEXT PRIMARY KEY,
                        title TEXT,
                        theme TEXT,
                        end_time TEXT,
                        market_link TEXT,
                        notified_new BOOLEAN DEFAULT FALSE,
                        notified_1h BOOLEAN DEFAULT FALSE,
                        notified_5m BOOLEAN DEFAULT FALSE,
                        notified_ended BOOLEAN DEFAULT FALSE,
                        delete_scheduled BOOLEAN DEFAULT FALSE,
                        notified_scheduled BOOLEAN DEFAULT FALSE,
                        notified_go_live_2m BOOLEAN DEFAULT FALSE,
                        image_followup_sent BOOLEAN DEFAULT FALSE,
                        is_scheduled BOOLEAN DEFAULT FALSE,
                        go_live_at TEXT,
                        detected_at TEXT
                    )
                """)
                cur.execute("ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS notified_scheduled BOOLEAN DEFAULT FALSE")
                cur.execute("ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS notified_go_live_2m BOOLEAN DEFAULT FALSE")
                cur.execute("ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS image_followup_sent BOOLEAN DEFAULT FALSE")
                cur.execute("ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS is_scheduled BOOLEAN DEFAULT FALSE")
                cur.execute("ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS go_live_at TEXT")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS market_messages (
                        id SERIAL PRIMARY KEY,
                        market_id TEXT,
                        chat_id TEXT,
                        message_id INTEGER,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS bot_state (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS premium_chats (
                        chat_id TEXT PRIMARY KEY,
                        added_by TEXT,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    UPDATE announced_markets
                    SET market_link = REPLACE(market_link, '/market/', '/m/')
                    WHERE market_link LIKE '%/market/%'
                """)
                cur.execute("""
                    UPDATE announced_markets
                    SET market_link = REPLACE(market_link, 'https://www.b4app.xyz', 'https://b4app.xyz')
                    WHERE market_link LIKE 'https://www.b4app.xyz%'
                """)
                cur.execute("""
                    UPDATE announced_markets
                    SET market_link = %s || '/' || market_id
                    WHERE market_link IS NULL
                       OR market_link NOT LIKE %s
                """, (MARKET_LINK_BASE, f"{MARKET_LINK_BASE}/%"))
        logger.info("database tables ready")
    except Exception as e:
        logger.error(f"error initialising database: {e}")
        raise


def is_admin(user_id):
    if ADMIN_ID is None or ADMIN_ID == 0:
        return False
    return user_id == ADMIN_ID


def set_bot_state(key, value):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO bot_state (key, value)
                    VALUES (%s, %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """, (key, str(value)))
    except Exception as e:
        logger.error(f"error setting bot state {key}: {e}")


def get_bot_state(key, default=None):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM bot_state WHERE key = %s", (key,))
                result = cur.fetchone()
                if result:
                    return result[0]
        return default
    except Exception as e:
        logger.error(f"error getting bot state {key}: {e}")
        return default


def set_pause_state(paused):
    set_bot_state("paused", paused)


def get_pause_state():
    return get_bot_state("paused", "False") == "True"


def set_ai_tone(tone):
    if tone not in VALID_TONES:
        return False
    set_bot_state("ai_tone", tone)
    return True


def get_ai_tone():
    return get_bot_state("ai_tone", "casual")


def set_chat_themes(chat_id, themes):
    cleaned_themes = [theme for theme in themes if theme in VALID_THEMES]
    if not cleaned_themes:
        cleaned_themes = ["all"]
    if "all" in cleaned_themes:
        cleaned_themes = ["all"]

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE subscribed_chats
                    SET themes = %s
                    WHERE chat_id = %s
                """, (",".join(cleaned_themes), str(chat_id)))
    except Exception as e:
        logger.error(f"error setting chat themes for {chat_id}: {e}")


def get_chat_themes(chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT themes FROM subscribed_chats WHERE chat_id = %s", (str(chat_id),))
                result = cur.fetchone()
                if result and result[0]:
                    return [theme for theme in result[0].split(",") if theme]
    except Exception as e:
        logger.error(f"error getting chat themes for {chat_id}: {e}")
    return ["all"]


def chat_wants_theme(chat_id, theme):
    themes = get_chat_themes(chat_id)
    return "all" in themes or theme in themes


def add_premium_chat(chat_id, added_by):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO premium_chats (chat_id, added_by)
                    VALUES (%s, %s)
                    ON CONFLICT (chat_id) DO UPDATE SET added_by = EXCLUDED.added_by
                """, (str(chat_id), str(added_by)))
        return True
    except Exception as e:
        logger.error(f"error adding premium chat {chat_id}: {e}")
        return False


def remove_premium_chat(chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM premium_chats WHERE chat_id = %s", (str(chat_id),))
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"error removing premium chat {chat_id}: {e}")
        return False


def get_premium_chat_ids():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT chat_id FROM premium_chats ORDER BY created_at DESC")
                return [row[0] for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"error fetching premium chats: {e}")
        return []


def is_premium_chat(chat_id):
    return str(chat_id) in set(get_premium_chat_ids())


def clean_ai_message(message):
    message = message.strip().strip('"').strip("'")
    return html.escape(message[:240])


def escape_text(value):
    return html.escape(str(value or "").strip())


def can_send_notification(notification_key):
    now_ts = time.time()
    last_sent = float(get_bot_state(f"last_sent_{notification_key}", "0") or 0)
    if now_ts - last_sent < NOTIFICATION_COOLDOWN_SECONDS:
        logger.warning(f"cooldown blocked notification {notification_key}")
        return False
    set_bot_state(f"last_sent_{notification_key}", now_ts)
    return True


def generate_smart_notification(title, theme, notification_type="new"):
    """Generate short, direct opinion market notifications."""
    if not ai_client:
        return None
    
    try:
        tone = get_ai_tone()
        tone_instruction = {
            "casual": "sound casual, warm, and direct.",
            "urgent": "sound urgent without sounding spammy.",
            "premium": "sound polished, confident, and concise.",
            "degen": "sound crypto-native, playful, and sharp, but avoid offensive language.",
            "professional": "sound clear, professional, and calm.",
        }.get(tone, "sound casual, warm, and direct.")

        if notification_type == "new":
            prompt = f"""you are a b4 opinion market bot. generate a short 1-sentence call to action for a new opinion market.
opinion: "{title}"
{tone_instruction} no fluff. just get people to share their opinion. lowercase."""
        elif notification_type == "1h":
            prompt = f"""generate a short 1-sentence reminder for an opinion market closing in 1 hour.
opinion: "{title}"
{tone_instruction} no fluff. just tell them to hurry up and share. lowercase."""
        elif notification_type == "10m":
            prompt = f"""generate a short 1-sentence URGENT reminder for an opinion market closing in 10 minutes.
opinion: "{title}"
{tone_instruction} this is the last call. no fluff. lowercase."""
        else:
            return None

        response = ai_client.chat.completions.create(
            model=ai_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50
        )
        
        message = clean_ai_message(response.choices[0].message.content)
        logger.info(f"generated ai notification for {notification_type}: {message[:50]}...")
        return message
    except Exception as e:
        logger.error(f"error generating smart notification: {e}")
        return None


def save_user(message):
    try:
        user_id = str(message.from_user.id)
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO users (user_id, username, first_name, join_date, is_admin)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO NOTHING
                """, (
                    user_id,
                    message.from_user.username or "No Username",
                    message.from_user.first_name or "No Name",
                    now_utc().isoformat(),
                    is_admin(message.from_user.id)
                ))
    except Exception as e:
        logger.error(f"error saving user: {e}")


def add_chat(chat_id, chat_name):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO subscribed_chats (chat_id, chat_name)
                    VALUES (%s, %s)
                    ON CONFLICT (chat_id) DO NOTHING
                """, (str(chat_id), chat_name))
    except Exception as e:
        logger.error(f"error adding chat: {e}")


def remove_chat(chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM subscribed_chats WHERE chat_id = %s", (str(chat_id),))
    except Exception as e:
        logger.error(f"error removing chat: {e}")


def get_all_chats():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT chat_id FROM subscribed_chats")
                rows = cur.fetchall()
                return [row[0] for row in rows]
    except Exception as e:
        logger.error(f"error fetching chats: {e}")
        return []


def get_all_users():
    try:
        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("SELECT * FROM users")
                return cur.fetchall()
    except Exception as e:
        logger.error(f"error fetching users: {e}")
        return []


def get_announced_market(market_id):
    try:
        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("SELECT * FROM announced_markets WHERE market_id = %s", (str(market_id),))
                return cur.fetchone()
    except Exception as e:
        logger.error(f"error fetching market {market_id}: {e}")
        return None


def save_announced_market(market_id, title, theme, end_time, notified_new=True, is_scheduled=False, go_live_at=None):
    try:
        market_link = build_market_link(market_id)
        go_live_value = go_live_at.isoformat() if isinstance(go_live_at, datetime) else go_live_at
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO announced_markets (
                        market_id, title, theme, end_time, market_link, notified_new,
                        notified_1h, notified_5m, notified_ended, delete_scheduled,
                        notified_scheduled, notified_go_live_2m, image_followup_sent,
                        is_scheduled, go_live_at, detected_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, %s, %s, %s)
                    ON CONFLICT (market_id) DO NOTHING
                    RETURNING market_id
                """, (
                    str(market_id), title, theme, end_time, market_link, notified_new,
                    is_scheduled, go_live_value, now_utc().isoformat()
                ))
                return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"error saving market {market_id}: {e}")
    return False


def update_market_flag(market_id, flag):
    allowed_flags = {
        "notified_new", "notified_1h", "notified_5m", "notified_ended",
        "delete_scheduled", "notified_scheduled", "notified_go_live_2m",
        "image_followup_sent"
    }
    if flag not in allowed_flags:
        logger.error(f"invalid market flag requested: {flag}")
        return

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE announced_markets SET {flag} = TRUE WHERE market_id = %s", (str(market_id),))
    except Exception as e:
        logger.error(f"error updating flag {flag} for market {market_id}: {e}")


def delete_announced_market(market_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM announced_markets WHERE market_id = %s", (str(market_id),))
    except Exception as e:
        logger.error(f"error deleting announced market {market_id}: {e}")


def get_all_announced_markets():
    try:
        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("SELECT * FROM announced_markets")
                return cur.fetchall()
    except Exception as e:
        logger.error(f"error fetching all markets: {e}")
        return []


def get_recent_announced_markets(limit=8):
    try:
        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("""
                    SELECT * FROM announced_markets
                    ORDER BY detected_at DESC NULLS LAST
                    LIMIT %s
                """, (limit,))
                return cur.fetchall()
    except Exception as e:
        logger.error(f"error fetching recent markets: {e}")
        return []


def update_market_live_state(market_id, is_scheduled=False, go_live_at=None):
    try:
        go_live_value = go_live_at.isoformat() if isinstance(go_live_at, datetime) else go_live_at
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE announced_markets
                    SET is_scheduled = %s, go_live_at = COALESCE(%s, go_live_at)
                    WHERE market_id = %s
                """, (is_scheduled, go_live_value, str(market_id)))
    except Exception as e:
        logger.error(f"error updating live state for {market_id}: {e}")


def save_message_id(market_id, chat_id, message_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO market_messages (market_id, chat_id, message_id)
                    VALUES (%s, %s, %s)
                """, (str(market_id), str(chat_id), message_id))
    except Exception as e:
        logger.error(f"error saving message id: {e}")


def get_market_messages(market_id):
    try:
        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("SELECT * FROM market_messages WHERE market_id = %s", (str(market_id),))
                return cur.fetchall()
    except Exception as e:
        logger.error(f"error fetching messages for market {market_id}: {e}")
        return []


def delete_market_messages_from_db(market_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM market_messages WHERE market_id = %s", (str(market_id),))
    except Exception as e:
        logger.error(f"error deleting messages for market {market_id}: {e}")


def delete_all_tracked_messages():
    try:
        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("SELECT * FROM market_messages")
                messages = cur.fetchall()

        deleted = 0
        failed = 0
        for msg in messages:
            try:
                bot.delete_message(int(msg["chat_id"]), int(msg["message_id"]))
                deleted += 1
                time.sleep(0.05)
            except Exception as e:
                logger.error(f"error deleting message {msg['message_id']} in chat {msg['chat_id']}: {e}")
                failed += 1

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM market_messages")

        return deleted, failed
    except Exception as e:
        logger.error(f"error deleting all tracked messages: {e}")
        raise


def delete_callback_message(call):
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
        return True
    except Exception as e:
        logger.warning(f"could not delete callback message {call.message.message_id}: {e}")
        return False


def schedule_delete_message(chat_id, message_id, delay_seconds=None):
    delay = TEMP_RESPONSE_DELETE_SECONDS if delay_seconds is None else delay_seconds
    if delay <= 0:
        return

    def delete_after_delay():
        time.sleep(delay)
        try:
            bot.delete_message(chat_id, message_id)
        except Exception as e:
            logger.info(f"could not auto-delete message {message_id} in chat {chat_id}: {e}")

    Thread(target=delete_after_delay, daemon=True).start()


def try_delete_user_message(message):
    try:
        bot.delete_message(message.chat.id, message.message_id)
        return True
    except Exception as e:
        logger.info(
            f"could not delete user message {message.message_id} in chat {message.chat.id}; "
            f"bot may need group admin delete permission: {e}"
        )
        return False


def send_temp_message(chat_id, text, reply_markup=None, parse_mode=None, reply_to_message_id=None):
    sent = bot.send_message(
        chat_id,
        text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
        reply_to_message_id=reply_to_message_id,
    )
    schedule_delete_message(chat_id, sent.message_id)
    return sent


def reply_temp(message, text, reply_markup=None, parse_mode=None):
    sent = bot.reply_to(message, text, reply_markup=reply_markup, parse_mode=parse_mode)
    schedule_delete_message(message.chat.id, sent.message_id)
    try_delete_user_message(message)
    return sent


def keyboard_to_payload(keyboard):
    if not keyboard:
        return None
    try:
        return json.loads(keyboard.to_json())
    except Exception as e:
        logger.warning(f"could not encode keyboard for rich message: {e}")
        return None


def send_rich_message_to_chat(chat_id, rich_html, keyboard=None):
    payload = {
        "chat_id": int(chat_id),
        "rich_message": {"html": rich_html},
    }
    reply_markup = keyboard_to_payload(keyboard)
    if reply_markup:
        payload["reply_markup"] = reply_markup

    response = requests.post(
        f"{TELEGRAM_API_URL}/sendRichMessage",
        json=payload,
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "sendRichMessage failed"))
    return int(data["result"]["message_id"])


def send_temp_rich(chat_id, rich_html, fallback_text, reply_markup=None):
    try:
        message_id = send_rich_message_to_chat(chat_id, rich_html, reply_markup)
        schedule_delete_message(chat_id, message_id)
        return message_id
    except Exception as e:
        logger.warning(f"rich temp message failed, falling back to text: {e}")
        sent = send_temp_message(chat_id, fallback_text, reply_markup=reply_markup, parse_mode="HTML")
        return sent.message_id


def send_notification_to_chat(chat_id, message_text, market_id=None, keyboard=None, photo_url=None, rich_html=None):
    sent_msg = None
    if rich_html:
        try:
            message_id = send_rich_message_to_chat(chat_id, rich_html, keyboard)
            if market_id:
                save_message_id(market_id, chat_id, message_id)
            time.sleep(SEND_DELAY_SECONDS)
            return True
        except Exception as e:
            logger.warning(f"rich message send failed for {chat_id}, falling back: {e}")

    if photo_url:
        try:
            sent_msg = bot.send_photo(
                int(chat_id),
                photo_url,
                caption=message_text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"photo send failed for {chat_id}, falling back to text: {e}")

    if not sent_msg:
        if keyboard:
            sent_msg = bot.send_message(int(chat_id), message_text, reply_markup=keyboard, parse_mode="HTML")
        else:
            sent_msg = bot.send_message(int(chat_id), message_text, parse_mode="HTML")

    if market_id:
        save_message_id(market_id, chat_id, sent_msg.message_id)
    time.sleep(SEND_DELAY_SECONDS)
    return True


def broadcast_to_all(message_text, market_id=None, keyboard=None, theme=None, notification_key=None, photo_url=None, premium_only=False, rich_html=None):
    try:
        if notification_key and not can_send_notification(notification_key):
            return

        premium_ids = set(get_premium_chat_ids()) if premium_only else None
        chats = [
            chat_id for chat_id in get_all_chats()
            if not theme or chat_wants_theme(chat_id, theme)
        ]
        if premium_ids is not None:
            chats = [chat_id for chat_id in chats if str(chat_id) in premium_ids]
        sent = 0

        if BROADCAST_WORKERS <= 1 or len(chats) <= 1:
            for chat_id in chats:
                try:
                    if send_notification_to_chat(chat_id, message_text, market_id, keyboard, photo_url, rich_html):
                        sent += 1
                except Exception as e:
                    logger.error(f"error sending to {chat_id}: {e}")
        else:
            with ThreadPoolExecutor(max_workers=BROADCAST_WORKERS) as executor:
                futures = {
                    executor.submit(send_notification_to_chat, chat_id, message_text, market_id, keyboard, photo_url, rich_html): chat_id
                    for chat_id in chats
                }
                for future in as_completed(futures):
                    chat_id = futures[future]
                    try:
                        if future.result():
                            sent += 1
                    except Exception as e:
                        logger.error(f"error sending to {chat_id}: {e}")

        if sent:
            set_bot_state("last_notification_sent", now_utc().isoformat())
        logger.info(f"broadcast sent to {sent} chats")
    except Exception as e:
        logger.error(f"error in broadcast_to_all: {e}")


def delete_all_market_messages(market_id):
    try:
        messages = get_market_messages(market_id)
        deleted = 0
        failed = 0

        for msg in messages:
            chat_id = int(msg["chat_id"])
            message_id = msg["message_id"]
            try:
                bot.delete_message(chat_id, message_id)
                deleted += 1
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"error deleting message {message_id} in chat {chat_id}: {e}")
                failed += 1

        delete_market_messages_from_db(market_id)
        logger.info(f"deleted {deleted} messages for market {market_id}, {failed} failed")
    except Exception as e:
        logger.error(f"error in delete_all_market_messages: {e}")


def refresh_market_message_buttons():
    refreshed = 0
    failed = 0
    markets = get_all_announced_markets()

    for market in markets:
        market_id = str(market.get("market_id", "")).strip()
        if not market_id:
            continue

        market_link = build_market_link(market_id)
        keyboard = create_market_keyboard(market_id, market_link)
        messages = get_market_messages(market_id)

        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE announced_markets SET market_link = %s WHERE market_id = %s",
                        (market_link, market_id)
                    )
        except Exception as e:
            logger.error(f"error updating stored link for {market_id}: {e}")

        for msg in messages:
            try:
                bot.edit_message_reply_markup(
                    int(msg["chat_id"]),
                    int(msg["message_id"]),
                    reply_markup=keyboard
                )
                refreshed += 1
                time.sleep(0.05)
            except Exception as e:
                logger.error(f"error refreshing button for market {market_id}: {e}")
                failed += 1

    return refreshed, failed


def schedule_message_deletion(market_id, title):
    def delete_after_delay():
        logger.info(f"waiting 10 minutes before deleting messages for: {title}")
        time.sleep(600)
        logger.info(f"deleting messages for market: {title}")
        delete_all_market_messages(market_id)
        update_market_flag(market_id, "delete_scheduled")

    delete_thread = Thread(target=delete_after_delay, daemon=True)
    delete_thread.start()


def fetch_b4_markets():
    try:
        response = requests.get(
            B4_API_URL,
            params={"_": int(time.time())},
            headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        if isinstance(data, dict) and "markets" in data:
            logger.info(f"fetched {len(data['markets'])} markets from api")
            set_bot_state("last_api_check", now_utc().isoformat())
            return data["markets"]
        elif isinstance(data, list):
            logger.info(f"fetched {len(data)} markets from api")
            set_bot_state("last_api_check", now_utc().isoformat())
            return data
        else:
            logger.error(f"unexpected api response: {data}")
            return []
    except Exception as e:
        logger.error(f"error fetching b4 markets: {e}")
        return []


def is_valid_market(market):
    market_id = str(market.get("market_id", "")).strip()
    if not market_id:
        return False

    title = str(market.get("title", "")).strip()
    if not title:
        return False

    end_time_unix = market.get("end_time")
    if not end_time_unix or not isinstance(end_time_unix, (int, float)) or int(end_time_unix) <= 0:
        return False

    return True


def is_market_active(market):
    try:
        if market.get("resolved", False):
            return False

        if market.get("hidden", False):
            return False

        end_time_unix = market.get("end_time")
        if end_time_unix:
            end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None)
            if now_utc() > end_time:
                return False

        return True
    except Exception as e:
        logger.error(f"error checking market status: {e}")
        return False


def create_market_keyboard(market_id, market_link):
    """create inline buttons for market notifications"""
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("🗳️ Vote Now", url=market_link)
    )
    return keyboard


def format_theme(theme):
    theme = normalize_theme(theme)
    theme_map = {
        "crypto": "🪙 Crypto",
        "politics": "🏛️ Politics",
        "entertainment": "🎬 Entertainment",
        "sports": "⚽ Sports",
        "travel": "✈️ Travel",
        "current_events": "📰 Current Events",
        "other": "💬 General"
    }
    return theme_map.get(theme, f"💬 {theme.title()}" if theme else "💬 General")


def get_market_cover_image(market):
    if market.get("cover_image_status") != "ready":
        return None
    cover_url = str(market.get("cover_image_url") or "").strip()
    return cover_url or None


def wait_for_market_cover_image(market_id, market):
    cover_url = get_market_cover_image(market)
    if cover_url or COVER_IMAGE_WAIT_SECONDS <= 0:
        return cover_url

    deadline = time.time() + COVER_IMAGE_WAIT_SECONDS
    while time.time() < deadline:
        time.sleep(COVER_IMAGE_RETRY_SECONDS)
        for latest_market in fetch_b4_markets():
            latest_id = str(latest_market.get("market_id", "")).strip()
            if latest_id != str(market_id):
                continue
            cover_url = get_market_cover_image(latest_market)
            if cover_url:
                logger.info(f"cover image ready for market {market_id}")
                return cover_url
            logger.info(
                f"cover image not ready for market {market_id}; "
                f"status={latest_market.get('cover_image_status')}"
            )
            break

    logger.info(f"cover image unavailable before notification for market {market_id}")
    return None


def build_market_promo_text(market):
    lines = []

    if market.get("first_staker_promo_available"):
        match_amount = market.get("first_staker_match_usdc")
        min_stake = market.get("first_staker_min_stake_usdc")
        if match_amount and min_stake:
            lines.append(
                f"🎁 First-staker promo: ${escape_text(match_amount)} match for ${escape_text(min_stake)}+ stake"
            )
        else:
            lines.append("🎁 First-staker promo available")

    sponsor_count = int(market.get("sponsor_match_count") or 0)
    if sponsor_count > 0:
        label = "sponsor boost" if sponsor_count == 1 else "sponsor boosts"
        lines.append(f"🤝 {sponsor_count} {label} active")

    return "\n".join(lines)


def build_new_market_notification(market, ai_message):
    title = str(market.get("title", "")).strip()
    raw_theme = normalize_theme(market.get("theme", "other"))
    theme = format_theme(raw_theme)
    end_time_unix = market.get("end_time")
    end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None)
    end_time_str = end_time.strftime('%b %d, %Y at %I:%M %p UTC')
    promo_text = build_market_promo_text(market)

    message = (
        f"🆕 <b>NEW MARKET LIVE</b>\n\n"
        f"📌 <b>{escape_text(title)}</b>\n\n"
        f"🏷️ Theme: {escape_text(theme)}\n"
        f"⏰ Closes: {escape_text(end_time_str)}"
    )

    if promo_text:
        message += f"\n{promo_text}"

    message += "\n\n"
    if ai_message:
        message += ai_message
    else:
        message += "Share your opinion before the market moves."

    return message


def build_scheduled_market_notification(market):
    title = str(market.get("title", "")).strip()
    raw_theme = normalize_theme(market.get("theme", "other"))
    theme = format_theme(raw_theme)
    go_live_at = get_market_go_live_at(market)
    go_live_text = go_live_at.strftime('%b %d, %Y at %I:%M %p UTC') if go_live_at else "soon"
    promo_text = build_market_promo_text(market)

    message = (
        f"⭐ <b>PREMIUM EARLY MARKET ALERT</b>\n\n"
        f"📌 <b>{escape_text(title)}</b>\n\n"
        f"🏷️ Theme: {escape_text(theme)}\n"
        f"🚀 Goes Live: <b>{escape_text(go_live_text)}</b>"
    )
    if promo_text:
        message += f"\n{promo_text}"

    message += "\n\nPremium users are seeing this before the public live alert."
    return message


def build_go_live_reminder_notification(market_data):
    title = market_data.get("title", "").strip()
    go_live_at = market_data.get("go_live_at")
    if isinstance(go_live_at, datetime):
        go_live_text = go_live_at.strftime('%I:%M %p UTC')
    else:
        parsed = parse_api_datetime(go_live_at)
        go_live_text = parsed.strftime('%I:%M %p UTC') if parsed else "very soon"

    return (
        f"⏱️ <b>PREMIUM 2-MINUTE LIVE REMINDER</b>\n\n"
        f"📌 <b>{escape_text(title)}</b>\n\n"
        f"This market goes live at <b>{escape_text(go_live_text)}</b>."
    )


def build_rich_media_block(image_url, caption):
    if not image_url:
        return ""
    return (
        f'<figure><img src="{escape_text(image_url)}"/>'
        f"<figcaption>{escape_text(caption)}</figcaption></figure>"
    )


def build_rich_scheduled_market(market):
    title = str(market.get("title", "")).strip()
    raw_theme = normalize_theme(market.get("theme", "other"))
    go_live_at = get_market_go_live_at(market)
    end_time_unix = market.get("end_time")
    end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None)
    cover_url = get_market_cover_image(market)
    promo_text = build_market_promo_text(market) or "No promo details attached yet."
    go_live_text = go_live_at.strftime('%b %d, %Y at %I:%M %p UTC') if go_live_at else "Soon"

    return (
        f"<h2>Premium Early Market Alert</h2>"
        f"{build_rich_media_block(cover_url, title)}"
        f"<p><b>{escape_text(title)}</b></p>"
        f"<table>"
        f"<tr><th>Theme</th><td>{escape_text(format_theme(raw_theme))}</td></tr>"
        f"<tr><th>Goes Live</th><td>{escape_text(go_live_text)}</td></tr>"
        f"<tr><th>Closes</th><td>{escape_text(end_time.strftime('%b %d, %Y at %I:%M %p UTC'))}</td></tr>"
        f"</table>"
        f"<blockquote>{escape_text(promo_text)}</blockquote>"
        f"<p>Premium users are seeing this before the public live alert.</p>"
    )


def build_rich_digest():
    markets = get_all_announced_markets()
    scheduled = [
        market for market in markets
        if market.get("is_scheduled") and not market.get("notified_new") and not market.get("notified_ended")
    ][:8]
    active = [
        market for market in markets
        if market.get("notified_new") and not market.get("notified_ended")
    ][:8]

    rows = ""
    for market in scheduled:
        go_live_at = market.get("go_live_at")
        parsed = go_live_at if isinstance(go_live_at, datetime) else parse_api_datetime(go_live_at)
        go_live_text = parsed.strftime('%b %d, %I:%M %p UTC') if parsed else "Soon"
        rows += (
            f"<tr><td>{escape_text(market.get('title'))}</td>"
            f"<td>Scheduled</td><td>{escape_text(go_live_text)}</td></tr>"
        )
    for market in active:
        rows += (
            f"<tr><td>{escape_text(market.get('title'))}</td>"
            f"<td>Live</td><td>Now</td></tr>"
        )
    if not rows:
        rows = "<tr><td>No markets tracked yet</td><td>-</td><td>-</td></tr>"

    return (
        f"<h2>Daily B4 Market Digest</h2>"
        f"<p>Clean summary of scheduled and live markets. No vote counts, no volume.</p>"
        f"<table><tr><th>Market</th><th>Status</th><th>Timing</th></tr>{rows}</table>"
    )


def build_rich_health():
    checks = [
        ("API checks", get_bot_state("last_api_check", "never")),
        ("Last market", get_bot_state("last_market_detected", "none")),
        ("Last notification", get_bot_state("last_notification_sent", "none")),
        ("AI", "Active" if ai_client else "Not configured"),
        ("Status", "Paused" if get_pause_state() else "Running"),
    ]
    rows = "".join(
        f"<tr><td>{escape_text(label)}</td><td>{escape_text(value)}</td></tr>"
        for label, value in checks
    )
    return (
        f"<h2>Notify Bot Health</h2>"
        f"<table><tr><th>Check</th><th>Value</th></tr>{rows}</table>"
        f"<blockquote>Poll: {MARKET_POLL_SECONDS}s | Image wait: {COVER_IMAGE_WAIT_SECONDS}s</blockquote>"
    )


def build_rich_recent():
    markets = get_recent_announced_markets()
    rows = ""
    for market in markets:
        status = "Scheduled" if market.get("is_scheduled") and not market.get("notified_new") else "Live"
        rows += (
            f"<tr><td>{escape_text(market.get('title'))}</td>"
            f"<td>{escape_text(status)}</td>"
            f"<td>{escape_text(market.get('market_id'))}</td></tr>"
        )
    if not rows:
        rows = "<tr><td>No markets yet</td><td>-</td><td>-</td></tr>"
    return f"<h2>Recent Announced Markets</h2><table><tr><th>Market</th><th>Status</th><th>ID</th></tr>{rows}</table>"


def schedule_image_followup(market_id, title, theme):
    if IMAGE_FOLLOWUP_WAIT_SECONDS <= 0:
        return

    def send_when_ready():
        deadline = time.time() + IMAGE_FOLLOWUP_WAIT_SECONDS
        while time.time() < deadline:
            time.sleep(COVER_IMAGE_RETRY_SECONDS)
            for latest_market in fetch_b4_markets():
                if str(latest_market.get("market_id", "")).strip() != str(market_id):
                    continue
                cover_url = get_market_cover_image(latest_market)
                if not cover_url:
                    break
                message = (
                    f"🖼️ <b>Market Cover Ready</b>\n\n"
                    f"📌 <b>{escape_text(title)}</b>"
                )
                broadcast_to_all(
                    message,
                    market_id,
                    create_market_keyboard(market_id, build_market_link(market_id)),
                    theme=theme,
                    notification_key=f"image_followup_{market_id}",
                    photo_url=cover_url,
                )
                update_market_flag(market_id, "image_followup_sent")
                logger.info(f"image follow-up sent for market {market_id}")
                return

    Thread(target=send_when_ready, daemon=True).start()


def normalize_theme(theme):
    theme_text = str(theme or "other").lower()
    for valid_theme in VALID_THEMES:
        if valid_theme != "all" and valid_theme in theme_text:
            return valid_theme
    return "other"


def build_admin_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("⏸ Pause", callback_data="admin_pause"),
        types.InlineKeyboardButton("▶️ Resume", callback_data="admin_resume"),
        types.InlineKeyboardButton("🧪 Test", callback_data="admin_test"),
        types.InlineKeyboardButton("🧹 Clean", callback_data="admin_clean"),
        types.InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
        types.InlineKeyboardButton("🎛 Tone", callback_data="admin_tone"),
    )
    return keyboard


def build_tone_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    for tone in VALID_TONES:
        keyboard.add(types.InlineKeyboardButton(tone.title(), callback_data=f"tone_{tone}"))
    keyboard.add(types.InlineKeyboardButton("⬅️ Admin", callback_data="admin_menu"))
    return keyboard


def build_theme_keyboard(selected_themes):
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    selected = set(selected_themes)
    for theme in VALID_THEMES:
        label = "All Markets" if theme == "all" else format_theme(theme)
        prefix = "✅" if theme in selected else "☑️"
        keyboard.add(types.InlineKeyboardButton(f"{prefix} {label}", callback_data=f"theme_{theme}"))
    return keyboard


def build_main_menu_keyboard(user_id=None, chat_type=None):
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(types.KeyboardButton("📊 Status"))
    keyboard.add(
        types.KeyboardButton("⏰ Ending Soon"),
        types.KeyboardButton("🏷 Preferences"),
    )
    keyboard.add(
        types.KeyboardButton("ℹ️ Help"),
        types.KeyboardButton("🆔 My ID"),
    )
    if chat_type == "private" and user_id and is_admin(user_id):
        keyboard.add(types.KeyboardButton("🛠 Admin"))
    return keyboard


def get_stats_text():
    all_markets = get_all_announced_markets()
    total_users = len(get_all_users())
    total_markets = len(all_markets)
    total_chats = len(get_all_chats())
    premium_chats = len(get_premium_chat_ids())
    active = sum(1 for m in all_markets if not m.get("notified_ended"))
    paused = "Paused" if get_pause_state() else "Running"
    tone = get_ai_tone().title()

    return (
        f"📊 <b>Bot Statistics</b>\n\n"
        f"👥 Users: <b>{total_users}</b>\n"
        f"💬 Subscribed Chats: <b>{total_chats}</b>\n"
        f"🔍 Markets Tracked: <b>{total_markets}</b>\n"
        f"🟢 Active Markets: <b>{active}</b>\n"
        f"🎛 AI Tone: <b>{tone}</b>\n"
        f"✅ Status: <b>{paused}</b>"
    )


def get_health_text():
    return (
        f"🩺 <b>Notify Bot Health</b>\n\n"
        f"Status: <b>{'Paused' if get_pause_state() else 'Running'}</b>\n"
        f"Last API Check: <code>{escape_text(get_bot_state('last_api_check', 'never'))}</code>\n"
        f"Last Market: <code>{escape_text(get_bot_state('last_market_detected', 'none'))}</code>\n"
        f"Last Notification: <code>{escape_text(get_bot_state('last_notification_sent', 'none'))}</code>\n"
        f"AI: <b>{'Active' if ai_client else 'Not configured'}</b>\n"
        f"Poll Interval: <b>{MARKET_POLL_SECONDS}s</b>\n"
        f"Image Wait: <b>{COVER_IMAGE_WAIT_SECONDS}s</b>\n"
        f"Market Link Base: <code>{escape_text(MARKET_LINK_BASE)}</code>"
    )


def build_recent_markets_text():
    markets = get_recent_announced_markets()
    if not markets:
        return "No markets announced yet."

    lines = ["🕘 <b>Recent Announced Markets</b>"]
    for market in markets:
        status = "scheduled" if market.get("is_scheduled") and not market.get("notified_new") else "live"
        lines.append(
            f"\n<b>{escape_text(market.get('title', 'Untitled'))}</b>\n"
            f"ID: <code>{escape_text(market.get('market_id'))}</code>\n"
            f"Status: {escape_text(status)}"
        )
    return "\n".join(lines)


def build_daily_summary_text():
    markets = get_all_announced_markets()
    active_markets = [m for m in markets if not m.get("notified_ended")]
    ending_soon = get_ending_soon_markets()

    theme_counts = {}
    for market in active_markets:
        theme = normalize_theme(market.get("theme", "other"))
        theme_counts[theme] = theme_counts.get(theme, 0) + 1

    top_themes = sorted(theme_counts.items(), key=lambda item: item[1], reverse=True)[:3]
    theme_line = ", ".join(f"{format_theme(theme)} ({count})" for theme, count in top_themes) if top_themes else "No active themes yet"

    summary = (
        f"☀️ <b>Daily B4 Market Summary</b>\n\n"
        f"🟢 Active Markets: <b>{len(active_markets)}</b>\n"
        f"⏰ Ending Within 1 Hour: <b>{len(ending_soon)}</b>\n"
        f"🏷️ Top Themes: {escape_text(theme_line)}"
    )

    if ending_soon:
        summary += "\n\n<b>Closing Soon</b>\n"
        for market in ending_soon[:5]:
            mins = int(market["time_until"] / 60)
            summary += f"• {escape_text(market['title'])} - {mins}m\n"

    return summary


def build_premium_digest_text():
    markets = get_all_announced_markets()
    scheduled = [
        market for market in markets
        if market.get("is_scheduled") and not market.get("notified_new") and not market.get("notified_ended")
    ]
    active = [
        market for market in markets
        if market.get("notified_new") and not market.get("notified_ended")
    ]

    lines = [
        "⭐ <b>Premium B4 Market Digest</b>",
        "",
        f"Scheduled early alerts: <b>{len(scheduled)}</b>",
        f"Live markets tracked: <b>{len(active)}</b>",
    ]

    if scheduled:
        lines.append("\n<b>Upcoming</b>")
        for market in scheduled[:5]:
            go_live_at = market.get("go_live_at")
            if isinstance(go_live_at, datetime):
                go_live_text = go_live_at.strftime('%b %d, %I:%M %p UTC')
            else:
                parsed = parse_api_datetime(go_live_at)
                go_live_text = parsed.strftime('%b %d, %I:%M %p UTC') if parsed else "soon"
            lines.append(f"• {escape_text(market.get('title'))} — {escape_text(go_live_text)}")

    if active:
        lines.append("\n<b>Live Now</b>")
        for market in active[:5]:
            lines.append(f"• {escape_text(market.get('title'))}")

    return "\n".join(lines)


def send_daily_summary_if_due():
    now = datetime.now(timezone.utc)
    if now.hour != DAILY_SUMMARY_UTC_HOUR:
        return

    today_key = now.strftime("%Y-%m-%d")
    if get_bot_state("last_daily_summary_date") == today_key:
        return

    broadcast_to_all(
        build_daily_summary_text(),
        notification_key=f"daily_summary_{today_key}",
        rich_html=build_rich_digest(),
    )
    broadcast_to_all(
        build_premium_digest_text(),
        notification_key=f"premium_digest_{today_key}",
        premium_only=True,
    )
    set_bot_state("last_daily_summary_date", today_key)
    logger.info(f"daily summary sent for {today_key}")


def is_scheduled_market(market):
    go_live_at = get_market_go_live_at(market)
    return bool(go_live_at and go_live_at > now_utc())


def announce_live_market(market, existing=None):
    market_id = str(market.get("market_id", "")).strip()
    title = str(market.get("title", "")).strip()
    raw_theme = normalize_theme(market.get("theme", "other"))
    end_time_unix = market.get("end_time")
    end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None)
    ai_message = generate_smart_notification(title, raw_theme, "new")
    keyboard = create_market_keyboard(market_id, build_market_link(market_id))
    notification = build_new_market_notification(market, ai_message)
    cover_image_url = wait_for_market_cover_image(market_id, market)

    if existing:
        update_market_flag(market_id, "notified_new")
        update_market_live_state(market_id, is_scheduled=False, go_live_at=get_market_go_live_at(market))
        should_broadcast = True
    else:
        should_broadcast = save_announced_market(
            market_id,
            title,
            raw_theme,
            end_time.isoformat(),
            notified_new=True,
            is_scheduled=False,
            go_live_at=get_market_go_live_at(market),
        )

    if not should_broadcast:
        logger.info(f"market {market_id} was already reserved for announcement")
        return

    broadcast_to_all(
        notification,
        market_id,
        keyboard,
        theme=raw_theme,
        notification_key=f"new_{market_id}",
        photo_url=cover_image_url,
    )
    set_bot_state("last_market_detected", f"{market_id} | {title}")
    if not cover_image_url:
        schedule_image_followup(market_id, title, raw_theme)
    logger.info(f"new market announced: {title}")


def announce_scheduled_market_to_premium(market):
    market_id = str(market.get("market_id", "")).strip()
    title = str(market.get("title", "")).strip()
    raw_theme = normalize_theme(market.get("theme", "other"))
    end_time_unix = market.get("end_time")
    end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None)
    go_live_at = get_market_go_live_at(market)

    saved = save_announced_market(
        market_id,
        title,
        raw_theme,
        end_time.isoformat(),
        notified_new=False,
        is_scheduled=True,
        go_live_at=go_live_at,
    )
    if not saved:
        return

    keyboard = create_market_keyboard(market_id, build_market_link(market_id))
    notification = build_scheduled_market_notification(market)
    cover_image_url = wait_for_market_cover_image(market_id, market)
    broadcast_to_all(
        notification,
        market_id,
        keyboard,
        theme=raw_theme,
        notification_key=f"scheduled_{market_id}",
        photo_url=cover_image_url,
        premium_only=True,
        rich_html=build_rich_scheduled_market(market),
    )
    update_market_flag(market_id, "notified_scheduled")
    set_bot_state("last_market_detected", f"{market_id} | {title} (scheduled)")
    logger.info(f"premium scheduled market announced: {title}")


def monitor_b4_markets():
    logger.info("b4 market monitoring thread started")
    while True:
        try:
            if get_pause_state():
                logger.info("notifications paused, skipping check")
                time.sleep(10)
                continue
            
            markets = fetch_b4_markets()
            logger.info(f"processing {len(markets)} markets")
            
            announced_markets = get_all_announced_markets()
            api_market_ids = set(str(m.get("market_id", "")).strip() for m in markets)
            
            for announced in announced_markets:
                announced_id = str(announced.get("market_id", "")).strip()
                
                if announced_id not in api_market_ids:
                    continue
                
                api_market = next((m for m in markets if str(m.get("market_id", "")).strip() == announced_id), None)
                if not api_market:
                    continue
                
                if api_market.get("hidden", False) and not announced.get("notified_ended"):
                    logger.info(f"market {announced_id} is now hidden, cleaning up notifications")
                    delete_all_market_messages(announced_id)
                    delete_announced_market(announced_id)
                    logger.info(f"removed hidden market {announced_id} from tracking")

            for market in markets:
                try:
                    market_id = str(market.get("market_id", "")).strip()

                    if not market_id:
                        continue

                    if not is_valid_market(market):
                        logger.warning(f"skipped {market_id}: failed validation")
                        continue

                    if not is_market_active(market):
                        end_time_unix = market.get("end_time")
                        logger.warning(f"skipped {market_id}: market not active (end_time: {end_time_unix}, resolved: {market.get('resolved')}, hidden: {market.get('hidden')})")
                        continue

                    existing = get_announced_market(market_id)
                    if not existing:
                        if is_scheduled_market(market):
                            announce_scheduled_market_to_premium(market)
                        else:
                            announce_live_market(market)
                    elif existing.get("is_scheduled") and not existing.get("notified_new") and not is_scheduled_market(market):
                        announce_live_market(market, existing=existing)

                except Exception as e:
                    logger.error(f"error processing market: {e}")

            check_scheduled_notifications()
            send_daily_summary_if_due()
            time.sleep(MARKET_POLL_SECONDS)

        except Exception as e:
            logger.error(f"error in monitor_b4_markets: {e}")
            time.sleep(60)


def check_scheduled_notifications():
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        markets = get_all_announced_markets()

        for market_data in markets:
            try:
                market_id = market_data["market_id"]

                if market_data.get("notified_ended"):
                    continue

                end_time_str = market_data.get("end_time")
                title = market_data.get("title", "").strip()
                raw_theme = normalize_theme(market_data.get("theme", "other"))

                if not end_time_str or not title:
                    continue

                go_live_value = market_data.get("go_live_at")
                if market_data.get("is_scheduled") and not market_data.get("notified_go_live_2m") and go_live_value:
                    go_live_at = go_live_value if isinstance(go_live_value, datetime) else parse_api_datetime(go_live_value)
                    if go_live_at:
                        seconds_to_live = (go_live_at - now).total_seconds()
                        if 0 < seconds_to_live <= PREMIUM_GO_LIVE_REMINDER_SECONDS:
                            market_link = market_data.get("market_link", build_market_link(market_id))
                            broadcast_to_all(
                                build_go_live_reminder_notification(market_data),
                                market_id,
                                create_market_keyboard(market_id, market_link),
                                theme=raw_theme,
                                notification_key=f"go_live_2m_{market_id}",
                                premium_only=True,
                            )
                            update_market_flag(market_id, "notified_go_live_2m")
                            logger.info(f"premium go-live reminder sent for: {title}")

                end_time = datetime.fromisoformat(end_time_str)
                time_until = (end_time - now).total_seconds()

                logger.info(f"market: {title} | time_until: {time_until:.0f}s | notified_1h: {market_data.get('notified_1h')} | notified_5m: {market_data.get('notified_5m')}")

                if time_until > 0:
                    hours_until = time_until / 3600
                    minutes_until = time_until / 60

                    if hours_until <= 1.0 and not market_data.get("notified_1h"):
                        mins_left = int(minutes_until)
                        
                        # try ai message
                        ai_message = generate_smart_notification(title, market_data.get("theme", "other"), "1h")
                        market_link = market_data.get("market_link", build_market_link(market_id))
                        
                        if ai_message:
                            notification = (
                                f"🔃 <b>MARKET CLOSING SOON</b>\n\n"
                                f"📌 <b>{escape_text(title)}</b>\n\n"
                                f"⏳ Time Remaining: <b>{mins_left} Minutes</b>\n\n"
                                f"{ai_message}"
                            )
                        else:
                            notification = (
                                f"🔃 <b>MARKET CLOSING SOON</b>\n\n"
                                f"📌 <b>{escape_text(title)}</b>\n\n"
                                f"⏳ Time Remaining: <b>{mins_left} Minutes</b>\n\n"
                                f"This is your last chance to stake!"
                            )
                        
                        keyboard = create_market_keyboard(market_id, market_link)
                        broadcast_to_all(
                            notification,
                            market_id,
                            keyboard,
                            theme=raw_theme,
                            notification_key=f"1h_{market_id}",
                        )
                        update_market_flag(market_id, "notified_1h")
                        logger.info(f"1 hour reminder sent for: {title}")

                    elif minutes_until <= 10.0 and not market_data.get("notified_5m"):
                        
                        # try ai message
                        ai_message = generate_smart_notification(title, market_data.get("theme", "other"), "10m")
                        market_link = market_data.get("market_link", build_market_link(market_id))
                        
                        if ai_message:
                            notification = (
                                f"🚨 <b>URGENT: MARKET CLOSING IN 10 MINUTES</b>\n\n"
                                f"📌 <b>{escape_text(title)}</b>\n\n"
                                f"{ai_message}"
                            )
                        else:
                            notification = (
                                f"🚨 <b>URGENT: MARKET CLOSING IN 10 MINUTES</b>\n\n"
                                f"📌 <b>{escape_text(title)}</b>\n\n"
                                f"⏳ Time Remaining: <b>10 Minutes</b>\n\n"
                                f"Act Now Or Lose This Opportunity!"
                            )
                        
                        keyboard = create_market_keyboard(market_id, market_link)
                        broadcast_to_all(
                            notification,
                            market_id,
                            keyboard,
                            theme=raw_theme,
                            notification_key=f"10m_{market_id}",
                        )
                        update_market_flag(market_id, "notified_5m")
                        logger.info(f"10 minute reminder sent for: {title}")

                else:
                    notification = (
                        f"⛔ <b>MARKET CLOSED</b>\n\n"
                        f"📌 <b>{escape_text(title)}</b>\n\n"
                        f"💰 Reward Distribution In Progress\n"
                        f"Check Your Wallet For Returns!\n\n"
                        f"🗑️ This message will be deleted in 10 minutes"
                    )
                    broadcast_to_all(
                        notification,
                        market_id,
                        theme=raw_theme,
                        notification_key=f"closed_{market_id}",
                    )
                    update_market_flag(market_id, "notified_ended")
                    logger.info(f"ended notification sent for: {title}")
                    schedule_message_deletion(market_id, title)

            except Exception as e:
                logger.error(f"error checking notification for market {market_id}: {e}")

    except Exception as e:
        logger.error(f"error in check_scheduled_notifications: {e}")


def get_ending_soon_markets():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    ending_soon = []
    markets = get_all_announced_markets()

    for market_data in markets:
        if market_data.get("notified_ended"):
            continue
        try:
            end_time = datetime.fromisoformat(market_data["end_time"])
            time_until = (end_time - now).total_seconds()
            if 0 < time_until <= 3600:
                ending_soon.append({
                    "title": market_data["title"],
                    "time_until": time_until,
                    "end_time": end_time
                })
        except:
            pass

    ending_soon.sort(key=lambda x: x["time_until"])
    return ending_soon


def refresh_market(call):
    try:
        logger.info(f"refresh button clicked: {call.data}")
        market_id = call.data.replace('refresh_', '')
        market_data = get_announced_market(market_id)
        
        if not market_data:
            bot.answer_callback_query(call.id, "market not found", show_alert=True)
            logger.warning(f"market {market_id} not found in database")
            return
        
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        end_time = datetime.fromisoformat(market_data["end_time"])
        time_until = (end_time - now).total_seconds()
        
        if time_until > 0:
            mins_left = int(time_until / 60)
            secs_left = int(time_until % 60)
            
            title = market_data.get("title", "")
            theme = market_data.get("theme", "")
            
            updated_msg = (
                f"📌 {title}\n\n"
                f"🏷️ Theme: {theme}\n"
                f"⏳ Time Remaining: {mins_left}m {secs_left}s"
            )
            
            market_link = market_data.get("market_link", build_market_link(market_id))
            keyboard = create_market_keyboard(market_id, market_link)
            
            bot.edit_message_text(
                updated_msg,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboard
            )
            bot.answer_callback_query(call.id, "⏳ updated")
            logger.info(f"refreshed market {market_id}: {mins_left}m {secs_left}s remaining")
        else:
            bot.answer_callback_query(call.id, "market has ended", show_alert=True)
            logger.info(f"refresh clicked for ended market {market_id}")
    
    except Exception as e:
        logger.error(f"error in refresh_market: {type(e).__name__} - {str(e)}")
        bot.answer_callback_query(call.id, f"error: {str(e)}", show_alert=True)


@bot.callback_query_handler(func=lambda call: True)
def handle_dashboard_callback(call):
    answered = False

    def answer(text=None, show_alert=False):
        nonlocal answered
        if answered:
            return
        try:
            bot.answer_callback_query(call.id, text, show_alert=show_alert)
            answered = True
        except Exception as e:
            logger.error(f"error answering callback {call.data}: {e}")

    try:
        data = call.data or ""
        logger.info(f"callback received: {data}")
        user_id = call.from_user.id

        if data.startswith("refresh_"):
            refresh_market(call)
            return

        if data.startswith("admin_") or data.startswith("tone_"):
            if not is_admin(user_id):
                answer("admin only", show_alert=True)
                return

        answer()

        if data == "admin_menu":
            delete_callback_message(call)
            send_temp_message(
                call.message.chat.id,
                get_stats_text(),
                reply_markup=build_admin_keyboard(),
                parse_mode="HTML"
            )
        elif data == "admin_pause":
            set_pause_state(True)
            delete_callback_message(call)
            send_temp_message(call.message.chat.id, "⏸️ Notifications paused.", parse_mode="HTML")
        elif data == "admin_resume":
            set_pause_state(False)
            delete_callback_message(call)
            send_temp_message(call.message.chat.id, "▶️ Notifications resumed.", parse_mode="HTML")
        elif data == "admin_clean":
            delete_callback_message(call)
            deleted_count, failed_count = delete_all_tracked_messages()
            send_temp_message(
                call.message.chat.id,
                f"✅ <b>Message Cleanup Complete</b>\n\n🗑️ Deleted {deleted_count}\n❌ Failed {failed_count}",
                parse_mode="HTML"
            )
        elif data == "admin_test":
            delete_callback_message(call)
            test_text = "🧪 <b>Test Notification</b>\n\nBot is online and ready."
            if ai_client:
                test_text += "\n✅ AI Engine: Active"
            else:
                test_text += "\n⚠️ AI Engine: Not Configured"
            send_temp_message(call.message.chat.id, test_text, parse_mode="HTML")
        elif data == "admin_stats":
            delete_callback_message(call)
            send_temp_message(call.message.chat.id, get_stats_text(), reply_markup=build_admin_keyboard(), parse_mode="HTML")
        elif data == "admin_tone":
            delete_callback_message(call)
            send_temp_message(
                call.message.chat.id,
                f"🎛 <b>AI Tone</b>\n\nCurrent tone: <b>{get_ai_tone().title()}</b>",
                reply_markup=build_tone_keyboard(),
                parse_mode="HTML"
            )
        elif data.startswith("tone_"):
            tone = data.replace("tone_", "")
            set_ai_tone(tone)
            delete_callback_message(call)
            send_temp_message(
                call.message.chat.id,
                f"🎛 <b>AI Tone</b>\n\nCurrent tone: <b>{tone.title()}</b>",
                reply_markup=build_tone_keyboard(),
                parse_mode="HTML"
            )
        elif data.startswith("theme_"):
            chat_id = call.message.chat.id
            theme = data.replace("theme_", "")
            current = get_chat_themes(chat_id)

            if theme == "all":
                updated = ["all"]
            else:
                updated = [item for item in current if item != "all"]
                if theme in updated:
                    updated.remove(theme)
                else:
                    updated.append(theme)
                if not updated:
                    updated = ["all"]

            set_chat_themes(chat_id, updated)
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=build_theme_keyboard(updated))
        else:
            send_temp_message(call.message.chat.id, f"Unknown button action: {escape_text(data)}", parse_mode="HTML")

    except Exception as e:
        logger.error(f"error handling callback {call.data}: {e}")
        answer("something went wrong", show_alert=True)


@bot.message_handler(commands=['getmyid'])
def get_my_id(message):
    try:
        user_id = message.from_user.id
        username = message.from_user.username or "No Username"
        reply = f"📱 Your Telegram ID: {user_id}\n👤 Your Username: @{username}"
        reply_temp(message, reply)
    except Exception as e:
        logger.error(f"error in getmyid: {e}")


@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        chat_id = str(message.chat.id)
        chat_name = message.chat.title if message.chat.type != 'private' else f"user_{message.from_user.username or message.from_user.id}"
        add_chat(chat_id, chat_name)
        save_user(message)

        welcome_msg = (
            "🚀 Welcome To B4 Market Alerts\n\n"
            "I Monitor B4 Markets In Real-Time\n\n"
            "📢 You Will Receive Notifications For:\n\n"
            "🎯 New Markets Launching\n"
            "⏰ 1 Hour Before Market Closes\n"
            "⏲️ 10 Minutes Before Market Closes\n"
            "💰 Market Closure & Reward Distribution\n\n"
            "✅ You Are Now Subscribed\n\n"
            "Sit Back And Receive Alerts!"
        )
        reply_temp(
            message,
            welcome_msg,
            reply_markup=build_main_menu_keyboard(message.from_user.id, message.chat.type)
        )
    except Exception as e:
        logger.error(f"error in start: {e}")


@bot.message_handler(commands=['menu'])
def show_main_menu(message):
    try:
        save_user(message)
        add_chat(
            str(message.chat.id),
            message.chat.title if message.chat.type != 'private' else f"user_{message.from_user.username or message.from_user.id}"
        )
        reply_temp(
            message,
            "📋 Menu opened. Choose an option below.",
            reply_markup=build_main_menu_keyboard(message.from_user.id, message.chat.type)
        )
    except Exception as e:
        logger.error(f"error in menu: {e}")


@bot.message_handler(commands=['help'])
def send_help(message):
    try:
        save_user(message)
        help_text = (
            "📖 B4 Market Alert Bot\n\n"
            "⚙️ Available Commands:\n\n"
            "/start - Subscribe To Market Alerts\n"
            "/menu - Open Button Menu\n"
            "/help - Show This Message\n"
            "/status - Check Bot Status\n"
            "/liveending - Show Markets Ending Soon\n"
            "/preferences - Choose Market Categories\n"
            "/getmyid - Get Your Telegram ID\n\n"
            "❓ What I Do:\n\n"
            "I Continuously Monitor B4 Markets And Send "
            "Real-Time Notifications At Critical Moments.\n\n"
            "Messages Are Auto-Deleted 10 Minutes After Market Closes.\n\n"
            "Never Miss A Market Opportunity!"
        )
        reply_temp(message, help_text)
    except Exception as e:
        logger.error(f"error in help: {e}")


@bot.message_handler(commands=['preferences'])
def preferences(message):
    try:
        save_user(message)
        add_chat(
            str(message.chat.id),
            message.chat.title if message.chat.type != 'private' else f"user_{message.from_user.username or message.from_user.id}"
        )
        selected = get_chat_themes(message.chat.id)
        reply_temp(
            message,
            "🏷️ <b>Market Preferences</b>\n\nChoose the market categories this chat should receive.",
            reply_markup=build_theme_keyboard(selected),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"error in preferences: {e}")


@bot.message_handler(commands=['admin'])
def admin_dashboard(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return

        reply_temp(message, get_stats_text(), reply_markup=build_admin_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in admin dashboard: {e}")


@bot.message_handler(commands=['tone'])
def tone_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return

        reply_temp(
            message,
            f"🎛 <b>AI Tone</b>\n\nCurrent tone: <b>{get_ai_tone().title()}</b>",
            reply_markup=build_tone_keyboard(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"error in tone command: {e}")


@bot.message_handler(commands=['summary'])
def summary_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return

        reply_temp(message, build_daily_summary_text(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in summary command: {e}")


@bot.message_handler(func=lambda message: message.text in ["📊 Status", "⏰ Ending Soon", "🏷 Preferences", "ℹ️ Help", "🆔 My ID", "🛠 Admin"])
def handle_menu_button(message):
    try:
        if message.text == "📊 Status":
            market_status(message)
        elif message.text == "⏰ Ending Soon":
            live_ending(message)
        elif message.text == "🏷 Preferences":
            preferences(message)
        elif message.text == "ℹ️ Help":
            send_help(message)
        elif message.text == "🆔 My ID":
            get_my_id(message)
        elif message.text == "🛠 Admin":
            admin_dashboard(message)
    except Exception as e:
        logger.error(f"error handling menu button: {e}")


@bot.message_handler(commands=['status'])
def market_status(message):
    try:
        save_user(message)
        all_markets = get_all_announced_markets()
        total_markets = len(all_markets)
        total_chats = len(get_all_chats())
        active = sum(1 for m in all_markets if not m.get("notified_ended"))
        ending_soon = len(get_ending_soon_markets())

        status_msg = (
            f"📊 Bot Status\n\n"
            f"🔍 Total Markets Tracked: {total_markets}\n"
            f"🟢 Currently Active: {active}\n"
            f"⏰ Ending Within 1 Hour: {ending_soon}\n"
            f"👥 Subscribed Users/Groups: {total_chats}\n\n"
            f"✅ Status: Running & Monitoring"
        )
        reply_temp(message, status_msg)
    except Exception as e:
        logger.error(f"error in status: {e}")


@bot.message_handler(commands=['liveending'])
def live_ending(message):
    try:
        save_user(message)
        ending_soon = get_ending_soon_markets()

        if not ending_soon:
            reply_temp(message, "⏰ No Markets Ending Within The Next Hour")
            return

        msg = "⏰ Markets Ending Soon\n\n"
        for market in ending_soon:
            mins = int(market["time_until"] / 60)
            msg += f"📌 {market['title']}\n⏳ Time Left: {mins} Minutes\n\n"

        reply_temp(message, msg)
    except Exception as e:
        logger.error(f"error in liveending: {e}")


@bot.message_handler(commands=['users'])
def show_users(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return
        total_users = len(get_all_users())
        reply_temp(message, f"📊 User Statistics\n\n👥 Total Users: {total_users}")
    except Exception as e:
        logger.error(f"error in users: {e}")


@bot.message_handler(commands=['listusers'])
def list_users(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return

        users = get_all_users()
        if not users:
            reply_temp(message, "No Users Yet")
            return

        users_list = "📋 Registered Users\n\n"
        for user in users:
            username = user.get("username", "No Username")
            first_name = user.get("first_name", "No Name")
            join_date = user.get("join_date", "Unknown")
            users_list += f"ID: {user['user_id']}\nName: {first_name}\nUsername: @{username}\nJoined: {join_date}\n\n"

        reply_temp(message, users_list)
    except Exception as e:
        logger.error(f"error in listusers: {e}")


@bot.message_handler(commands=['stats'])
def show_stats(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return

        reply_temp(message, get_stats_text(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in stats: {e}")


@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return

        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            reply_temp(message, "Format: /broadcast Your Message Here")
            return

        broadcast_msg = escape_text(args[1])
        broadcast_to_all(broadcast_msg)
        reply_temp(message, f"📢 Message Sent To {len(get_all_chats())} Chats")
    except Exception as e:
        logger.error(f"error in broadcast: {e}")


@bot.message_handler(commands=['premium_add'])
def premium_add_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return

        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            reply_temp(message, "Format: /premium_add telegram_chat_or_user_id")
            return

        chat_id = args[1].strip()
        if add_premium_chat(chat_id, message.from_user.id):
            reply_temp(message, f"⭐ Premium enabled for <code>{escape_text(chat_id)}</code>", parse_mode="HTML")
        else:
            reply_temp(message, "❌ Could not add premium chat.")
    except Exception as e:
        logger.error(f"error in premium_add: {e}")


@bot.message_handler(commands=['premium_remove'])
def premium_remove_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return

        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            reply_temp(message, "Format: /premium_remove telegram_chat_or_user_id")
            return

        chat_id = args[1].strip()
        removed = remove_premium_chat(chat_id)
        reply_temp(
            message,
            f"⭐ Premium removed for <code>{escape_text(chat_id)}</code>" if removed else "That chat was not premium.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"error in premium_remove: {e}")


@bot.message_handler(commands=['premium_users'])
def premium_users_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return

        premium_ids = get_premium_chat_ids()
        if not premium_ids:
            reply_temp(message, "No premium chats yet.")
            return

        lines = ["⭐ <b>Premium Chats</b>"]
        lines.extend(f"<code>{escape_text(chat_id)}</code>" for chat_id in premium_ids[:50])
        reply_temp(message, "\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in premium_users: {e}")


@bot.message_handler(commands=['premium_digest'])
def premium_digest_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return
        reply_temp(message, build_premium_digest_text(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in premium_digest: {e}")


@bot.message_handler(commands=['health'])
def health_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return
        send_temp_rich(message.chat.id, build_rich_health(), get_health_text())
        try_delete_user_message(message)
    except Exception as e:
        logger.error(f"error in health: {e}")


@bot.message_handler(commands=['recent'])
def recent_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return
        send_temp_rich(message.chat.id, build_rich_recent(), build_recent_markets_text())
        try_delete_user_message(message)
    except Exception as e:
        logger.error(f"error in recent: {e}")


@bot.message_handler(commands=['preview'])
def preview_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return

        markets = [market for market in fetch_b4_markets() if is_valid_market(market) and is_market_active(market)]
        if not markets:
            reply_temp(message, "No active API markets available to preview.")
            return

        market = markets[0]
        market_id = str(market.get("market_id", "")).strip()
        raw_theme = normalize_theme(market.get("theme", "other"))
        if is_scheduled_market(market):
            preview_text = build_scheduled_market_notification(market)
            rich_preview = build_rich_scheduled_market(market)
        else:
            ai_message = generate_smart_notification(str(market.get("title", "")).strip(), raw_theme, "new")
            preview_text = build_new_market_notification(market, ai_message)
            rich_preview = (
                f"<h2>Preview: New Market Live</h2>"
                f"{build_rich_media_block(get_market_cover_image(market), str(market.get('title', '')).strip())}"
                f"<blockquote>{preview_text}</blockquote>"
            )

        cover_image_url = get_market_cover_image(market)
        send_notification_to_chat(
            message.chat.id,
            f"👀 <b>Preview Only</b>\n\n{preview_text}",
            market_id=None,
            keyboard=create_market_keyboard(market_id, build_market_link(market_id)),
            photo_url=cover_image_url,
            rich_html=rich_preview,
        )
        try_delete_user_message(message)
    except Exception as e:
        logger.error(f"error in preview: {e}")
        reply_temp(message, f"❌ Error: {e}")


@bot.message_handler(commands=['reset'])
def reset_notifications(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM market_messages")
                messages_to_delete = cur.fetchall()
        
        deleted_count = 0
        failed_count = 0
        
        for msg in messages_to_delete:
            try:
                chat_id = int(msg[2])
                message_id = int(msg[3])
                bot.delete_message(chat_id, message_id)
                deleted_count += 1
                time.sleep(0.05)
            except Exception as e:
                logger.error(f"error deleting message {msg[3]} from chat {msg[2]}: {e}")
                failed_count += 1
        
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM announced_markets")
                cur.execute("DELETE FROM market_messages")
        
        reply_temp(message, f"✅ Reset Complete\n\n🗑️ Deleted {deleted_count} messages\n❌ Failed: {failed_count}\n\nBot will start fresh.")
        logger.info(f"notifications reset by admin - deleted {deleted_count} messages, {failed_count} failed")
    except Exception as e:
        logger.error(f"error in reset: {e}")
        reply_temp(message, f"❌ Error: {e}")


@bot.message_handler(commands=['cleanmessages'])
def clean_messages(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return

        deleted_count, failed_count = delete_all_tracked_messages()
        reply_temp(
            message,
            f"✅ Message Cleanup Complete\n\n"
            f"🗑️ Deleted {deleted_count} tracked messages\n"
            f"❌ Failed: {failed_count}\n\n"
            f"Market history was preserved, so active markets will not be announced as new again."
        )
        logger.info(f"tracked messages cleaned by admin - deleted {deleted_count}, failed {failed_count}")
    except Exception as e:
        logger.error(f"error in cleanmessages: {e}")
        reply_temp(message, f"❌ Error: {e}")


@bot.message_handler(commands=['refreshlinks'])
def refresh_links(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return

        refreshed, failed = refresh_market_message_buttons()
        reply_temp(
            message,
            f"✅ Link Refresh Complete\n\n"
            f"🔗 Updated buttons: {refreshed}\n"
            f"❌ Failed: {failed}\n\n"
            f"Current base: {MARKET_LINK_BASE}"
        )
    except Exception as e:
        logger.error(f"error in refreshlinks: {e}")
        reply_temp(message, f"❌ Error: {e}")


@bot.message_handler(commands=['pause'])
def pause_notifications(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return
        
        set_pause_state(True)
        reply_temp(message, "⏸️ Notifications PAUSED\n\nNo more market alerts will be sent until you /resume")
        logger.info("notifications paused by admin")
    except Exception as e:
        logger.error(f"error in pause: {e}")


@bot.message_handler(commands=['resume'])
def resume_notifications(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return
        
        set_pause_state(False)
        reply_temp(message, "▶️ Notifications RESUMED\n\nMarket alerts are now active again")
        logger.info("notifications resumed by admin")
    except Exception as e:
        logger.error(f"error in resume: {e}")


@bot.message_handler(commands=['test'])
def test_notification(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return
        
        test_msg = (
            "🧪 TEST NOTIFICATION\n\n"
            "If you see this, the bot is working correctly.\n\n"
            "✅ Bot Status: Operational\n"
            "✅ Notifications: Ready\n\n"
            "Safe to /resume notifications to all users"
        )
        
        if ai_client:
            test_msg += "\n✅ AI Engine: Active"
        else:
            test_msg += "\n⚠️ AI Engine: Not Configured"
        
        send_temp_message(message.chat.id, test_msg)
        logger.info("test notification sent to admin")
    except Exception as e:
        logger.error(f"error in test: {e}")
        reply_temp(message, f"❌ Error: {e}")


@bot.message_handler(commands=['reinvite'])
def reinvite_users(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return
        
        users = get_all_users()
        chats = get_all_chats()
        chat_ids = set(chats)
        
        reinvited = 0
        failed = 0
        
        for user in users:
            user_id = int(user["user_id"])
            if str(user_id) not in chat_ids:
                try:
                    invite_msg = (
                        "👋 we noticed you unsubscribed from b4 market alerts.\n\n"
                        "we've fixed some issues and improved the bot. "
                        "interested in getting market notifications again?\n\n"
                        "just use /start to resubscribe."
                    )
                    bot.send_message(user_id, invite_msg)
                    reinvited += 1
                    time.sleep(0.1)
                except Exception as e:
                    logger.error(f"error sending reinvite to {user_id}: {e}")
                    failed += 1
        
        reply_temp(message, f"✅ Reinvite sent\n\n📨 Sent to {reinvited} users\n❌ Failed: {failed}")
        logger.info(f"reinvited {reinvited} users, {failed} failed")
    except Exception as e:
        logger.error(f"error in reinvite: {e}")
        reply_temp(message, f"❌ Error: {e}")


logger.info("Starting Bot...")
init_db()

try:
    public_commands = [
        telebot.types.BotCommand("start", "Subscribe to market alerts"),
        telebot.types.BotCommand("menu", "Open button menu"),
        telebot.types.BotCommand("help", "Show available commands"),
        telebot.types.BotCommand("status", "Check bot status"),
        telebot.types.BotCommand("liveending", "Show markets ending soon"),
        telebot.types.BotCommand("preferences", "Choose market categories"),
        telebot.types.BotCommand("getmyid", "Get your telegram id"),
    ]
    
    bot.set_my_commands(public_commands)
    bot.set_my_commands(public_commands, scope=telebot.types.BotCommandScopeAllPrivateChats())
    bot.set_my_commands(public_commands, scope=telebot.types.BotCommandScopeAllGroupChats())
    
    admin_commands = public_commands + [
        telebot.types.BotCommand("admin", "Open admin dashboard"),
        telebot.types.BotCommand("pause", "Pause all notifications"),
        telebot.types.BotCommand("resume", "Resume notifications"),
        telebot.types.BotCommand("test", "Send test notification"),
        telebot.types.BotCommand("tone", "Change AI message tone"),
        telebot.types.BotCommand("summary", "Preview daily summary"),
        telebot.types.BotCommand("preview", "Preview latest market alert"),
        telebot.types.BotCommand("health", "Show bot health"),
        telebot.types.BotCommand("recent", "Show recent announced markets"),
        telebot.types.BotCommand("premium_add", "Add premium user or chat"),
        telebot.types.BotCommand("premium_remove", "Remove premium user or chat"),
        telebot.types.BotCommand("premium_users", "List premium users or chats"),
        telebot.types.BotCommand("premium_digest", "Preview premium digest"),
        telebot.types.BotCommand("reset", "Reset all data"),
        telebot.types.BotCommand("cleanmessages", "Delete tracked messages only"),
        telebot.types.BotCommand("refreshlinks", "Refresh market button links"),
        telebot.types.BotCommand("broadcast", "Broadcast message"),
        telebot.types.BotCommand("stats", "Show bot statistics"),
        telebot.types.BotCommand("users", "Show user count"),
        telebot.types.BotCommand("listusers", "List all users"),
    ]
    
    if ADMIN_ID and ADMIN_ID != 0:
        scope = telebot.types.BotCommandScopeChat(chat_id=ADMIN_ID)
        bot.set_my_commands(admin_commands, scope=scope)
    
    logger.info("bot commands registered")
except Exception as e:
    logger.error(f"error registering commands: {e}")

monitor_thread = Thread(target=monitor_b4_markets, daemon=True)
monitor_thread.start()

from flask import Flask
from threading import Thread as FlaskThread

app = Flask(__name__)


@app.route('/')
def hello():
    return 'bot is running'


def run_flask():
    try:
        port = int(os.getenv("PORT", "5000"))
        app.run(host='0.0.0.0', port=port)
    except Exception as e:
        logger.error(f"error running flask: {e}")


logger.info("Starting Flask Server...")
flask_thread = FlaskThread(target=run_flask, daemon=True)
flask_thread.start()

logger.info("Bot Is Ready")
try:
    bot.remove_webhook()
    logger.info("webhook removed before polling")

    bot_info = bot.get_me()
    logger.info(f"polling as @{bot_info.username} ({bot_info.id})")

    bot.infinity_polling(
        timeout=20,
        long_polling_timeout=20,
        allowed_updates=[
            "message",
            "edited_message",
            "callback_query",
            "my_chat_member",
            "chat_member",
        ],
    )
except Exception as e:
    logger.error(f"critical error in bot: {e}")
    time.sleep(10)


