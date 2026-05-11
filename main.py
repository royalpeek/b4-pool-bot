import telebot
import json
import os
import time
from threading import Thread
from datetime import datetime, timedelta
import requests

bot_token = "8698408538:AAH01ZNyMpN2kI8qb6f4mEoK6-ZPmfYPk_g"
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

ADMIN_ID = None

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
    if ADMIN_ID is None:
        return False
    return user_id == ADMIN_ID

def save_user(message):
    try:
        user_id = str(message.from_user.id)
        
        if user_id not in users_db:
            users_db[user_id] = {
                "user_id": message.from_user.id,
                "username": message.from_user.username or "no username",
                "first_name": message.from_user.first_name or "no name",
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
                            
                            notification = f"🚀 NEW POOL ALERT\n\n━━━━━━━━━━━━━━━━━━━\nPool ID: {pool_id}\nStatus: LIVE\nDuration: 24 Hours\nCreated: {create_time}\n━━━━━━━━━━━━━━━━━━━\n\n⏱️ Stake now before this opportunity closes!\n\n#B4 #Solana #Staking"
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
                        notification = f"⏰ POOL CLOSING SOON\n\n━━━━━━━━━━━━━━━━━━━\nPool ID: {pool_id}\nTime Remaining: 1 HOUR\n━━━━━━━━━━━━━━━━━━━\n\n🔴 This is your last chance to stake!\n\n#B4 #Solana #Staking"
                        broadcast_to_all(notification)
                        announced_pools[signature]["notified_1h"] = True
                        save_data()
                        print(f"1 hour reminder sent for pool: {pool_id}")
                    
                    elif minutes_until <= 5.0 and not pool_data.get("notified_5m"):
                        notification = f"🚨 URGENT: POOL CLOSING IN 5 MINUTES\n\n━━━━━━━━━━━━━━━━━━━\nPool ID: {pool_id}\nTime Remaining: 5 MINUTES ⏳\n━━━━━━━━━━━━━━━━━━━\n\n🔴 ACT NOW or lose this opportunity!\n\n#B4 #Solana #Staking"
                        broadcast_to_all(notification)
                        announced_pools[signature]["notified_5m"] = True
                        save_data()
                        print(f"5 minute reminder sent for pool: {pool_id}")
                
                else:
                    if not pool_data.get("notified_ended"):
                        notification = f"✅ POOL CLOSED\n\n━━━━━━━━━━━━━━━━━━━\nPool ID: {pool_id}\nStatus: ENDED\n━━━━━━━━━━━━━━━━━━━\n\n💰 Reward distribution in progress...\nCheck your wallet for returns!\n\n#B4 #Solana #Staking"
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
        username = message.from_user.username or "no username"
        reply = f"your telegram id: {user_id}\nyour username: @{username}"
        bot.reply_to(message, reply)
    except Exception as e:
        print(f"error in getmyid: {e}")
        bot.reply_to(message, "error getting your id")

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
        
        welcome_msg = "welcome to b4 pool alerts 🚀\n\ni monitor solana pools on b4 in real-time and send you instant notifications about:\n• new pools launching\n• 1 hour before pool closes\n• 5 minutes before pool closes\n• pool closure & reward distribution\n\nyou're now subscribed. sit back and receive alerts!"
        bot.reply_to(message, welcome_msg)
    except Exception as e:
        print(f"error in start: {e}")
        bot.reply_to(message, "error subscribing you")

@bot.message_handler(commands=['help'])
def send_help(message):
    try:
        save_user(message)
        help_text = "b4 pool alert bot\n\n📋 commands:\n/start - subscribe to pool alerts\n/help - show this message\n/status - check monitoring status\n/getmyid - get your telegram id\n\n⚙️ what i do:\ni continuously monitor b4 pools on solana and send real-time notifications at critical moments. never miss a staking opportunity."
        bot.reply_to(message, help_text)
    except Exception as e:
        print(f"error in help: {e}")
        bot.reply_to(message, "error showing help")

@bot.message_handler(commands=['status'])
def pool_status(message):
    try:
        save_user(message)
        total_pools = len(announced_pools)
        total_chats = len(subscribed_chats)
        status_msg = f"📊 bot status\n\nactive pools: {total_pools}\nsubscribed users/groups: {total_chats}\n\nstatus: ✅ running & monitoring"
        bot.reply_to(message, status_msg)
    except Exception as e:
        print(f"error in status: {e}")
        bot.reply_to(message, "error getting status")

@bot.message_handler(commands=['users'])
def show_users(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ you don't have permission to use this command")
            return
        
        total_users = len(users_db)
        users_msg = f"📊 user statistics\n\ntotal users: {total_users}"
        bot.reply_to(message, users_msg)
    except Exception as e:
        print(f"error in users: {e}")
        bot.reply_to(message, "error getting users")

@bot.message_handler(commands=['listusers'])
def list_users(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ you don't have permission to use this command")
            return
        
        if not users_db:
            bot.reply_to(message, "no users yet")
            return
        
        users_list = "📋 registered users\n\n"
        for user_id, user_data in users_db.items():
            username = user_data.get("username", "no username")
            first_name = user_data.get("first_name", "no name")
            join_date = user_data.get("join_date", "unknown")
            users_list += f"id: {user_id}\nname: {first_name}\nusername: @{username}\njoined: {join_date}\n\n"
        
        bot.reply_to(message, users_list)
    except Exception as e:
        print(f"error in listusers: {e}")
        bot.reply_to(message, "error listing users")

@bot.message_handler(commands=['stats'])
def show_stats(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ you don't have permission to use this command")
            return
        
        total_users = len(users_db)
        total_pools = len(announced_pools)
        total_chats = len(subscribed_chats)
        
        stats_msg = f"📊 bot statistics\n\n━━━━━━━━━━━━━━━━━━━\ntotal users: {total_users}\nactive pools: {total_pools}\nsubscribed chats: {total_chats}\nstatus: ✅ running\n━━━━━━━━━━━━━━━━━━━"
        bot.reply_to(message, stats_msg)
    except Exception as e:
        print(f"error in stats: {e}")
        bot.reply_to(message, "error getting stats")

@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ you don't have permission to use this command")
            return
        
        args = message.text.split(maxsplit=1)
        
        if len(args) < 2:
            bot.reply_to(message, "format: /broadcast your message here")
            return
        
        broadcast_msg = args[1]
        broadcast_to_all(broadcast_msg)
        bot.reply_to(message, f"message sent to {len(subscribed_chats)} chats")
    except Exception as e:
        print(f"error in broadcast: {e}")
        bot.reply_to(message, "error broadcasting message")

print("starting bot...")
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

print("starting flask server...")
flask_thread = FlaskThread(target=run_flask, daemon=True)
flask_thread.start()

print("bot is ready")
try:
    bot.infinity_polling()
except Exception as e:
    print(f"critical error in bot: {e}")
    time.sleep(10)
