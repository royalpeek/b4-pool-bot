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
announced_pools = {}
scheduled_notifications = {}
users_db = {}

subscriptions_file = "subscribed_chats.json"
pools_file = "announced_pools.json"
users_file = "users.json"

SOLANA_PROGRAM_ID = "9XQDD38sy1qJ57DqAQvADuLRTjcYUXD48H7deyNuaehH"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"

ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

def load_data():
    global subscribed_chats, announced_pools, scheduled_notifications, users_db
    try:
        if os.path.exists(subscriptions_file):
            with open(subscriptions_file, 'r') as f:
                subscribed_chats = json.load(f)
        if os.path.exists(pools_file):
            with open(pools_file, 'r') as f:
                announced_pools = json.load(f)
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
        with open(pools_file, 'w') as f:
            json.dump(announced_pools, f)
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

def get_latest_transactions():
    try:
        url = SOLANA_RPC
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [
                SOLANA_PROGRAM_ID,
                {"limit": 10}
            ]
        }
        response = requests.post(url, json=payload, timeout=10)
        data = response.json()
        
        if "result" in data:
            return data["result"]
        return []
    except Exception as e:
        print(f"error fetching transactions: {e}")
        return []

def parse_transaction_for_pool(signature):
    try:
        url = SOLANA_RPC
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "jsonParsed"}
            ]
        }
        response = requests.post(url, json=payload, timeout=10)
        data = response.json()
        
        if "result" not in data or data["result"] is None:
            return None
        
        transaction = data["result"]
        
        if "meta" not in transaction or transaction["meta"] is None:
            return None
        
        logs = transaction["meta"].get("logMessages", [])
        
        for log in logs:
            if "CreateMarket" in log:
                block_time = transaction.get("blockTime")
                if block_time:
                    pool_id = signature[:8]
                    create_time = datetime.fromtimestamp(block_time)
                    end_time = create_time + timedelta(hours=24)
                    
                    return {
                        "pool_id": pool_id,
                        "signature": signature,
                        "create_time": create_time.isoformat(),
                        "end_time": end_time.isoformat(),
                        "block_time": block_time,
                        "notified_1h": False,
                        "notified_5m": False,
                        "notified_ended": False
                    }
        
        return None
    except Exception as e:
        print(f"error parsing transaction {signature}: {e}")
        return None

def monitor_solana_pools():
    print("solana monitoring thread started")
    while True:
        try:
            transactions = get_latest_transactions()
            
            for tx in transactions:
                try:
                    signature = tx.get("signature")
                    
                    if signature and signature not in announced_pools:
                        pool_data = parse_transaction_for_pool(signature)
                        
                        if pool_data:
                            pool_id = pool_data["pool_id"]
                            create_time = pool_data["create_time"]
                            end_time = pool_data["end_time"]
                            
                            notification = f"🚀 NEW POOL LIVE\n\nPool ID: {pool_id}\nStatus: ACTIVE\nDuration: 24 Hours\nCreated: {create_time}\n\nStake now before this opportunity closes!"
                            broadcast_to_all(notification)
                            
                            announced_pools[signature] = pool_data
                            save_data()
                            
                            print(f"new pool detected: {pool_id}")
                except Exception as e:
                    print(f"error processing transaction: {e}")
            
            check_scheduled_notifications()
            
            time.sleep(30)
        
        except Exception as e:
            print(f"error in monitor_solana_pools: {e}")
            time.sleep(60)

def check_scheduled_notifications():
    try:
        now = datetime.now()
        
        for signature, pool_data in announced_pools.items():
            try:
                end_time = datetime.fromisoformat(pool_data["end_time"])
                pool_id = pool_data["pool_id"]
                time_until = (end_time - now).total_seconds()
                
                if time_until > 0:
                    hours_until = time_until / 3600
                    minutes_until = time_until / 60
                    
                    if hours_until <= 1.0 and not pool_data.get("notified_1h"):
                        notification = f"⏰ POOL CLOSING SOON\n\nPool ID: {pool_id}\nTime Remaining: 1 Hour\n\nThis is your last chance to stake!"
                        broadcast_to_all(notification)
                        announced_pools[signature]["notified_1h"] = True
                        save_data()
                        print(f"1 hour reminder sent for pool: {pool_id}")
                    
                    elif minutes_until <= 5.0 and not pool_data.get("notified_5m"):
                        notification = f"🚨 URGENT: POOL CLOSING IN 5 MINUTES\n\nPool ID: {pool_id}\nTime Remaining: 5 Minutes\n\nAct Now or lose this opportunity!"
                        broadcast_to_all(notification)
                        announced_pools[signature]["notified_5m"] = True
                        save_data()
                        print(f"5 minute reminder sent for pool: {pool_id}")
                
                else:
                    if not pool_data.get("notified_ended"):
                        notification = f"✅ POOL CLOSED\n\nPool ID: {pool_id}\nStatus: Ended\n\nReward Distribution in Progress. Check your wallet for returns!"
                        broadcast_to_all(notification)
                        announced_pools[signature]["notified_ended"] = True
                        save_data()
                        print(f"pool ended notification sent for pool: {pool_id}")
            except Exception as e:
                print(f"error checking notification for pool: {e}")
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
        bot.reply_to(message, "Error getting your ID")

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
        
        welcome_msg = "🚀 Welcome To B4 Pool Alerts\n\nI Monitor B4 Pool In Real-Time\n\n📢 You Will Receive Notifications For:\n\n🎯 New Pools Launching\n⏰ 1 Hour Before Pool Closes\n⏲️ 5 Minutes Before Pool Closes\n💰 Pool Closure & Reward Distribution\n\n✅ You Are Now Subscribed\n\nSit Back And Receive Alerts!"
        bot.reply_to(message, welcome_msg)
    except Exception as e:
        print(f"error in start: {e}")
        bot.reply_to(message, "Error Subscribing You")

@bot.message_handler(commands=['help'])
def send_help(message):
    try:
        save_user(message)
        help_text = "📖 B4 Pool Alert Bot\n\n⚙️ Available Commands:\n\n/start - Subscribe To Pool Alerts\n/help - Show This Message\n/status - Check Bot Status\n/getmyid - Get Your Telegram ID\n\n❓ What I Do:\n\nI Continuously Monitor B4 Pools On Solana And Send Real-Time Notifications At Critical Moments.\n\nNever Miss A Staking Opportunity!"
        bot.reply_to(message, help_text)
    except Exception as e:
        print(f"error in help: {e}")
        bot.reply_to(message, "Error Showing Help")

@bot.message_handler(commands=['status'])
def pool_status(message):
    try:
        save_user(message)
        total_pools = len(announced_pools)
        total_chats = len(subscribed_chats)
        status_msg = f"📊 Bot Status\n\n🔍 Active Pools: {total_pools}\n👥 Subscribed Users/Groups: {total_chats}\n\n✅ Status: Running & Monitoring"
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
        total_pools = len(announced_pools)
        total_chats = len(subscribed_chats)
        
        stats_msg = f"📊 Bot Statistics\n\n👥 Total Users: {total_users}\n🔍 Active Pools: {total_pools}\n💬 Subscribed Chats: {total_chats}\n\n✅ Status: Running"
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
monitor_thread = Thread(target=monitor_solana_pools, daemon=True)
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
