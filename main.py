print("NEW DEPLOY VERSION - WITH FREEMODEL AI")
import telebot
from telebot import types
import json
import os
import time
import logging
import psycopg
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
bot = telebot.TeleBot(bot_token)

B4_API_URL = "https://b4app.xyz/api/markets"
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
DATABASE_URL = os.getenv("DATABASE_URL")

# Freemodel AI client
freemodel_api_key = os.getenv("FREEMODEL_API_KEY")
freemodel_base_url = os.getenv("FREEMODEL_BASE_URL")

ai_client = None
if freemodel_api_key and freemodel_base_url:
    ai_client = OpenAI(api_key=freemodel_api_key, base_url=freemodel_base_url)
    logger.info("freemodel ai client initialized")
else:
    logger.warning("freemodel not configured, using template notifications")


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
                        created_at TIMESTAMP DEFAULT NOW()
                    )
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
                
                cur.execute("DROP TABLE IF EXISTS announced_markets")

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
        logger.info("database tables ready")
    except Exception as e:
        logger.error(f"error initialising database: {e}")
        raise


def is_admin(user_id):
    if ADMIN_ID is None or ADMIN_ID == 0:
        return False
    return user_id == ADMIN_ID


def set_pause_state(paused):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM bot_state WHERE key = 'paused'")
                cur.execute("""
                    INSERT INTO bot_state (key, value)
                    VALUES ('paused', %s)
                """, (str(paused),))
    except Exception as e:
        logger.error(f"error setting pause state: {e}")


def get_pause_state():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM bot_state WHERE key = 'paused'")
                result = cur.fetchone()
                if result:
                    return result[0] == 'True'
        return False
    except Exception as e:
        logger.error(f"error getting pause state: {e}")
        return False


def generate_smart_notification(title, theme, notification_type="new"):
    """use groq ai to generate short, direct opinion market notifications"""
    if not ai_client:
        return None
    
    try:
        if notification_type == "new":
            prompt = f"""you are a b4 opinion market bot. generate a short 1-sentence call to action for a new opinion market.
opinion: "{title}"
be direct, brief, casual. no fluff. just get people to share their opinion. no corporate language. lowercase."""
        elif notification_type == "1h":
            prompt = f"""generate a short 1-sentence reminder for an opinion market closing in 1 hour.
opinion: "{title}"
be direct. no fluff. just tell them to hurry up and share. lowercase."""
        elif notification_type == "10m":
            prompt = f"""generate a short 1-sentence URGENT reminder for an opinion market closing in 10 minutes.
opinion: "{title}"
be super direct. this is the last call. no fluff. lowercase."""
        else:
            return None

        response = ai_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50
        )
        
        message = response.choices[0].message.content.strip()
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
        # FIX: correct market link format
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
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE announced_markets SET {flag} = TRUE WHERE market_id = %s", (str(market_id),))
    except Exception as e:
        logger.error(f"error updating flag {flag} for market {market_id}: {e}")


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


def broadcast_to_all(message_text, market_id=None, keyboard=None):
    try:
        chats = get_all_chats()
        sent = 0
        for chat_id in chats:
            try:
                if keyboard:
                    sent_msg = bot.send_message(int(chat_id), message_text, reply_markup=keyboard)
                else:
                    sent_msg = bot.send_message(int(chat_id), message_text)
                sent += 1
                if market_id:
                    save_message_id(market_id, chat_id, sent_msg.message_id)
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


def create_vote_only_keyboard(market_link):
    """vote now button only - used for new market notifications"""
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("vote now", url=market_link)
    )
    return keyboard


def create_market_keyboard(market_id, market_link):
    """vote now + refresh buttons - used for reminder notifications only"""
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("vote now", url=market_link),
        types.InlineKeyboardButton("refresh", callback_data=f"refresh_{market_id}")
    )
    return keyboard


def format_theme(theme):
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
                        theme = format_theme(market.get("theme", "other"))
                        end_time_unix = market.get("end_time")
                        end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None)
                        end_time_str = end_time.strftime('%b %d, %Y at %I:%M %p UTC')
                        # FIX: correct market link format
                        market_link = f"https://www.b4app.xyz/m/{market_id}"

                        # try to generate ai notification
                        ai_message = generate_smart_notification(title, market.get("theme", "other"), "new")
                        
                        if ai_message:
                            notification = (
                                f"🆕 NEW MARKET LIVE\n\n"
                                f"📌 {title}\n\n"
                                f"🏷️ Theme: {theme}\n"
                                f"⏰ Closes: {end_time_str}\n\n"
                                f"{ai_message}"
                            )
                        else:
                            notification = (
                                f"🆕 NEW MARKET LIVE\n\n"
                                f"📌 {title}\n\n"
                                f"🏷️ Theme: {theme}\n"
                                f"⏰ Closes: {end_time_str}\n\n"
                                f"Place your stake now!"
                            )

                        # FIX: new market gets vote only keyboard (no refresh button)
                        keyboard = create_vote_only_keyboard(market_link)
                        broadcast_to_all(notification, market_id, keyboard)
                        save_announced_market(market_id, title, theme, end_time.isoformat())
                        logger.info(f"new market announced: {title}")

                except Exception as e:
                    logger.error(f"error processing market: {e}")

            check_scheduled_notifications()
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

                if not end_time_str or not title:
                    continue

                end_time = datetime.fromisoformat(end_time_str)
                time_until = (end_time - now).total_seconds()

                logger.info(f"market: {title} | time_until: {time_until:.0f}s | notified_1h: {market_data.get('notified_1h')} | notified_5m: {market_data.get('notified_5m')}")

                # FIX: correct fallback url in all reminder notifications
                market_link = market_data.get("market_link") or f"https://www.b4app.xyz/m/{market_id}"

                if time_until > 0:
                    hours_until = time_until / 3600
                    minutes_until = time_until / 60

                    if hours_until <= 1.0 and not market_data.get("notified_1h"):
                        mins_left = int(minutes_until)
                        
                        ai_message = generate_smart_notification(title, market_data.get("theme", "other"), "1h")
                        
                        if ai_message:
                            notification = (
                                f"🔃 MARKET CLOSING SOON\n\n"
                                f"📌 {title}\n\n"
                                f"⏳ Time Remaining: {mins_left} Minutes\n\n"
                                f"{ai_message}"
                            )
                        else:
                            notification = (
                                f"🔃 MARKET CLOSING SOON\n\n"
                                f"📌 {title}\n\n"
                                f"⏳ Time Remaining: {mins_left} Minutes\n\n"
                                f"This is your last chance to stake!"
                            )
                        
                        # reminder gets both buttons
                        keyboard = create_market_keyboard(market_id, market_link)
                        broadcast_to_all(notification, market_id, keyboard)
                        update_market_flag(market_id, "notified_1h")
                        logger.info(f"1 hour reminder sent for: {title}")

                    elif minutes_until <= 10.0 and not market_data.get("notified_5m"):
                        
                        ai_message = generate_smart_notification(title, market_data.get("theme", "other"), "10m")
                        
                        if ai_message:
                            notification = (
                                f"🚨 URGENT: MARKET CLOSING IN 10 MINUTES\n\n"
                                f"📌 {title}\n\n"
                                f"{ai_message}"
                            )
                        else:
                            notification = (
                                f"🚨 URGENT: MARKET CLOSING IN 10 MINUTES\n\n"
                                f"📌 {title}\n\n"
                                f"⏳ Time Remaining: 10 Minutes\n\n"
                                f"Act Now Or Lose This Opportunity!"
                            )
                        
                        # reminder gets both buttons
                        keyboard = create_market_keyboard(market_id, market_link)
                        broadcast_to_all(notification, market_id, keyboard)
                        update_market_flag(market_id, "notified_5m")
                        logger.info(f"10 minute reminder sent for: {title}")

                else:
                    notification = (
                        f"⛔ MARKET CLOSED\n\n"
                        f"📌 {title}\n\n"
                        f"💰 Reward Distribution In Progress\n"
                        f"Check Your Wallet For Returns!\n\n"
                        f"🗑️ This message will be deleted in 10 minutes"
                    )
                    broadcast_to_all(notification, market_id)
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
        market_id = call.data.replace('refresh_', '')
        market_data = get_announced_market(market_id)
        
        if not market_data:
            bot.answer_callback_query(call.id, "market not found")
            return
        
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        end_time = datetime.fromisoformat(market_data["end_time"])
        time_until = (end_time - now).total_seconds()
        
        # FIX: correct fallback url
        market_link = market_data.get("market_link") or f"https://www.b4app.xyz/m/{market_id}"
        
        if time_until > 0:
            mins_left = int(time_until / 60)
            secs_left = int(time_until % 60)
            
            title = market_data.get("title", "")
            theme = market_data.get("theme", "")
            
            # FIX: rebuild the full reminder message so telegram accepts the edit
            updated_msg = (
                f"🔃 MARKET CLOSING SOON\n\n"
                f"📌 {title}\n\n"
                f"🏷️ Theme: {theme}\n"
                f"⏳ Time Remaining: {mins_left}m {secs_left}s"
            )
            
            keyboard = create_market_keyboard(market_id, market_link)
            
            try:
                bot.edit_message_text(
                    updated_msg,
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=keyboard
                )
                bot.answer_callback_query(call.id, "updated!")
            except Exception as edit_err:
                # if message text is identical telegram throws an error - that's fine
                if "message is not modified" in str(edit_err).lower():
                    bot.answer_callback_query(call.id, "already up to date")
                else:
                    logger.error(f"error editing message: {edit_err}")
                    bot.answer_callback_query(call.id, "error updating")
        else:
            bot.answer_callback_query(call.id, "market has already ended")
            logger.info(f"refresh clicked for ended market {market_id}")
    
    except Exception as e:
        logger.error(f"error in refresh_market: {e}")
        bot.answer_callback_query(call.id, "error updating market")


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

        all_markets = get_all_announced_markets()
        total_users = len(get_all_users())
        total_markets = len(all_markets)
        total_chats = len(get_all_chats())
        active = sum(1 for m in all_markets if not m.get("notified_ended"))

        stats_msg = (
            f"📊 Bot Statistics\n\n"
            f"👥 Total Users: {total_users}\n"
            f"🔍 Total Markets: {total_markets}\n"
            f"🟢 Active Markets: {active}\n"
            f"💬 Subscribed Chats: {total_chats}\n\n"
            f"✅ Status: Running"
        )
        bot.reply_to(message, stats_msg)
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

        broadcast_msg = args[1]
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
        telebot.types.BotCommand("getmyid", "Get your telegram id"),
    ]
    
    bot.set_my_commands(public_commands)
    
    admin_commands = public_commands + [
        telebot.types.BotCommand("pause", "Pause all notifications"),
        telebot.types.BotCommand("resume", "Resume notifications"),
        telebot.types.BotCommand("test", "Send test notification"),
        telebot.types.BotCommand("reset", "Reset all data"),
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
        app.run(host='0.0.0.0', port=5000)
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
