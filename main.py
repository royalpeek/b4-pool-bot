import telebot
import json
import os
import time
import logging
import psycopg2
import psycopg2.extras
from threading import Thread
from datetime import datetime
import requests

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


def get_db():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    return conn


def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()

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

        cur.execute("""
            CREATE TABLE IF NOT EXISTS announced_markets (
                market_id TEXT PRIMARY KEY,
                title TEXT,
                theme TEXT,
                end_time TEXT,
                notified_new BOOLEAN DEFAULT FALSE,
                notified_1h BOOLEAN DEFAULT FALSE,
                notified_5m BOOLEAN DEFAULT FALSE,
                notified_ended BOOLEAN DEFAULT FALSE,
                detected_at TEXT
            )
        """)

        conn.commit()
        cur.close()
        conn.close()
        logger.info("database tables ready")
    except Exception as e:
        logger.error(f"error initialising database: {e}")
        raise


def is_admin(user_id):
    if ADMIN_ID is None or ADMIN_ID == 0:
        return False
    return user_id == ADMIN_ID


def save_user(message):
    try:
        user_id = str(message.from_user.id)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (user_id, username, first_name, join_date, is_admin)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO NOTHING
        """, (
            user_id,
            message.from_user.username or "No Username",
            message.from_user.first_name or "No Name",
            datetime.now().isoformat(),
            is_admin(message.from_user.id)
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"error saving user: {e}")


def add_chat(chat_id, chat_name):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO subscribed_chats (chat_id, chat_name)
            VALUES (%s, %s)
            ON CONFLICT (chat_id) DO NOTHING
        """, (str(chat_id), chat_name))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"error adding chat: {e}")


def remove_chat(chat_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM subscribed_chats WHERE chat_id = %s", (str(chat_id),))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"error removing chat: {e}")


def get_all_chats():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT chat_id FROM subscribed_chats")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [row[0] for row in rows]
    except Exception as e:
        logger.error(f"error fetching chats: {e}")
        return []


def get_all_users():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM users")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"error fetching users: {e}")
        return []


def get_announced_market(market_id):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM announced_markets WHERE market_id = %s", (str(market_id),))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row
    except Exception as e:
        logger.error(f"error fetching market {market_id}: {e}")
        return None


def save_announced_market(market_id, title, theme, end_time):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO announced_markets (market_id, title, theme, end_time, notified_new, notified_1h, notified_5m, notified_ended, detected_at)
            VALUES (%s, %s, %s, %s, TRUE, FALSE, FALSE, FALSE, %s)
            ON CONFLICT (market_id) DO NOTHING
        """, (str(market_id), title, theme, end_time, datetime.now().isoformat()))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"error saving market {market_id}: {e}")


def update_market_flag(market_id, flag):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(f"UPDATE announced_markets SET {flag} = TRUE WHERE market_id = %s", (str(market_id),))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"error updating flag {flag} for market {market_id}: {e}")


def get_all_announced_markets():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM announced_markets")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"error fetching all markets: {e}")
        return []


def broadcast_to_all(message_text):
    try:
        chats = get_all_chats()
        failed = []
        sent = 0
        for chat_id in chats:
            try:
                bot.send_message(int(chat_id), message_text)
                sent += 1
            except Exception as e:
                logger.error(f"error sending to {chat_id}: {e}")
                failed.append(chat_id)

        for chat_id in failed:
            remove_chat(chat_id)

        logger.info(f"broadcast sent to {sent} chats, {len(failed)} failed")
    except Exception as e:
        logger.error(f"error in broadcast_to_all: {e}")


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
        logger.warning("skipped market: missing market_id")
        return False

    title = str(market.get("title", "")).strip()
    if not title:
        logger.warning(f"skipped market {market_id}: blank or missing title")
        return False

    end_time_unix = market.get("end_time")
    if not end_time_unix or not isinstance(end_time_unix, (int, float)) or int(end_time_unix) <= 0:
        logger.warning(f"skipped market {market_id}: invalid end_time ({end_time_unix})")
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
            end_time = datetime.fromtimestamp(int(end_time_unix))
            if datetime.now() > end_time:
                return False

        return True
    except Exception as e:
        logger.error(f"error checking market status: {e}")
        return False


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
            markets = fetch_b4_markets()
            logger.info(f"processing {len(markets)} markets")

            for market in markets:
                try:
                    market_id = str(market.get("market_id", "")).strip()

                    if not market_id:
                        logger.warning("skipped market: missing market_id")
                        continue

                    if not is_valid_market(market):
                        continue

                    if not is_market_active(market):
                        continue

                    existing = get_announced_market(market_id)
                    if not existing:
                        title = str(market.get("title", "")).strip()
                        theme = format_theme(market.get("theme", "other"))
                        end_time_unix = market.get("end_time")
                        end_time = datetime.fromtimestamp(int(end_time_unix))
                        end_time_str = end_time.strftime('%b %d, %Y at %I:%M %p')

                        notification = (
                            f"🚀 NEW MARKET LIVE\n\n"
                            f"📌 {title}\n\n"
                            f"🏷️ Theme: {theme}\n"
                            f"⏰ Closes: {end_time_str}\n\n"
                            f"Stake Now And Earn Rewards!"
                        )

                        broadcast_to_all(notification)
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
        now = datetime.now()
        markets = get_all_announced_markets()

        for market_data in markets:
            try:
                market_id = market_data["market_id"]

                if market_data.get("notified_ended"):
                    logger.info(f"skipped {market_data.get('title', market_id)}: already sent ended notification")
                    continue

                end_time_str = market_data.get("end_time")
                title = market_data.get("title", "").strip()

                if not end_time_str:
                    logger.warning(f"skipped market {market_id}: missing end_time in db")
                    continue

                if not title:
                    logger.warning(f"skipped market {market_id}: blank title in db")
                    continue

                end_time = datetime.fromisoformat(end_time_str)
                time_until = (end_time - now).total_seconds()

                if time_until > 0:
                    hours_until = time_until / 3600
                    minutes_until = time_until / 60

                    if hours_until <= 1.0 and not market_data.get("notified_1h"):
                        mins_left = int(minutes_until)
                        notification = (
                            f"⏰ MARKET CLOSING SOON\n\n"
                            f"📌 {title}\n\n"
                            f"⏳ Time Remaining: {mins_left} Minutes\n\n"
                            f"This Is Your Last Chance To Stake!"
                        )
                        broadcast_to_all(notification)
                        update_market_flag(market_id, "notified_1h")
                        logger.info(f"1 hour reminder sent for: {title}")

                    elif minutes_until <= 5.0 and not market_data.get("notified_5m"):
                        notification = (
                            f"🚨 URGENT: MARKET CLOSING IN 5 MINUTES\n\n"
                            f"📌 {title}\n\n"
                            f"⏳ Time Remaining: 5 Minutes\n\n"
                            f"Act Now Or Lose This Opportunity!"
                        )
                        broadcast_to_all(notification)
                        update_market_flag(market_id, "notified_5m")
                        logger.info(f"5 minute reminder sent for: {title}")

                else:
                    if not market_data.get("notified_ended"):
                        notification = (
                            f"✅ MARKET CLOSED\n\n"
                            f"📌 {title}\n\n"
                            f"💰 Reward Distribution In Progress\n"
                            f"Check Your Wallet For Returns!"
                        )
                        broadcast_to_all(notification)
                        update_market_flag(market_id, "notified_ended")
                        logger.info(f"ended notification sent for: {title}")

            except Exception as e:
                logger.error(f"error checking notification for market {market_id}: {e}")

    except Exception as e:
        logger.error(f"error in check_scheduled_notifications: {e}")


def get_ending_soon_markets():
    now = datetime.now()
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
            "⏲️ 5 Minutes Before Market Closes\n"
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


logger.info("Starting Bot...")
init_db()

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
