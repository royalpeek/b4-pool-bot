import telebot
import json
import os
import time
from threading import Thread
from datetime import datetime, timedelta
import requests

bot_token = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(bot_token)

subscribed_chats = {}
announced_markets = {}
scheduled_notifications = {}
users_db = {}

subscriptions_file = "subscribed_chats.json"
markets_file = "announced_markets.json"
users_file = "users.json"

B4_API_URL = "https://b4app.xyz/api/markets"

ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

def load_data():
    global subscribed_chats, announced_markets, scheduled_notifications, users_db
    try:
        if os.path.exists(subscriptions_file):
            with open(subscriptions_file, 'r') as f:
                subscribed_chats = json.load(f)
        if os.path.exists(markets_file):
            with open(markets_file, 'r') as f:
                announced_markets = json.load(f)
        if os.path.exists(users_file):
            with open(users_file, 'r') as f:
                users_db = json.load(f)
        print("data loaded successfully")
    except Exception as e:
        print(f"error loading data: {e}")

def save_data():
    try:
        with open(subscriptions_file, 'w') as f:
            json.dump(subscribed_chats, f)
        with open(markets_file, 'w') as f:
            json.dump(announced_markets, f)
        with open(users_file, 'w') as f:
            json.dump(users_db, f)
    except Exception as e:
        print(f"error saving data: {e}")

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
        print(f"error saving user: {e}")

def broadcast_to_all(message):
    try:
        failed = []
        for chat_id in subscribed_chats.keys():
            try:
                bot.send_message(int(chat_id), message)
            except Exception as e:
                print(f"error sending to {chat_id}: {e}")
                failed.append(chat_id)
        
        if failed:
            for chat_id in failed:
                del subscribed_chats[chat_id]
            save_data()
    except Exception as e:
        print(f"error in broadcast_to_all: {e}")

def fetch_b4_markets():
    try:
        response = requests.get(B4_API_URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if isinstance(data, dict) and "markets" in data:
            return data["markets"]
        elif isinstance(data, list):
            return data
        else:
            print(f"unexpected api response format: {data}")
            return []
    except Exception as e:
        print(f"error fetching b4 markets: {e}")
        return []

def monitor_b4_markets():
    print("b4 market monitoring thread started")
    while True:
        try:
            markets = fetch_b4_markets()
            
            for market in markets:
                try:
                    market_id = market.get("market_id") or market.get("market_pubkey")
                    
                    if market_id and market_id not in announced_markets:
                        title = market.get("title", "Unknown Market")
                        go_live_at = market.get("go_live_at", "")
                        theme = market.get("theme", "")
                        live_status = market.get("live_status", False)
                        
                        if live_status:
                            live_time = datetime.fromisoformat(go_live_at.replace('Z', '+00:00')) if go_live_at else datetime.now()
                            end_time = live_time + timedelta(hours=24)
                            
                            notification = f"🚀 NEW MARKET LIVE\n\nTitle: {title}\nTheme: {theme}\n\nStake now and earn rewards!"
                            broadcast_to_all(notification)
                            
                            announced_markets[market_id] = {
                                "market_id": market_id,
                                "title": title,
                                "go_live_at": go_live_at,
                                "end_time": end_time.isoformat(),
                                "notified_new": True,
                                "notified_1h": False,
                                "notified_5m": False,
                                "notified_ended": False
                            }
                            save_data()
                            
                            print(f"new market detected: {title}")
                except Exception as e:
                    print(f"error processing market: {e}")
            
            check_scheduled_notifications()
            
            time.sleep(30)
        
        except Exception as e:
            print(f"error in monitor_b4_markets: {e}")
            time.sleep(60)

def check_scheduled_notifications():
    try:
        now = datetime.now()
        
        for market_id, market_data in list(announced_markets.items()):
            try:
                end_time = datetime.fromisoformat(market_data["end_time"])
                title = market_data["title"]
                time_until = (end_time - now).total_seconds()
                
                if time_until > 0:
                    hours_until = time_until / 3600
                    minutes_until = time_until / 60
                    
                    if hours_until <= 1.0 and not market_data.get("notified_1h"):
                        notification = f"⏰ MARKET CLOSING SOON\n\nTitle: {title}\nTime Remaining: 1 Hour\n\nThis is your last chance to stake!"
                        broadcast_to_all(notification)
                        announced_markets[market_id]["notified_1h"] = True
                        save_data()
                        print(f"1 hour reminder sent for market: {title}")
                    
                    elif minutes_until <= 5.0 and not market_data.get("notified_5m"):
                        notification = f"🚨 URGENT: MARKET CLOSING IN 5 MINUTES\n\nTitle: {title}\nTime Remaining: 5 Minutes\n\nAct Now or lose this opportunity!"
                        broadcast_to_all(notification)
                        announced_markets[market_id]["notified_5m"] = True
                        save_data()
                        print(f"5 minute reminder sent for market: {title}")
                
                else:
                    if not market_data.get("notified_ended"):
                        notification = f"✅ MARKET CLOSED\n\nTitle: {title}\nStatus: Ended\n\nReward Distribution in Progress. Check your wallet for returns!"
                        broadcast_to_all(notification)
                        announced_markets[market_id]["notified_ended"] = True
                        save_data()
                        print(f"market ended notification sent for market: {title}")
            except Exception as e:
                print(f"error checking notification for market: {e}")
    except Exception as e:
        print(f"error in check_scheduled_notifications: {e}")

@bot.message_handler(commands=['getmyid'])
def get_my_id(message):
    try:
        user_id = message.from_user.id
        username = message.from_user.username or "No Username"
        reply = f"📱 Your Telegram ID: {user_id}\n👤 Your Username: @{username}"
        bot.reply_to(message, reply)
    except Exception as e:
        print(f"error in getmyid: {e}")
        bot.reply_to(message, "Error Getting Your ID")

@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        chat_id = str(message.chat.id)
        
        if message.chat.type == 'private':
            chat_name = f"user_{message.from_user.username or message.from_user.id}"
        else:
            chat_name = message.chat.title
        
        subscribed_chats[chat_id] = chat_name
        save_user(message)
        save_data()
        
        welcome_msg = "🚀 Welcome To B4 Market Alerts\n\nI Monitor B4 Pools In Real-Time\n\n📢 You Will Receive Notifications For:\n\n🎯 New Markets Launching\n⏰ 1 Hour Before Market Closes\n⏲️ 5 Minutes Before Market Closes\n💰 Market Closure & Reward Distribution\n\n✅ You Are Now Subscribed\n\nSit Back And Receive Alerts!"
        bot.reply_to(message, welcome_msg)
    except Exception as e:
        print(f"error in start: {e}")
        bot.reply_to(message, "Error Subscribing You")

@bot.message_handler(commands=['help'])
def send_help(message):
    try:
        save_user(message)
        help_text = "📖 B4 Market Alert Bot\n\n⚙️ Available Commands:\n\n/start - Subscribe To Market Alerts\n/help - Show This Message\n/status - Check Bot Status\n/getmyid - Get Your Telegram ID\n\n❓ What I Do:\n\nI Continuously Monitor B4 Markets On Solana And Send Real-Time Notifications At Critical Moments.\n\nNever Miss A Market Opportunity!"
        bot.reply_to(message, help_text)
    except Exception as e:
        print(f"error in help: {e}")
        bot.reply_to(message, "Error Showing Help")

@bot.message_handler(commands=['status'])
def market_status(message):
    try:
        save_user(message)
        total_markets = len(announced_markets)
        total_chats = len(subscribed_chats)
        status_msg = f"📊 Bot Status\n\n🔍 Active Markets: {total_markets}\n👥 Subscribed Users/Groups: {total_chats}\n\n✅ Status: Running & Monitoring"
        bot.reply_to(message, status_msg)
    except Exception as e:
        print(f"error in status: {e}")
        bot.reply_to(message, "Error Getting Status")

@bot.message_handler(commands=['users'])
def show_users(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ Permission Denied. Admin Only Command")
            return
        
        total_users = len(users_db)
        users_msg = f"📊 User Statistics\n\n👥 Total Users: {total_users}"
        bot.reply_to(message, users_msg)
    except Exception as e:
        print(f"error in users: {e}")
        bot.reply_to(message, "Error Getting Users")

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
        print(f"error in listusers: {e}")
        bot.reply_to(message, "Error Listing Users")

@bot.message_handler(commands=['stats'])
def show_stats(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ Permission Denied. Admin Only Command")
            return
        
        total_users = len(users_db)
        total_markets = len(announced_markets)
        total_chats = len(subscribed_chats)
        
        stats_msg = f"📊 Bot Statistics\n\n👥 Total Users: {total_users}\n🔍 Active Markets: {total_markets}\n💬 Subscribed Chats: {total_chats}\n\n✅ Status: Running"
        bot.reply_to(message, stats_msg)
    except Exception as e:
        print(f"error in stats: {e}")
        bot.reply_to(message, "Error Getting Stats")

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
        print(f"error in broadcast: {e}")
        bot.reply_to(message, "Error Broadcasting Message")

print("Starting Bot...")
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
        print(f"error running flask: {e}")

print("Starting Flask Server...")
flask_thread = FlaskThread(target=run_flask, daemon=True)
flask_thread.start()

print("Bot Is Ready")
try:
    bot.infinity_polling()
except Exception as e:
    print(f"critical error in bot: {e}")
    time.sleep(10)
