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
from datetime import datetime, timezone, timedelta
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
DEFAULT_AI_MODEL = "openai/gpt-oss-20b"
ai_model = os.getenv("AI_MODEL", DEFAULT_AI_MODEL)
NOTIFICATION_COOLDOWN_SECONDS = int(os.getenv("NOTIFICATION_COOLDOWN_SECONDS", "2"))
SEND_DELAY_SECONDS = float(os.getenv("SEND_DELAY_SECONDS", "0.1"))
BROADCAST_WORKERS = max(1, int(os.getenv("BROADCAST_WORKERS", "4")))
MARKET_POLL_SECONDS = float(os.getenv("MARKET_POLL_SECONDS", "1"))
PUBLIC_ALERT_DELAY_SECONDS = float(os.getenv("PUBLIC_ALERT_DELAY_SECONDS", "45"))
COVER_IMAGE_WAIT_SECONDS = float(os.getenv("COVER_IMAGE_WAIT_SECONDS", "8"))
COVER_IMAGE_RETRY_SECONDS = float(os.getenv("COVER_IMAGE_RETRY_SECONDS", "1"))
API_TIMEOUT_SECONDS = float(os.getenv("API_TIMEOUT_SECONDS", "6"))
API_PAGE_LIMIT = int(os.getenv("API_PAGE_LIMIT", "100"))
AI_ON_PREMIUM_FAST_ALERT = os.getenv("AI_ON_PREMIUM_FAST_ALERT", "false").lower() == "true"
IMAGE_FOLLOWUP_WAIT_SECONDS = float(os.getenv("IMAGE_FOLLOWUP_WAIT_SECONDS", "45"))
PREMIUM_GO_LIVE_REMINDER_SECONDS = int(os.getenv("PREMIUM_GO_LIVE_REMINDER_SECONDS", "120"))
TEMP_RESPONSE_DELETE_SECONDS = int(os.getenv("TEMP_RESPONSE_DELETE_SECONDS", "180"))
DAILY_SUMMARY_UTC_HOUR = int(os.getenv("DAILY_SUMMARY_UTC_HOUR", "9"))
PRIORITY_CHAT_IDS = [
    chat_id.strip()
    for chat_id in os.getenv("PRIORITY_CHAT_IDS", "").split(",")
    if chat_id.strip()
]
PREMIUM_PAYMENT_TEXT = os.getenv(
    "PREMIUM_PAYMENT_TEXT",
    "To activate premium, choose a plan, make payment, then send your payment proof here. Admin will activate your access after confirmation."
)
PREMIUM_PLAN_PRICES = {
    "monthly": "$3",
    "3months": "$8",
    "6months": "$15",
    "yearly": "$25",
}
PREMIUM_PLAN_DAYS = {
    "monthly": 30,
    "3months": 90,
    "6months": 180,
    "yearly": 365,
}
PREMIUM_ALERT_MODES = {
    "all": "All priority alerts",
    "promo": "Boosted and promo markets only",
    "first_staker": "First-staker promos only",
    "sponsor": "Sponsor boosts only",
}
CUSTOM_EMOJI_IDS = {
    "live": "5416081784641168838",
    "premium": "5251203410396458957",
    "one_hour": "5260280853841321805",
    "ten_minutes": "5317000131822760128",
    "ended": "5206204230582425091",
}
CUSTOM_EMOJI_FALLBACKS = {
    "live": "LIVE",
    "premium": "PREMIUM",
    "one_hour": "1H",
    "ten_minutes": "10M",
    "ended": "END",
}
VALID_CUSTOM_EMOJI_KEYS = set(CUSTOM_EMOJI_IDS.keys())
VALID_THEMES = ["all", "crypto", "politics", "entertainment", "sports", "travel", "current_events", "other"]
VALID_TONES = ["casual", "urgent", "premium", "degen", "professional"]
STUDIO_BUTTON_SCOPES = {
    "all",
    "new_market",
    "scheduled_market",
    "reminder_1h",
    "reminder_10m",
    "go_live_reminder",
    "image_followup",
    "broadcast",
}

ai_client = None
if ai_api_key and ai_base_url:
    ai_client = OpenAI(api_key=ai_api_key, base_url=ai_base_url)
    logger.info("ai client initialized with model %s", ai_model)
else:
    logger.warning("ai not configured, using template notifications")


class _CacheEntry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value, ttl):
        self.value = value
        self.expires_at = time.monotonic() + ttl


_cache: dict[str, _CacheEntry] = {}
DEFAULT_CACHE_TTL = 5


def cache_get(key, ttl=DEFAULT_CACHE_TTL):
    entry = _cache.get(key)
    if entry and time.monotonic() < entry.expires_at:
        return entry.value
    return None


def cache_set(key, value, ttl=DEFAULT_CACHE_TTL):
    _cache[key] = _CacheEntry(value, ttl)


def cache_invalidate(*keys):
    for key in keys:
        _cache.pop(key, None)


def cache_invalidate_prefix(prefix):
    keys_to_remove = [k for k in _cache if k.startswith(prefix)]
    for key in keys_to_remove:
        _cache.pop(key, None)


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


def format_market_time(dt, include_date=True):
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    wat_time = dt.astimezone(timezone(timedelta(hours=1)))
    pattern = '%b %d, %Y at %I:%M %p' if include_date else '%I:%M %p'
    return f"{wat_time.strftime(pattern)} WAT"


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
                        plan TEXT,
                        expires_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                cur.execute("ALTER TABLE premium_chats ADD COLUMN IF NOT EXISTS plan TEXT")
                cur.execute("ALTER TABLE premium_chats ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS message_templates (
                        template_key TEXT PRIMARY KEY,
                        body TEXT NOT NULL,
                        rich_body TEXT,
                        updated_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS custom_buttons (
                        id SERIAL PRIMARY KEY,
                        scope TEXT NOT NULL,
                        label TEXT NOT NULL,
                        url TEXT NOT NULL,
                        sort_order INTEGER DEFAULT 100,
                        enabled BOOLEAN DEFAULT TRUE,
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


def init_intelligence_tables():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS markets (
                        market_id TEXT PRIMARY KEY,
                        title TEXT,
                        description TEXT,
                        creator_wallet TEXT,
                        market_pubkey TEXT,
                        theme TEXT,
                        end_time TIMESTAMPTZ,
                        go_live_at TIMESTAMPTZ,
                        created_at_api TIMESTAMPTZ,
                        detected_at TIMESTAMPTZ DEFAULT NOW(),
                        is_private BOOLEAN DEFAULT FALSE,
                        cover_image_url TEXT,
                        first_staker_promo BOOLEAN DEFAULT FALSE,
                        first_staker_match NUMERIC,
                        first_staker_min NUMERIC,
                        sponsor_count INT DEFAULT 0,
                        resolved BOOLEAN DEFAULT FALSE,
                        outcome INT DEFAULT 0,
                        hidden BOOLEAN DEFAULT FALSE,
                        last_updated_at TIMESTAMPTZ,
                        last_synced_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS market_snapshots (
                        id SERIAL PRIMARY KEY,
                        market_id TEXT REFERENCES markets(market_id),
                        snapshot_at TIMESTAMPTZ DEFAULT NOW(),
                        yes_pool NUMERIC DEFAULT 0,
                        no_pool NUMERIC DEFAULT 0,
                        yes_votes INT DEFAULT 0,
                        no_votes INT DEFAULT 0,
                        likes_count INT DEFAULT 0,
                        comments_count INT DEFAULT 0,
                        total_volume NUMERIC DEFAULT 0,
                        total_participants INT DEFAULT 0,
                        controversy_score NUMERIC DEFAULT 0,
                        avg_stake_size NUMERIC DEFAULT 0
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_snapshots_market_time
                    ON market_snapshots (market_id, snapshot_at DESC)
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS creators (
                        wallet_address TEXT PRIMARY KEY,
                        first_seen_at TIMESTAMPTZ DEFAULT NOW(),
                        last_seen_at TIMESTAMPTZ DEFAULT NOW(),
                        total_markets INT DEFAULT 0,
                        total_volume NUMERIC DEFAULT 0,
                        best_volume NUMERIC DEFAULT 0
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS creator_categories (
                        id SERIAL PRIMARY KEY,
                        wallet_address TEXT REFERENCES creators(wallet_address),
                        theme TEXT,
                        market_count INT DEFAULT 0,
                        total_volume NUMERIC DEFAULT 0,
                        UNIQUE(wallet_address, theme)
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS market_events (
                        id SERIAL PRIMARY KEY,
                        market_id TEXT REFERENCES markets(market_id),
                        event_type TEXT NOT NULL,
                        event_data JSONB DEFAULT '{}',
                        recorded_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_events_market_time
                    ON market_events (market_id, recorded_at DESC)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_events_type
                    ON market_events (event_type, recorded_at DESC)
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS market_fingerprints (
                        market_id TEXT PRIMARY KEY REFERENCES markets(market_id),
                        opening_word TEXT,
                        word_count INT,
                        has_question_mark BOOLEAN DEFAULT FALSE,
                        is_yes_no BOOLEAN DEFAULT FALSE,
                        topic_tags TEXT[] DEFAULT '{}',
                        detected_keywords TEXT[] DEFAULT '{}',
                        category TEXT,
                        complexity_score NUMERIC DEFAULT 0,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS market_scores (
                        market_id TEXT PRIMARY KEY REFERENCES markets(market_id),
                        volume_percentile NUMERIC DEFAULT 0,
                        controversy NUMERIC DEFAULT 0,
                        momentum NUMERIC DEFAULT 0,
                        engagement_ratio NUMERIC DEFAULT 0,
                        freshness NUMERIC DEFAULT 0,
                        composite_score NUMERIC DEFAULT 0,
                        scored_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS market_dna (
                        market_id TEXT PRIMARY KEY REFERENCES markets(market_id),
                        question_type TEXT,
                        sentiment_lean TEXT,
                        appeal_score NUMERIC DEFAULT 0,
                        divisiveness NUMERIC DEFAULT 0,
                        broad_appeal BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS ai_predictions (
                        id SERIAL PRIMARY KEY,
                        market_id TEXT REFERENCES markets(market_id),
                        predicted_volume_range TEXT,
                        predicted_controversy NUMERIC,
                        predicted_engagement NUMERIC,
                        confidence NUMERIC DEFAULT 0,
                        model_version TEXT,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_predictions_market
                    ON ai_predictions (market_id, created_at DESC)
                """)
        logger.info("intelligence tables ready")
    except Exception as e:
        logger.error(f"error initialising intelligence tables: {e}")


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


def set_ai_model(model):
    model = str(model or "").strip()
    if not model or len(model) > 120 or any(char.isspace() for char in model):
        return False
    set_bot_state("ai_model", model)
    return True


def get_ai_model():
    return get_bot_state("ai_model", ai_model) or ai_model


def set_custom_emoji(key, emoji_id, fallback=None):
    key = str(key or "").strip().lower()
    emoji_id = str(emoji_id or "").strip()
    if key not in VALID_CUSTOM_EMOJI_KEYS:
        return False
    if emoji_id.lower() in {"none", "off", "disable", "disabled"}:
        set_bot_state(f"custom_emoji_{key}", "")
    elif not emoji_id.isdigit():
        return False
    else:
        set_bot_state(f"custom_emoji_{key}", emoji_id)
    if fallback is not None and str(fallback).strip():
        set_bot_state(f"custom_emoji_fallback_{key}", str(fallback).strip()[:24])
    return True


def get_custom_emoji_id(key):
    saved = get_bot_state(f"custom_emoji_{key}", None)
    if saved is None:
        return CUSTOM_EMOJI_IDS.get(key, "")
    return saved


def get_custom_emoji_fallback(key, default=None):
    fallback = default or CUSTOM_EMOJI_FALLBACKS.get(key, key.upper())
    return get_bot_state(f"custom_emoji_fallback_{key}", fallback) or fallback


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
        cache_invalidate(f"themes:{chat_id}")
    except Exception as e:
        logger.error(f"error setting chat themes for {chat_id}: {e}")


def get_chat_themes(chat_id):
    cache_key = f"themes:{chat_id}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT themes FROM subscribed_chats WHERE chat_id = %s", (str(chat_id),))
                result = cur.fetchone()
                if result and result[0]:
                    themes = [theme for theme in result[0].split(",") if theme]
                    cache_set(cache_key, themes)
                    return themes
    except Exception as e:
        logger.error(f"error getting chat themes for {chat_id}: {e}")
    default = ["all"]
    cache_set(cache_key, default)
    return default


def chat_wants_theme(chat_id, theme):
    themes = get_chat_themes(chat_id)
    return "all" in themes or theme in themes


def normalize_premium_plan(plan):
    plan = str(plan or "monthly").strip().lower().replace("-", "").replace("_", "")
    aliases = {
        "month": "monthly",
        "1month": "monthly",
        "3month": "3months",
        "3months": "3months",
        "6month": "6months",
        "6months": "6months",
        "year": "yearly",
        "1year": "yearly",
        "yearly": "yearly",
        "annual": "yearly",
    }
    return aliases.get(plan, "monthly")


def premium_expiry_for_plan(plan):
    plan = normalize_premium_plan(plan)
    return now_utc() + timedelta(days=PREMIUM_PLAN_DAYS.get(plan, 30))


def get_premium_wallet():
    return get_bot_state("premium_usdc_sol_wallet", os.getenv("PREMIUM_USDC_SOL_WALLET", "")).strip()


def set_premium_wallet(wallet):
    wallet = str(wallet or "").strip()
    set_bot_state("premium_usdc_sol_wallet", wallet)
    return wallet


def set_user_premium_interest(user_id, plan):
    set_bot_state(f"premium_interest_{user_id}", normalize_premium_plan(plan))


def get_user_premium_interest(user_id):
    return get_bot_state(f"premium_interest_{user_id}", "")


def add_premium_chat(chat_id, added_by, plan="monthly", expires_at=None):
    plan = normalize_premium_plan(plan)
    expires_at = expires_at or premium_expiry_for_plan(plan)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO premium_chats (chat_id, added_by, plan, expires_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (chat_id)
                    DO UPDATE SET added_by = EXCLUDED.added_by,
                                  plan = EXCLUDED.plan,
                                  expires_at = EXCLUDED.expires_at
                """, (str(chat_id), str(added_by), plan, expires_at))
        cache_invalidate("premium:ids")
        return True
    except Exception as e:
        logger.error(f"error adding premium chat {chat_id}: {e}")
        return False


def remove_premium_chat(chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM premium_chats WHERE chat_id = %s", (str(chat_id),))
                removed = cur.rowcount > 0
        if removed:
            cache_invalidate("premium:ids")
        return removed
    except Exception as e:
        logger.error(f"error removing premium chat {chat_id}: {e}")
        return False


def get_premium_chat_ids():
    cached = cache_get("premium:ids")
    if cached is not None:
        return cached
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT chat_id FROM premium_chats
                    WHERE expires_at IS NULL OR expires_at > NOW()
                    ORDER BY created_at DESC
                """)
                result = [row[0] for row in cur.fetchall()]
                cache_set("premium:ids", result)
                return result
    except Exception as e:
        logger.error(f"error fetching premium chats: {e}")
        return []


def is_premium_chat(chat_id):
    return str(chat_id) in set(get_premium_chat_ids())


def get_premium_alert_mode(chat_id):
    mode = get_bot_state(f"premium_alert_mode_{chat_id}", "all")
    return mode if mode in PREMIUM_ALERT_MODES else "all"


def set_premium_alert_mode(chat_id, mode):
    mode = str(mode or "all").strip().lower()
    if mode not in PREMIUM_ALERT_MODES:
        mode = "all"
    set_bot_state(f"premium_alert_mode_{chat_id}", mode)
    return mode


class SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def get_template(template_key):
    try:
        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("SELECT * FROM message_templates WHERE template_key = %s", (template_key,))
                return cur.fetchone()
    except Exception as e:
        logger.error(f"error fetching template {template_key}: {e}")
        return None


def save_template(template_key, body, rich_body=None):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO message_templates (template_key, body, rich_body, updated_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (template_key)
                    DO UPDATE SET body = EXCLUDED.body, rich_body = EXCLUDED.rich_body, updated_at = NOW()
                """, (template_key, body, rich_body))
        return True
    except Exception as e:
        logger.error(f"error saving template {template_key}: {e}")
        return False


def delete_template(template_key):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM message_templates WHERE template_key = %s", (template_key,))
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"error deleting template {template_key}: {e}")
        return False


def list_templates():
    try:
        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("SELECT * FROM message_templates ORDER BY template_key")
                return cur.fetchall()
    except Exception as e:
        logger.error(f"error listing templates: {e}")
        return []


def simple_rich_markup(text):
    lines = []
    for raw_line in str(text or "").splitlines():
        line = escape_text(raw_line)
        if line.startswith("# "):
            lines.append(f"<h2>{line[2:]}</h2>")
        elif line.startswith("## "):
            lines.append(f"<h3>{line[3:]}</h3>")
        elif line.startswith("&gt; "):
            lines.append(f"<blockquote>{line[5:]}</blockquote>")
        elif line.startswith("- "):
            lines.append(f"<li>{line[2:]}</li>")
        elif line:
            lines.append(f"<p>{line}</p>")
    return "".join(lines).replace("**", "<b>", 1).replace("**", "</b>", 1)


def render_template_text(template_key, context, fallback):
    template = get_template(template_key)
    body = template.get("body") if template else None
    source = body or fallback
    try:
        return str(source).format_map(SafeFormatDict(context))
    except Exception as e:
        logger.error(f"error rendering template {template_key}: {e}")
        return fallback


def render_template_rich(template_key, context, fallback_rich):
    template = get_template(template_key)
    source = (template.get("rich_body") or template.get("body")) if template else None
    if not source:
        return fallback_rich
    try:
        rendered = str(source).format_map(SafeFormatDict(context))
        return rendered if "<" in rendered and ">" in rendered else simple_rich_markup(rendered)
    except Exception as e:
        logger.error(f"error rendering rich template {template_key}: {e}")
        return fallback_rich


def add_custom_button(scope, label, url, sort_order=100):
    scope = str(scope or "").strip().lower()
    label = str(label or "").strip()
    url = str(url or "").strip()
    if scope not in STUDIO_BUTTON_SCOPES:
        logger.warning(f"invalid studio button scope: {scope}")
        return None
    if not label or not url:
        return None
    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("tg://") or "{" in url):
        logger.warning(f"invalid studio button url: {url}")
        return None
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO custom_buttons (scope, label, url, sort_order)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                """, (scope, label, url, sort_order))
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        logger.error(f"error adding button {scope}: {e}")
        return None


def delete_custom_button(button_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM custom_buttons WHERE id = %s", (button_id,))
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"error deleting button {button_id}: {e}")
        return False


def get_custom_buttons(scope=None):
    try:
        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                if scope:
                    cur.execute("""
                        SELECT * FROM custom_buttons
                        WHERE enabled = TRUE AND scope IN ('all', %s)
                        ORDER BY sort_order, id
                    """, (scope,))
                else:
                    cur.execute("SELECT * FROM custom_buttons ORDER BY scope, sort_order, id")
                return cur.fetchall()
    except Exception as e:
        logger.error(f"error fetching buttons: {e}")
        return []


def render_url(url, context):
    try:
        return str(url).format_map(SafeFormatDict(context))
    except Exception:
        return str(url)


def clean_ai_message(message):
    message = message.strip().strip('"').strip("'")
    return html.escape(message[:240])


def escape_text(value):
    return html.escape(str(value or "").strip())


def custom_emoji(key, fallback):
    fallback = get_custom_emoji_fallback(key, fallback)
    emoji_id = get_custom_emoji_id(key)
    if not emoji_id:
        return escape_text(fallback)
    return f'<tg-emoji emoji-id="{emoji_id}">{escape_text(fallback)}</tg-emoji>'


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
            "casual": "sound friendly, warm, and conversational.",
            "urgent": "sound urgent and useful without sounding spammy.",
            "premium": "sound polished, confident, and exclusive.",
            "degen": "sound crypto-native, playful, and sharp, but keep it clean.",
            "professional": "sound clear, professional, and calm.",
        }.get(tone, "sound friendly, warm, and conversational.")

        base_rules = (
            "Write one natural sentence under 190 characters. "
            "Do not mention votes, volume, pools, odds, money, or fake facts. "
            "Do not add hashtags. Do not use quotation marks. "
            "Invite people to share their opinion."
        )

        if notification_type == "new":
            prompt = f"""You are the friendly AI writer for a community B4 opinion market notification bot.
opinion: "{title}"
Tone: {tone_instruction}
{base_rules}"""
        elif notification_type == "1h":
            prompt = f"""You are the friendly AI writer for a community B4 opinion market notification bot.
Write a 1 hour remaining reminder.
opinion: "{title}"
Tone: {tone_instruction}
{base_rules}"""
        elif notification_type == "10m":
            prompt = f"""You are the friendly AI writer for a community B4 opinion market notification bot.
Write a final 10 minute reminder.
opinion: "{title}"
Tone: {tone_instruction}
{base_rules}"""
        else:
            return None

        response = ai_client.chat.completions.create(
            model=get_ai_model(),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50
        )
        
        message = clean_ai_message(response.choices[0].message.content)
        logger.info(f"generated ai notification for {notification_type}: {message[:50]}...")
        return message
    except Exception as e:
        logger.error(f"error generating smart notification: {e}")
        return None


def build_fast_market_cta(title, notification_type="new"):
    """Local market-aware fallback text for fast alerts without an AI network call."""
    clean_title = str(title or "this market").strip().rstrip("?!.")
    variants = {
        "new": [
            "New market is live. What is your read on: {title}?",
            "Fresh poll just opened. Back your opinion on: {title}.",
            "Early eyes on this one: {title}. What side are you taking?",
            "A new debate is open now. Make your call on: {title}.",
            "This market just went live. Pick your side on: {title}.",
        ],
        "1h": [
            "One hour left. If you have a strong view on {title}, now is the time.",
            "This market is closing soon. Final chance to act on: {title}.",
            "One hour remaining for {title}. Make your opinion count.",
        ],
        "10m": [
            "Final 10 minutes. Last call for: {title}.",
            "This market is almost closed. Decide quickly on: {title}.",
            "Last stretch for {title}. Take your side before it closes.",
        ],
    }
    options = variants.get(notification_type, variants["new"])
    index = sum(ord(char) for char in clean_title) % len(options)
    return escape_text(options[index].format(title=clean_title))


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
        cache_invalidate("chats:all")
    except Exception as e:
        logger.error(f"error adding chat: {e}")


def remove_chat(chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM subscribed_chats WHERE chat_id = %s", (str(chat_id),))
        cache_invalidate("chats:all", f"themes:{chat_id}")
    except Exception as e:
        logger.error(f"error removing chat: {e}")


def get_all_chats():
    cached = cache_get("chats:all")
    if cached is not None:
        return cached
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT chat_id FROM subscribed_chats")
                rows = cur.fetchall()
                result = [row[0] for row in rows]
                cache_set("chats:all", result)
                return result
    except Exception as e:
        logger.error(f"error fetching chats: {e}")
        return []


def get_ordered_priority_chat_ids():
    ordered = []
    if ADMIN_ID:
        ordered.append(str(ADMIN_ID))
    for chat_id in PRIORITY_CHAT_IDS:
        chat_id = str(chat_id).strip()
        if chat_id and chat_id not in ordered:
            ordered.append(chat_id)
    return ordered


def get_priority_chat_ids():
    return set(get_ordered_priority_chat_ids())


def prioritize_chats(chats):
    priority_ids = get_priority_chat_ids()
    ordered_priority_ids = get_ordered_priority_chat_ids()
    seen = set()
    priority_map = {}
    normal = []
    for chat_id in chats:
        chat_id_str = str(chat_id)
        if chat_id_str in seen:
            continue
        seen.add(chat_id_str)
        if chat_id_str in priority_ids:
            priority_map[chat_id_str] = chat_id
        else:
            normal.append(chat_id)
    priority = [priority_map[chat_id] for chat_id in ordered_priority_ids if chat_id in priority_map]
    return priority + normal


def get_all_users():
    try:
        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("SELECT * FROM users")
                return cur.fetchall()
    except Exception as e:
        logger.error(f"error fetching users: {e}")
        return []


def describe_user_for_admin(user):
    username = f"@{user.username}" if getattr(user, "username", None) else "No username"
    first_name = getattr(user, "first_name", "") or "No name"
    return (
        f"Name: <b>{escape_text(first_name)}</b>\n"
        f"Username: <b>{escape_text(username)}</b>\n"
        f"Telegram ID: <code>{escape_text(user.id)}</code>"
    )


def notify_admin_premium_event(user, plan, event_title, extra_text=None):
    if not ADMIN_ID:
        return
    plan = normalize_premium_plan(plan)
    price = PREMIUM_PLAN_PRICES.get(plan, PREMIUM_PLAN_PRICES["monthly"])
    text = (
        f"<b>{escape_text(event_title)}</b>\n\n"
        f"{describe_user_for_admin(user)}\n\n"
        f"Plan: <b>{escape_text(plan)}</b> ({escape_text(price)})\n\n"
        "Activate after payment confirmation:\n"
        f"<code>/premium_add {escape_text(user.id)} {escape_text(plan)}</code>"
    )
    if extra_text:
        text += f"\n\n{extra_text}"
    try:
        rich_html = (
            f"<h2>{escape_text(event_title)}</h2>"
            "<table>"
            f"<tr><th>Name</th><td>{escape_text(getattr(user, 'first_name', '') or 'No name')}</td></tr>"
            f"<tr><th>Username</th><td>{escape_text('@' + user.username if getattr(user, 'username', None) else 'No username')}</td></tr>"
            f"<tr><th>Telegram ID</th><td>{escape_text(user.id)}</td></tr>"
            f"<tr><th>Plan</th><td>{escape_text(plan)} ({escape_text(price)})</td></tr>"
            "</table>"
            f"<blockquote>/premium_add {escape_text(user.id)} {escape_text(plan)}</blockquote>"
        )
        if extra_text:
            rich_html += f"<p>{extra_text}</p>"
        send_persistent_rich(ADMIN_ID, rich_html, text)
    except Exception as e:
        logger.error(f"error notifying admin about premium event: {e}")


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


def send_persistent_message(chat_id, text, reply_markup=None, parse_mode=None):
    return bot.send_message(
        chat_id,
        text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )


def send_persistent_rich(chat_id, rich_html, fallback_text, reply_markup=None):
    try:
        message_id = send_rich_message_to_chat(chat_id, rich_html, reply_markup)
        return message_id
    except Exception as e:
        logger.warning(f"persistent rich message failed, falling back to text: {e}")
        sent = send_persistent_message(chat_id, fallback_text, reply_markup=reply_markup, parse_mode="HTML")
        return sent.message_id


def reply_persistent(message, text, reply_markup=None, parse_mode=None):
    return bot.reply_to(message, text, reply_markup=reply_markup, parse_mode=parse_mode)


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


def broadcast_to_all(
    message_text,
    market_id=None,
    keyboard=None,
    theme=None,
    notification_key=None,
    photo_url=None,
    premium_only=False,
    rich_html=None,
    exclude_premium=False,
    premium_filter=None,
):
    try:
        if notification_key and not can_send_notification(notification_key):
            return

        priority_ids = get_priority_chat_ids()
        premium_ids = set(get_premium_chat_ids()) if (premium_only or exclude_premium or premium_filter) else None
        if premium_ids is not None:
            premium_ids.update(priority_ids)
        base_chats = list(get_all_chats())
        if premium_only or premium_filter:
            base_chats.extend(priority_ids)
        chats = [
            chat_id for chat_id in base_chats
            if str(chat_id) in priority_ids or not theme or chat_wants_theme(chat_id, theme)
        ]
        if premium_ids is not None:
            if premium_only:
                chats = [chat_id for chat_id in chats if str(chat_id) in premium_ids]
            if exclude_premium:
                chats = [chat_id for chat_id in chats if str(chat_id) not in premium_ids]
            if premium_filter:
                chats = [chat_id for chat_id in chats if str(chat_id) in priority_ids or premium_filter(chat_id)]
        chats = prioritize_chats(chats)
        sent = 0
        priority_chats = [chat_id for chat_id in chats if str(chat_id) in priority_ids]
        regular_chats = [chat_id for chat_id in chats if str(chat_id) not in priority_ids]

        for chat_id in priority_chats:
            try:
                if send_notification_to_chat(chat_id, message_text, market_id, keyboard, photo_url, rich_html):
                    sent += 1
            except Exception as e:
                logger.error(f"error sending priority notification to {chat_id}: {e}")

        if BROADCAST_WORKERS <= 1 or len(regular_chats) <= 1:
            for chat_id in regular_chats:
                try:
                    if send_notification_to_chat(chat_id, message_text, market_id, keyboard, photo_url, rich_html):
                        sent += 1
                except Exception as e:
                    logger.error(f"error sending to {chat_id}: {e}")
        else:
            with ThreadPoolExecutor(max_workers=BROADCAST_WORKERS) as executor:
                futures = {
                    executor.submit(send_notification_to_chat, chat_id, message_text, market_id, keyboard, photo_url, rich_html): chat_id
                    for chat_id in regular_chats
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
            params={"page": 1, "limit": API_PAGE_LIMIT, "_": int(time.time())},
            headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
            timeout=API_TIMEOUT_SECONDS,
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


INTELLIGENCE_SNAPSHOT_INTERVAL = 300
SCORE_RUN_INTERVAL = 300
_last_score_run = 0
_intelligence_snapshot_cache = {}


def _parse_fingerprint(title):
    title = str(title or "").strip()
    words = title.split()
    opening_word = words[0] if words else ""
    word_count = len(words)
    has_question = "?" in title
    is_yes_no = any(
        title.lower().startswith(p)
        for p in ("is ", "are ", "was ", "were ", "do ", "does ", "did ", "can ", "could ", "should ", "would ", "will ", "has ", "have ")
    )
    return opening_word, word_count, has_question, is_yes_no


def _detect_keywords(title):
    title_lower = str(title or "").lower()
    keywords = []
    keyword_map = {
        "crypto": ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "token", "defi", "nft"],
        "politics": ["election", "president", "vote", "government", "trump", "biden", "congress", "democrat", "republican"],
        "sports": ["nba", "nfl", "mlb", "soccer", "football", "basketball", "tennis", "golf", "championship", "world cup"],
        "entertainment": ["movie", "music", "album", "oscar", "grammy", "netflix", "celebrity", "actor", "singer"],
        "economy": ["inflation", "recession", "stock", "market", "gdp", "interest rate", "unemployment", "trade", "tariff"],
        "science": ["earth", "moon", "mars", "climate", "space", "dna", "virus", "vaccine", "quantum"],
        "food": ["food", "restaurant", "cooking", "recipe", "pizza", "burger", "sushi", "coffee", "beer"],
        "society": ["people", "society", "generation", "youth", "education", "school", "university", "job", "career"],
    }
    for tag, words in keyword_map.items():
        if any(w in title_lower for w in words):
            keywords.append(tag)
    return keywords


def _extract_topic_tags(theme, keywords):
    tags = set()
    if theme and theme != "other":
        tags.add(theme)
    tags.update(keywords)
    return sorted(tags)


def ingest_market(market):
    market_id = str(market.get("market_id", "")).strip()
    if not market_id:
        return
    title = str(market.get("title", "")).strip()
    description = str(market.get("description", "") or "").strip()
    creator = str(market.get("creator", "") or "").strip()
    theme = str(market.get("theme", "other") or "other").strip()
    end_time_unix = market.get("end_time")
    end_time = None
    if end_time_unix:
        try:
            end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc)
        except Exception:
            pass
    go_live_at = parse_api_datetime(market.get("go_live_at"))
    created_at_api = parse_api_datetime(market.get("created_at"))
    updated_at = parse_api_datetime(market.get("updated_at"))
    opening_word, word_count, has_question, is_yes_no = _parse_fingerprint(title)
    keywords = _detect_keywords(title)
    topic_tags = _extract_topic_tags(theme, keywords)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO markets (
                        market_id, title, description, creator_wallet, market_pubkey, theme,
                        end_time, go_live_at, created_at_api, is_private, cover_image_url,
                        first_staker_promo, first_staker_match, first_staker_min, sponsor_count,
                        resolved, outcome, hidden, last_updated_at, last_synced_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, NOW()
                    )
                    ON CONFLICT (market_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        description = EXCLUDED.description,
                        creator_wallet = EXCLUDED.creator_wallet,
                        theme = EXCLUDED.theme,
                        end_time = EXCLUDED.end_time,
                        go_live_at = EXCLUDED.go_live_at,
                        is_private = EXCLUDED.is_private,
                        cover_image_url = EXCLUDED.cover_image_url,
                        first_staker_promo = EXCLUDED.first_staker_promo,
                        first_staker_match = EXCLUDED.first_staker_match,
                        first_staker_min = EXCLUDED.first_staker_min,
                        sponsor_count = EXCLUDED.sponsor_count,
                        resolved = EXCLUDED.resolved,
                        outcome = EXCLUDED.outcome,
                        hidden = EXCLUDED.hidden,
                        last_updated_at = EXCLUDED.last_updated_at,
                        last_synced_at = NOW()
                """, (
                    market_id, title, description, creator or None, market.get("market_pubkey"), theme,
                    end_time, go_live_at, created_at_api, market.get("is_private", False),
                    market.get("cover_image_url"),
                    market.get("first_staker_promo_available", False),
                    market.get("first_staker_match_usdc"),
                    market.get("first_staker_min_stake_usdc"),
                    market.get("sponsor_match_count", 0),
                    market.get("resolved", False), market.get("outcome", 0), market.get("hidden", False),
                    updated_at,
                ))
                if creator:
                    cur.execute("""
                        INSERT INTO creators (wallet_address, last_seen_at, total_markets)
                        VALUES (%s, NOW(), 1)
                        ON CONFLICT (wallet_address) DO UPDATE SET
                            last_seen_at = NOW(),
                            total_markets = creators.total_markets + 1
                    """, (creator,))
                    cur.execute("""
                        INSERT INTO creator_categories (wallet_address, theme, market_count)
                        VALUES (%s, %s, 1)
                        ON CONFLICT (wallet_address, theme) DO UPDATE SET
                            market_count = creator_categories.market_count + 1
                    """, (creator, theme))
                cur.execute("""
                    INSERT INTO market_fingerprints (
                        market_id, opening_word, word_count, has_question_mark,
                        is_yes_no, topic_tags, detected_keywords, category
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (market_id) DO NOTHING
                """, (
                    market_id, opening_word, word_count, has_question,
                    is_yes_no, topic_tags, keywords, theme,
                ))
    except Exception as e:
        logger.error(f"error ingesting market {market_id}: {e}")


def snapshot_market(market_id, market):
    market_id = str(market_id or "").strip()
    if not market_id:
        return
    yes_pool = int(market.get("yes_pool") or 0)
    no_pool = int(market.get("no_pool") or 0)
    yes_votes = int(market.get("yes_votes") or 0)
    no_votes = int(market.get("no_votes") or 0)
    likes = int(market.get("likes_count") or 0)
    comments = int(market.get("comments_count") or 0)
    total_volume = yes_pool + no_pool
    total_participants = yes_votes + no_votes
    controversy = 0.0
    if total_volume > 0:
        controversy = min(yes_pool, no_pool) / max(yes_pool, no_pool) if max(yes_pool, no_pool) > 0 else 0
    avg_stake = 0.0
    if total_participants > 0:
        avg_stake = total_volume / total_participants
    cache_key = f"snapshot:{market_id}"
    last = _intelligence_snapshot_cache.get(cache_key)
    changed = not last or (
        last.get("yes_pool") != yes_pool
        or last.get("no_pool") != no_pool
        or last.get("yes_votes") != yes_votes
        or last.get("no_votes") != no_votes
        or last.get("likes") != likes
        or last.get("comments") != comments
    )
    time_elapsed = not last or (time.time() - last.get("time", 0)) >= INTELLIGENCE_SNAPSHOT_INTERVAL
    if not changed and not time_elapsed:
        return
    _intelligence_snapshot_cache[cache_key] = {
        "yes_pool": yes_pool, "no_pool": no_pool,
        "yes_votes": yes_votes, "no_votes": no_votes,
        "likes": likes, "comments": comments, "time": time.time(),
    }
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO market_snapshots (
                        market_id, yes_pool, no_pool, yes_votes, no_votes,
                        likes_count, comments_count, total_volume, total_participants,
                        controversy_score, avg_stake_size
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    market_id, yes_pool, no_pool, yes_votes, no_votes,
                    likes, comments, total_volume, total_participants,
                    round(controversy, 4), round(avg_stake, 2),
                ))
                if total_volume > 0:
                    cur.execute("""
                        UPDATE creators SET
                            total_volume = creators.total_volume + %s,
                            best_volume = GREATEST(creators.best_volume, %s)
                        WHERE wallet_address = (
                            SELECT creator_wallet FROM markets WHERE market_id = %s
                        )
                    """, (total_volume, total_volume, market_id))
                    cur.execute("""
                        INSERT INTO creator_categories (wallet_address, theme, total_volume)
                        SELECT creator_wallet, theme, %s FROM markets WHERE market_id = %s
                        ON CONFLICT (wallet_address, theme) DO UPDATE SET
                            total_volume = creator_categories.total_volume + EXCLUDED.total_volume
                    """, (total_volume, market_id))
    except Exception as e:
        logger.error(f"error snapshotting market {market_id}: {e}")


def record_market_event(market_id, event_type, event_data=None):
    market_id = str(market_id or "").strip()
    if not market_id:
        return
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO market_events (market_id, event_type, event_data)
                    VALUES (%s, %s, %s)
                """, (market_id, event_type, json.dumps(event_data or {})))
    except Exception as e:
        logger.error(f"error recording event for {market_id}: {e}")


def _detect_market_event(market, announced):
    market_id = str(market.get("market_id", "")).strip()
    if not market_id:
        return
    is_new_market = not announced
    is_now_live = announced and announced.get("is_scheduled") and not announced.get("notified_new") and not is_scheduled_market(market)
    is_ending = False
    try:
        end_time_unix = market.get("end_time")
        if end_time_unix:
            end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None)
            time_until = (end_time - now_utc()).total_seconds()
            if 0 < time_until <= 3600 and not announced.get("notified_1h"):
                is_ending = True
    except Exception:
        pass
    if is_new_market:
        record_market_event(market_id, "market_detected", {
            "title": market.get("title"),
            "creator": market.get("creator"),
            "theme": market.get("theme"),
        })
    if is_now_live:
        record_market_event(market_id, "market_live", {
            "title": market.get("title"),
        })
    if is_ending:
        record_market_event(market_id, "market_ending_1h", {
            "title": market.get("title"),
        })
    if market.get("resolved"):
        record_market_event(market_id, "market_resolved", {
            "outcome": market.get("outcome"),
        })
    if market.get("hidden") and not (announced and announced.get("notified_ended")):
        record_market_event(market_id, "market_hidden", {})


def score_markets():
    global _last_score_run
    now = time.time()
    if now - _last_score_run < SCORE_RUN_INTERVAL:
        return
    _last_score_run = now
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT market_id FROM markets WHERE resolved = FALSE AND hidden = FALSE")
                market_ids = [row[0] for row in cur.fetchall()]
        if not market_ids:
            return
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT market_id, MAX(total_volume) as max_vol, MAX(total_participants) as max_part
                    FROM market_snapshots
                    GROUP BY market_id
                """)
                stats = {row[0]: {"max_vol": float(row[1] or 0), "max_part": float(row[2] or 0)} for row in cur.fetchall()}
        with get_db() as conn:
            with conn.cursor() as cur:
                for market_id in market_ids:
                    cur.execute("""
                        SELECT total_volume, total_participants, controversy_score
                        FROM market_snapshots
                        WHERE market_id = %s
                        ORDER BY snapshot_at DESC
                        LIMIT 1
                    """, (market_id,))
                    row = cur.fetchone()
                    if not row:
                        continue
                    volume = float(row[0] or 0)
                    participants = int(row[1] or 0)
                    controversy = float(row[2] or 0)
                    ms = stats.get(market_id, {})
                    max_vol = ms.get("max_vol", 0)
                    max_part = ms.get("max_part", 0)
                    volume_pct = (volume / max_vol * 100) if max_vol > 0 else 0
                    engagement = (participants / volume * 1000) if volume > 0 else 0
                    cur.execute("""
                        INSERT INTO market_scores (
                            market_id, volume_percentile, controversy, engagement_ratio,
                            composite_score, scored_at
                        ) VALUES (%s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (market_id) DO UPDATE SET
                            volume_percentile = EXCLUDED.volume_percentile,
                            controversy = EXCLUDED.controversy,
                            engagement_ratio = EXCLUDED.engagement_ratio,
                            composite_score = EXCLUDED.composite_score,
                            scored_at = NOW()
                    """, (
                        market_id,
                        round(volume_pct, 2),
                        round(controversy, 4),
                        round(engagement, 4),
                        round((volume_pct * 0.4 + controversy * 100 * 0.3 + engagement * 0.3), 2),
                    ))
        logger.info(f"scored {len(market_ids)} markets")
    except Exception as e:
        logger.error(f"error scoring markets: {e}")


def run_intelligence_pipeline(markets):
    for market in markets:
        try:
            market_id = str(market.get("market_id", "")).strip()
            if not market_id:
                continue
            ingest_market(market)
            snapshot_market(market_id, market)
            existing = None
            try:
                existing = get_announced_market(market_id)
            except Exception:
                pass
            _detect_market_event(market, existing)
        except Exception as e:
            logger.error(f"intelligence pipeline error for {market.get('market_id', '?')}: {e}")


def create_market_keyboard(market_id, market_link, scope="new_market", context=None):
    """create inline buttons for market notifications"""
    context = context or {}
    context.setdefault("market_id", market_id)
    context.setdefault("market_link", market_link)
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Vote Now", url=market_link))
    for button in get_custom_buttons(scope):
        keyboard.add(
            types.InlineKeyboardButton(
                str(button.get("label")),
                url=render_url(button.get("url"), context),
            )
        )
    return keyboard


def create_custom_keyboard(scope="broadcast", context=None):
    """create inline buttons without the default market vote button"""
    context = context or {}
    keyboard = types.InlineKeyboardMarkup()
    has_buttons = False
    for button in get_custom_buttons(scope):
        keyboard.add(
            types.InlineKeyboardButton(
                str(button.get("label")),
                url=render_url(button.get("url"), context),
            )
        )
        has_buttons = True
    return keyboard if has_buttons else None


def create_single_button_keyboard(label, url):
    label = str(label or "").strip()
    url = str(url or "").strip()
    if not label or not url:
        return None
    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("tg://")):
        return None
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(label, url=url))
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


def market_has_first_staker_promo(market):
    return bool(market.get("first_staker_promo_available"))


def market_sponsor_count(market):
    try:
        return int(market.get("sponsor_match_count") or 0)
    except Exception:
        return 0


def market_has_promo(market):
    return market_has_first_staker_promo(market) or market_sponsor_count(market) > 0


def premium_chat_wants_market(chat_id, market):
    mode = get_premium_alert_mode(chat_id)
    if mode == "all":
        return True
    if mode == "promo":
        return market_has_promo(market)
    if mode == "first_staker":
        return market_has_first_staker_promo(market)
    if mode == "sponsor":
        return market_sponsor_count(market) > 0
    return True


def get_premium_market_heading(market):
    if market_has_first_staker_promo(market) and market_sponsor_count(market) > 0:
        return "Premium Promo Signal"
    if market_has_first_staker_promo(market):
        return "First-Staker Signal"
    if market_sponsor_count(market) > 0:
        return "Sponsor Boost Signal"
    return "Premium Priority Alert"


def build_market_template_context(market, ai_message=None):
    market_id = str(market.get("market_id", "")).strip()
    raw_theme = normalize_theme(market.get("theme", "other"))
    end_time_unix = market.get("end_time")
    end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None) if end_time_unix else None
    go_live_at = get_market_go_live_at(market)
    context = {
        "market_id": market_id,
        "market_link": build_market_link(market_id) if market_id else "",
        "title": escape_text(market.get("title", "")),
        "theme": escape_text(format_theme(raw_theme)),
        "raw_theme": escape_text(raw_theme),
        "close_time": escape_text(format_market_time(end_time) if end_time else ""),
        "go_live_time": escape_text(format_market_time(go_live_at) if go_live_at else ""),
        "promo_text": escape_text(build_market_promo_text(market)),
        "ai_text": ai_message or "",
        "cover_image": escape_text(get_market_cover_image(market) or ""),
        "first_staker_match_usdc": escape_text(market.get("first_staker_match_usdc", "")),
        "first_staker_min_stake_usdc": escape_text(market.get("first_staker_min_stake_usdc", "")),
        "sponsor_match_count": escape_text(market.get("sponsor_match_count", "")),
    }
    return context


def build_new_market_notification(market, ai_message):
    title = str(market.get("title", "")).strip()
    end_time_unix = market.get("end_time")
    end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None)
    end_time_str = format_market_time(end_time)
    promo_text = build_market_promo_text(market)
    body_text = ai_message or build_fast_market_cta(title, "new")

    message = (
        f"{custom_emoji('live', '🟢')} <b>LIVE MARKET</b>\n\n"
        f"🔥 <b>{escape_text(title)}</b>\n\n"
        f"⏰ Closes: {escape_text(end_time_str)}"
    )

    if promo_text:
        message += f"\n{promo_text}"

    message += "\n\n"
    message += body_text

    return render_template_text("new_market_text", build_market_template_context(market, body_text), message)


def build_premium_priority_notification(market, ai_message):
    heading = get_premium_market_heading(market)
    base = build_new_market_notification(market, ai_message)
    return (
        f"{custom_emoji('premium', '🛡')} <b>{escape_text(heading.upper())}</b>\n"
        "Premium first-hand notice.\n\n"
        f"{base}"
    )


def build_scheduled_market_notification(market):
    title = str(market.get("title", "")).strip()
    go_live_at = get_market_go_live_at(market)
    go_live_text = format_market_time(go_live_at) if go_live_at else "soon"
    promo_text = build_market_promo_text(market)

    message = (
        f"{custom_emoji('premium', '🛡')} <b>PREMIUM EARLY ACCESS</b>\n\n"
        f"⚡ <b>{escape_text(title)}</b>\n\n"
        f"🚀 Goes Live: <b>{escape_text(go_live_text)}</b>"
    )
    if promo_text:
        message += f"\n{promo_text}"

    message += "\n\nPremium users are seeing this before the public live alert."
    return render_template_text("scheduled_market_text", build_market_template_context(market), message)


def build_go_live_reminder_notification(market_data):
    title = market_data.get("title", "").strip()
    go_live_at = market_data.get("go_live_at")
    if isinstance(go_live_at, datetime):
        go_live_text = format_market_time(go_live_at, include_date=False)
    else:
        parsed = parse_api_datetime(go_live_at)
        go_live_text = format_market_time(parsed, include_date=False) if parsed else "very soon"

    return (
        f"{custom_emoji('premium', '🛡')} <b>PREMIUM 2-MINUTE LIVE REMINDER</b>\n\n"
        f"⚡ <b>{escape_text(title)}</b>\n\n"
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
    go_live_at = get_market_go_live_at(market)
    end_time_unix = market.get("end_time")
    end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None)
    cover_url = get_market_cover_image(market)
    promo_text = build_market_promo_text(market) or "No promo details attached yet."
    go_live_text = format_market_time(go_live_at) if go_live_at else "Soon"

    fallback = (
        f"<h3>{custom_emoji('premium', '🛡')} Premium Early Access</h3>"
        f"{build_rich_media_block(cover_url, title)}"
        f"<h2>{escape_text(title)}</h2>"
        f"<table>"
        f"<tr><th>Goes Live</th><td>{escape_text(go_live_text)}</td></tr>"
        f"<tr><th>Closes</th><td>{escape_text(format_market_time(end_time))}</td></tr>"
        f"</table>"
        f"<blockquote>{escape_text(promo_text)}</blockquote>"
        f"<p>Premium users are seeing this before the public live alert.</p>"
    )
    return render_template_rich("scheduled_market_rich", build_market_template_context(market), fallback)


def build_rich_new_market(market, ai_message, heading="New Market Live", cover_url=None):
    title = str(market.get("title", "")).strip()
    end_time_unix = market.get("end_time")
    end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None)
    cover_url = cover_url or get_market_cover_image(market)
    promo_text = build_market_promo_text(market)
    body_text = ai_message or build_fast_market_cta(title, "new")

    html_parts = [
        f"<h3>{custom_emoji('live', '🟢')} {escape_text(heading)}</h3>",
        build_rich_media_block(cover_url, title),
        f"<h2>{escape_text(title)}</h2>",
        "<table>",
        f"<tr><th>Closes</th><td>{escape_text(format_market_time(end_time))}</td></tr>",
        "</table>",
    ]
    if promo_text:
        html_parts.append(f"<blockquote>{escape_text(promo_text)}</blockquote>")
    html_parts.append(f"<p>{body_text}</p>")
    fallback = "".join(html_parts)
    return render_template_rich("new_market_rich", build_market_template_context(market, body_text), fallback)


def build_rich_reminder(title, minutes_left, ai_message=None, urgent=False):
    heading = "10 Minutes Left" if urgent else "1 Hour Left"
    emoji_key = "ten_minutes" if urgent else "one_hour"
    emoji_fallback = "4️⃣" if urgent else "❌"
    time_text = "10 minutes" if urgent else f"{int(minutes_left)} minutes"
    html_parts = [
        f"<h3>{custom_emoji(emoji_key, emoji_fallback)} {heading}</h3>",
        f"<h2>{escape_text(title)}</h2>",
        "<table>",
        f"<tr><th>Time Left</th><td>{escape_text(time_text)}</td></tr>",
        f"<tr><th>Status</th><td>{'Final call' if urgent else 'Closing soon'}</td></tr>",
        "</table>",
    ]
    if ai_message:
        html_parts.append(f"<blockquote>{ai_message}</blockquote>")
    else:
        html_parts.append("<p>Share your opinion before the market closes.</p>")
    fallback = "".join(html_parts)
    key = "reminder_10m_rich" if urgent else "reminder_1h_rich"
    context = {
        "title": escape_text(title),
        "time_left": escape_text(time_text),
        "ai_text": ai_message or "",
        "status": "Final call" if urgent else "Closing soon",
    }
    return render_template_rich(key, context, fallback)


def build_rich_go_live_reminder(market_data):
    title = market_data.get("title", "").strip()
    go_live_at = market_data.get("go_live_at")
    parsed = go_live_at if isinstance(go_live_at, datetime) else parse_api_datetime(go_live_at)
    go_live_text = format_market_time(parsed, include_date=False) if parsed else "very soon"
    fallback = (
        f"<h3>{custom_emoji('premium', '🛡')} Premium Go-Live Reminder</h3>"
        f"<h2>{escape_text(title)}</h2>"
        "<table>"
        f"<tr><th>Goes Live</th><td>{escape_text(go_live_text)}</td></tr>"
        "<tr><th>Access</th><td>Premium early reminder</td></tr>"
        "</table>"
        "<p>This market is almost live.</p>"
    )
    return render_template_rich(
        "go_live_reminder_rich",
        {"title": escape_text(title), "go_live_time": escape_text(go_live_text)},
        fallback,
    )


def build_rich_market_closed(title):
    fallback = (
        f"<h3>{custom_emoji('ended', '▫️')} Market Ended</h3>"
        f"<h2>{escape_text(title)}</h2>"
        "<table>"
        "<tr><th>Status</th><td>Closed</td></tr>"
        "<tr><th>Messages</th><td>Scheduled for cleanup</td></tr>"
        "</table>"
        "<p>Reward distribution may now be in progress.</p>"
    )
    return render_template_rich("market_closed_rich", {"title": escape_text(title)}, fallback)


def build_rich_image_followup(title, cover_url):
    return (
        "<h2>Market Cover Ready</h2>"
        f"{build_rich_media_block(cover_url, title)}"
        f"<h2>{escape_text(title)}</h2>"
    )


def build_rich_digest():
    markets = get_all_announced_markets()
    active = [
        market for market in markets
        if market.get("notified_new") and not market.get("notified_ended")
    ][:8]
    try:
        boosted = [
            market for market in fetch_b4_markets()
            if is_valid_market(market) and is_market_active(market) and market_has_promo(market)
        ][:8]
    except Exception as e:
        logger.warning(f"could not fetch boosted markets for digest: {e}")
        boosted = []

    stat_rows = (
        f"<tr><td>Boosted</td><td>{len(boosted)}</td></tr>"
        f"<tr><td>Live</td><td>{len(active)}</td></tr>"
        f"<tr><td>Ending Soon</td><td>{len(get_ending_soon_markets())}</td></tr>"
    )

    boosted_items = ""
    for market in boosted[:5]:
        promo_text = build_market_promo_text(market) or "Promo active"
        boosted_items += (
            f"<li><b>{escape_text(market.get('title'))}</b><br/>"
            f"{escape_text(promo_text)}</li>"
        )
    if not boosted_items:
        boosted_items = "<li>No boosted markets visible right now.</li>"

    live_items = ""
    for market in active[:5]:
        live_items += (
            f"<li><b>{escape_text(market.get('title'))}</b><br/>"
            f"Status: Live</li>"
        )
    if not live_items:
        live_items = "<li>No live markets tracked right now.</li>"

    fallback = (
        f"<h2>Daily B4 Market Digest</h2>"
        f"<table><tr><th>Category</th><th>Total</th></tr>{stat_rows}</table>"
        f"<h3>Boosted / Promo</h3><ul>{boosted_items}</ul>"
        f"<h3>Live Now</h3><ul>{live_items}</ul>"
    )
    return render_template_rich(
        "daily_summary_rich",
        {
            "scheduled_count": 0,
            "boosted_count": len(boosted),
            "live_count": len(active),
            "ending_soon_count": len(get_ending_soon_markets()),
            "scheduled_items": boosted_items,
            "boosted_items": boosted_items,
            "live_items": live_items,
        },
        fallback,
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
    for market in markets[:8]:
        status = "Scheduled" if market.get("is_scheduled") and not market.get("notified_new") else "Live"
        rows += (
            f"<tr><td>{escape_text(market.get('title'))}</td>"
            f"<td>{escape_text(status)}</td></tr>"
        )
    if not rows:
        rows = "<tr><td>No markets yet</td><td>-</td></tr>"
    return f"<h2>Recent Announced Markets</h2><table><tr><th>Market</th><th>Status</th></tr>{rows}</table>"


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
                    f"<b>?? {escape_text(title)}</b>"
                )
                broadcast_to_all(
                    message,
                    market_id,
                    create_market_keyboard(market_id, build_market_link(market_id), scope="image_followup"),
                    theme=theme,
                    notification_key=f"image_followup_{market_id}",
                    photo_url=cover_url,
                    rich_html=build_rich_image_followup(title, cover_url),
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


def build_main_menu_keyboard(user_id=None, chat_type=None):
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(types.KeyboardButton("Status"))
    keyboard.add(
        types.KeyboardButton("Ending Soon"),
        types.KeyboardButton("Preferences"),
    )
    keyboard.add(
        types.KeyboardButton("Recent"),
        types.KeyboardButton("Daily Summary"),
    )
    keyboard.add(
        types.KeyboardButton("Help"),
        types.KeyboardButton("My ID"),
    )
    if chat_type == "private":
        keyboard.add(types.KeyboardButton("Upgrade"))
        if user_id and is_premium_chat(user_id):
            keyboard.add(types.KeyboardButton("Premium Filters"))
    if chat_type == "private" and user_id and is_admin(user_id):
        keyboard.add(types.KeyboardButton("Admin"))
    return keyboard


def build_upgrade_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton(f"Monthly {PREMIUM_PLAN_PRICES['monthly']}", callback_data="upgrade_monthly"),
        types.InlineKeyboardButton(f"3 Months {PREMIUM_PLAN_PRICES['3months']}", callback_data="upgrade_3months"),
        types.InlineKeyboardButton(f"6 Months {PREMIUM_PLAN_PRICES['6months']}", callback_data="upgrade_6months"),
        types.InlineKeyboardButton(f"1 Year {PREMIUM_PLAN_PRICES['yearly']}", callback_data="upgrade_yearly"),
    )
    keyboard.add(types.InlineKeyboardButton("Back to Menu", callback_data="upgrade_back"))
    return keyboard


def build_premium_filters_keyboard(chat_id):
    current = get_premium_alert_mode(chat_id)
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for mode, label in PREMIUM_ALERT_MODES.items():
        prefix = "✓ " if mode == current else ""
        keyboard.add(types.InlineKeyboardButton(f"{prefix}{label}", callback_data=f"premium_mode_{mode}"))
    keyboard.add(types.InlineKeyboardButton("Back to Menu", callback_data="upgrade_back"))
    return keyboard


def get_premium_filters_text(chat_id):
    current = get_premium_alert_mode(chat_id)
    return (
        f"{custom_emoji('premium', '🛡')} <b>Premium Alert Filters</b>\n\n"
        "Choose which priority alerts this private chat should receive.\n\n"
        f"Current mode: <b>{escape_text(PREMIUM_ALERT_MODES.get(current, PREMIUM_ALERT_MODES['all']))}</b>\n\n"
        "This only controls premium priority alerts. Public alerts still follow the normal bot rules."
    )


def get_upgrade_text(selected_plan=None, user_id=None):
    wallet = get_premium_wallet()
    plan_lines = [
        f"Monthly - {PREMIUM_PLAN_PRICES['monthly']}",
        f"3 Months - {PREMIUM_PLAN_PRICES['3months']}",
        f"6 Months - {PREMIUM_PLAN_PRICES['6months']}",
        f"1 Year - {PREMIUM_PLAN_PRICES['yearly']}",
    ]
    text = (
        "<b>B4 Premium Access</b>\n\n"
        "Premium is for users who want faster, cleaner market signals before the crowd reacts.\n\n"
        "<b>What you get</b>\n"
        "1. Priority new-market alerts before the public alert goes out\n"
        "2. First-hand notice for first-staker promos when available\n"
        "3. Sponsor boost alerts when a boosted market appears\n"
        "4. Premium filters: all alerts, promo-only, first-staker-only, or sponsor-only\n"
        "5. Premium digest focused on boosted and live opportunities\n"
        "6. Priority access to new premium alert features as they are added\n\n"
        "<b>Plans</b>\n"
        + "\n".join(plan_lines)
        + "\n\n<b>Payment</b>\nUSDC on Solana network\n"
    )
    if wallet:
        text += f"<code>{escape_text(wallet)}</code>\n\n"
    else:
        text += "<i>Payment address is being updated by admin.</i>\n\n"
    if selected_plan:
        plan = normalize_premium_plan(selected_plan)
        text += f"Selected plan: <b>{escape_text(plan)}</b> ({PREMIUM_PLAN_PRICES.get(plan, '$3')})\n\n"
    if user_id:
        text += f"Your Telegram ID: <code>{escape_text(user_id)}</code>\n\n"
    text += escape_text(PREMIUM_PAYMENT_TEXT)
    return text


def get_upgrade_rich(selected_plan=None, user_id=None):
    wallet = get_premium_wallet()
    plan = normalize_premium_plan(selected_plan) if selected_plan else None
    selected_row = ""
    if plan:
        selected_row = f"<tr><th>Selected</th><td>{escape_text(plan)} ({escape_text(PREMIUM_PLAN_PRICES.get(plan, '$3'))})</td></tr>"
    user_row = f"<tr><th>Your ID</th><td>{escape_text(user_id)}</td></tr>" if user_id else ""
    wallet_html = (
        f"<blockquote>{escape_text(wallet)}</blockquote>"
        if wallet else
        "<blockquote>Payment address is being updated by admin.</blockquote>"
    )
    return (
        "<h2>B4 Premium Access</h2>"
        "<p>Get faster, cleaner market signals before public alerts go out.</p>"
        "<h3>Benefits</h3>"
        "<ul>"
        "<li>Priority new-market alerts before the public alert goes out</li>"
        "<li>First-hand first-staker promo notices when available</li>"
        "<li>Sponsor boost alerts when a boosted market appears</li>"
        "<li>Premium filters for all alerts, promo-only, first-staker-only, or sponsor-only</li>"
        "<li>Premium digest focused on boosted and live opportunities</li>"
        "<li>Priority access to new premium alert features</li>"
        "</ul>"
        "<h3>Plans</h3>"
        "<table>"
        f"<tr><th>Monthly</th><td>{escape_text(PREMIUM_PLAN_PRICES['monthly'])}</td></tr>"
        f"<tr><th>3 Months</th><td>{escape_text(PREMIUM_PLAN_PRICES['3months'])}</td></tr>"
        f"<tr><th>6 Months</th><td>{escape_text(PREMIUM_PLAN_PRICES['6months'])}</td></tr>"
        f"<tr><th>1 Year</th><td>{escape_text(PREMIUM_PLAN_PRICES['yearly'])}</td></tr>"
        f"{selected_row}{user_row}"
        "</table>"
        "<h3>Payment</h3>"
        "<p>Pay USDC on Solana network to:</p>"
        f"{wallet_html}"
        f"<p>{escape_text(PREMIUM_PAYMENT_TEXT)}</p>"
    )


def build_admin_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("Pause", callback_data="admin_pause"),
        types.InlineKeyboardButton("Resume", callback_data="admin_resume"),
        types.InlineKeyboardButton("Test", callback_data="admin_test"),
        types.InlineKeyboardButton("Clean", callback_data="admin_clean"),
        types.InlineKeyboardButton("Stats", callback_data="admin_stats"),
        types.InlineKeyboardButton("Tone", callback_data="admin_tone"),
        types.InlineKeyboardButton("Studio", callback_data="studio_menu"),
        types.InlineKeyboardButton("Premium", callback_data="admin_premium"),
        types.InlineKeyboardButton("AI", callback_data="admin_ai"),
        types.InlineKeyboardButton("Emojis", callback_data="admin_emojis"),
    )
    return keyboard


def build_studio_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("Templates", callback_data="studio_templates"),
        types.InlineKeyboardButton("Buttons", callback_data="studio_buttons"),
        types.InlineKeyboardButton("Preview", callback_data="studio_preview"),
        types.InlineKeyboardButton("Commands", callback_data="studio_commands"),
        types.InlineKeyboardButton("Admin", callback_data="admin_menu"),
    )
    return keyboard


def get_studio_text():
    return (
        "<b>Admin Message Studio</b>\n\n"
        "Edit bot wording, rich text layouts, inline buttons, social links, and broadcast helpers without touching code.\n\n"
        "<b>Useful placeholders</b>\n"
        "<code>{title}</code>, <code>{theme}</code>, <code>{close_time}</code>, <code>{go_live_time}</code>, "
        "<code>{market_link}</code>, <code>{promo_text}</code>, <code>{ai_text}</code>, <code>{cover_image}</code>\n\n"
        "Use /studio_commands to see editing commands."
    )


def get_studio_commands_text():
    return (
        "<b>Studio Commands</b>\n\n"
        "<b>Templates</b>\n"
        "<code>/settemplate key | message</code>\n"
        "<code>/setrich key | rich message</code>\n"
        "<code>/deltemplate key</code>\n"
        "<code>/templates</code>\n\n"
        "<b>Inline Buttons</b>\n"
        "<code>/addbutton scope | label | url</code>\n"
        "<code>/delbutton id</code>\n"
        "<code>/buttons</code>\n\n"
        "<b>Broadcast</b>\n"
        "<code>/studiobroadcast all | message</code>\n"
        "<code>/studiobroadcast premium | message</code>\n\n"
        "<b>Easy Broadcast With One Button</b>\n"
        "<code>/quickbroadcast Button Text | Button Link | Message</code>\n\n"
        "<b>Scopes</b>\n"
        "<code>all</code>, <code>new_market</code>, <code>scheduled_market</code>, "
        "<code>reminder_1h</code>, <code>reminder_10m</code>, <code>go_live_reminder</code>, "
        "<code>image_followup</code>, <code>broadcast</code>\n\n"
        "<b>Examples</b>\n"
        "<code>/addbutton all | Follow X | https://x.com/yourname</code>\n"
        "<code>/addbutton new_market | Community | https://t.me/yourgroup</code>\n"
        "<code>/addbutton broadcast | Follow X | https://x.com/yourname</code>\n"
        "<code>/quickbroadcast Follow us on X | https://x.com/yourname | Big update today!</code>\n"
        "<code>/settemplate new_market_text | NEW MARKET: {title}\\n\\n{ai_text}</code>"
    )


def get_ai_settings_text():
    status = "active" if ai_client else "not configured"
    return (
        "<b>AI Settings</b>\n\n"
        f"Status: <b>{escape_text(status)}</b>\n"
        f"Current model: <code>{escape_text(get_ai_model())}</code>\n"
        f"Tone: <b>{escape_text(get_ai_tone().title())}</b>\n\n"
        "Free Groq model now recommended:\n"
        "<code>openai/gpt-oss-20b</code>\n\n"
        "Change model:\n"
        "<code>/aimodel openai/gpt-oss-20b</code>\n\n"
        "Change tone:\n"
        "<code>/tone</code>\n\n"
        "Keep API keys in Railway only."
    )


def get_emoji_settings_text():
    lines = [
        "<b>Custom Emoji Settings</b>",
        "",
        "Change any notification emoji without editing code.",
        "",
    ]
    for key in sorted(VALID_CUSTOM_EMOJI_KEYS):
        emoji_id = get_custom_emoji_id(key) or "disabled"
        fallback = get_custom_emoji_fallback(key)
        lines.append(f"<code>{escape_text(key)}</code>: <code>{escape_text(emoji_id)}</code> | fallback: <code>{escape_text(fallback)}</code>")
    lines.extend([
        "",
        "Set emoji:",
        "<code>/setemoji live 5416081784641168838</code>",
        "",
        "Set emoji with fallback text:",
        "<code>/setemoji premium 5251203410396458957 PREMIUM</code>",
        "",
        "Disable custom emoji:",
        "<code>/setemoji live none LIVE</code>",
        "",
        "Keys: <code>live</code>, <code>premium</code>, <code>one_hour</code>, <code>ten_minutes</code>, <code>ended</code>",
    ])
    return "\n".join(lines)


def get_templates_text():
    templates = list_templates()
    if not templates:
        return "No custom templates saved yet.\n\nUse /settemplate or /setrich to add one."
    lines = ["<b>Saved Templates</b>"]
    for template in templates:
        lines.append(
            f"\n<code>{escape_text(template.get('template_key'))}</code>\n"
            f"Text: {'yes' if template.get('body') else 'no'} | Rich: {'yes' if template.get('rich_body') else 'no'}"
        )
    return "\n".join(lines)


def get_buttons_text():
    buttons = get_custom_buttons()
    if not buttons:
        return "No custom buttons saved yet.\n\nUse /addbutton scope | label | url"
    lines = ["<b>Custom Buttons</b>"]
    for button in buttons:
        lines.append(
            f"\nID <code>{button.get('id')}</code> | <b>{escape_text(button.get('scope'))}</b>\n"
            f"{escape_text(button.get('label'))}\n"
            f"<code>{escape_text(button.get('url'))}</code>"
        )
    return "\n".join(lines)


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
    active = [
        market for market in markets
        if market.get("notified_new") and not market.get("notified_ended")
    ]
    try:
        boosted = [
            market for market in fetch_b4_markets()
            if is_valid_market(market) and is_market_active(market) and market_has_promo(market)
        ]
    except Exception as e:
        logger.warning(f"could not fetch boosted markets for premium digest: {e}")
        boosted = []

    lines = [
        f"{custom_emoji('premium', '*')} <b>Premium B4 Market Digest</b>",
        "",
        f"Boosted opportunities visible: <b>{len(boosted)}</b>",
        f"Live markets tracked: <b>{len(active)}</b>",
    ]

    if boosted:
        lines.append("\n<b>Boosted / Promo Now</b>")
        for market in boosted[:5]:
            promo_text = build_market_promo_text(market) or "Promo active"
            lines.append(f"- <b>{escape_text(market.get('title'))}</b>\n{escape_text(promo_text)}")

    if active:
        lines.append("\n<b>Live Now</b>")
        for market in active[:5]:
            lines.append(f"- {escape_text(market.get('title'))}")

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
    context = build_market_template_context(market, None)
    keyboard = create_market_keyboard(market_id, build_market_link(market_id), scope="new_market", context=context)

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

    premium_ai_message = generate_smart_notification(title, raw_theme, "new") if AI_ON_PREMIUM_FAST_ALERT else None
    premium_notification = build_premium_priority_notification(market, premium_ai_message)
    broadcast_to_all(
        premium_notification,
        market_id,
        keyboard,
        theme=raw_theme,
        notification_key=f"premium_new_{market_id}",
        premium_only=True,
        premium_filter=lambda chat_id: premium_chat_wants_market(chat_id, market),
    )

    def send_public_alert():
        ai_message = generate_smart_notification(title, raw_theme, "new")
        notification = build_new_market_notification(market, ai_message)
        cover_image_url = wait_for_market_cover_image(market_id, market)
        if PUBLIC_ALERT_DELAY_SECONDS > 0:
            time.sleep(PUBLIC_ALERT_DELAY_SECONDS)
        broadcast_to_all(
            notification,
            market_id,
            keyboard,
            theme=raw_theme,
            notification_key=f"public_new_{market_id}",
            photo_url=cover_image_url,
            rich_html=build_rich_new_market(market, ai_message, cover_url=cover_image_url),
            exclude_premium=True,
        )

    Thread(target=send_public_alert, daemon=True).start()
    set_bot_state("last_market_detected", f"{market_id} | {title}")
    if not get_market_cover_image(market):
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

    context = build_market_template_context(market)
    keyboard = create_market_keyboard(market_id, build_market_link(market_id), scope="scheduled_market", context=context)
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
            try:
                run_intelligence_pipeline(markets)
                score_markets()
            except Exception as e:
                logger.error(f"intelligence pipeline error: {e}")
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
                                create_market_keyboard(market_id, market_link, scope="go_live_reminder"),
                                theme=raw_theme,
                                notification_key=f"go_live_2m_{market_id}",
                                premium_only=True,
                                rich_html=build_rich_go_live_reminder(market_data),
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
                                f"{custom_emoji('one_hour', '!')} <b>1 HOUR LEFT</b>\n\n"
                                f"<b>{escape_text(title)}</b>\n\n"
                                f"Time Remaining: <b>{mins_left} Minutes</b>\n\n"
                                f"{ai_message}"
                            )
                        else:
                            notification = (
                                f"{custom_emoji('one_hour', '!')} <b>1 HOUR LEFT</b>\n\n"
                                f"<b>{escape_text(title)}</b>\n\n"
                                f"Time Remaining: <b>{mins_left} Minutes</b>\n\n"
                                f"This is your last chance to stake!"
                            )
                        
                        keyboard = create_market_keyboard(market_id, market_link, scope="reminder_1h")
                        broadcast_to_all(
                            notification,
                            market_id,
                            keyboard,
                            theme=raw_theme,
                            notification_key=f"1h_{market_id}",
                            rich_html=build_rich_reminder(title, mins_left, ai_message, urgent=False),
                        )
                        update_market_flag(market_id, "notified_1h")
                        logger.info(f"1 hour reminder sent for: {title}")

                    elif minutes_until <= 10.0 and not market_data.get("notified_5m"):
                        
                        # try ai message
                        ai_message = generate_smart_notification(title, market_data.get("theme", "other"), "10m")
                        market_link = market_data.get("market_link", build_market_link(market_id))
                        
                        if ai_message:
                            notification = (
                                f"{custom_emoji('ten_minutes', '4')} <b>10 MINUTES LEFT</b>\n\n"
                                f"<b>{escape_text(title)}</b>\n\n"
                                f"{ai_message}"
                            )
                        else:
                            notification = (
                                f"{custom_emoji('ten_minutes', '4')} <b>10 MINUTES LEFT</b>\n\n"
                                f"<b>{escape_text(title)}</b>\n\n"
                                f"Time Remaining: <b>10 Minutes</b>\n\n"
                                f"Act Now Or Lose This Opportunity!"
                            )
                        
                        keyboard = create_market_keyboard(market_id, market_link, scope="reminder_10m")
                        broadcast_to_all(
                            notification,
                            market_id,
                            keyboard,
                            theme=raw_theme,
                            notification_key=f"10m_{market_id}",
                            rich_html=build_rich_reminder(title, 10, ai_message, urgent=True),
                        )
                        update_market_flag(market_id, "notified_5m")
                        logger.info(f"10 minute reminder sent for: {title}")

                else:
                    notification = (
                        f"{custom_emoji('ended', '.')} <b>MARKET ENDED</b>\n\n"
                        f"<b>{escape_text(title)}</b>\n\n"
                        f"Reward Distribution In Progress\n"
                        f"Check Your Wallet For Returns!\n\n"
                        f"🗑️ This message will be deleted in 10 minutes"
                    )
                    broadcast_to_all(
                        notification,
                        market_id,
                        theme=raw_theme,
                        notification_key=f"closed_{market_id}",
                        rich_html=build_rich_market_closed(title),
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

        if data.startswith("admin_") or data.startswith("tone_") or data.startswith("studio_"):
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
        elif data == "studio_menu":
            delete_callback_message(call)
            send_temp_message(call.message.chat.id, get_studio_text(), reply_markup=build_studio_keyboard(), parse_mode="HTML")
        elif data == "studio_templates":
            delete_callback_message(call)
            send_temp_message(call.message.chat.id, get_templates_text(), reply_markup=build_studio_keyboard(), parse_mode="HTML")
        elif data == "studio_buttons":
            delete_callback_message(call)
            send_temp_message(call.message.chat.id, get_buttons_text(), reply_markup=build_studio_keyboard(), parse_mode="HTML")
        elif data == "studio_commands":
            delete_callback_message(call)
            send_temp_message(call.message.chat.id, get_studio_commands_text(), reply_markup=build_studio_keyboard(), parse_mode="HTML")
        elif data == "studio_preview":
            delete_callback_message(call)
            send_studio_preview(call.message.chat.id)
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
        elif data == "admin_premium":
            delete_callback_message(call)
            wallet = get_premium_wallet() or "Not set"
            send_temp_message(
                call.message.chat.id,
                "<b>Premium Admin</b>\n\n"
                f"USDC Solana wallet:\n<code>{escape_text(wallet)}</code>\n\n"
                "Set wallet:\n<code>/setpremiumwallet YOUR_SOL_USDC_ADDRESS</code>\n\n"
                "Activate user:\n"
                "<code>/premium_add USER_ID monthly</code>\n"
                "<code>/premium_add USER_ID 3months</code>\n"
                "<code>/premium_add USER_ID 6months</code>\n"
                "<code>/premium_add USER_ID yearly</code>",
                reply_markup=build_admin_keyboard(),
                parse_mode="HTML"
            )
        elif data == "admin_tone":
            delete_callback_message(call)
            send_temp_message(
                call.message.chat.id,
                f"🎛 <b>AI Tone</b>\n\nCurrent tone: <b>{get_ai_tone().title()}</b>",
                reply_markup=build_tone_keyboard(),
                parse_mode="HTML"
            )
        elif data == "admin_ai":
            delete_callback_message(call)
            send_temp_message(
                call.message.chat.id,
                get_ai_settings_text(),
                reply_markup=build_admin_keyboard(),
                parse_mode="HTML"
            )
        elif data == "admin_emojis":
            delete_callback_message(call)
            send_temp_message(
                call.message.chat.id,
                get_emoji_settings_text(),
                reply_markup=build_admin_keyboard(),
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
        elif data.startswith("upgrade_"):
            if call.message.chat.type != "private":
                answer("open the bot privately to upgrade", show_alert=True)
                return
            if data == "upgrade_back":
                delete_callback_message(call)
                send_persistent_message(
                    call.message.chat.id,
                    "Menu is available below. Choose what you need next.",
                    reply_markup=build_main_menu_keyboard(call.from_user.id, call.message.chat.type),
                    parse_mode="HTML",
                )
                return
            plan = data.replace("upgrade_", "")
            set_user_premium_interest(call.from_user.id, plan)
            notify_admin_premium_event(call.from_user, plan, "Premium Plan Selected")
            delete_callback_message(call)
            send_persistent_rich(
                reply_markup=build_upgrade_keyboard(),
                chat_id=call.message.chat.id,
                rich_html=get_upgrade_rich(plan, call.from_user.id),
                fallback_text=get_upgrade_text(plan, call.from_user.id),
            )
        elif data.startswith("premium_mode_"):
            if call.message.chat.type != "private":
                answer("open the bot privately to manage premium filters", show_alert=True)
                return
            if not is_premium_chat(call.from_user.id):
                answer("premium users only", show_alert=True)
                return
            mode = data.replace("premium_mode_", "")
            set_premium_alert_mode(call.from_user.id, mode)
            delete_callback_message(call)
            send_persistent_rich(
                chat_id=call.message.chat.id,
                rich_html=get_premium_filters_text(call.from_user.id),
                fallback_text=get_premium_filters_text(call.from_user.id),
                reply_markup=build_premium_filters_keyboard(call.from_user.id),
            )
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
            "/recent - Show Recent Announced Markets\n"
            "/summary - Show Daily Market Summary\n"
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


@bot.message_handler(commands=['upgrade'])
def upgrade_command(message):
    try:
        save_user(message)
        if message.chat.type != "private":
            reply_temp(message, "Please open the bot in private chat to view premium plans.")
            return
        notify_admin_premium_event(message.from_user, "monthly", "Premium Upgrade Viewed")
        send_persistent_rich(
            message.chat.id,
            get_upgrade_rich(user_id=message.from_user.id),
            get_upgrade_text(user_id=message.from_user.id),
            reply_markup=build_upgrade_keyboard(),
        )
    except Exception as e:
        logger.error(f"error in upgrade: {e}")


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


def send_studio_preview(chat_id):
    markets = [market for market in fetch_b4_markets() if is_valid_market(market) and is_market_active(market)]
    if not markets:
        send_temp_message(chat_id, "No active API market available for preview.", parse_mode="HTML")
        return
    market = markets[0]
    market_id = str(market.get("market_id", "")).strip()
    raw_theme = normalize_theme(market.get("theme", "other"))
    ai_message = generate_smart_notification(str(market.get("title", "")).strip(), raw_theme, "new")
    context = build_market_template_context(market, ai_message)
    keyboard = create_market_keyboard(market_id, build_market_link(market_id), scope="new_market", context=context)
    send_notification_to_chat(
        chat_id,
        build_new_market_notification(market, ai_message),
        keyboard=keyboard,
        photo_url=get_market_cover_image(market),
        rich_html=build_rich_new_market(market, ai_message, heading="Studio Preview"),
    )


@bot.message_handler(commands=['studio'])
def studio_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "Permission denied.")
            return
        reply_temp(message, get_studio_text(), reply_markup=build_studio_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in studio: {e}")


@bot.message_handler(commands=['studio_commands'])
def studio_commands_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "Permission denied.")
            return
        reply_temp(message, get_studio_commands_text(), reply_markup=build_studio_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in studio_commands: {e}")


@bot.message_handler(commands=['ai', 'aistatus'])
def ai_status_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "Permission denied.")
            return
        reply_temp(message, get_ai_settings_text(), reply_markup=build_admin_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in ai status command: {e}")


@bot.message_handler(commands=['aimodel'])
def ai_model_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "Permission denied.")
            return
        parts = str(message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            reply_temp(message, get_ai_settings_text(), reply_markup=build_admin_keyboard(), parse_mode="HTML")
            return
        model = parts[1].strip()
        if not set_ai_model(model):
            reply_temp(
                message,
                "Invalid model name.\n\nExample:\n<code>/aimodel openai/gpt-oss-20b</code>",
                parse_mode="HTML"
            )
            return
        reply_temp(
            message,
            f"<b>AI model updated</b>\n\nCurrent model: <code>{escape_text(get_ai_model())}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"error in aimodel command: {e}")


@bot.message_handler(commands=['emojis'])
def emojis_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "Permission denied.")
            return
        reply_temp(message, get_emoji_settings_text(), reply_markup=build_admin_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in emojis command: {e}")


@bot.message_handler(commands=['setemoji'])
def setemoji_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "Permission denied.")
            return
        parts = str(message.text or "").split(maxsplit=3)
        if len(parts) < 3:
            reply_temp(
                message,
                "Use:\n<code>/setemoji key custom_emoji_id fallback</code>\n\n"
                "Example:\n<code>/setemoji live 5416081784641168838 LIVE</code>\n\n"
                "Disable:\n<code>/setemoji live none LIVE</code>\n\n"
                "Keys: <code>live</code>, <code>premium</code>, <code>one_hour</code>, <code>ten_minutes</code>, <code>ended</code>",
                parse_mode="HTML"
            )
            return
        key = parts[1].strip().lower()
        emoji_id = parts[2].strip()
        fallback = parts[3].strip() if len(parts) > 3 else None
        if not set_custom_emoji(key, emoji_id, fallback):
            reply_temp(
                message,
                "Invalid emoji setting.\n\n"
                "The key must be one of: <code>live</code>, <code>premium</code>, <code>one_hour</code>, <code>ten_minutes</code>, <code>ended</code>.\n"
                "The emoji ID must be numbers only, or <code>none</code>.",
                parse_mode="HTML"
            )
            return
        reply_temp(
            message,
            f"<b>Emoji updated</b>\n\n<code>{escape_text(key)}</code>: {custom_emoji(key, get_custom_emoji_fallback(key))}\n"
            f"ID: <code>{escape_text(get_custom_emoji_id(key) or 'disabled')}</code>\n"
            f"Fallback: <code>{escape_text(get_custom_emoji_fallback(key))}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"error in setemoji command: {e}")


@bot.message_handler(commands=['templates'])
def templates_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "Permission denied.")
            return
        reply_temp(message, get_templates_text(), reply_markup=build_studio_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in templates: {e}")


@bot.message_handler(commands=['settemplate'])
def settemplate_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "Permission denied.")
            return
        payload = message.text.split(maxsplit=1)
        if len(payload) < 2 or "|" not in payload[1]:
            reply_temp(message, "Format: /settemplate key | message")
            return
        key, body = [part.strip() for part in payload[1].split("|", 1)]
        body = body.replace("\\n", "\n")
        existing = get_template(key)
        rich_body = existing.get("rich_body") if existing else None
        if save_template(key, body, rich_body):
            reply_temp(message, f"Saved text template <code>{escape_text(key)}</code>.", parse_mode="HTML")
        else:
            reply_temp(message, "Could not save template.")
    except Exception as e:
        logger.error(f"error in settemplate: {e}")


@bot.message_handler(commands=['setrich'])
def setrich_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "Permission denied.")
            return
        payload = message.text.split(maxsplit=1)
        if len(payload) < 2 or "|" not in payload[1]:
            reply_temp(message, "Format: /setrich key | rich message")
            return
        key, rich_body = [part.strip() for part in payload[1].split("|", 1)]
        existing = get_template(key)
        body = existing.get("body") if existing else rich_body
        rich_body = rich_body.replace("\\n", "\n")
        if save_template(key, body, rich_body):
            reply_temp(message, f"Saved rich template <code>{escape_text(key)}</code>.", parse_mode="HTML")
        else:
            reply_temp(message, "Could not save rich template.")
    except Exception as e:
        logger.error(f"error in setrich: {e}")


@bot.message_handler(commands=['deltemplate'])
def deltemplate_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "Permission denied.")
            return
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            reply_temp(message, "Format: /deltemplate key")
            return
        deleted = delete_template(args[1].strip())
        reply_temp(message, "Template deleted." if deleted else "Template not found.")
    except Exception as e:
        logger.error(f"error in deltemplate: {e}")


@bot.message_handler(commands=['buttons'])
def buttons_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "Permission denied.")
            return
        reply_temp(message, get_buttons_text(), reply_markup=build_studio_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in buttons: {e}")


@bot.message_handler(commands=['addbutton'])
def addbutton_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "Permission denied.")
            return
        payload = message.text.split(maxsplit=1)
        if len(payload) < 2:
            reply_temp(message, "Format: /addbutton scope | label | url")
            return
        parts = [part.strip() for part in payload[1].split("|")]
        if len(parts) < 3:
            reply_temp(message, "Format: /addbutton scope | label | url")
            return
        button_id = add_custom_button(parts[0], parts[1], parts[2])
        if button_id:
            reply_temp(message, f"Button saved with ID <code>{button_id}</code>.", parse_mode="HTML")
        else:
            reply_temp(message, "Could not save button. Check the scope and URL.")
    except Exception as e:
        logger.error(f"error in addbutton: {e}")


@bot.message_handler(commands=['delbutton'])
def delbutton_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "Permission denied.")
            return
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            reply_temp(message, "Format: /delbutton id")
            return
        deleted = delete_custom_button(int(args[1].strip()))
        reply_temp(message, "Button deleted." if deleted else "Button not found.")
    except Exception as e:
        logger.error(f"error in delbutton: {e}")


@bot.message_handler(commands=['studiobroadcast'])
def studiobroadcast_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "Permission denied.")
            return
        payload = message.text.split(maxsplit=1)
        if len(payload) < 2 or "|" not in payload[1]:
            reply_temp(message, "Format: /studiobroadcast all | message")
            return

        audience, body = [part.strip() for part in payload[1].split("|", 1)]
        audience = audience.lower()
        if audience not in {"all", "premium"}:
            reply_temp(message, "Audience must be all or premium.")
            return

        rich_html = simple_rich_markup(body.replace("\\n", "\n"))
        text = escape_text(body.replace("\\n", "\n"))
        keyboard = create_custom_keyboard("broadcast", {"audience": audience})
        broadcast_to_all(
            text,
            keyboard=keyboard,
            notification_key=f"studio_broadcast_{int(time.time())}",
            premium_only=audience == "premium",
            rich_html=rich_html,
        )
        reply_temp(message, f"Studio broadcast sent to <b>{escape_text(audience)}</b> subscribers.", parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in studiobroadcast: {e}")
        reply_temp(message, f"Error: {e}")


def handle_quick_broadcast(message, premium_only=False):
    if not is_admin(message.from_user.id):
        reply_temp(message, "Permission denied.")
        return

    payload = message.text.split(maxsplit=1)
    command_name = "quickpremium" if premium_only else "quickbroadcast"
    if len(payload) < 2:
        reply_temp(message, f"Format: /{command_name} Button Text | Button Link | Message")
        return

    parts = [part.strip() for part in payload[1].split("|", 2)]
    if len(parts) < 3:
        reply_temp(message, f"Format: /{command_name} Button Text | Button Link | Message")
        return

    button_text, button_url, body = parts
    keyboard = create_single_button_keyboard(button_text, button_url)
    if not keyboard:
        reply_temp(message, "Button link must start with http://, https://, or tg://")
        return

    body = body.replace("\\n", "\n")
    broadcast_to_all(
        escape_text(body),
        keyboard=keyboard,
        notification_key=f"{command_name}_{int(time.time())}",
        premium_only=premium_only,
        rich_html=simple_rich_markup(body),
    )
    audience = "premium users" if premium_only else "all subscribers"
    reply_temp(message, f"Broadcast sent to <b>{audience}</b>.", parse_mode="HTML")


@bot.message_handler(commands=['quickbroadcast'])
def quickbroadcast_command(message):
    try:
        handle_quick_broadcast(message, premium_only=False)
    except Exception as e:
        logger.error(f"error in quickbroadcast: {e}")
        reply_temp(message, f"Error: {e}")


@bot.message_handler(commands=['quickpremium'])
def quickpremium_command(message):
    try:
        handle_quick_broadcast(message, premium_only=True)
    except Exception as e:
        logger.error(f"error in quickpremium: {e}")
        reply_temp(message, f"Error: {e}")


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
        save_user(message)
        add_chat(
            str(message.chat.id),
            message.chat.title if message.chat.type != 'private' else f"user_{message.from_user.username or message.from_user.id}"
        )
        send_temp_rich(message.chat.id, build_rich_digest(), build_daily_summary_text())
        try_delete_user_message(message)
    except Exception as e:
        logger.error(f"error in summary command: {e}")


@bot.message_handler(commands=['premiumfilters'])
def premiumfilters_command(message):
    try:
        save_user(message)
        if message.chat.type != "private":
            reply_temp(message, "Please open the bot in private chat to manage premium filters.")
            return
        if not is_premium_chat(message.from_user.id):
            reply_temp(message, "Premium filters are available after premium activation. Use /upgrade to view plans.")
            return
        send_persistent_rich(
            chat_id=message.chat.id,
            rich_html=get_premium_filters_text(message.from_user.id),
            fallback_text=get_premium_filters_text(message.from_user.id),
            reply_markup=build_premium_filters_keyboard(message.from_user.id),
        )
    except Exception as e:
        logger.error(f"error in premiumfilters command: {e}")


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


@bot.message_handler(func=lambda message: message.text in ["Status", "Ending Soon", "Preferences", "Recent", "Daily Summary", "Help", "My ID", "Upgrade", "Premium Filters", "Admin"])
def handle_clean_menu_button(message):
    try:
        if message.text == "Status":
            market_status(message)
        elif message.text == "Ending Soon":
            live_ending(message)
        elif message.text == "Preferences":
            preferences(message)
        elif message.text == "Recent":
            recent_command(message)
        elif message.text == "Daily Summary":
            summary_command(message)
        elif message.text == "Help":
            send_help(message)
        elif message.text == "My ID":
            get_my_id(message)
        elif message.text == "Upgrade":
            upgrade_command(message)
        elif message.text == "Premium Filters":
            premiumfilters_command(message)
        elif message.text == "Admin":
            admin_dashboard(message)
    except Exception as e:
        logger.error(f"error handling clean menu button: {e}")


@bot.message_handler(
    content_types=['text', 'photo', 'document'],
    func=lambda message: (
        message.chat.type == "private"
        and not is_admin(message.from_user.id)
        and bool(get_user_premium_interest(message.from_user.id))
        and (message.content_type != "text" or not str(message.text or "").startswith("/"))
    )
)
def handle_premium_payment_proof(message):
    try:
        plan = get_user_premium_interest(message.from_user.id)

        notify_admin_premium_event(
            message.from_user,
            plan,
            "Premium Payment Proof Received",
            extra_text="The user's proof message is forwarded below.",
        )
        if ADMIN_ID:
            try:
                bot.forward_message(ADMIN_ID, message.chat.id, message.message_id)
            except Exception as e:
                logger.error(f"error forwarding premium proof: {e}")

        reply_persistent(
            message,
            "Payment proof received. Admin will confirm and activate your premium access after checking it.",
        )
    except Exception as e:
        logger.error(f"error handling premium proof: {e}")


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
            reply_temp(message, "Permission denied. Admin only command.")
            return

        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            reply_temp(message, "Format: /premium_add telegram_chat_or_user_id monthly")
            return

        parts = args[1].split()
        chat_id = parts[0].strip()
        plan = normalize_premium_plan(parts[1] if len(parts) > 1 else "monthly")
        expires_at = premium_expiry_for_plan(plan)
        if add_premium_chat(chat_id, message.from_user.id, plan=plan, expires_at=expires_at):
            reply_temp(
                message,
                f"Premium enabled for <code>{escape_text(chat_id)}</code>\n"
                f"Plan: <b>{escape_text(plan)}</b>\n"
                f"Expires: <b>{escape_text(expires_at.strftime('%b %d, %Y'))}</b>",
                parse_mode="HTML",
            )
        else:
            reply_temp(message, "Could not add premium chat.")
    except Exception as e:
        logger.error(f"error in premium_add: {e}")


@bot.message_handler(commands=['setpremiumwallet'])
def setpremiumwallet_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "Permission denied. Admin only command.")
            return

        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            reply_temp(message, "Format: /setpremiumwallet YOUR_SOL_USDC_ADDRESS")
            return

        wallet = set_premium_wallet(args[1])
        reply_temp(
            message,
            f"USDC Solana premium wallet updated:\n<code>{escape_text(wallet)}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"error in setpremiumwallet: {e}")


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
            reply_temp(message, "Permission denied. Admin only command.")
            return

        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("""
                    SELECT chat_id, plan, expires_at, created_at
                    FROM premium_chats
                    ORDER BY created_at DESC
                    LIMIT 50
                """)
                rows = cur.fetchall()

        if not rows:
            reply_temp(message, "No premium chats yet.")
            return

        lines = ["<b>Premium Users / Chats</b>"]
        for row in rows:
            expires_at = row.get("expires_at")
            if expires_at:
                expiry_text = expires_at.strftime('%b %d, %Y') if hasattr(expires_at, 'strftime') else str(expires_at)
            else:
                expiry_text = "No expiry"
            status = "active"
            if expires_at:
                expiry_dt = expires_at if hasattr(expires_at, "strftime") else parse_api_datetime(str(expires_at))
                if expiry_dt and expiry_dt <= now_utc():
                    status = "expired"
            lines.append(
                f"\n<code>{escape_text(row.get('chat_id'))}</code>\n"
                f"Plan: <b>{escape_text(row.get('plan') or 'legacy')}</b> | {escape_text(status)}\n"
                f"Expires: {escape_text(expiry_text)}"
            )
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
        save_user(message)
        add_chat(
            str(message.chat.id),
            message.chat.title if message.chat.type != 'private' else f"user_{message.from_user.username or message.from_user.id}"
        )
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
            rich_preview = build_rich_new_market(market, ai_message, heading="Preview: New Market Live")

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


@bot.message_handler(commands=['intel'])
def intelligence_status(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM markets")
                total_markets = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM market_snapshots")
                total_snapshots = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM creators")
                total_creators = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM market_events")
                total_events = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM market_scores")
                total_scores = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM market_fingerprints")
                total_fingerprints = cur.fetchone()[0]
        text = (
            "🧠 <b>Intelligence Engine Status</b>\n\n"
            f"📊 Markets tracked: <b>{total_markets}</b>\n"
            f"📸 Snapshots recorded: <b>{total_snapshots}</b>\n"
            f"👤 Creators registered: <b>{total_creators}</b>\n"
            f"📝 Events logged: <b>{total_events}</b>\n"
            f"🎯 Markets scored: <b>{total_scores}</b>\n"
            f"🧬 Fingerprints extracted: <b>{total_fingerprints}</b>\n\n"
            f"⏱ Snapshot interval: <b>{INTELLIGENCE_SNAPSHOT_INTERVAL}s</b>\n"
            f"⏱ Score interval: <b>{SCORE_RUN_INTERVAL}s</b>"
        )
        reply_temp(message, text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in intel command: {e}")
        reply_temp(message, f"❌ Error: {e}")


@bot.message_handler(commands=['creators'])
def show_creators(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT c.wallet_address, c.total_markets, c.total_volume, c.best_volume,
                           c.first_seen_at, c.last_seen_at
                    FROM creators c
                    ORDER BY c.total_volume DESC
                    LIMIT 10
                """)
                rows = cur.fetchall()
        if not rows:
            reply_temp(message, "No creators tracked yet.")
            return
        lines = ["<b>Top Creators by Volume</b>"]
        for row in rows:
            wallet = str(row[0])[:12] + "..."
            markets = row[1]
            volume = float(row[2] or 0)
            best = float(row[3] or 0)
            lines.append(
                f"\n<code>{escape_text(wallet)}</code>\n"
                f"Markets: <b>{markets}</b> | Volume: <b>{volume:,.0f}</b> | Best: <b>{best:,.0f}</b>"
            )
        reply_temp(message, "\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in creators command: {e}")
        reply_temp(message, f"❌ Error: {e}")


@bot.message_handler(commands=['marketstats'])
def market_statistics(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT m.market_id, m.title, m.theme,
                           s.total_volume, s.total_participants, s.controversy_score,
                           sc.volume_percentile, sc.composite_score
                    FROM markets m
                    LEFT JOIN market_snapshots s ON m.market_id = s.market_id
                        AND s.snapshot_at = (
                            SELECT MAX(snapshot_at) FROM market_snapshots WHERE market_id = m.market_id
                        )
                    LEFT JOIN market_scores sc ON m.market_id = sc.market_id
                    WHERE m.resolved = FALSE AND m.hidden = FALSE
                    ORDER BY sc.composite_score DESC NULLS LAST
                    LIMIT 8
                """)
                rows = cur.fetchall()
        if not rows:
            reply_temp(message, "No active markets with scores yet.")
            return
        lines = ["<b>Market Intelligence</b>"]
        for row in rows:
            title = str(row[1] or "")[:40]
            theme = row[2] or "?"
            volume = float(row[3] or 0)
            participants = int(row[4] or 0)
            controversy = float(row[5] or 0)
            score = float(row[7] or 0)
            lines.append(
                f"\n<b>{escape_text(title)}</b>\n"
                f"Theme: {escape_text(theme)} | Vol: {volume:,.0f} | "
                f"Participants: {participants} | Controversy: {controversy:.2f}\n"
                f"Score: <b>{score:.1f}</b>"
            )
        reply_temp(message, "\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in marketstats command: {e}")
        reply_temp(message, f"❌ Error: {e}")


logger.info("Starting Bot...")
init_db()
init_intelligence_tables()

try:
    public_commands = [
        telebot.types.BotCommand("start", "Subscribe to market alerts"),
        telebot.types.BotCommand("menu", "Open button menu"),
        telebot.types.BotCommand("help", "Show available commands"),
        telebot.types.BotCommand("status", "Check bot status"),
        telebot.types.BotCommand("liveending", "Show markets ending soon"),
        telebot.types.BotCommand("recent", "Show recent announced markets"),
        telebot.types.BotCommand("summary", "Show daily market summary"),
        telebot.types.BotCommand("preferences", "Choose market categories"),
        telebot.types.BotCommand("getmyid", "Get your telegram id"),
    ]
    
    private_commands = public_commands + [
        telebot.types.BotCommand("upgrade", "View premium plans"),
        telebot.types.BotCommand("premiumfilters", "Manage premium alert filters"),
    ]

    bot.set_my_commands(public_commands)
    bot.set_my_commands(private_commands, scope=telebot.types.BotCommandScopeAllPrivateChats())
    bot.set_my_commands(public_commands, scope=telebot.types.BotCommandScopeAllGroupChats())
    
    admin_commands = private_commands + [
        telebot.types.BotCommand("admin", "Open admin dashboard"),
        telebot.types.BotCommand("pause", "Pause all notifications"),
        telebot.types.BotCommand("resume", "Resume notifications"),
        telebot.types.BotCommand("test", "Send test notification"),
        telebot.types.BotCommand("tone", "Change AI message tone"),
        telebot.types.BotCommand("ai", "Show AI settings"),
        telebot.types.BotCommand("aimodel", "Change AI model"),
        telebot.types.BotCommand("emojis", "Show custom emoji settings"),
        telebot.types.BotCommand("setemoji", "Change custom emoji"),
        telebot.types.BotCommand("preview", "Preview latest market alert"),
        telebot.types.BotCommand("health", "Show bot health"),
        telebot.types.BotCommand("premium_add", "Add premium user or chat"),
        telebot.types.BotCommand("setpremiumwallet", "Set USDC Solana payment address"),
        telebot.types.BotCommand("premium_remove", "Remove premium user or chat"),
        telebot.types.BotCommand("premium_users", "List premium users or chats"),
        telebot.types.BotCommand("premium_digest", "Preview premium digest"),
        telebot.types.BotCommand("studio", "Open admin message studio"),
        telebot.types.BotCommand("studio_commands", "Show message studio commands"),
        telebot.types.BotCommand("templates", "List custom templates"),
        telebot.types.BotCommand("buttons", "List custom inline buttons"),
        telebot.types.BotCommand("settemplate", "Edit plain message template"),
        telebot.types.BotCommand("setrich", "Edit rich message template"),
        telebot.types.BotCommand("deltemplate", "Delete custom template"),
        telebot.types.BotCommand("addbutton", "Add custom inline button"),
        telebot.types.BotCommand("delbutton", "Delete custom inline button"),
        telebot.types.BotCommand("studiobroadcast", "Send rich broadcast with studio buttons"),
        telebot.types.BotCommand("quickbroadcast", "Broadcast with one custom button"),
        telebot.types.BotCommand("quickpremium", "Premium broadcast with one custom button"),
        telebot.types.BotCommand("reset", "Reset all data"),
        telebot.types.BotCommand("cleanmessages", "Delete tracked messages only"),
        telebot.types.BotCommand("refreshlinks", "Refresh market button links"),
        telebot.types.BotCommand("broadcast", "Broadcast message"),
        telebot.types.BotCommand("stats", "Show bot statistics"),
        telebot.types.BotCommand("users", "Show user count"),
        telebot.types.BotCommand("listusers", "List all users"),
        telebot.types.BotCommand("intel", "Intelligence engine status"),
        telebot.types.BotCommand("creators", "Top creators by volume"),
        telebot.types.BotCommand("marketstats", "Market intelligence scores"),
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
