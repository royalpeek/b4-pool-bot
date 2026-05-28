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

B4_API_URL = "https://b4app.xyz/api/markets"
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
                        detected_at TEXT
                    )
                """)
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
                    UPDATE announced_markets
                    SET market_link = REPLACE(market_link, '/market/', '/m/')
                    WHERE market_link LIKE '%/market/%'
                """)
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


def save_announced_market(market_id, title, theme, end_time):
    try:
        market_link = f"https://www.b4app.xyz/m/{market_id}"
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO announced_markets (market_id, title, theme, end_time, market_link, notified_new, notified_1h, notified_5m, notified_ended, delete_scheduled, detected_at)
                    VALUES (%s, %s, %s, %s, %s, TRUE, FALSE, FALSE, FALSE, FALSE, %s)
                    ON CONFLICT (market_id) DO NOTHING
                """, (str(market_id), title, theme, end_time, market_link, now_utc().isoformat()))
    except Exception as e:
        logger.error(f"error saving market {market_id}: {e}")


def update_market_flag(market_id, flag):
    allowed_flags = {"notified_new", "notified_1h", "notified_5m", "notified_ended", "delete_scheduled"}
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


def broadcast_to_all(message_text, market_id=None, keyboard=None, theme=None, notification_key=None):
    try:
        if notification_key and not can_send_notification(notification_key):
            return

        chats = get_all_chats()
        sent = 0
        for chat_id in chats:
            try:
                if theme and not chat_wants_theme(chat_id, theme):
                    continue

                if keyboard:
                    sent_msg = bot.send_message(int(chat_id), message_text, reply_markup=keyboard, parse_mode="HTML")
                else:
                    sent_msg = bot.send_message(int(chat_id), message_text, parse_mode="HTML")
                sent += 1
                if market_id:
                    save_message_id(market_id, chat_id, sent_msg.message_id)
                time.sleep(SEND_DELAY_SECONDS)
            except Exception as e:
                logger.error(f"error sending to {chat_id}: {e}")

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
        response = requests.get(B4_API_URL, timeout=15)
        response.raise_for_status()
        data = response.json()

        if isinstance(data, dict) and "markets" in data:
            logger.info(f"fetched {len(data['markets'])} markets from api")
            return data["markets"]
        elif isinstance(data, list):
            logger.info(f"fetched {len(data)} markets from api")
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


def get_stats_text():
    all_markets = get_all_announced_markets()
    total_users = len(get_all_users())
    total_markets = len(all_markets)
    total_chats = len(get_all_chats())
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
    )
    set_bot_state("last_daily_summary_date", today_key)
    logger.info(f"daily summary sent for {today_key}")


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
                        title = str(market.get("title", "")).strip()
                        raw_theme = normalize_theme(market.get("theme", "other"))
                        theme = format_theme(raw_theme)
                        end_time_unix = market.get("end_time")
                        end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None)
                        end_time_str = end_time.strftime('%b %d, %Y at %I:%M %p UTC')

                        # try to generate ai notification
                        ai_message = generate_smart_notification(title, raw_theme, "new")
                        market_link = f"https://www.b4app.xyz/m/{market_id}"
                        
                        if ai_message:
                            notification = (
                                f"🆕 <b>NEW MARKET LIVE</b>\n\n"
                                f"📌 <b>{escape_text(title)}</b>\n\n"
                                f"🏷️ Theme: {escape_text(theme)}\n"
                                f"⏰ Closes: {escape_text(end_time_str)}\n\n"
                                f"{ai_message}"
                            )
                        else:
                            notification = (
                                f"🆕 <b>NEW MARKET LIVE</b>\n\n"
                                f"📌 <b>{escape_text(title)}</b>\n\n"
                                f"🏷️ Theme: {escape_text(theme)}\n"
                                f"⏰ Closes: {escape_text(end_time_str)}\n\n"
                                f"Place your stake now!"
                            )

                        keyboard = create_market_keyboard(market_id, market_link)
                        broadcast_to_all(
                            notification,
                            market_id,
                            keyboard,
                            theme=raw_theme,
                            notification_key=f"new_{market_id}",
                        )
                        save_announced_market(market_id, title, raw_theme, end_time.isoformat())
                        logger.info(f"new market announced: {title}")

                except Exception as e:
                    logger.error(f"error processing market: {e}")

            check_scheduled_notifications()
            send_daily_summary_if_due()
            time.sleep(30)

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
                        market_link = market_data.get("market_link", f"https://www.b4app.xyz/m/{market_id}")
                        
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
                        market_link = market_data.get("market_link", f"https://www.b4app.xyz/m/{market_id}")
                        
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


@bot.callback_query_handler(func=lambda call: call.data.startswith('refresh_'))
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
            
            market_link = market_data.get("market_link", f"https://www.b4app.xyz/m/{market_id}")
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


@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_') or call.data.startswith('tone_') or call.data.startswith('theme_'))
def handle_dashboard_callback(call):
    try:
        data = call.data
        user_id = call.from_user.id

        if data.startswith("admin_") or data.startswith("tone_"):
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "admin only", show_alert=True)
                return

        if data == "admin_menu":
            bot.answer_callback_query(call.id)
            bot.edit_message_text(
                get_stats_text(),
                call.message.chat.id,
                call.message.message_id,
                reply_markup=build_admin_keyboard(),
                parse_mode="HTML"
            )
        elif data == "admin_pause":
            set_pause_state(True)
            bot.answer_callback_query(call.id, "notifications paused")
            bot.edit_message_text(get_stats_text(), call.message.chat.id, call.message.message_id, reply_markup=build_admin_keyboard(), parse_mode="HTML")
        elif data == "admin_resume":
            set_pause_state(False)
            bot.answer_callback_query(call.id, "notifications resumed")
            bot.edit_message_text(get_stats_text(), call.message.chat.id, call.message.message_id, reply_markup=build_admin_keyboard(), parse_mode="HTML")
        elif data == "admin_clean":
            deleted_count, failed_count = delete_all_tracked_messages()
            bot.answer_callback_query(call.id, "cleanup complete")
            bot.send_message(
                call.message.chat.id,
                f"✅ <b>Message Cleanup Complete</b>\n\n🗑️ Deleted {deleted_count}\n❌ Failed {failed_count}",
                parse_mode="HTML"
            )
        elif data == "admin_test":
            bot.answer_callback_query(call.id, "test sent")
            test_text = "🧪 <b>Test Notification</b>\n\nBot is online and ready."
            if ai_client:
                test_text += "\n✅ AI Engine: Active"
            else:
                test_text += "\n⚠️ AI Engine: Not Configured"
            bot.send_message(call.message.chat.id, test_text, parse_mode="HTML")
        elif data == "admin_stats":
            bot.answer_callback_query(call.id)
            bot.edit_message_text(get_stats_text(), call.message.chat.id, call.message.message_id, reply_markup=build_admin_keyboard(), parse_mode="HTML")
        elif data == "admin_tone":
            bot.answer_callback_query(call.id)
            bot.send_message(
                call.message.chat.id,
                f"🎛 <b>AI Tone</b>\n\nCurrent tone: <b>{get_ai_tone().title()}</b>",
                reply_markup=build_tone_keyboard(),
                parse_mode="HTML"
            )
        elif data.startswith("tone_"):
            tone = data.replace("tone_", "")
            set_ai_tone(tone)
            bot.answer_callback_query(call.id, f"tone set to {tone}")
            bot.edit_message_text(
                f"🎛 <b>AI Tone</b>\n\nCurrent tone: <b>{tone.title()}</b>",
                call.message.chat.id,
                call.message.message_id,
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
            bot.answer_callback_query(call.id, "preferences updated")
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=build_theme_keyboard(updated))

    except Exception as e:
        logger.error(f"error handling callback {call.data}: {e}")
        bot.answer_callback_query(call.id, "something went wrong", show_alert=True)


@bot.message_handler(commands=['getmyid'])
def get_my_id(message):
    try:
        user_id = message.from_user.id
        username = message.from_user.username or "No Username"
        reply = f"📱 Your Telegram ID: {user_id}\n👤 Your Username: @{username}"
        bot.reply_to(message, reply)
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
        bot.reply_to(message, welcome_msg)
    except Exception as e:
        logger.error(f"error in start: {e}")


@bot.message_handler(commands=['help'])
def send_help(message):
    try:
        save_user(message)
        help_text = (
            "📖 B4 Market Alert Bot\n\n"
            "⚙️ Available Commands:\n\n"
            "/start - Subscribe To Market Alerts\n"
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
        bot.reply_to(message, help_text)
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
        bot.reply_to(
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
            bot.reply_to(message, "❌ Permission Denied. Admin Only Command")
            return

        bot.reply_to(message, get_stats_text(), reply_markup=build_admin_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in admin dashboard: {e}")


@bot.message_handler(commands=['tone'])
def tone_command(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ Permission Denied. Admin Only Command")
            return

        bot.reply_to(
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
            bot.reply_to(message, "❌ Permission Denied. Admin Only Command")
            return

        bot.reply_to(message, build_daily_summary_text(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in summary command: {e}")


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
        bot.reply_to(message, status_msg)
    except Exception as e:
        logger.error(f"error in status: {e}")


@bot.message_handler(commands=['liveending'])
def live_ending(message):
    try:
        save_user(message)
        ending_soon = get_ending_soon_markets()

        if not ending_soon:
            bot.reply_to(message, "⏰ No Markets Ending Within The Next Hour")
            return

        msg = "⏰ Markets Ending Soon\n\n"
        for market in ending_soon:
            mins = int(market["time_until"] / 60)
            msg += f"📌 {market['title']}\n⏳ Time Left: {mins} Minutes\n\n"

        bot.reply_to(message, msg)
    except Exception as e:
        logger.error(f"error in liveending: {e}")


@bot.message_handler(commands=['users'])
def show_users(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ Permission Denied. Admin Only Command")
            return
        total_users = len(get_all_users())
        bot.reply_to(message, f"📊 User Statistics\n\n👥 Total Users: {total_users}")
    except Exception as e:
        logger.error(f"error in users: {e}")


@bot.message_handler(commands=['listusers'])
def list_users(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ Permission Denied. Admin Only Command")
            return

        users = get_all_users()
        if not users:
            bot.reply_to(message, "No Users Yet")
            return

        users_list = "📋 Registered Users\n\n"
        for user in users:
            username = user.get("username", "No Username")
            first_name = user.get("first_name", "No Name")
            join_date = user.get("join_date", "Unknown")
            users_list += f"ID: {user['user_id']}\nName: {first_name}\nUsername: @{username}\nJoined: {join_date}\n\n"

        bot.reply_to(message, users_list)
    except Exception as e:
        logger.error(f"error in listusers: {e}")


@bot.message_handler(commands=['stats'])
def show_stats(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ Permission Denied. Admin Only Command")
            return

        bot.reply_to(message, get_stats_text(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in stats: {e}")


@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ Permission Denied. Admin Only Command")
            return

        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            bot.reply_to(message, "Format: /broadcast Your Message Here")
            return

        broadcast_msg = escape_text(args[1])
        broadcast_to_all(broadcast_msg)
        bot.reply_to(message, f"📢 Message Sent To {len(get_all_chats())} Chats")
    except Exception as e:
        logger.error(f"error in broadcast: {e}")


@bot.message_handler(commands=['reset'])
def reset_notifications(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ Permission Denied. Admin Only Command")
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
        
        bot.reply_to(message, f"✅ Reset Complete\n\n🗑️ Deleted {deleted_count} messages\n❌ Failed: {failed_count}\n\nBot will start fresh.")
        logger.info(f"notifications reset by admin - deleted {deleted_count} messages, {failed_count} failed")
    except Exception as e:
        logger.error(f"error in reset: {e}")
        bot.reply_to(message, f"❌ Error: {e}")


@bot.message_handler(commands=['cleanmessages'])
def clean_messages(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ Permission Denied. Admin Only Command")
            return

        deleted_count, failed_count = delete_all_tracked_messages()
        bot.reply_to(
            message,
            f"✅ Message Cleanup Complete\n\n"
            f"🗑️ Deleted {deleted_count} tracked messages\n"
            f"❌ Failed: {failed_count}\n\n"
            f"Market history was preserved, so active markets will not be announced as new again."
        )
        logger.info(f"tracked messages cleaned by admin - deleted {deleted_count}, failed {failed_count}")
    except Exception as e:
        logger.error(f"error in cleanmessages: {e}")
        bot.reply_to(message, f"❌ Error: {e}")


@bot.message_handler(commands=['pause'])
def pause_notifications(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ Permission Denied. Admin Only Command")
            return
        
        set_pause_state(True)
        bot.reply_to(message, "⏸️ Notifications PAUSED\n\nNo more market alerts will be sent until you /resume")
        logger.info("notifications paused by admin")
    except Exception as e:
        logger.error(f"error in pause: {e}")


@bot.message_handler(commands=['resume'])
def resume_notifications(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ Permission Denied. Admin Only Command")
            return
        
        set_pause_state(False)
        bot.reply_to(message, "▶️ Notifications RESUMED\n\nMarket alerts are now active again")
        logger.info("notifications resumed by admin")
    except Exception as e:
        logger.error(f"error in resume: {e}")


@bot.message_handler(commands=['test'])
def test_notification(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ Permission Denied. Admin Only Command")
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
        
        bot.send_message(message.chat.id, test_msg)
        logger.info("test notification sent to admin")
    except Exception as e:
        logger.error(f"error in test: {e}")
        bot.reply_to(message, f"❌ Error: {e}")


@bot.message_handler(commands=['reinvite'])
def reinvite_users(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ Permission Denied. Admin Only Command")
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
        
        bot.reply_to(message, f"✅ Reinvite sent\n\n📨 Sent to {reinvited} users\n❌ Failed: {failed}")
        logger.info(f"reinvited {reinvited} users, {failed} failed")
    except Exception as e:
        logger.error(f"error in reinvite: {e}")
        bot.reply_to(message, f"❌ Error: {e}")


logger.info("Starting Bot...")
init_db()

try:
    public_commands = [
        telebot.types.BotCommand("start", "Subscribe to market alerts"),
        telebot.types.BotCommand("help", "Show available commands"),
        telebot.types.BotCommand("status", "Check bot status"),
        telebot.types.BotCommand("liveending", "Show markets ending soon"),
        telebot.types.BotCommand("preferences", "Choose market categories"),
        telebot.types.BotCommand("getmyid", "Get your telegram id"),
    ]
    
    bot.set_my_commands(public_commands)
    
    admin_commands = public_commands + [
        telebot.types.BotCommand("admin", "Open admin dashboard"),
        telebot.types.BotCommand("pause", "Pause all notifications"),
        telebot.types.BotCommand("resume", "Resume notifications"),
        telebot.types.BotCommand("test", "Send test notification"),
        telebot.types.BotCommand("tone", "Change AI message tone"),
        telebot.types.BotCommand("summary", "Preview daily summary"),
        telebot.types.BotCommand("reset", "Reset all data"),
        telebot.types.BotCommand("cleanmessages", "Delete tracked messages only"),
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
    bot.infinity_polling()
except Exception as e:
    logger.error(f"critical error in bot: {e}")
    time.sleep(10)
