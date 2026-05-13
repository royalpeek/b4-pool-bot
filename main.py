import telebot
import json
import os
import time
import logging
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

subscribed_chats = {}
announced_markets = {}
users_db = {}

subscriptions_file = "subscribed_chats.json"
markets_file = "announced_markets.json"
users_file = "users.json"

B4_API_URL = "https://b4app.xyz/api/markets"

ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

def load_json_file(filepath):
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                content = f.read().strip()
                if content:
                    return json.loads(content)
        return {}
    except Exception as e:
        logger.error(f"error loading {filepath}: {e}")
        return {}

def save_json_file(filepath, data):
    try:
        temp_file = filepath + ".tmp"
        with open(temp_file, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(temp_file, filepath)
    except Exception as e:
        logger.error(f"error saving {filepath}: {e}")

def load_data():
    global subscribed_chats, announced_markets, users_db
    subscribed_chats = load_json_file(subscriptions_file)
    announced_markets = load_json_file(markets_file)
    users_db = load_json_file(users_file)
    logger.info(f"data loaded: {len(subscribed_chats)} chats, {len(announced_markets)} markets, {len(users_db)} users")

def save_data():
    save_json_file(subscriptions_file, subscribed_chats)
    save_json_file(markets_file, announced_markets)
    save_json_file(users_file, users_db)

load_data()

def is_admin(user_id):
    if ADMIN_ID is None or ADMIN_ID == 0:
        return False
    return user_id == ADMIN_ID

def save_user(message):
    try:
        user_id = str(message.from_user.id)
        if user_id not in users_db:
            users_db[user_id] = {
                "user_id": message.from_user.id,
                "username": message.from_user.username or "No Username",
                "first_name": message.from_user.first_name or "No Name",
                "join_date": datetime.now().isoformat(),
                "is_admin": is_admin(message.from_user.id)
            }
            save_data()
    except Exception as e:
        logger.error(f"error saving user: {e}")

def broadcast_to_all(message_text):
    try:
        failed = []
        sent = 0
        for chat_id in list(subscribed_chats.keys()):
            try:
                bot.send_message(int(chat_id), message_text)
                sent += 1
            except Exception as e:
                logger.error(f"error sending to {chat_id}: {e}")
                failed.append(chat_id)

        if failed:
            for chat_id in failed:
                if chat_id in subscribed_chats:
                    del subscribed_chats[chat_id]
            save_data()

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
                    market_id = str(market.get("market_id", ""))

                    if not market_id:
                        continue

                    if not is_market_active(market):
                        continue

                    if market_id not in announced_markets:
                        title = market.get("title", "Unknown Market")
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

                        announced_markets[market_id] = {
                            "market_id": market_id,
                            "title": title,
                            "theme": theme,
                            "end_time": end_time.isoformat(),
                            "notified_new": True,
                            "notified_1h": False,
                            "notified_5m": False,
                            "notified_ended": False,
                            "detected_at": datetime.now().isoformat()
                        }
                        save_data()
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

        for market_id, market_data in list(announced_markets.items()):
            try:
                if market_data.get("notified_ended"):
                    continue

                end_time = datetime.fromisoformat(market_data["end_time"])
                title = market_data["title"]
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
                        announced_markets[market_id]["notified_1h"] = True
                        save_data()
                        logger.info(f"1 hour reminder sent for: {title}")

                    elif minutes_until <= 5.0 and not market_data.get("notified_5m"):
                        notification = (
                            f"🚨 URGENT: MARKET CLOSING IN 5 MINUTES\n\n"
                            f"📌 {title}\n\n"
                            f"⏳ Time Remaining: 5 Minutes\n\n"
                            f"Act Now Or Lose This Opportunity!"
                        )
                        broadcast_to_all(notification)
                        announced_markets[market_id]["notified_5m"] = True
                        save_data()
                        logger.info(f"5 minute reminder sent for: {title}")

                else:
                    notification = (
                        f"✅ MARKET CLOSED\n\n"
                        f"📌 {title}\n\n"
                        f"💰 Reward Distribution In Progress\n"
                        f"Check Your Wallet For Returns!"
                    )
                    broadcast_to_all(notification)
                    announced_markets[market_id]["notified_ended"] = True
                    save_data()
                    logger.info(f"ended notification sent for: {title}")

            except Exception as e:
                logger.error(f"error checking notification for market {market_id}: {e}")

    except Exception as e:
        logger.error(f"error in check_scheduled_notifications: {e}")

def get_ending_soon_markets():
    now = datetime.now()
    ending_soon = []

    for market_id, market_data in announced_markets.items():
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
        subscribed_chats[chat_id] = chat_name
        save_user(message)
        save_data()

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
        total_markets = len(announced_markets)
        total_chats = len(subscribed_chats)
        active = sum(1 for m in announced_markets.values() if not m.get("notified_ended"))
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
        total_users = len(users_db)
        bot.reply_to(message, f"📊 User Statistics\n\n👥 Total Users: {total_users}")
    except Exception as e:
        logger.error(f"error in users: {e}")


@bot.message_handler(commands=['listusers'])
def list_users(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ Permission Denied. Admin Only Command")
            return

        if not users_db:
            bot.reply_to(message, "No Users Yet")
            return

        users_list = "📋 Registered Users\n\n"
        for user_id, user_data in users_db.items():
            username = user_data.get("username", "No Username")
            first_name = user_data.get("first_name", "No Name")
            join_date = user_data.get("join_date", "Unknown")
            users_list += f"ID: {user_id}\nName: {first_name}\nUsername: @{username}\nJoined: {join_date}\n\n"

        bot.reply_to(message, users_list)
    except Exception as e:
        logger.error(f"error in listusers: {e}")


@bot.message_handler(commands=['stats'])
def show_stats(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ Permission Denied. Admin Only Command")
            return

        total_users = len(users_db)
        total_markets = len(announced_markets)
        total_chats = len(subscribed_chats)
        active = sum(1 for m in announced_markets.values() if not m.get("notified_ended"))

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
        bot.reply_to(message, f"📢 Message Sent To {len(subscribed_chats)} Chats")
    except Exception as e:
        logger.error(f"error in broadcast: {e}")


logger.info("Starting Bot...")
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
