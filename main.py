print("NEW DEPLOY VERSION - PHASE 2 EDITORIAL LAYER")
import telebot
from telebot import types
import json
import os
import time
import logging
import statistics
import psycopg
import html
import base64
import struct
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
PUBLIC_ALERT_DELAY_SECONDS = float(os.getenv("PUBLIC_ALERT_DELAY_SECONDS", "90"))
ENABLE_PUBLIC_10_MIN_REMINDER = os.getenv("ENABLE_PUBLIC_10_MIN_REMINDER", "false").lower() == "true"
ENABLE_PUBLIC_FIRST_STAKER_ALERT = os.getenv("ENABLE_PUBLIC_FIRST_STAKER_ALERT", "false").lower() == "true"
COVER_IMAGE_WAIT_SECONDS = float(os.getenv("COVER_IMAGE_WAIT_SECONDS", "8"))
COVER_IMAGE_RETRY_SECONDS = float(os.getenv("COVER_IMAGE_RETRY_SECONDS", "1"))
API_TIMEOUT_SECONDS = float(os.getenv("API_TIMEOUT_SECONDS", "6"))
API_PAGE_LIMIT = int(os.getenv("API_PAGE_LIMIT", "100"))
AI_ON_PREMIUM_FAST_ALERT = os.getenv("AI_ON_PREMIUM_FAST_ALERT", "false").lower() == "true"
ONCHAIN_PROVIDER_ENABLED = os.getenv("ONCHAIN_PROVIDER_ENABLED", "true").lower() == "true"
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
B4_SOLANA_PROGRAM_ID = os.getenv("B4_SOLANA_PROGRAM_ID", "9XQDD38sy1qJ57DqAQvADuLRTjcYUXD48H7deyNuaehH")
ONCHAIN_POLL_SECONDS = float(os.getenv("ONCHAIN_POLL_SECONDS", "3"))
ONCHAIN_MARKET_ACCOUNT_SIZE = int(os.getenv("ONCHAIN_MARKET_ACCOUNT_SIZE", "464"))
ONCHAIN_MARKET_ID_OFFSET = int(os.getenv("ONCHAIN_MARKET_ID_OFFSET", "8"))
ONCHAIN_TITLE_LENGTH_OFFSET = int(os.getenv("ONCHAIN_TITLE_LENGTH_OFFSET", "48"))
ONCHAIN_TITLE_OFFSET = int(os.getenv("ONCHAIN_TITLE_OFFSET", "52"))
ONCHAIN_MARKET_DURATION_SECONDS = int(os.getenv("ONCHAIN_MARKET_DURATION_SECONDS", "86400"))
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

last_onchain_poll_at = 0.0


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
                cur.execute("ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS notified_12h BOOLEAN DEFAULT FALSE")
                cur.execute("ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS notified_6h BOOLEAN DEFAULT FALSE")
                cur.execute("ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS notified_30m BOOLEAN DEFAULT FALSE")
                cur.execute("ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS is_featured BOOLEAN DEFAULT FALSE")
                cur.execute("ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'api'")
                cur.execute("ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS market_pubkey TEXT")
                cur.execute("ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS cover_image_url TEXT")
                cur.execute("ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS onchain_detected_at TIMESTAMP")
                cur.execute("ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS api_detected_at TIMESTAMP")
                cur.execute("ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS premium_notified_onchain BOOLEAN DEFAULT FALSE")
                cur.execute("ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS premium_lead_seconds INTEGER")
                cur.execute("ALTER TABLE announced_markets ADD COLUMN IF NOT EXISTS metadata_json TEXT")
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
                        badge TEXT,
                        scored_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("ALTER TABLE market_scores ADD COLUMN IF NOT EXISTS badge TEXT")
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

                # ── Analytics Engine migrations ──
                # Phase 1 v1: basic classification + cache
                cur.execute("ALTER TABLE market_fingerprints ADD COLUMN IF NOT EXISTS question_type TEXT")
                cur.execute("ALTER TABLE market_fingerprints ADD COLUMN IF NOT EXISTS sentiment_lean TEXT")
                cur.execute("ALTER TABLE markets ADD COLUMN IF NOT EXISTS final_volume NUMERIC")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS analytics_cache (
                        cache_key TEXT PRIMARY KEY,
                        cache_data JSONB NOT NULL,
                        computed_at TIMESTAMPTZ DEFAULT NOW(),
                        expires_at TIMESTAMPTZ
                    )
                """)
                # Phase 1 v2: fix market_scores missing columns
                cur.execute("ALTER TABLE market_scores ADD COLUMN IF NOT EXISTS latest_volume NUMERIC DEFAULT 0")
                cur.execute("ALTER TABLE market_scores ADD COLUMN IF NOT EXISTS latest_participants INT DEFAULT 0")
                cur.execute("ALTER TABLE market_scores ADD COLUMN IF NOT EXISTS latest_controversy NUMERIC DEFAULT 0")
                cur.execute("ALTER TABLE market_scores ADD COLUMN IF NOT EXISTS creator TEXT")
                cur.execute("ALTER TABLE market_scores ADD COLUMN IF NOT EXISTS consensus_score NUMERIC DEFAULT 0")
                # Phase 1 v2: fix market_dna missing title
                cur.execute("ALTER TABLE market_dna ADD COLUMN IF NOT EXISTS title TEXT")
                # Phase 1 v2: multi-tag classification
                cur.execute("ALTER TABLE market_fingerprints ADD COLUMN IF NOT EXISTS primary_category TEXT")
                cur.execute("ALTER TABLE market_fingerprints ADD COLUMN IF NOT EXISTS secondary_categories TEXT[] DEFAULT '{}'")
                cur.execute("ALTER TABLE market_fingerprints ADD COLUMN IF NOT EXISTS all_categories TEXT[] DEFAULT '{}'")
                # Phase 1 v2: enhanced question features
                cur.execute("ALTER TABLE market_fingerprints ADD COLUMN IF NOT EXISTS emotional_words TEXT[] DEFAULT '{}'")
                cur.execute("ALTER TABLE market_fingerprints ADD COLUMN IF NOT EXISTS absolute_words TEXT[] DEFAULT '{}'")
                cur.execute("ALTER TABLE market_fingerprints ADD COLUMN IF NOT EXISTS word_count_bucket TEXT")
                # Phase 1 v2: posting time analytics
                cur.execute("ALTER TABLE markets ADD COLUMN IF NOT EXISTS posted_weekday INT")
                cur.execute("ALTER TABLE markets ADD COLUMN IF NOT EXISTS posted_hour INT")
                cur.execute("ALTER TABLE markets ADD COLUMN IF NOT EXISTS posted_month INT")
                cur.execute("ALTER TABLE markets ADD COLUMN IF NOT EXISTS resolved_weekday INT")
                cur.execute("ALTER TABLE markets ADD COLUMN IF NOT EXISTS resolved_hour INT")
                # Phase 1 v2: growth milestones
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS market_milestones (
                        id SERIAL PRIMARY KEY,
                        market_id TEXT REFERENCES markets(market_id),
                        milestone_hours INT NOT NULL,
                        yes_pool NUMERIC DEFAULT 0,
                        no_pool NUMERIC DEFAULT 0,
                        volume NUMERIC DEFAULT 0,
                        participants INT DEFAULT 0,
                        comments INT DEFAULT 0,
                        recorded_at TIMESTAMPTZ DEFAULT NOW(),
                        UNIQUE(market_id, milestone_hours)
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_milestones_market ON market_milestones (market_id)")
                # Phase 1 v2: similarity cache
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS market_similarity_cache (
                        id SERIAL PRIMARY KEY,
                        source_market_id TEXT REFERENCES markets(market_id),
                        similar_market_id TEXT REFERENCES markets(market_id),
                        similarity_score NUMERIC DEFAULT 0,
                        computed_at TIMESTAMPTZ DEFAULT NOW(),
                        UNIQUE(source_market_id, similar_market_id)
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_similarity_source ON market_similarity_cache (source_market_id)")
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


def save_announced_market(
    market_id,
    title,
    theme,
    end_time,
    notified_new=True,
    is_scheduled=False,
    go_live_at=None,
    source="api",
    market_pubkey=None,
    cover_image_url=None,
    onchain_detected_at=None,
    api_detected_at=None,
    premium_notified_onchain=False,
    metadata=None,
    is_featured=False,
):
    try:
        market_link = build_market_link(market_id)
        go_live_value = go_live_at.isoformat() if isinstance(go_live_at, datetime) else go_live_at
        metadata_json = json.dumps(metadata or {}, default=str) if metadata is not None else None
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO announced_markets (
                        market_id, title, theme, end_time, market_link, notified_new,
                        notified_1h, notified_5m, notified_ended, delete_scheduled,
                        notified_scheduled, notified_go_live_2m, image_followup_sent,
                        is_scheduled, go_live_at, detected_at, source, market_pubkey,
                        cover_image_url, onchain_detected_at, api_detected_at,
                        premium_notified_onchain, metadata_json,
                        notified_12h, notified_6h, notified_30m, is_featured
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        FALSE, FALSE, FALSE, %s
                    )
                    ON CONFLICT (market_id) DO NOTHING
                    RETURNING market_id
                """, (
                    str(market_id), title, theme, end_time, market_link, notified_new,
                    is_scheduled, go_live_value, now_utc().isoformat(), source,
                    market_pubkey, cover_image_url, onchain_detected_at, api_detected_at,
                    premium_notified_onchain, metadata_json, is_featured
                ))
                return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"error saving market {market_id}: {e}")
    return False


def reconcile_market_from_api(market, existing=None):
    market_id = str(market.get("market_id", "")).strip()
    if not market_id:
        return None
    existing = existing or get_announced_market(market_id)
    api_detected_at = now_utc()
    market_link = build_market_link(market_id)
    metadata_json = json.dumps(market, default=str)
    title = str(market.get("title", "")).strip()
    raw_theme = normalize_theme(market.get("theme", "other"))
    end_time_unix = market.get("end_time")
    end_time = None
    if end_time_unix:
        end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None)
    go_live_at = get_market_go_live_at(market)
    go_live_value = go_live_at.isoformat() if isinstance(go_live_at, datetime) else go_live_at
    cover_url = get_market_cover_image(market)
    pubkey = str(market.get("market_pubkey") or "").strip() or None

    try:
        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("""
                    UPDATE announced_markets
                    SET title = COALESCE(NULLIF(%s, ''), title),
                        theme = COALESCE(%s, theme),
                        end_time = COALESCE(%s, end_time),
                        market_link = %s,
                        source = CASE
                            WHEN source = 'onchain' THEN 'onchain+api'
                            ELSE 'api'
                        END,
                        market_pubkey = COALESCE(%s, market_pubkey),
                        cover_image_url = COALESCE(%s, cover_image_url),
                        api_detected_at = COALESCE(api_detected_at, %s),
                        go_live_at = COALESCE(%s, go_live_at),
                        metadata_json = %s,
                        premium_lead_seconds = CASE
                            WHEN onchain_detected_at IS NOT NULL THEN
                                EXTRACT(EPOCH FROM (COALESCE(api_detected_at, %s) - onchain_detected_at))::INTEGER
                            ELSE premium_lead_seconds
                        END
                    WHERE market_id = %s
                    RETURNING *
                """, (
                    title,
                    raw_theme,
                    end_time.isoformat() if end_time else None,
                    market_link,
                    pubkey,
                    cover_url,
                    api_detected_at,
                    go_live_value,
                    metadata_json,
                    api_detected_at,
                    market_id,
                ))
                row = cur.fetchone()
                if row and row.get("premium_lead_seconds") is not None:
                    set_bot_state("last_premium_lead_seconds", row.get("premium_lead_seconds"))
                return row
    except Exception as e:
        logger.error(f"error reconciling api market {market_id}: {e}")
        return existing


def update_market_flag(market_id, flag):
    allowed_flags = {
        "notified_new", "notified_1h", "notified_5m", "notified_ended",
        "delete_scheduled", "notified_scheduled", "notified_go_live_2m",
        "image_followup_sent", "notified_12h", "notified_6h", "notified_30m"
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


def update_announced_end_time(market_id, new_end_time):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE announced_markets SET end_time = %s WHERE market_id = %s AND end_time != %s",
                    (new_end_time, str(market_id), new_end_time)
                )
    except Exception as e:
        logger.error(f"error updating end_time for market {market_id}: {e}")


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


def reply_temp(message, text, reply_markup=None, parse_mode=None, disable_web_page_preview=False):
    sent = bot.reply_to(message, text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=disable_web_page_preview)
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


# ── Market Provider Abstraction ────────────────────────────────────────────────
# Decouples the notification engine from the data source.
# When V2 endpoints become available, add a new provider without touching
# the monitor loop, notification builders, or intelligence pipeline.

class MarketProvider:
    """Base class for market data providers."""

    def fetch_active_markets(self):
        """Return list of active market dicts.

        Each market must include at minimum:
            market_id, title, end_time, yes_pool, no_pool,
            yes_votes, no_votes, creator, theme, hidden, resolved,
            mechanics_version, go_live_at

        Additional V2 fields (when available):
            yes_weighted_pool, no_weighted_pool, yes_display_weight,
            no_display_weight, reputation data, etc.
        """
        raise NotImplementedError

    def health_check(self):
        """Return True if provider is reachable."""
        raise NotImplementedError


class PublicAPIProvider(MarketProvider):
    """Provider using the B4 public REST API (V1).

    This is the current data source. The public API returns all active
    markets with mechanics_version=1 and some pre-calculated V2 fields
    (weighted pools) but no authenticated data.
    """

    def __init__(self, api_url=None, timeout=None, page_limit=None):
        self.api_url = api_url or B4_API_URL
        self.timeout = timeout or API_TIMEOUT_SECONDS
        self.page_limit = page_limit or API_PAGE_LIMIT

    def fetch_active_markets(self):
        try:
            response = requests.get(
                self.api_url,
                params={"page": 1, "limit": self.page_limit, "_": int(time.time())},
                headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()

            if isinstance(data, dict) and "markets" in data:
                markets = data["markets"]
            elif isinstance(data, list):
                markets = data
            else:
                logger.error(f"unexpected api response: {data}")
                return []

            logger.info(f"fetched {len(markets)} markets from public api")
            set_bot_state("last_api_check", now_utc().isoformat())
            return markets

        except Exception as e:
            logger.error(f"error fetching b4 markets: {e}")
            return []

    def health_check(self):
        try:
            response = requests.get(
                self.api_url,
                params={"page": 1, "limit": 1},
                timeout=5,
            )
            return response.status_code == 200
        except Exception:
            return False


class V2Provider(MarketProvider):
    """Placeholder for V2 authenticated API provider.

    Activate this provider when:
    1. APK analysis reveals the authenticated API endpoints
    2. Auth tokens become available (via wallet session or admin setup)
    3. V2-specific data fields are needed for new features

    Implementation will require:
    - Authentication token management (JWT/wallet signature)
    - Token refresh mechanism
    - V2-specific field parsing (reputation, display weights, etc.)
    - WebSocket connection for real-time updates (optional)
    """

    def __init__(self, auth_token=None, api_url=None):
        self.auth_token = auth_token
        self.api_url = api_url  # Will be set from APK analysis
        self._fallback = PublicAPIProvider()

    def fetch_active_markets(self):
        if not self.auth_token or not self.api_url:
            logger.warning("v2 provider not configured, falling back to public api")
            return self._fallback.fetch_active_markets()

        # TODO: Implement V2 API calls here
        # Example structure:
        # headers = {
        #     "Authorization": f"Bearer {self.auth_token}",
        #     "X-App-Version": "2.0",
        # }
        # response = requests.get(
        #     f"{self.api_url}/markets",
        #     headers=headers,
        #     params={"mechanics_version": 2, "limit": self.page_limit},
        #     timeout=self.timeout,
        # )
        # return response.json()["markets"]

        logger.warning("v2 provider not yet implemented, falling back to public api")
        return self._fallback.fetch_active_markets()

    def health_check(self):
        if not self.auth_token or not self.api_url:
            return False
        # TODO: Implement V2 health check
        return False


# Active market provider — swap this to change data source
# Set MARKET_PROVIDER env var to "public" (default) or "v2"
_market_provider_name = os.getenv("MARKET_PROVIDER", "public").lower()
if _market_provider_name == "v2":
    active_market_provider = V2Provider(
        auth_token=os.getenv("B4_AUTH_TOKEN"),
        api_url=os.getenv("B4_V2_API_URL"),
    )
else:
    active_market_provider = PublicAPIProvider()


def fetch_b4_markets():
    """Fetch active markets using the configured provider.

    All callers should use this function — it delegates to the active
    MarketProvider. To switch data sources, set MARKET_PROVIDER env var
    or swap active_market_provider at runtime.
    """
    return active_market_provider.fetch_active_markets()


def build_onchain_cover_url(market_id):
    return f"https://www.b4app.xyz/api/assets/market-cover/{market_id}.png"


def read_u64_le(data, offset):
    if offset < 0 or offset + 8 > len(data):
        return None
    return struct.unpack_from("<Q", data, offset)[0]


def read_u32_le(data, offset):
    if offset < 0 or offset + 4 > len(data):
        return None
    return struct.unpack_from("<I", data, offset)[0]


def decode_onchain_market_account(pubkey, encoded_data):
    try:
        raw_data = encoded_data[0] if isinstance(encoded_data, list) else encoded_data
        data = base64.b64decode(raw_data)
        market_id = read_u64_le(data, ONCHAIN_MARKET_ID_OFFSET)
        title_len = read_u32_le(data, ONCHAIN_TITLE_LENGTH_OFFSET)
        if not market_id or not title_len:
            return None
        if market_id < 1_700_000_000_000_000 or market_id > 1_900_000_000_000_000:
            return None
        if title_len < 6 or title_len > 180:
            return None
        title_end = ONCHAIN_TITLE_OFFSET + title_len
        if title_end > len(data):
            return None
        title = data[ONCHAIN_TITLE_OFFSET:title_end].decode("utf-8", errors="strict").strip("\x00").strip()
        if len(title) < 6 or "?" not in title:
            return None

        created_unix = int(market_id // 1_000_000)
        end_unix = created_unix + ONCHAIN_MARKET_DURATION_SECONDS
        return {
            "market_id": str(market_id),
            "market_pubkey": str(pubkey),
            "title": title,
            "description": "",
            "theme": "other",
            "end_time": end_unix,
            "created_at": datetime.fromtimestamp(created_unix, tz=timezone.utc).isoformat(),
            "updated_at": now_utc().isoformat(),
            "go_live_at": datetime.fromtimestamp(created_unix, tz=timezone.utc).isoformat(),
            "hidden": False,
            "resolved": False,
            "is_private": False,
            "cover_image_url": build_onchain_cover_url(market_id),
            "cover_image_status": "ready",
            "source": "onchain",
        }
    except Exception as e:
        logger.debug(f"could not decode on-chain market account {pubkey}: {e}")
        return None


def fetch_onchain_markets():
    if not ONCHAIN_PROVIDER_ENABLED:
        return []
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getProgramAccounts",
            "params": [
                B4_SOLANA_PROGRAM_ID,
                {
                    "encoding": "base64",
                    "commitment": "confirmed",
                    "filters": [{"dataSize": ONCHAIN_MARKET_ACCOUNT_SIZE}],
                    "dataSlice": {"offset": 0, "length": 220},
                },
            ],
        }
        response = requests.post(SOLANA_RPC_URL, json=payload, timeout=max(API_TIMEOUT_SECONDS, 12))
        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            logger.error(f"solana rpc error: {data['error']}")
            return []

        markets = []
        now_ts = int(datetime.now(timezone.utc).timestamp())
        for account in data.get("result", []):
            market = decode_onchain_market_account(
                account.get("pubkey"),
                account.get("account", {}).get("data"),
            )
            if not market:
                continue
            if int(market["end_time"]) <= now_ts:
                continue
            markets.append(market)

        set_bot_state("last_onchain_check", now_utc().isoformat())
        logger.info(f"decoded {len(markets)} live markets from on-chain provider")
        return sorted(markets, key=lambda item: item["market_id"], reverse=True)
    except Exception as e:
        logger.error(f"error fetching on-chain markets: {e}")
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
RECALC_INTERVAL = 300
USDC_DECIMALS = 6
USDC_DIVISOR = 10 ** USDC_DECIMALS  # B4 API returns pools in base units (lamports). Divide by 1,000,000 for USDC.
_last_score_run = 0
_last_recalc_run = 0
_intelligence_snapshot_cache = {}

# ── Phase 2: Editorial & Discovery Layer ──────────────────────────────────────
# Featured creators (wallet addresses). Set via FEATURED_WALLETS env var (comma-separated).
# Markets from these wallets get Editor's Pick treatment and smart reminder scheduling.
_raw_featured = os.getenv("FEATURED_WALLETS", "")
FEATURED_WALLETS: set[str] = {w.strip().lower() for w in _raw_featured.split(",") if w.strip()}

# Smart Reminder Engine: volume targets (USDC). Only send featured reminders if volume is below target.
FEATURED_REMINDER_TARGET_12H = float(os.getenv("FEATURED_REMINDER_TARGET_12H", "50"))
FEATURED_REMINDER_TARGET_6H = float(os.getenv("FEATURED_REMINDER_TARGET_6H", "100"))
FEATURED_REMINDER_TARGET_30M = float(os.getenv("FEATURED_REMINDER_TARGET_30M", "150"))

# Hot Debate: momentum thresholds (percentage change between snapshots within interval window)
HOT_DEBATE_VOLUME_SPIKE = float(os.getenv("HOT_DEBATE_VOLUME_SPIKE", "80"))   # % volume increase
HOT_DEBATE_PARTICIPANT_SPIKE = float(os.getenv("HOT_DEBATE_PARTICIPANT_SPIKE", "60"))  # % participant increase
HOT_DEBATE_COMMENT_SPIKE = float(os.getenv("HOT_DEBATE_COMMENT_SPIKE", "100"))  # % comment increase

# Hidden Gem: strong characteristics but low attention
HIDDEN_GEM_MIN_CONTROVERSY = float(os.getenv("HIDDEN_GEM_MIN_CONTROVERSY", "0.35"))
HIDDEN_GEM_MIN_ENGAGEMENT = float(os.getenv("HIDDEN_GEM_MIN_ENGAGEMENT", "5.0"))
HIDDEN_GEM_MAX_VOLUME_PERCENTILE = float(os.getenv("HIDDEN_GEM_MAX_VOLUME_PERCENTILE", "25"))

# Rotating teasers — natural, non-repetitive text that adds editorial flavour
TEASERS = [
    "This one could split opinions.",
    "Not an easy question.",
    "Curious where the crowd lands.",
    "Expect arguments on both sides.",
    "One of today's toughest debates.",
    "This one may surprise people.",
    "Simple question. Difficult answer.",
    "The crowd won't agree on this one.",
    "Strong feelings expected on both sides.",
    "A polarizing topic from the start.",
    "No safe middle ground here.",
    "Watch where the early money goes.",
    "This could get heated.",
    "Opinions will be divided on this one.",
    "Not one to sit out.",
    "The early signals are interesting.",
    "This market will tell us something.",
    "Both sides have a case here.",
    "This one is already generating buzz.",
    "A debate worth watching closely.",
    "Smart money is moving on this one.",
    "The odds could shift quickly.",
    "Don't blink on this one.",
    "This one has legs.",
    "A real test of conviction.",
]


def is_featured_creator(market):
    """Check if a market was created by a featured wallet."""
    if not FEATURED_WALLETS:
        return False
    creator = str(market.get("creator", "") or "").strip().lower()
    return creator in FEATURED_WALLETS


def get_random_teaser(seed=None):
    """Return a rotating teaser based on seed for consistency, or random."""
    if seed is not None:
        return TEASERS[seed % len(TEASERS)]
    import random as _random
    return _random.choice(TEASERS)


def get_market_volume(market_id):
    """Get latest total_volume from the most recent snapshot."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT total_volume, yes_pool, no_pool
                    FROM market_snapshots
                    WHERE market_id = %s
                    ORDER BY snapshot_at DESC
                    LIMIT 1
                """, (market_id,))
                row = cur.fetchone()
                if row:
                    return float(row[0] or 0), float(row[1] or 0), float(row[2] or 0)
    except Exception as e:
        logger.error(f"error getting volume for {market_id}: {e}")
    return 0.0, 0.0, 0.0


def get_market_snapshots_for_momentum(market_id, limit=3):
    """Get recent snapshots for momentum calculation."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT total_volume, total_participants, comments_count, snapshot_at
                    FROM market_snapshots
                    WHERE market_id = %s
                    ORDER BY snapshot_at DESC
                    LIMIT %s
                """, (market_id, limit))
                return cur.fetchall()
    except Exception as e:
        logger.error(f"error getting snapshots for {market_id}: {e}")
    return []


def calculate_momentum(market_id):
    """Calculate momentum score from recent snapshot deltas."""
    snapshots = get_market_snapshots_for_momentum(market_id, 3)
    if len(snapshots) < 2:
        return 0.0, 0.0, 0.0

    latest = snapshots[0]
    previous = snapshots[1]
    vol_now = float(latest[0] or 0)
    vol_prev = float(previous[0] or 0)
    part_now = int(latest[1] or 0)
    part_prev = int(previous[1] or 0)
    comment_now = int(latest[2] or 0)
    comment_prev = int(previous[2] or 0)

    vol_growth = ((vol_now - vol_prev) / vol_prev * 100) if vol_prev > 0 else 0
    part_growth = ((part_now - part_prev) / part_prev * 100) if part_prev > 0 else 0
    comment_growth = ((comment_now - comment_prev) / comment_prev * 100) if comment_prev > 0 else 0

    return vol_growth, part_growth, comment_growth


def detect_hot_debate(market_id):
    """Detect if a market has sudden momentum spikes."""
    vol_growth, part_growth, comment_growth = calculate_momentum(market_id)
    is_hot = (
        vol_growth >= HOT_DEBATE_VOLUME_SPIKE
        or part_growth >= HOT_DEBATE_PARTICIPANT_SPIKE
        or comment_growth >= HOT_DEBATE_COMMENT_SPIKE
    )
    return is_hot, vol_growth, part_growth, comment_growth


def detect_hidden_gem(market_id):
    """Detect markets with strong characteristics but low attention."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT ms.volume_percentile, ms.controversy, ms.engagement_ratio, ms.composite_score
                    FROM market_scores ms
                    WHERE ms.market_id = %s
                """, (market_id,))
                row = cur.fetchone()
                if not row:
                    return False
                vol_pct = float(row[0] or 0)
                controversy = float(row[1] or 0)
                engagement = float(row[2] or 0)

                return (
                    controversy >= HIDDEN_GEM_MIN_CONTROVERSY
                    and engagement >= HIDDEN_GEM_MIN_ENGAGEMENT
                    and vol_pct <= HIDDEN_GEM_MAX_VOLUME_PERCENTILE
                )
    except Exception as e:
        logger.error(f"error detecting hidden gem for {market_id}: {e}")
    return False


def assign_badge(market_id):
    """
    Assign a single badge to a market. Priority:
    1. ⚡ Closing Soon (market ends within 30 minutes)
    2. 🔥 Hot Debate (sudden momentum spike)
    3. ⭐ Editor's Pick (featured creator)
    4. 💎 Hidden Gem (strong metrics, low attention)
    5. 📈 Market Watch (good scores, above average)
    """
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT m.market_id, m.resolved, m.hidden, m.end_time,
                           ms.total_volume, ms.composite_score, sc.composite_score
                    FROM markets m
                    LEFT JOIN market_snapshots ms ON m.market_id = ms.market_id
                        AND ms.snapshot_at = (SELECT MAX(snapshot_at) FROM market_snapshots WHERE market_id = m.market_id)
                    LEFT JOIN market_scores sc ON m.market_id = sc.market_id
                    WHERE m.market_id = %s AND m.resolved = FALSE AND m.hidden = FALSE
                """, (market_id,))
                row = cur.fetchone()
                if not row:
                    return None

                end_time_unix = row[3]
                now = now_utc()
                if end_time_unix:
                    end_dt = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None)
                    minutes_left = (end_dt - now).total_seconds() / 60
                else:
                    minutes_left = 999

                # Check closing soon
                if minutes_left <= 30:
                    return "⚡"

                # Check hot debate
                is_hot, _, _, _ = detect_hot_debate(market_id)
                if is_hot:
                    return "🔥"

                # Check editor's pick
                cur.execute("SELECT creator_wallet FROM markets WHERE market_id = %s", (market_id,))
                creator_row = cur.fetchone()
                if creator_row and creator_row[0] and creator_row[0].strip().lower() in FEATURED_WALLETS:
                    return "⭐"

                # Check hidden gem
                if detect_hidden_gem(market_id):
                    return "💎"

                # Check market watch (above average composite score)
                avg_score = 0
                try:
                    cur.execute("SELECT AVG(composite_score) FROM market_scores WHERE composite_score > 0")
                    avg_row = cur.fetchone()
                    avg_score = float(avg_row[0] or 0) if avg_row else 0
                except Exception:
                    pass
                composite = float(row[6] or 0) if row[6] else 0
                if composite > avg_score and composite > 0:
                    return "📈"

                return None
    except Exception as e:
        logger.error(f"error assigning badge for {market_id}: {e}")
    return None


def badge_label(badge):
    """Return the full label for a badge emoji."""
    labels = {
        "⭐": "Editor's Pick",
        "🔥": "Hot Debate",
        "💎": "Hidden Gem",
        "📈": "Market Watch",
        "⚡": "Closing Soon",
    }
    return labels.get(badge, "")


def format_badge_line(badge):
    """Return a formatted badge line for notifications."""
    if not badge:
        return ""
    label = badge_label(badge)
    return f"{badge} <b>{label}</b>"


def run_badge_engine(market_ids):
    """Run badge assignment for all active markets and store results."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                for market_id in market_ids:
                    badge = assign_badge(market_id)
                    cur.execute("""
                        INSERT INTO market_scores (market_id, scored_at)
                        VALUES (%s, NOW())
                        ON CONFLICT (market_id) DO NOTHING
                    """, (market_id,))
                    cur.execute("""
                        UPDATE market_scores SET badge = %s WHERE market_id = %s
                    """, (badge, market_id))
        logger.info(f"badge engine processed {len(market_ids)} markets")
    except Exception as e:
        logger.error(f"error in badge engine: {e}")


# ── Featured Reminder Engine ──────────────────────────────────────────────────
# Premium market alerts for Editor's Pick markets. Replaces generic countdowns
# with intelligence-driven cards that create FOMO and drive voting activity.

def format_time_remaining(seconds):
    """Convert seconds to human-readable string: '11h 58m', '58m', '9m'."""
    if seconds <= 0:
        return "ended"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def get_reminder_urgency(hours, minutes):
    """
    Return (emoji, heading, tier) based on remaining time.
    Tier is used to select the right reminder window.
    """
    if hours > 6.0:
        return "🕛", f"{int(round(hours))} Hours Left", "12h"
    elif hours > 1.0:
        h = int(hours)
        m = int(minutes % 60)
        return "⏰", f"{h}h {m:02d}m Left", "6h"
    elif minutes > 30.0:
        return "⏳", f"{int(round(minutes))} Minutes Left", "1h"
    elif minutes > 10.0:
        return "⚡", f"{int(round(minutes))} Minutes Left", "30m"
    else:
        return "🚨", "Final Minutes", "10m"


def get_market_intelligence(market_id):
    """
    Fetch all available intelligence data for a market.
    Returns dict with volume, participants, comments, momentum, badge, scores.
    Gracefully returns empty/zero values if data doesn't exist.
    """
    intel = {
        "volume": 0.0,
        "yes_pool": 0.0,
        "no_pool": 0.0,
        "participants": 0,
        "comments": 0,
        "vol_growth": 0.0,
        "part_growth": 0.0,
        "comment_growth": 0.0,
        "badge": None,
        "composite_score": 0.0,
        "controversy": 0.0,
    }
    try:
        vol, yes, no = get_market_volume(market_id)
        intel["volume"] = vol
        intel["yes_pool"] = yes
        intel["no_pool"] = no
    except Exception:
        pass

    try:
        snapshots = get_market_snapshots_for_momentum(market_id, 3)
        if snapshots:
            latest = snapshots[0]
            intel["participants"] = int(latest[1] or 0)
            intel["comments"] = int(latest[2] or 0)
            if len(snapshots) >= 2:
                vol_g, part_g, comment_g = calculate_momentum(market_id)
                intel["vol_growth"] = vol_g
                intel["part_growth"] = part_g
                intel["comment_growth"] = comment_g
    except Exception:
        pass

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT sc.badge, sc.composite_score, sc.controversy
                    FROM market_scores sc
                    WHERE sc.market_id = %s
                """, (market_id,))
                row = cur.fetchone()
                if row:
                    intel["badge"] = row[0]
                    intel["composite_score"] = float(row[1] or 0)
                    intel["controversy"] = float(row[2] or 0)
    except Exception:
        pass

    return intel


def generate_dynamic_badges(intel):
    """
    Generate a list of dynamic badges from intelligence data.
    Returns list of (emoji, label) tuples. Max 3 badges shown.
    """
    badges = []

    if intel["vol_growth"] >= 20:
        badges.append(("🔥", "Trending"))
    if intel["comment_growth"] >= 30 or intel["comments"] >= 30:
        badges.append(("💬", "Most Discussed"))
    if intel["vol_growth"] >= 35:
        badges.append(("⚡", "Fast Growing"))
    if intel["participants"] >= 50:
        badges.append(("👥", "Active Market"))
    if intel["volume"] >= 500:
        badges.append(("🏆", "Top Volume"))
    if intel["controversy"] >= 0.5:
        badges.append(("🔥", "Heated Debate"))

    seen = set()
    unique = []
    for b in badges:
        if b[1] not in seen:
            seen.add(b[1])
            unique.append(b)
    return unique[:3]


def build_status_line(intel):
    """Build a one-line status summary from intelligence data."""
    parts = []
    if intel["vol_growth"] >= 20:
        parts.append(f"Volume +{intel['vol_growth']:.0f}%")
    if intel["comment_growth"] >= 20:
        parts.append(f"Comments +{intel['comment_growth']:.0f}%")
    if intel["controversy"] >= 0.4:
        parts.append("Controversial")
    if not parts:
        parts.append("Building momentum")
    return " · ".join(parts[:2])


def build_featured_reminder_card(title, market_id, seconds_until, market_link=None):
    """
    Build a premium intelligence-driven reminder card for Editor's Pick markets.
    Falls back gracefully if intelligence data is missing.
    """
    hours = seconds_until / 3600
    minutes = seconds_until / 60

    emoji, heading, tier = get_reminder_urgency(hours, minutes)
    time_text = format_time_remaining(seconds_until)
    intel = get_market_intelligence(market_id)
    dynamic_badges = generate_dynamic_badges(intel)
    status_line = build_status_line(intel)

    if not market_link:
        market_link = build_market_link(market_id)

    lines = [
        f"⭐ <b>EDITOR'S PICK</b>",
        "",
        f"{emoji} <b>{heading}</b>",
        "",
        f"<b>{escape_text(title)}</b>",
        "",
        "━━━━━━━━━━━━━━━━",
        "",
    ]

    if dynamic_badges:
        badge_str = "  ".join(f"{e} {l}" for e, l in dynamic_badges)
        lines.append(f"📈 {badge_str}")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━")
    lines.append("")

    if intel["volume"] > 0 and intel["vol_growth"] > 0:
        lines.append(f"Volume increased {intel['vol_growth']:.0f}% recently.")
        lines.append("Don't miss the discussion.")
    elif intel["volume"] > 0:
        lines.append("Still enough time to influence the outcome.")
    else:
        teaser = get_random_teaser(sum(ord(c) for c in title) + int(hours))
        lines.append(teaser)

    notification = "\n".join(lines)

    rich_fallback = (
        f"<h3>{escape_text(emoji)} {escape_text(heading)}</h3>"
        f"<h2>{escape_text(title)}</h2>"
        "<table>"
        f"<tr><th>Time Left</th><td>{escape_text(time_text)}</td></tr>"
        f"<tr><th>Status</th><td>{escape_text(status_line)}</td></tr>"
        "</table>"
        "<p>Join the debate before the market closes.</p>"
    )

    return notification, rich_fallback, tier


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


# ── Analytics Engine ──────────────────────────────────────────────────────────
# Reusable analytics functions that power all intelligence features.
# Every metric flows through these functions — no one-off queries.

ANALYTICS_REFRESH_SECONDS = int(os.getenv("ANALYTICS_REFRESH_SECONDS", "1800"))  # 30 min

QUESTION_TYPE_RULES = {
    "Ethics": ["should", "moral", "ethical", "right", "wrong", "fair", "just", "equal", "justice"],
    "Relationships": ["love", "marriage", "dating", "relationship", "partner", "boyfriend", "girlfriend", "wife", "husband", "cheating", "breakup"],
    "AI": ["ai", "artificial intelligence", "chatgpt", "gpt", "robot", "machine learning", "deep learning", "neural"],
    "Politics": ["election", "president", "government", "democrat", "republican", "congress", "senate", "vote", "political", "trump", "biden"],
    "Finance": ["stock", "invest", "crypto", "bitcoin", "ethereum", "inflation", "recession", "gdp", "interest rate", "bank", "loan", "debt"],
    "Career": ["job", "career", "hire", "fired", "salary", "work", "remote", "office", "promotion", "interview"],
    "Parenting": ["parent", "child", "kid", "baby", "pregnancy", "school", "teenager", "discipline", "raising"],
    "Technology": ["apple", "google", "microsoft", "iphone", "android", "software", "app", "tech", "internet", "social media"],
    "Health": ["health", "doctor", "hospital", "disease", "vaccine", "cancer", "mental health", "diet", "exercise", "sleep"],
    "Sports": ["nba", "nfl", "mlb", "soccer", "football", "basketball", "tennis", "championship", "world cup", "olympics"],
    "Crypto": ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "token", "defi", "nft", "blockchain"],
    "Lifestyle": ["travel", "food", "fashion", "home", "car", "luxury", "minimalism", "vegan", "coffee"],
    "Environment": ["climate", "environment", "pollution", "renewable", "solar", "fossil fuel", "carbon", "green"],
    "Education": ["university", "college", "degree", "student", "teacher", "school", "learning", "online course"],
    "Workplace": ["boss", "coworker", "office", "remote work", "hiring", "layoff", "salary", "promotion", "burnout"],
    "Entertainment": ["movie", "music", "album", "oscar", "grammy", "netflix", "celebrity", "actor", "show", "tv"],
}

EMOTIONAL_WORDS = [
    "love", "hate", "fear", "angry", "happy", "sad", "terrible", "amazing",
    "disgusting", "beautiful", "horrible", "wonderful", "outrageous", "brilliant",
    "catastrophic", "devastating", "enraged", "thrilled", "devastated", "furious",
]

ABSOLUTE_WORDS = [
    "always", "never", "everyone", "nobody", "all", "none", "every",
    "forever", "impossible", "certain", "guaranteed", "definitely",
    "absolutely", "worst", "best", "entirely", "completely", "totally",
]

MILESTONE_HOURS = [1, 3, 6, 12, 18, 24]

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "out", "off", "over",
    "under", "again", "further", "then", "once", "here", "there", "when",
    "where", "why", "how", "all", "both", "each", "few", "more", "most",
    "other", "some", "such", "no", "nor", "not", "only", "own", "same",
    "so", "than", "too", "very", "just", "about", "and", "but", "or",
    "if", "because", "that", "this", "these", "those", "it", "its",
}


# ── Classification ────────────────────────────────────────────────────────────

def classify_question_types(title):
    """Multi-tag classification. Returns (primary, [secondary]) with keyword scoring."""
    title_lower = str(title or "").lower()
    scores = {}
    for qtype, keywords in QUESTION_TYPE_RULES.items():
        score = sum(1 for kw in keywords if kw in title_lower)
        if score > 0:
            scores[qtype] = score
    if not scores:
        return ("General", [])
    sorted_types = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    primary = sorted_types[0][0]
    primary_score = sorted_types[0][1]
    threshold = primary_score * 0.5
    secondary = [t for t, s in sorted_types[1:] if s >= threshold][:3]
    return (primary, secondary)


def classify_sentiment(title):
    """Classify question sentiment leaning."""
    title_lower = str(title or "").lower()
    positive = ["good", "great", "best", "success", "win", "improve", "better", "love", "happy", "positive", "boom", "growth"]
    negative = ["bad", "worst", "fail", "lose", "decline", "worse", "hate", "sad", "negative", "crash", "recession", "die", "dead"]
    pos = sum(1 for w in positive if w in title_lower)
    neg = sum(1 for w in negative if w in title_lower)
    if pos > 0 and neg > 0:
        return "mixed"
    if pos > 0:
        return "positive"
    if neg > 0:
        return "negative"
    return "neutral"


def extract_question_features(title):
    """Extract complexity, emotional words, absolute words, length bucket from title."""
    title_lower = str(title or "").lower()
    words = [w for w in title_lower.split() if w.isalpha()]
    wc = len(words)
    avg_word_length = sum(len(w) for w in words) / max(wc, 1)
    complex_words = sum(1 for w in words if len(w) > 7)
    complexity = round(avg_word_length * 0.5 + (complex_words / max(wc, 1)) * 10, 2)
    emotional_found = [w for w in EMOTIONAL_WORDS if w in title_lower]
    absolute_found = [w for w in ABSOLUTE_WORDS if w in title_lower]
    if wc <= 6:
        length_bucket = "short"
    elif wc <= 10:
        length_bucket = "medium"
    elif wc <= 15:
        length_bucket = "long"
    elif wc <= 20:
        length_bucket = "very_long"
    else:
        length_bucket = "extreme"
    is_yes_no = wc <= 8 and any(w in title_lower for w in ["will", "is", "do", "should", "can", "would", "has", "did"])
    return {
        "complexity_score": complexity,
        "emotional_words": emotional_found,
        "absolute_words": absolute_found,
        "word_count": wc,
        "word_count_bucket": length_bucket,
        "has_question_mark": "?" in str(title),
        "is_yes_no": is_yes_no,
    }


def extract_posting_time(market):
    """Extract posting time metadata from market API data."""
    created_at_api = parse_api_datetime(market.get("created_at"))
    result = {"posted_weekday": None, "posted_hour": None, "posted_month": None}
    if created_at_api:
        result["posted_weekday"] = created_at_api.weekday()
        result["posted_hour"] = created_at_api.hour
        result["posted_month"] = created_at_api.month
    return result


def calculate_consensus_score(yes_pool, no_pool):
    """Consensus score: 1.0 = perfect 50/50 split, 0.0 = one-sided.
    Higher = more divided = better debate market."""
    total = float(yes_pool or 0) + float(no_pool or 0)
    if total <= 0:
        return 0.0
    return round(1.0 - abs(float(yes_pool or 0) - float(no_pool or 0)) / total, 4)


def calculate_distribution(values):
    """Calculate distribution metrics for a list of numeric values."""
    if not values:
        return {"count": 0, "min": 0, "max": 0, "mean": 0, "median": 0,
                "p25": 0, "p75": 0, "std_dev": 0}
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    result = {
        "count": n,
        "min": round(sorted_vals[0], 2),
        "max": round(sorted_vals[-1], 2),
        "mean": round(statistics.mean(sorted_vals), 2),
        "median": round(statistics.median(sorted_vals), 2),
        "p25": round(sorted_vals[n // 4], 2) if n >= 4 else round(sorted_vals[0], 2),
        "p75": round(sorted_vals[3 * n // 4], 2) if n >= 4 else round(sorted_vals[-1], 2),
        "std_dev": round(statistics.stdev(sorted_vals), 2) if n > 1 else 0,
    }
    return result


# ── Milestones ────────────────────────────────────────────────────────────────

def check_and_record_milestone(market_id, yes_pool, no_pool, volume, participants, comments, created_at_api):
    """Check if current time crosses a milestone threshold and record it."""
    if not created_at_api:
        return
    try:
        hours_since_creation = (now_utc() - created_at_api).total_seconds() / 3600
        for milestone_h in MILESTONE_HOURS:
            if hours_since_creation >= milestone_h:
                try:
                    with get_db() as conn:
                        with conn.cursor() as cur:
                            cur.execute("""
                                INSERT INTO market_milestones (
                                    market_id, milestone_hours, yes_pool, no_pool,
                                    volume, participants, comments
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (market_id, milestone_hours) DO NOTHING
                            """, (
                                market_id, milestone_h,
                                round(yes_pool, 6), round(no_pool, 6),
                                round(volume, 6), participants, comments,
                            ))
                except Exception:
                    pass
    except Exception:
        pass


def get_market_milestones(market_id):
    """Retrieve milestone data for a market."""
    market_id = str(market_id or "").strip()
    if not market_id:
        return []
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT milestone_hours, yes_pool, no_pool, volume, participants, comments, recorded_at
                    FROM market_milestones WHERE market_id = %s
                    ORDER BY milestone_hours ASC
                """, (market_id,))
                return [
                    {
                        "milestone_hours": r[0], "yes_pool": float(r[1] or 0),
                        "no_pool": float(r[2] or 0), "volume": float(r[3] or 0),
                        "participants": int(r[4] or 0), "comments": int(r[5] or 0),
                        "recorded_at": r[6].isoformat() if r[6] else None,
                    }
                    for r in cur.fetchall()
                ]
    except Exception:
        return []


# ── Similar Market Search ─────────────────────────────────────────────────────

def find_similar_markets(title, limit=5):
    """Find markets with similar keywords/topics. Uses keyword overlap scoring."""
    title_lower = str(title or "").lower()
    input_words = set(w for w in title_lower.split() if w.isalpha() and w not in STOPWORDS and len(w) > 2)
    input_primary, input_secondary = classify_question_types(title)
    input_categories = set([input_primary] + input_secondary)
    if not input_words and not input_categories:
        return []
    cache_key = f"analytics:similar:{hash(title_lower)}"
    cached = get_cached(cache_key)
    if cached:
        return cached[:limit]
    result = []
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT m.market_id, m.title, m.theme, m.creator_wallet, m.resolved, m.final_volume,
                           f.primary_category, f.secondary_categories, f.detected_keywords,
                           ms.latest_volume, ms.consensus_score
                    FROM markets m
                    JOIN market_fingerprints f ON f.market_id = m.market_id
                    JOIN market_scores ms ON ms.market_id = m.market_id
                    WHERE f.detected_keywords != '{}'::text[] OR f.primary_category IS NOT NULL
                    LIMIT 500
                """)
                for r in cur.fetchall():
                    mid, mtitle, mtheme, mcreator, mresolved, mfinal = r[0], r[1], r[2], r[3], r[4], r[5]
                    mprimary, msecondary, mkeywords = r[6], r[7] or [], r[8] or []
                    mvolume, mconsensus = r[9], r[10]
                    score = 0.0
                    market_words = set(w for w in str(mtitle or "").lower().split() if w.isalpha() and w not in STOPWORDS and len(w) > 2)
                    if input_words and market_words:
                        intersection = input_words & market_words
                        union = input_words | market_words
                        if union:
                            score += (len(intersection) / len(union)) * 0.6
                    market_cats = set([mprimary] + (msecondary or []))
                    cat_overlap = input_categories & market_cats
                    if cat_overlap:
                        score += 0.2 * min(len(cat_overlap), 2)
                    if mkeywords:
                        kw_overlap = len(input_words & set(mkeywords))
                        score += min(kw_overlap * 0.05, 0.2)
                    if score > 0.1:
                        result.append({
                            "market_id": mid, "title": mtitle, "theme": mtheme,
                            "creator": mcreator, "resolved": mresolved,
                            "final_volume": float(mfinal) if mfinal else None,
                            "volume": float(mvolume or 0),
                            "consensus_score": float(mconsensus or 0),
                            "similarity": round(score, 3),
                        })
            result.sort(key=lambda x: x["similarity"], reverse=True)
            result = result[:limit]
    except Exception as e:
        logger.error(f"error finding similar markets: {e}")
    set_cached(cache_key, result, ttl_seconds=3600)
    return result


# ── Analytics Cache ───────────────────────────────────────────────────────────

def get_cached(cache_key):
    """Retrieve cached analytics data if still valid."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT cache_data, expires_at FROM analytics_cache
                    WHERE cache_key = %s
                """, (cache_key,))
                row = cur.fetchone()
                if row:
                    expires = row[1]
                    if expires and expires > now_utc():
                        return row[0]
    except Exception:
        pass
    return None


def set_cached(cache_key, data, ttl_seconds=None):
    """Store analytics data in cache with TTL."""
    if ttl_seconds is None:
        ttl_seconds = ANALYTICS_REFRESH_SECONDS
    expires = now_utc() + timedelta(seconds=ttl_seconds)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO analytics_cache (cache_key, cache_data, computed_at, expires_at)
                    VALUES (%s, %s, NOW(), %s)
                    ON CONFLICT (cache_key) DO UPDATE SET
                        cache_data = EXCLUDED.cache_data,
                        computed_at = NOW(),
                        expires_at = EXCLUDED.expires_at
                """, (cache_key, json.dumps(data), expires))
    except Exception as e:
        logger.error(f"error setting cache {cache_key}: {e}")


def invalidate_cache(pattern=None):
    """Invalidate cached analytics. If pattern is None, clear all."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if pattern:
                    cur.execute("DELETE FROM analytics_cache WHERE cache_key LIKE %s", (f"{pattern}%",))
                else:
                    cur.execute("DELETE FROM analytics_cache")
    except Exception as e:
        logger.error(f"error invalidating cache: {e}")


# ── Analytics Functions ───────────────────────────────────────────────────────
# Every function is reusable, returns a dict, uses cache.
# No Telegram dependencies. No state mutations. Pure analytics.

def analyze_creator(wallet, force_refresh=False):
    """Lifetime analytics for a single creator."""
    cache_key = f"analytics:creator:{wallet}"
    if not force_refresh:
        cached = get_cached(cache_key)
        if cached:
            return cached
    result = {
        "wallet": wallet, "total_markets": 0, "total_volume": 0.0,
        "avg_volume": 0.0, "median_volume": 0.0, "peak_volume": 0.0,
        "volume_distribution": {},
        "total_participants": 0, "avg_participants": 0.0,
        "participant_distribution": {},
        "avg_controversy": 0.0, "avg_consensus": 0.0,
        "total_comments": 0, "avg_comments": 0.0,
        "total_likes": 0, "resolved_markets": 0,
        "avg_market_duration_hours": 0.0,
        "themes": {}, "question_types": {}, "sentiments": {},
        "primary_categories": {}, "posting_hours": {},
    }
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*), COALESCE(SUM(latest_volume), 0),
                           COALESCE(AVG(latest_volume), 0),
                           COALESCE(MAX(latest_volume), 0),
                           COALESCE(AVG(latest_participants), 0),
                           COALESCE(AVG(latest_controversy), 0),
                           COALESCE(AVG(consensus_score), 0)
                    FROM market_scores WHERE creator = %s
                """, (wallet,))
                row = cur.fetchone()
                if row:
                    result["total_markets"] = row[0]
                    result["total_volume"] = round(float(row[1] or 0), 2)
                    result["avg_volume"] = round(float(row[2] or 0), 2)
                    result["peak_volume"] = round(float(row[3] or 0), 2)
                    result["avg_participants"] = round(float(row[4] or 0), 1)
                    result["avg_controversy"] = round(float(row[5] or 0), 3)
                    result["avg_consensus"] = round(float(row[6] or 0), 3)

                cur.execute("""
                    SELECT COALESCE(SUM(s.comments_count), 0), COALESCE(SUM(s.likes_count), 0)
                    FROM market_snapshots s
                    JOIN markets m ON m.market_id = s.market_id
                    WHERE m.creator_wallet = %s
                """, (wallet,))
                row = cur.fetchone()
                if row:
                    result["total_comments"] = int(row[0] or 0)
                    result["total_likes"] = int(row[1] or 0)
                    if result["total_markets"] > 0:
                        result["avg_comments"] = round(result["total_comments"] / result["total_markets"], 1)

                cur.execute("""
                    SELECT latest_volume FROM market_scores
                    WHERE creator = %s AND latest_volume > 0
                """, (wallet,))
                volumes = [float(r[0]) for r in cur.fetchall() if r[0]]
                if volumes:
                    result["volume_distribution"] = calculate_distribution(volumes)

                cur.execute("""
                    SELECT latest_participants FROM market_scores
                    WHERE creator = %s AND latest_participants > 0
                """, (wallet,))
                parts = [int(r[0]) for r in cur.fetchall() if r[0]]
                if parts:
                    result["participant_distribution"] = calculate_distribution(parts)

                cur.execute("""
                    SELECT COUNT(*), COALESCE(AVG(final_volume), 0)
                    FROM markets WHERE creator_wallet = %s AND resolved = true
                """, (wallet,))
                row = cur.fetchone()
                if row and row[0] > 0:
                    result["resolved_markets"] = row[0]

                cur.execute("""
                    SELECT COALESCE(AVG(EXTRACT(EPOCH FROM (end_time - created_at_api)) / 3600), 0)
                    FROM markets WHERE creator_wallet = %s AND end_time IS NOT NULL AND created_at_api IS NOT NULL
                """, (wallet,))
                row = cur.fetchone()
                if row:
                    result["avg_market_duration_hours"] = round(float(row[0] or 0), 1)

                cur.execute("""
                    SELECT theme, COUNT(*) FROM markets WHERE creator_wallet = %s AND theme IS NOT NULL
                    GROUP BY theme ORDER BY COUNT(*) DESC
                """, (wallet,))
                result["themes"] = {r[0]: r[1] for r in cur.fetchall()}

                cur.execute("""
                    SELECT f.primary_category, COUNT(*)
                    FROM market_fingerprints f JOIN markets m ON m.market_id = f.market_id
                    WHERE m.creator_wallet = %s AND f.primary_category IS NOT NULL
                    GROUP BY f.primary_category ORDER BY COUNT(*) DESC
                """, (wallet,))
                result["primary_categories"] = {r[0]: r[1] for r in cur.fetchall()}

                cur.execute("""
                    SELECT f.sentiment_lean, COUNT(*)
                    FROM market_fingerprints f JOIN markets m ON m.market_id = f.market_id
                    WHERE m.creator_wallet = %s AND f.sentiment_lean IS NOT NULL
                    GROUP BY f.sentiment_lean ORDER BY COUNT(*) DESC
                """, (wallet,))
                result["sentiments"] = {r[0]: r[1] for r in cur.fetchall()}

                cur.execute("""
                    SELECT posted_hour, COUNT(*) FROM markets
                    WHERE creator_wallet = %s AND posted_hour IS NOT NULL
                    GROUP BY posted_hour ORDER BY COUNT(*) DESC
                """, (wallet,))
                result["posting_hours"] = {r[0]: r[1] for r in cur.fetchall()}

    except Exception as e:
        logger.error(f"error analyzing creator {wallet}: {e}")
    set_cached(cache_key, result)
    return result


def analyze_market(market_id):
    """Full analytics for a single market."""
    market_id = str(market_id or "").strip()
    cache_key = f"analytics:market:{market_id}"
    cached = get_cached(cache_key)
    if cached:
        return cached
    result = {
        "market_id": market_id, "volume": 0.0, "participants": 0,
        "comments": 0, "likes": 0, "controversy": 0.0,
        "consensus_score": 0.0, "momentum": 0.0, "badge": None,
        "primary_category": None, "secondary_categories": [],
        "sentiment_lean": None, "category": None,
        "creator": None, "title": None, "final_volume": None,
        "resolved": False, "complexity_score": 0.0,
        "emotional_words": [], "absolute_words": [],
        "posted_weekday": None, "posted_hour": None,
        "milestones": [], "history": [],
        "volume_distribution": {},
    }
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT title, creator_wallet, theme, resolved, final_volume,
                           posted_weekday, posted_hour
                    FROM markets WHERE market_id = %s
                """, (market_id,))
                mrow = cur.fetchone()
                if mrow:
                    result["title"] = mrow[0]
                    result["creator"] = mrow[1]
                    result["category"] = mrow[2]
                    result["resolved"] = mrow[3]
                    result["final_volume"] = float(mrow[4]) if mrow[4] else None
                    result["posted_weekday"] = mrow[5]
                    result["posted_hour"] = mrow[6]

                cur.execute("""
                    SELECT latest_volume, latest_participants, latest_controversy,
                           badge, consensus_score
                    FROM market_scores WHERE market_id = %s
                """, (market_id,))
                srow = cur.fetchone()
                if srow:
                    result["volume"] = float(srow[0] or 0)
                    result["participants"] = int(srow[1] or 0)
                    result["controversy"] = float(srow[2] or 0)
                    result["badge"] = srow[3]
                    result["consensus_score"] = float(srow[4] or 0)

                cur.execute("""
                    SELECT COALESCE(MAX(comments_count), 0), COALESCE(MAX(likes_count), 0)
                    FROM market_snapshots WHERE market_id = %s
                """, (market_id,))
                srow = cur.fetchone()
                if srow:
                    result["comments"] = int(srow[0] or 0)
                    result["likes"] = int(srow[1] or 0)

                cur.execute("""
                    SELECT total_volume FROM market_snapshots
                    WHERE market_id = %s ORDER BY snapshot_at DESC LIMIT 2
                """, (market_id,))
                snaps = [float(r[0]) for r in cur.fetchall()]
                if len(snaps) == 2 and snaps[1] > 0:
                    result["momentum"] = round((snaps[0] - snaps[1]) / snaps[1], 3)

                cur.execute("""
                    SELECT primary_category, secondary_categories, sentiment_lean,
                           complexity_score, emotional_words, absolute_words
                    FROM market_fingerprints WHERE market_id = %s
                """, (market_id,))
                frow = cur.fetchone()
                if frow:
                    result["primary_category"] = frow[0]
                    result["secondary_categories"] = frow[1] or []
                    result["sentiment_lean"] = frow[2]
                    result["complexity_score"] = float(frow[3] or 0)
                    result["emotional_words"] = frow[4] or []
                    result["absolute_words"] = frow[5] or []

                cur.execute("""
                    SELECT total_volume FROM market_snapshots WHERE market_id = %s
                    ORDER BY snapshot_at DESC LIMIT 20
                """, (market_id,))
                vols = [float(r[0] or 0) for r in cur.fetchall()]
                if vols:
                    result["volume_distribution"] = calculate_distribution(vols)

                cur.execute("""
                    SELECT yes_pool, no_pool, total_volume, total_participants,
                           comments_count, likes_count, snapshot_at
                    FROM market_snapshots WHERE market_id = %s
                    ORDER BY snapshot_at DESC LIMIT 20
                """, (market_id,))
                result["history"] = [
                    {"yes_pool": float(r[0] or 0), "no_pool": float(r[1] or 0),
                     "volume": float(r[2] or 0), "participants": int(r[3] or 0),
                     "comments": int(r[4] or 0), "likes": int(r[5] or 0),
                     "time": r[6].isoformat() if r[6] else None}
                    for r in cur.fetchall()
                ]

                result["milestones"] = get_market_milestones(market_id)

    except Exception as e:
        logger.error(f"error analyzing market {market_id}: {e}")
    set_cached(cache_key, result)
    return result


def analyze_category(theme, force_refresh=False):
    """Aggregate analytics for a topic category."""
    cache_key = f"analytics:category:{theme}"
    if not force_refresh:
        cached = get_cached(cache_key)
        if cached:
            return cached
    result = {
        "theme": theme, "total_markets": 0, "total_volume": 0.0,
        "avg_volume": 0.0, "peak_volume": 0.0,
        "volume_distribution": {},
        "avg_participants": 0.0, "avg_controversy": 0.0,
        "avg_consensus": 0.0,
        "total_comments": 0, "avg_comments": 0.0,
        "avg_market_duration_hours": 0.0,
        "primary_category_breakdown": {}, "sentiment_breakdown": {},
        "top_creators": [], "success_metrics": {},
        "best_posting_hours": [],
    }
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*), COALESCE(SUM(ms.latest_volume), 0),
                           COALESCE(AVG(ms.latest_volume), 0),
                           COALESCE(MAX(ms.latest_volume), 0),
                           COALESCE(AVG(ms.latest_participants), 0),
                           COALESCE(AVG(ms.latest_controversy), 0),
                           COALESCE(AVG(ms.consensus_score), 0)
                    FROM market_scores ms
                    JOIN markets m ON m.market_id = ms.market_id
                    WHERE m.theme = %s
                """, (theme,))
                row = cur.fetchone()
                if row:
                    result["total_markets"] = row[0]
                    result["total_volume"] = round(float(row[1] or 0), 2)
                    result["avg_volume"] = round(float(row[2] or 0), 2)
                    result["peak_volume"] = round(float(row[3] or 0), 2)
                    result["avg_participants"] = round(float(row[4] or 0), 1)
                    result["avg_controversy"] = round(float(row[5] or 0), 3)
                    result["avg_consensus"] = round(float(row[6] or 0), 3)

                cur.execute("""
                    SELECT ms.latest_volume FROM market_scores ms
                    JOIN markets m ON m.market_id = ms.market_id
                    WHERE m.theme = %s AND ms.latest_volume > 0
                """, (theme,))
                volumes = [float(r[0]) for r in cur.fetchall() if r[0]]
                if volumes:
                    result["volume_distribution"] = calculate_distribution(volumes)

                cur.execute("""
                    SELECT COALESCE(SUM(s.comments_count), 0)
                    FROM market_snapshots s
                    JOIN markets m ON m.market_id = s.market_id
                    WHERE m.theme = %s
                """, (theme,))
                row = cur.fetchone()
                if row:
                    result["total_comments"] = int(row[0] or 0)
                    if result["total_markets"] > 0:
                        result["avg_comments"] = round(result["total_comments"] / result["total_markets"], 1)

                cur.execute("""
                    SELECT COALESCE(AVG(EXTRACT(EPOCH FROM (end_time - created_at_api)) / 3600), 0)
                    FROM markets WHERE theme = %s AND end_time IS NOT NULL AND created_at_api IS NOT NULL
                """, (theme,))
                row = cur.fetchone()
                if row:
                    result["avg_market_duration_hours"] = round(float(row[0] or 0), 1)

                cur.execute("""
                    SELECT f.primary_category, COUNT(*)
                    FROM market_fingerprints f JOIN markets m ON m.market_id = f.market_id
                    WHERE m.theme = %s AND f.primary_category IS NOT NULL
                    GROUP BY f.primary_category ORDER BY COUNT(*) DESC
                """, (theme,))
                result["primary_category_breakdown"] = {r[0]: r[1] for r in cur.fetchall()}

                cur.execute("""
                    SELECT f.sentiment_lean, COUNT(*)
                    FROM market_fingerprints f JOIN markets m ON m.market_id = f.market_id
                    WHERE m.theme = %s AND f.sentiment_lean IS NOT NULL
                    GROUP BY f.sentiment_lean ORDER BY COUNT(*) DESC
                """, (theme,))
                result["sentiment_breakdown"] = {r[0]: r[1] for r in cur.fetchall()}

                cur.execute("""
                    SELECT m.creator_wallet, SUM(ms.latest_volume) as vol
                    FROM market_scores ms JOIN markets m ON m.market_id = ms.market_id
                    WHERE m.theme = %s AND m.creator_wallet IS NOT NULL AND m.creator_wallet != ''
                    GROUP BY m.creator_wallet ORDER BY vol DESC LIMIT 10
                """, (theme,))
                result["top_creators"] = [{"wallet": r[0], "volume": round(float(r[1]), 2)} for r in cur.fetchall()]

                cur.execute("""
                    SELECT COUNT(*), COALESCE(AVG(final_volume), 0)
                    FROM markets WHERE theme = %s AND resolved = true AND final_volume IS NOT NULL
                """, (theme,))
                row = cur.fetchone()
                if row and row[0] > 0:
                    result["success_metrics"]["resolved"] = row[0]
                    result["success_metrics"]["avg_final_volume"] = round(float(row[1]), 2)

                cur.execute("""
                    SELECT posted_hour, COUNT(*), COALESCE(AVG(final_volume), 0)
                    FROM markets WHERE theme = %s AND posted_hour IS NOT NULL AND resolved = true
                    GROUP BY posted_hour ORDER BY AVG(final_volume) DESC LIMIT 5
                """, (theme,))
                result["best_posting_hours"] = [
                    {"hour": r[0], "count": r[1], "avg_volume": round(float(r[2]), 2)}
                    for r in cur.fetchall()
                ]

    except Exception as e:
        logger.error(f"error analyzing category {theme}: {e}")
    set_cached(cache_key, result)
    return result


def analyze_question_patterns(force_refresh=False):
    """Cross-market analysis of question patterns vs performance."""
    cache_key = "analytics:question_patterns"
    if not force_refresh:
        cached = get_cached(cache_key)
        if cached:
            return cached
    result = {
        "opening_word_performance": [],
        "word_count_buckets": [],
        "primary_category_performance": [],
        "sentiment_performance": [],
        "complexity_performance": [],
        "emotional_impact": [],
        "absolute_impact": [],
        "posting_time_performance": [],
    }
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT f.opening_word, COUNT(*) as cnt,
                           COALESCE(AVG(ms.latest_volume), 0) as avg_vol,
                           COALESCE(AVG(ms.latest_controversy), 0) as avg_cont,
                           COALESCE(AVG(ms.latest_participants), 0) as avg_part,
                           COALESCE(AVG(ms.consensus_score), 0) as avg_cons
                    FROM market_fingerprints f
                    JOIN market_scores ms ON ms.market_id = f.market_id
                    WHERE f.opening_word IS NOT NULL
                    GROUP BY f.opening_word HAVING COUNT(*) >= 3
                    ORDER BY avg_vol DESC LIMIT 20
                """)
                result["opening_word_performance"] = [
                    {"word": r[0], "count": r[1], "avg_volume": round(float(r[2]), 2),
                     "avg_controversy": round(float(r[3]), 3), "avg_participants": round(float(r[4]), 1),
                     "avg_consensus": round(float(r[5]), 3)}
                    for r in cur.fetchall()
                ]

                cur.execute("""
                    SELECT f.word_count_bucket, COUNT(*) as cnt,
                           COALESCE(AVG(ms.latest_volume), 0) as avg_vol,
                           COALESCE(AVG(ms.latest_controversy), 0) as avg_cont,
                           COALESCE(AVG(ms.latest_participants), 0) as avg_part,
                           COALESCE(AVG(ms.consensus_score), 0) as avg_cons
                    FROM market_fingerprints f
                    JOIN market_scores ms ON ms.market_id = f.market_id
                    WHERE f.word_count_bucket IS NOT NULL
                    GROUP BY f.word_count_bucket ORDER BY avg_vol DESC
                """)
                result["word_count_buckets"] = [
                    {"bucket": r[0], "count": r[1], "avg_volume": round(float(r[2]), 2),
                     "avg_controversy": round(float(r[3]), 3), "avg_participants": round(float(r[4]), 1),
                     "avg_consensus": round(float(r[5]), 3)}
                    for r in cur.fetchall()
                ]

                cur.execute("""
                    SELECT f.primary_category, COUNT(*) as cnt,
                           COALESCE(AVG(ms.latest_volume), 0) as avg_vol,
                           COALESCE(AVG(ms.latest_controversy), 0) as avg_cont,
                           COALESCE(AVG(ms.latest_participants), 0) as avg_part,
                           COALESCE(AVG(ms.consensus_score), 0) as avg_cons
                    FROM market_fingerprints f
                    JOIN market_scores ms ON ms.market_id = f.market_id
                    WHERE f.primary_category IS NOT NULL
                    GROUP BY f.primary_category ORDER BY avg_vol DESC
                """)
                result["primary_category_performance"] = [
                    {"type": r[0], "count": r[1], "avg_volume": round(float(r[2]), 2),
                     "avg_controversy": round(float(r[3]), 3), "avg_participants": round(float(r[4]), 1),
                     "avg_consensus": round(float(r[5]), 3)}
                    for r in cur.fetchall()
                ]

                cur.execute("""
                    SELECT f.sentiment_lean, COUNT(*) as cnt,
                           COALESCE(AVG(ms.latest_volume), 0) as avg_vol,
                           COALESCE(AVG(ms.latest_controversy), 0) as avg_cont,
                           COALESCE(AVG(ms.latest_participants), 0) as avg_part,
                           COALESCE(AVG(ms.consensus_score), 0) as avg_cons
                    FROM market_fingerprints f
                    JOIN market_scores ms ON ms.market_id = f.market_id
                    WHERE f.sentiment_lean IS NOT NULL
                    GROUP BY f.sentiment_lean ORDER BY avg_vol DESC
                """)
                result["sentiment_performance"] = [
                    {"sentiment": r[0], "count": r[1], "avg_volume": round(float(r[2]), 2),
                     "avg_controversy": round(float(r[3]), 3), "avg_participants": round(float(r[4]), 1),
                     "avg_consensus": round(float(r[5]), 3)}
                    for r in cur.fetchall()
                ]

                cur.execute("""
                    SELECT
                        CASE WHEN f.complexity_score < 3 THEN 'simple'
                             WHEN f.complexity_score < 6 THEN 'moderate'
                             ELSE 'complex' END as level,
                    COUNT(*) as cnt,
                    COALESCE(AVG(ms.latest_volume), 0) as avg_vol,
                    COALESCE(AVG(ms.latest_controversy), 0) as avg_cont,
                    COALESCE(AVG(ms.consensus_score), 0) as avg_cons
                    FROM market_fingerprints f
                    JOIN market_scores ms ON ms.market_id = f.market_id
                    GROUP BY level ORDER BY avg_vol DESC
                """)
                result["complexity_performance"] = [
                    {"level": r[0], "count": r[1], "avg_volume": round(float(r[2]), 2),
                     "avg_controversy": round(float(r[3]), 3), "avg_consensus": round(float(r[4]), 3)}
                    for r in cur.fetchall()
                ]

                cur.execute("""
                    SELECT
                        CASE WHEN array_length(f.emotional_words, 1) > 0 THEN 'emotional' ELSE 'neutral' END as level,
                    COUNT(*) as cnt,
                    COALESCE(AVG(ms.latest_volume), 0) as avg_vol,
                    COALESCE(AVG(ms.latest_controversy), 0) as avg_cont,
                    COALESCE(AVG(ms.latest_participants), 0) as avg_part
                    FROM market_fingerprints f
                    JOIN market_scores ms ON ms.market_id = f.market_id
                    GROUP BY level ORDER BY avg_vol DESC
                """)
                result["emotional_impact"] = [
                    {"level": r[0], "count": r[1], "avg_volume": round(float(r[2]), 2),
                     "avg_controversy": round(float(r[3]), 3), "avg_participants": round(float(r[4]), 1)}
                    for r in cur.fetchall()
                ]

                cur.execute("""
                    SELECT
                        CASE WHEN array_length(f.absolute_words, 1) > 0 THEN 'absolute' ELSE 'neutral' END as level,
                    COUNT(*) as cnt,
                    COALESCE(AVG(ms.latest_volume), 0) as avg_vol,
                    COALESCE(AVG(ms.latest_controversy), 0) as avg_cont,
                    COALESCE(AVG(ms.consensus_score), 0) as avg_cons
                    FROM market_fingerprints f
                    JOIN market_scores ms ON ms.market_id = f.market_id
                    GROUP BY level ORDER BY avg_vol DESC
                """)
                result["absolute_impact"] = [
                    {"level": r[0], "count": r[1], "avg_volume": round(float(r[2]), 2),
                     "avg_controversy": round(float(r[3]), 3), "avg_consensus": round(float(r[4]), 3)}
                    for r in cur.fetchall()
                ]

                cur.execute("""
                    SELECT m.posted_hour, COUNT(*) as cnt,
                           COALESCE(AVG(ms.latest_volume), 0) as avg_vol,
                           COALESCE(AVG(ms.latest_controversy), 0) as avg_cont,
                           COALESCE(AVG(ms.consensus_score), 0) as avg_cons
                    FROM markets m
                    JOIN market_scores ms ON ms.market_id = m.market_id
                    WHERE m.posted_hour IS NOT NULL
                    GROUP BY m.posted_hour ORDER BY avg_vol DESC
                """)
                result["posting_time_performance"] = [
                    {"hour": r[0], "count": r[1], "avg_volume": round(float(r[2]), 2),
                     "avg_controversy": round(float(r[3]), 3), "avg_consensus": round(float(r[4]), 3)}
                    for r in cur.fetchall()
                ]

    except Exception as e:
        logger.error(f"error analyzing question patterns: {e}")
    set_cached(cache_key, result)
    return result


def calculate_success_metrics(force_refresh=False):
    """Platform-wide success metrics with distributions and milestone predictions."""
    cache_key = "analytics:success_metrics"
    if not force_refresh:
        cached = get_cached(cache_key)
        if cached:
            return cached
    result = {
        "total_markets": 0, "resolved_markets": 0,
        "final_volume_distribution": {},
        "volume_distribution": {},
        "threshold_rates": {},
        "category_success": [],
        "milestone_predictions": [],
        "consensus_distribution": {},
        "weekday_performance": [],
    }
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM markets")
                result["total_markets"] = cur.fetchone()[0]

                cur.execute("""
                    SELECT COUNT(*), COALESCE(AVG(final_volume), 0), COALESCE(MAX(final_volume), 0)
                    FROM markets WHERE resolved = true AND final_volume IS NOT NULL
                """)
                row = cur.fetchone()
                if row and row[0] > 0:
                    result["resolved_markets"] = row[0]

                cur.execute("SELECT final_volume FROM markets WHERE resolved = true AND final_volume IS NOT NULL")
                vols = [float(r[0]) for r in cur.fetchall()]
                if vols:
                    result["final_volume_distribution"] = calculate_distribution(vols)

                cur.execute("""
                    SELECT ms.latest_volume FROM market_scores ms
                    JOIN markets m ON m.market_id = ms.market_id
                    WHERE ms.latest_volume > 0
                """)
                all_vols = [float(r[0]) for r in cur.fetchall()]
                if all_vols:
                    result["volume_distribution"] = calculate_distribution(all_vols)

                for t in [500, 1000, 2000, 5000]:
                    if result["resolved_markets"] > 0:
                        cur.execute("SELECT COUNT(*) FROM markets WHERE final_volume >= %s AND resolved = true", (t,))
                        cnt = cur.fetchone()[0]
                        result["threshold_rates"][f"${t}+"] = round(cnt / result["resolved_markets"], 3)

                cur.execute("""
                    SELECT m.theme, COUNT(*) as total,
                           COALESCE(AVG(m.final_volume), 0) as avg_vol,
                           COALESCE(MAX(m.final_volume), 0) as peak_vol,
                           COALESCE(AVG(ms.consensus_score), 0) as avg_cons
                    FROM markets m
                    LEFT JOIN market_scores ms ON ms.market_id = m.market_id
                    WHERE m.resolved = true AND m.final_volume IS NOT NULL
                    GROUP BY m.theme ORDER BY avg_vol DESC
                """)
                result["category_success"] = [
                    {"theme": r[0] or "other", "resolved": r[1],
                     "avg_volume": round(float(r[2]), 2), "peak_volume": round(float(r[3]), 2),
                     "avg_consensus": round(float(r[4]), 3)}
                    for r in cur.fetchall()
                ]

                cur.execute("""
                    SELECT ms2.milestone_hours,
                           COUNT(*) as total,
                           AVG(ms2.volume) as avg_vol_at_milestone
                    FROM market_milestones ms2
                    JOIN markets m ON m.market_id = ms2.market_id
                    WHERE m.resolved = true AND m.final_volume IS NOT NULL
                    GROUP BY ms2.milestone_hours
                    ORDER BY ms2.milestone_hours
                """)
                milestone_data = [
                    {"milestone_hours": r[0], "count": r[1], "avg_volume": round(float(r[2]), 2)}
                    for r in cur.fetchall()
                ]
                result["milestone_predictions"] = milestone_data

                cur.execute("""
                    SELECT ms.consensus_score, COUNT(*), COALESCE(AVG(m.final_volume), 0)
                    FROM market_scores ms JOIN markets m ON m.market_id = ms.market_id
                    WHERE ms.consensus_score > 0 AND m.resolved = true AND m.final_volume IS NOT NULL
                    GROUP BY ms.consensus_score ORDER BY ms.consensus_score DESC LIMIT 10
                """)
                result["consensus_distribution"] = [
                    {"consensus": round(float(r[0]), 3), "count": r[1], "avg_volume": round(float(r[2]), 2)}
                    for r in cur.fetchall()
                ]

                cur.execute("""
                    SELECT m.posted_weekday, COUNT(*), COALESCE(AVG(m.final_volume), 0)
                    FROM markets m
                    WHERE m.posted_weekday IS NOT NULL AND m.resolved = true AND m.final_volume IS NOT NULL
                    GROUP BY m.posted_weekday ORDER BY AVG(m.final_volume) DESC
                """)
                result["weekday_performance"] = [
                    {"weekday": r[0], "count": r[1], "avg_volume": round(float(r[2]), 2)}
                    for r in cur.fetchall()
                ]

    except Exception as e:
        logger.error(f"error calculating success metrics: {e}")
    set_cached(cache_key, result)
    return result


def rank_markets(sort_by="volume", limit=20):
    """Rank markets by various metrics. Parameterized to prevent SQL injection."""
    valid_sorts = {
        "volume": "ms.latest_volume DESC",
        "engagement": "(COALESCE(ms.latest_participants, 0) + COALESCE(ms.latest_volume, 0)) DESC",
        "controversy": "ms.latest_controversy DESC",
        "consensus": "ms.consensus_score DESC",
        "comments": "COALESCE((SELECT MAX(s.comments_count) FROM market_snapshots s WHERE s.market_id = m.market_id), 0) DESC",
        "growth": "COALESCE((SELECT s1.total_volume - s2.total_volume FROM market_snapshots s1 JOIN market_snapshots s2 ON s2.market_id = s1.market_id AND s2.snapshot_at < s1.snapshot_at WHERE s1.market_id = m.market_id ORDER BY s1.snapshot_at DESC LIMIT 1), 0) DESC",
        "balanced": "ABS(COALESCE(ms.latest_volume, 0) - 2000) ASC",
        "one_sided": "ABS(COALESCE(ms.latest_volume, 0) - 2000) DESC",
    }
    if sort_by not in valid_sorts:
        sort_by = "volume"
    cache_key = f"analytics:rank:{sort_by}:{limit}"
    cached = get_cached(cache_key)
    if cached:
        return cached
    order_clause = valid_sorts[sort_by]
    result = []
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT m.market_id, m.title, m.theme, m.creator_wallet, m.resolved, m.final_volume,
                           ms.latest_volume, ms.latest_participants, ms.latest_controversy,
                           ms.badge, ms.consensus_score
                    FROM markets m
                    JOIN market_scores ms ON ms.market_id = m.market_id
                    WHERE ms.latest_volume > 0
                    ORDER BY {order_clause}
                    LIMIT %s
                """, (limit,))
                result = [
                    {
                        "market_id": r[0], "title": r[1], "theme": r[2], "creator": r[3],
                        "resolved": r[4], "final_volume": float(r[5]) if r[5] else None,
                        "volume": float(r[6] or 0), "participants": int(r[7] or 0),
                        "controversy": round(float(r[8] or 0), 3), "badge": r[9],
                        "consensus_score": round(float(r[10] or 0), 3),
                    }
                    for r in cur.fetchall()
                ]
    except Exception as e:
        logger.error(f"error ranking markets by {sort_by}: {e}")
    set_cached(cache_key, result)
    return result


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

    # ── Multi-tag classification ──
    primary_category, secondary_categories = classify_question_types(title)
    all_categories = [primary_category] + secondary_categories

    # ── Question features ──
    features = extract_question_features(title)

    # ── Posting time ──
    posting_time = extract_posting_time(market)

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO markets (
                        market_id, title, description, creator_wallet, market_pubkey, theme,
                        end_time, go_live_at, created_at_api, is_private, cover_image_url,
                        first_staker_promo, first_staker_match, first_staker_min, sponsor_count,
                        resolved, outcome, hidden, last_updated_at, last_synced_at,
                        posted_weekday, posted_hour, posted_month
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, NOW(),
                        %s, %s, %s
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
                        last_synced_at = NOW(),
                        posted_weekday = COALESCE(EXCLUDED.posted_weekday, posted_weekday),
                        posted_hour = COALESCE(EXCLUDED.posted_hour, posted_hour),
                        posted_month = COALESCE(EXCLUDED.posted_month, posted_month)
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
                    posting_time["posted_weekday"], posting_time["posted_hour"], posting_time["posted_month"],
                ))
                if creator:
                    cur.execute("""
                        INSERT INTO creators (wallet_address, last_seen_at, total_markets)
                        VALUES (%s, NOW(), 1)
                        ON CONFLICT (wallet_address) DO UPDATE SET
                            last_seen_at = NOW()
                    """, (creator,))
                cur.execute("""
                    INSERT INTO creator_categories (wallet_address, theme, market_count)
                    VALUES (%s, %s, 1)
                    ON CONFLICT (wallet_address, theme) DO NOTHING
                """, (creator, theme))

                sentiment_lean = classify_sentiment(title)

                cur.execute("""
                    INSERT INTO market_fingerprints (
                        market_id, opening_word, word_count, has_question_mark,
                        is_yes_no, topic_tags, detected_keywords, category,
                        question_type, sentiment_lean,
                        primary_category, secondary_categories, all_categories,
                        complexity_score, emotional_words, absolute_words,
                        word_count_bucket
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (market_id) DO UPDATE SET
                        primary_category = EXCLUDED.primary_category,
                        secondary_categories = EXCLUDED.secondary_categories,
                        all_categories = EXCLUDED.all_categories,
                        sentiment_lean = EXCLUDED.sentiment_lean,
                        complexity_score = EXCLUDED.complexity_score,
                        emotional_words = EXCLUDED.emotional_words,
                        absolute_words = EXCLUDED.absolute_words,
                        word_count_bucket = EXCLUDED.word_count_bucket
                """, (
                    market_id, opening_word, word_count, has_question,
                    is_yes_no, topic_tags, keywords, theme,
                    primary_category, sentiment_lean,
                    primary_category, secondary_categories, all_categories,
                    features["complexity_score"], features["emotional_words"],
                    features["absolute_words"], features["word_count_bucket"],
                ))

                try:
                    cur.execute("""
                        INSERT INTO market_dna (market_id, title, question_type, sentiment_lean, category)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (market_id) DO UPDATE SET
                            title = EXCLUDED.title,
                            question_type = EXCLUDED.question_type,
                            sentiment_lean = EXCLUDED.sentiment_lean,
                            category = EXCLUDED.category
                    """, (market_id, title, primary_category, sentiment_lean, theme))
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"error ingesting market {market_id}: {e}")


def snapshot_market(market_id, market):
    market_id = str(market_id or "").strip()
    if not market_id:
        return
    yes_pool_raw = int(market.get("yes_pool") or 0)
    no_pool_raw = int(market.get("no_pool") or 0)
    yes_pool = yes_pool_raw / USDC_DIVISOR
    no_pool = no_pool_raw / USDC_DIVISOR
    yes_votes = int(market.get("yes_votes") or 0)
    no_votes = int(market.get("no_votes") or 0)
    likes = int(market.get("likes_count") or 0)
    comments = int(market.get("comments_count") or 0)
    total_volume = yes_pool + no_pool
    total_participants = yes_votes + no_votes
    controversy = 0.0
    if total_volume > 0 and max(yes_pool, no_pool) > 0:
        controversy = min(yes_pool, no_pool) / max(yes_pool, no_pool)
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
                    market_id, round(yes_pool, 6), round(no_pool, 6), yes_votes, no_votes,
                    likes, comments, round(total_volume, 6), total_participants,
                    round(controversy, 4), round(avg_stake, 6),
                ))

                # ── Record growth milestones ──
                try:
                    created_at_api = parse_api_datetime(market.get("created_at"))
                    if not created_at_api:
                        cur2 = conn.cursor()
                        cur2.execute("SELECT created_at_api FROM markets WHERE market_id = %s", (market_id,))
                        row = cur2.fetchone()
                        if row and row[0]:
                            created_at_api = row[0]
                        cur2.close()
                    check_and_record_milestone(
                        market_id, yes_pool, no_pool, total_volume,
                        total_participants, comments, created_at_api
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"error snapshotting market {market_id}: {e}")


def capture_market_resolution(market_id, market_data):
    """Store final volume and resolution metadata when a market resolves."""
    market_id = str(market_id or "").strip()
    if not market_id:
        return
    try:
        yes_pool_raw = int(market_data.get("yes_pool") or 0)
        no_pool_raw = int(market_data.get("no_pool") or 0)
        yes_pool = yes_pool_raw / USDC_DIVISOR
        no_pool = no_pool_raw / USDC_DIVISOR
        final_volume = round(yes_pool + no_pool, 6)
        final_consensus = calculate_consensus_score(yes_pool, no_pool)
        now = now_utc()
        resolved_weekday = now.weekday()
        resolved_hour = now.hour
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE markets SET final_volume = %s,
                        resolved_weekday = %s, resolved_hour = %s
                    WHERE market_id = %s
                """, (final_volume, resolved_weekday, resolved_hour, market_id))
                cur.execute("""
                    INSERT INTO market_events (market_id, event_type, event_data)
                    VALUES (%s, 'market_resolved', %s)
                """, (market_id, json.dumps({
                    "final_volume": final_volume,
                    "final_consensus": final_consensus,
                    "resolved_weekday": resolved_weekday,
                    "resolved_hour": resolved_hour,
                })))
                cur.execute("""
                    UPDATE market_scores SET consensus_score = %s
                    WHERE market_id = %s
                """, (round(final_consensus, 4), market_id))
        invalidate_cache("market:")
        invalidate_cache("analytics:")
    except Exception as e:
        logger.error(f"error capturing resolution for {market_id}: {e}")


def recalculate_creator_totals():
    """
    Recalculate creator lifetime stats from latest market state.
    total_volume = sum of latest total_volume per unique market
    best_volume  = max of latest total_volume across markets
    total_markets = count of distinct market_ids
    """
    global _last_recalc_run
    now = time.time()
    if now - _last_recalc_run < RECALC_INTERVAL:
        return
    _last_recalc_run = now
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Get each creator's markets with their latest snapshot volume
                cur.execute("""
                    SELECT
                        m.creator_wallet,
                        m.market_id,
                        COALESCE(
                            (SELECT ms.total_volume FROM market_snapshots ms
                             WHERE ms.market_id = m.market_id
                             ORDER BY ms.snapshot_at DESC LIMIT 1),
                            0
                        ) as latest_volume
                    FROM markets m
                    WHERE m.creator_wallet IS NOT NULL
                      AND m.creator_wallet != ''
                """)
                rows = cur.fetchall()

                # Aggregate per creator
                creators = {}
                for wallet, market_id, volume in rows:
                    if wallet not in creators:
                        creators[wallet] = {"markets": set(), "volumes": []}
                    creators[wallet]["markets"].add(market_id)
                    creators[wallet]["volumes"].append(float(volume or 0))

                # Update each creator
                for wallet, data in creators.items():
                    total_vol = round(sum(data["volumes"]), 6)
                    best_vol = round(max(data["volumes"]), 6) if data["volumes"] else 0
                    market_count = len(data["markets"])
                    cur.execute("""
                        UPDATE creators SET
                            total_volume = %s,
                            best_volume = %s,
                            total_markets = %s
                        WHERE wallet_address = %s
                    """, (total_vol, best_vol, market_count, wallet))

                logger.info(f"recalculated totals for {len(creators)} creators")
    except Exception as e:
        logger.error(f"error recalculating creator totals: {e}")


def recalculate_creator_categories():
    """
    Recalculate creator category totals from latest market state.
    total_volume per (wallet, theme) = sum of latest volumes for that theme.
    Shares rate limit with recalculate_creator_totals.
    """
    global _last_recalc_run
    now = time.time()
    if now - _last_recalc_run < RECALC_INTERVAL:
        return
    _last_recalc_run = now
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        m.creator_wallet,
                        m.theme,
                        m.market_id,
                        COALESCE(
                            (SELECT ms.total_volume FROM market_snapshots ms
                             WHERE ms.market_id = m.market_id
                             ORDER BY ms.snapshot_at DESC LIMIT 1),
                            0
                        ) as latest_volume
                    FROM markets m
                    WHERE m.creator_wallet IS NOT NULL
                      AND m.creator_wallet != ''
                """)
                rows = cur.fetchall()

                cats = {}
                for wallet, theme, market_id, volume in rows:
                    key = (wallet, theme)
                    if key not in cats:
                        cats[key] = {"markets": set(), "volumes": []}
                    cats[key]["markets"].add(market_id)
                    cats[key]["volumes"].append(float(volume or 0))

                for (wallet, theme), data in cats.items():
                    total_vol = round(sum(data["volumes"]), 6)
                    market_count = len(data["markets"])
                    cur.execute("""
                        INSERT INTO creator_categories (wallet_address, theme, market_count, total_volume)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (wallet_address, theme) DO UPDATE SET
                            market_count = EXCLUDED.market_count,
                            total_volume = EXCLUDED.total_volume
                    """, (wallet, theme, market_count, total_vol))

                logger.info(f"recalculated categories for {len(cats)} wallet-theme pairs")
    except Exception as e:
        logger.error(f"error recalculating creator categories: {e}")


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
                        SELECT total_volume, total_participants, controversy_score,
                               yes_pool, no_pool
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
                    yes_pool = float(row[3] or 0)
                    no_pool = float(row[4] or 0)
                    consensus = calculate_consensus_score(yes_pool, no_pool)
                    ms = stats.get(market_id, {})
                    max_vol = ms.get("max_vol", 0)
                    max_part = ms.get("max_part", 0)
                    volume_pct = (volume / max_vol * 100) if max_vol > 0 else 0
                    engagement = (participants / volume * 1000) if volume > 0 else 0
                    cur.execute("""
                        SELECT creator_wallet FROM markets WHERE market_id = %s
                    """, (market_id,))
                    creator_row = cur.fetchone()
                    creator = creator_row[0] if creator_row else None
                    cur.execute("""
                        INSERT INTO market_scores (
                            market_id, volume_percentile, controversy, engagement_ratio,
                            composite_score, scored_at,
                            latest_volume, latest_participants, latest_controversy,
                            creator, consensus_score
                        ) VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s)
                        ON CONFLICT (market_id) DO UPDATE SET
                            volume_percentile = EXCLUDED.volume_percentile,
                            controversy = EXCLUDED.controversy,
                            engagement_ratio = EXCLUDED.engagement_ratio,
                            composite_score = EXCLUDED.composite_score,
                            scored_at = NOW(),
                            latest_volume = EXCLUDED.latest_volume,
                            latest_participants = EXCLUDED.latest_participants,
                            latest_controversy = EXCLUDED.latest_controversy,
                            creator = EXCLUDED.creator,
                            consensus_score = EXCLUDED.consensus_score
                    """, (
                        market_id,
                        round(volume_pct, 2),
                        round(controversy, 4),
                        round(engagement, 4),
                        round((volume_pct * 0.4 + controversy * 100 * 0.3 + engagement * 0.3), 2),
                        round(volume, 6),
                        participants,
                        round(controversy, 4),
                        creator,
                        round(consensus, 4),
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


def create_market_keyboard(market_id, market_link, scope="new_market", context=None, label=None):
    """create inline buttons for market notifications"""
    context = context or {}
    context.setdefault("market_id", market_id)
    context.setdefault("market_link", market_link)
    keyboard = types.InlineKeyboardMarkup()
    button_label = label or "Vote Now"
    keyboard.add(types.InlineKeyboardButton(button_label, url=market_link))
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


def build_market_promo_text(market, include_first_staker=True, include_sponsor=True):
    lines = []

    if include_first_staker and market.get("first_staker_promo_available"):
        match_amount = market.get("first_staker_match_usdc")
        min_stake = market.get("first_staker_min_stake_usdc")
        if match_amount and min_stake:
            lines.append(
                f"🎁 First-staker promo: ${escape_text(match_amount)} match for ${escape_text(min_stake)}+ stake"
            )
        else:
            lines.append("🎁 First-staker promo available")

    if include_sponsor:
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


def build_market_template_context(market, ai_message=None, is_premium=False):
    market_id = str(market.get("market_id", "")).strip()
    raw_theme = normalize_theme(market.get("theme", "other"))
    end_time_unix = market.get("end_time")
    end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None) if end_time_unix else None
    go_live_at = get_market_go_live_at(market)
    include_first_staker = is_premium or ENABLE_PUBLIC_FIRST_STAKER_ALERT
    context = {
        "market_id": market_id,
        "market_link": build_market_link(market_id) if market_id else "",
        "title": escape_text(market.get("title", "")),
        "theme": escape_text(format_theme(raw_theme)),
        "raw_theme": escape_text(raw_theme),
        "close_time": escape_text(format_market_time(end_time) if end_time else ""),
        "go_live_time": escape_text(format_market_time(go_live_at) if go_live_at else ""),
        "promo_text": escape_text(build_market_promo_text(market, include_first_staker=include_first_staker, include_sponsor=is_premium)),
        "ai_text": ai_message or "",
        "cover_image": escape_text(get_market_cover_image(market) or ""),
        "first_staker_match_usdc": escape_text(market.get("first_staker_match_usdc", "")) if include_first_staker else "",
        "first_staker_min_stake_usdc": escape_text(market.get("first_staker_min_stake_usdc", "")) if include_first_staker else "",
        "sponsor_match_count": escape_text(market.get("sponsor_match_count", "")) if is_premium else "",
    }
    return context


def build_new_market_notification(market, ai_message, is_premium=False):
    title = str(market.get("title", "")).strip()
    end_time_unix = market.get("end_time")
    end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None)
    end_time_str = format_market_time(end_time)
    include_first_staker = is_premium or ENABLE_PUBLIC_FIRST_STAKER_ALERT
    promo_text = build_market_promo_text(market, include_first_staker=include_first_staker)
    body_text = ai_message or build_fast_market_cta(title, "new")
    featured = is_featured_creator(market)

    if featured:
        teaser = get_random_teaser(sum(ord(c) for c in title))
        message = (
            f"⭐ <b>EDITOR'S PICK</b>\n\n"
            f"{custom_emoji('live', '🟢')} <b>FIRST STAKER SIGNAL</b>\n\n"
            f"🔥 <b>{escape_text(title)}</b>\n\n"
            f"{teaser}\n\n"
            f"⏰ Closes: {escape_text(end_time_str)}"
        )
    else:
        message = (
            f"{custom_emoji('live', '🟢')} <b>LIVE MARKET</b>\n\n"
            f"🔥 <b>{escape_text(title)}</b>\n\n"
            f"⏰ Closes: {escape_text(end_time_str)}"
        )

    if promo_text:
        message += f"\n{promo_text}"

    message += "\n\n"
    message += body_text

    return render_template_text("new_market_text", build_market_template_context(market, body_text, is_premium=is_premium), message)


def build_premium_priority_notification(market, ai_message):
    heading = get_premium_market_heading(market)
    base = build_new_market_notification(market, ai_message, is_premium=True)
    return (
        f"{custom_emoji('premium', '🛡')} <b>{escape_text(heading.upper())}</b>\n"
        "Premium first-hand notice.\n\n"
        f"{base}"
    )


def build_onchain_premium_notification(market):
    title = str(market.get("title", "")).strip()
    end_time_unix = int(market.get("end_time"))
    end_time = datetime.fromtimestamp(end_time_unix, tz=timezone.utc).replace(tzinfo=None)
    body_text = build_fast_market_cta(title, "new")
    message = (
        f"{custom_emoji('premium', '*')} <b>PREMIUM EARLY SIGNAL</b>\n"
        "You are seeing this before the public feed catches up.\n\n"
        f"<b>{escape_text(title)}</b>\n\n"
        f"Closes: {escape_text(format_market_time(end_time))}\n\n"
        f"{body_text}"
    )
    return render_template_text("onchain_early_market_text", build_market_template_context(market, body_text), message)


def build_rich_onchain_premium_notification(market):
    title = str(market.get("title", "")).strip()
    end_time_unix = int(market.get("end_time"))
    end_time = datetime.fromtimestamp(end_time_unix, tz=timezone.utc).replace(tzinfo=None)
    cover_url = get_market_cover_image(market)
    body_text = build_fast_market_cta(title, "new")
    fallback = (
        f"<h3>{custom_emoji('premium', '*')} Premium Early Signal</h3>"
        f"{build_rich_media_block(cover_url, title)}"
        f"<h2>{escape_text(title)}</h2>"
        "<table>"
        f"<tr><th>Closes</th><td>{escape_text(format_market_time(end_time))}</td></tr>"
        "<tr><th>Access</th><td>Premium members first</td></tr>"
        "</table>"
        "<blockquote>You are seeing this before the public feed catches up.</blockquote>"
        f"<p>{body_text}</p>"
    )
    return render_template_rich(
        "onchain_early_market_rich",
        build_market_template_context(market, body_text),
        fallback,
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


def build_rich_new_market(market, ai_message, heading="New Market Live", cover_url=None, is_premium=False):
    title = str(market.get("title", "")).strip()
    end_time_unix = market.get("end_time")
    end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None)
    cover_url = cover_url or get_market_cover_image(market)
    include_first_staker = is_premium or ENABLE_PUBLIC_FIRST_STAKER_ALERT
    promo_text = build_market_promo_text(market, include_first_staker=include_first_staker)
    body_text = ai_message or build_fast_market_cta(title, "new")
    featured = is_featured_creator(market)
    if featured:
        teaser = get_random_teaser(sum(ord(c) for c in title))
        heading = f"⭐ Editor's Pick — {heading}"

    html_parts = [
        f"<h3>{custom_emoji('live', '🟢')} {escape_text(heading)}</h3>",
        build_rich_media_block(cover_url, title),
        f"<h2>{escape_text(title)}</h2>",
        "<table>",
        f"<tr><th>Closes</th><td>{escape_text(format_market_time(end_time))}</td></tr>",
        "</table>",
    ]
    if featured:
        html_parts.append(f"<p><i>{escape_text(teaser)}</i></p>")
    if promo_text:
        html_parts.append(f"<blockquote>{escape_text(promo_text)}</blockquote>")
    html_parts.append(f"<p>{body_text}</p>")
    fallback = "".join(html_parts)
    return render_template_rich("new_market_rich", build_market_template_context(market, body_text, is_premium=is_premium), fallback)


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
        promo_text = build_market_promo_text(market, include_first_staker=ENABLE_PUBLIC_FIRST_STAKER_ALERT) or "Promo active"
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
        types.InlineKeyboardButton("Premium", callback_data="admin_premium"),
    )
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
        f"Last On-Chain Check: <code>{escape_text(get_bot_state('last_onchain_check', 'never'))}</code>\n"
        f"Last Market: <code>{escape_text(get_bot_state('last_market_detected', 'none'))}</code>\n"
        f"Last On-Chain Market: <code>{escape_text(get_bot_state('last_onchain_market_detected', 'none'))}</code>\n"
        f"Last Premium Lead: <code>{escape_text(get_bot_state('last_premium_lead_seconds', 'none'))}s</code>\n"
        f"Last Notification: <code>{escape_text(get_bot_state('last_notification_sent', 'none'))}</code>\n"
        f"AI: <b>{'Active' if ai_client else 'Not configured'}</b>\n"
        f"Poll Interval: <b>{MARKET_POLL_SECONDS}s</b>\n"
        f"On-Chain Poll: <b>{ONCHAIN_POLL_SECONDS}s</b>\n"
        f"Image Wait: <b>{COVER_IMAGE_WAIT_SECONDS}s</b>\n"
        f"Market Link Base: <code>{escape_text(MARKET_LINK_BASE)}</code>"
    )


def get_premium_lead_report_text():
    try:
        with get_db() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) AS reconciled_count,
                        AVG(premium_lead_seconds)::INTEGER AS average_lead,
                        MIN(premium_lead_seconds) AS min_lead,
                        MAX(premium_lead_seconds) AS max_lead
                    FROM announced_markets
                    WHERE onchain_detected_at IS NOT NULL
                      AND api_detected_at IS NOT NULL
                      AND premium_lead_seconds IS NOT NULL
                """)
                stats = cur.fetchone() or {}
                cur.execute("""
                    SELECT market_id, title, premium_lead_seconds
                    FROM announced_markets
                    WHERE premium_lead_seconds IS NOT NULL
                    ORDER BY api_detected_at DESC NULLS LAST
                    LIMIT 5
                """)
                recent = cur.fetchall()
    except Exception as e:
        logger.error(f"error building premium lead report: {e}")
        return "Could not load premium lead metrics right now."

    count = stats.get("reconciled_count") or 0
    average = stats.get("average_lead")
    min_lead = stats.get("min_lead")
    max_lead = stats.get("max_lead")
    lines = [
        "<b>Premium Lead-Time Report</b>",
        "",
        f"Reconciled markets: <b>{escape_text(count)}</b>",
        f"Average advantage: <b>{escape_text(average if average is not None else 'n/a')}s</b>",
        f"Minimum advantage: <b>{escape_text(min_lead if min_lead is not None else 'n/a')}s</b>",
        f"Maximum advantage: <b>{escape_text(max_lead if max_lead is not None else 'n/a')}s</b>",
    ]
    if recent:
        lines.append("\n<b>Recent measured markets</b>")
        for row in recent:
            lines.append(
                f"- <b>{escape_text(row.get('title') or row.get('market_id'))}</b>: "
                f"{escape_text(row.get('premium_lead_seconds'))}s"
            )
    else:
        lines.append("\nNo reconciled lead-time samples yet. This fills after an early market later appears in the public API.")
    return "\n".join(lines)


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
    return  # disabled — daily digest removed
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


def mark_onchain_premium_notified(market_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE announced_markets
                    SET premium_notified_onchain = TRUE
                    WHERE market_id = %s
                """, (str(market_id),))
    except Exception as e:
        logger.error(f"error marking on-chain premium notification for {market_id}: {e}")


def announce_onchain_market_to_premium(market):
    market_id = str(market.get("market_id", "")).strip()
    if not market_id:
        return

    existing = get_announced_market(market_id)
    if existing and existing.get("premium_notified_onchain"):
        return
    if existing and existing.get("notified_new"):
        return

    title = str(market.get("title", "")).strip()
    raw_theme = normalize_theme(market.get("theme", "other"))
    end_time = datetime.fromtimestamp(int(market.get("end_time")), tz=timezone.utc).replace(tzinfo=None)
    detected_at = now_utc()

    if not existing:
        saved = save_announced_market(
            market_id,
            title,
            raw_theme,
            end_time.isoformat(),
            notified_new=False,
            is_scheduled=False,
            go_live_at=get_market_go_live_at(market),
            source="onchain",
            market_pubkey=market.get("market_pubkey"),
            cover_image_url=get_market_cover_image(market),
            onchain_detected_at=detected_at,
            premium_notified_onchain=False,
            metadata=market,
        )
        if not saved:
            existing = get_announced_market(market_id)
            if existing and existing.get("premium_notified_onchain"):
                return

    context = build_market_template_context(market)
    keyboard = create_market_keyboard(market_id, build_market_link(market_id), scope="new_market", context=context)
    broadcast_to_all(
        build_onchain_premium_notification(market),
        market_id,
        keyboard,
        theme=raw_theme,
        notification_key=f"onchain_premium_new_{market_id}",
        photo_url=get_market_cover_image(market),
        premium_only=True,
        rich_html=build_rich_onchain_premium_notification(market),
    )
    mark_onchain_premium_notified(market_id)
    set_bot_state("last_onchain_market_detected", f"{market_id} | {title}")
    logger.info(f"premium on-chain market announced: {title}")


def announce_live_market(market, existing=None):
    market_id = str(market.get("market_id", "")).strip()
    title = str(market.get("title", "")).strip()
    raw_theme = normalize_theme(market.get("theme", "other"))
    end_time_unix = market.get("end_time")
    end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None)
    context = build_market_template_context(market, None)
    keyboard = create_market_keyboard(market_id, build_market_link(market_id), scope="new_market", context=context)
    featured = is_featured_creator(market)

    premium_already_notified = bool(existing and existing.get("premium_notified_onchain"))

    if existing:
        reconcile_market_from_api(market, existing=existing)
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
            source="api",
            market_pubkey=market.get("market_pubkey"),
            cover_image_url=get_market_cover_image(market),
            api_detected_at=now_utc(),
            metadata=market,
            is_featured=featured,
        )

    if not should_broadcast:
        logger.info(f"market {market_id} was already reserved for announcement")
        return

    if not premium_already_notified:
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

    def send_premium_cover_image():
        cover_image_url = get_market_cover_image(market)
        if not cover_image_url:
            cover_image_url = wait_for_market_cover_image(market_id, market)
        if not cover_image_url:
            return
        broadcast_to_all(
            f"🖼️ <b>{escape_text(title)}</b>",
            market_id,
            create_market_keyboard(market_id, build_market_link(market_id), scope="image_followup"),
            theme=raw_theme,
            notification_key=f"premium_cover_{market_id}",
            premium_only=True,
            premium_filter=lambda chat_id: premium_chat_wants_market(chat_id, market),
            photo_url=cover_image_url,
            rich_html=build_rich_image_followup(title, cover_image_url),
        )

    Thread(target=send_premium_cover_image, daemon=True).start()

    def send_public_alert():
        ai_message = generate_smart_notification(title, raw_theme, "new")
        notification = build_new_market_notification(market, ai_message, is_premium=False)
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
            rich_html=build_rich_new_market(market, ai_message, cover_url=cover_image_url, is_premium=False),
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
    featured = is_featured_creator(market)

    saved = save_announced_market(
        market_id,
        title,
        raw_theme,
        end_time.isoformat(),
        notified_new=False,
        is_scheduled=True,
        go_live_at=go_live_at,
        is_featured=featured,
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
    global last_onchain_poll_at
    logger.info("b4 market monitoring thread started")
    while True:
        try:
            if get_pause_state():
                logger.info("notifications paused, skipping check")
                time.sleep(10)
                continue

            if ONCHAIN_PROVIDER_ENABLED and time.time() - last_onchain_poll_at >= ONCHAIN_POLL_SECONDS:
                last_onchain_poll_at = time.time()
                for market in fetch_onchain_markets():
                    try:
                        if is_valid_market(market) and is_market_active(market):
                            announce_onchain_market_to_premium(market)
                    except Exception as e:
                        logger.error(f"error processing on-chain market: {e}")
            
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
                
                # refresh stale end_time from API
                api_end_unix = api_market.get("end_time")
                if api_end_unix:
                    try:
                        api_end_dt = datetime.fromtimestamp(int(api_end_unix), tz=timezone.utc).replace(tzinfo=None)
                        api_end_iso = api_end_dt.isoformat()
                        if announced.get("end_time") != api_end_iso:
                            update_announced_end_time(announced_id, api_end_iso)
                    except Exception:
                        pass
                
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
                    elif not existing.get("notified_new") and not is_scheduled_market(market):
                        announce_live_market(market, existing=existing)
                    else:
                        reconcile_market_from_api(market, existing=existing)

                except Exception as e:
                    logger.error(f"error processing market: {e}")


            check_scheduled_notifications()
            try:
                run_intelligence_pipeline(markets)
                score_markets()
                active_ids = [str(m.get("market_id", "")).strip() for m in markets if str(m.get("market_id", "")).strip()]
                if active_ids:
                    run_badge_engine(active_ids)
                recalculate_creator_totals()
                recalculate_creator_categories()
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

                if not market_data.get("notified_new"):
                    continue

                end_time = datetime.fromisoformat(end_time_str)
                time_until = (end_time - now).total_seconds()

                logger.info(f"market: {title} | time_until: {time_until:.0f}s | notified_1h: {market_data.get('notified_1h')} | notified_5m: {market_data.get('notified_5m')}")

                if time_until > 0:
                    hours_until = time_until / 3600
                    minutes_until = time_until / 60

                    # ── Featured Reminder Engine: Editor's Pick markets ──
                    if market_data.get("is_featured") and FEATURED_WALLETS:
                        market_link = market_data.get("market_link", build_market_link(market_id))
                        featured_label = "⭐ Vote Now"

                        # 12-hour reminder (12h–6h window)
                        if hours_until <= 12.0 and hours_until > 6.0 and not market_data.get("notified_12h"):
                            notification, rich_fallback, _ = build_featured_reminder_card(title, market_id, time_until, market_link)
                            keyboard = create_market_keyboard(market_id, market_link, scope="reminder_1h", label=featured_label)
                            broadcast_to_all(
                                notification, market_id, keyboard,
                                theme=raw_theme, notification_key=f"12h_{market_id}",
                                rich_html=rich_fallback,
                            )
                            update_market_flag(market_id, "notified_12h")
                            logger.info(f"featured 12h reminder sent for: {title}")

                        # 6-hour reminder (6h–1h window)
                        elif hours_until <= 6.0 and hours_until > 1.0 and not market_data.get("notified_6h"):
                            notification, rich_fallback, _ = build_featured_reminder_card(title, market_id, time_until, market_link)
                            keyboard = create_market_keyboard(market_id, market_link, scope="reminder_1h", label=featured_label)
                            broadcast_to_all(
                                notification, market_id, keyboard,
                                theme=raw_theme, notification_key=f"6h_{market_id}",
                                rich_html=rich_fallback,
                            )
                            update_market_flag(market_id, "notified_6h")
                            logger.info(f"featured 6h reminder sent for: {title}")

                        # 1-hour reminder (1h–30m window)
                        elif hours_until <= 1.0 and minutes_until > 30.0 and not market_data.get("notified_1h"):
                            notification, rich_fallback, _ = build_featured_reminder_card(title, market_id, time_until, market_link)
                            keyboard = create_market_keyboard(market_id, market_link, scope="reminder_1h", label=featured_label)
                            broadcast_to_all(
                                notification, market_id, keyboard,
                                theme=raw_theme, notification_key=f"1h_{market_id}",
                                rich_html=rich_fallback,
                            )
                            update_market_flag(market_id, "notified_1h")
                            logger.info(f"featured 1h reminder sent for: {title}")

                        # 30-minute reminder (30m–10m window)
                        elif minutes_until <= 30.0 and minutes_until > 10.0 and not market_data.get("notified_30m"):
                            notification, rich_fallback, _ = build_featured_reminder_card(title, market_id, time_until, market_link)
                            keyboard = create_market_keyboard(market_id, market_link, scope="reminder_10m", label=featured_label)
                            broadcast_to_all(
                                notification, market_id, keyboard,
                                theme=raw_theme, notification_key=f"30m_{market_id}",
                                rich_html=rich_fallback,
                            )
                            update_market_flag(market_id, "notified_30m")
                            logger.info(f"featured 30m reminder sent for: {title}")

                        # 10-minute reminder (10m–0 window)
                        elif minutes_until <= 10.0 and not market_data.get("notified_5m"):
                            notification, rich_fallback, _ = build_featured_reminder_card(title, market_id, time_until, market_link)
                            keyboard = create_market_keyboard(market_id, market_link, scope="reminder_10m", label=featured_label)
                            broadcast_to_all(
                                notification, market_id, keyboard,
                                theme=raw_theme, notification_key=f"10m_{market_id}",
                                rich_html=rich_fallback,
                            )
                            update_market_flag(market_id, "notified_5m")
                            logger.info(f"featured 10m reminder sent for: {title}")

                    # ── Standard reminders: all other markets ──
                    elif hours_until <= 1.0 and not market_data.get("notified_1h"):
                        mins_left = int(minutes_until)
                        ai_message = generate_smart_notification(title, market_data.get("theme", "other"), "1h")
                        market_link = market_data.get("market_link", build_market_link(market_id))

                        if ai_message:
                            notification = (
                                f"{custom_emoji('one_hour', '!')} <b>1 HOUR LEFT</b>\n\n"
                                f"<b>{escape_text(title)}</b>\n\n"
                                f"Time Remaining: <b>{format_time_remaining(time_until)}</b>\n\n"
                                f"{ai_message}"
                            )
                        else:
                            notification = (
                                f"{custom_emoji('one_hour', '!')} <b>1 HOUR LEFT</b>\n\n"
                                f"<b>{escape_text(title)}</b>\n\n"
                                f"Time Remaining: <b>{format_time_remaining(time_until)}</b>\n\n"
                                f"This is your last chance to stake!"
                            )

                        keyboard = create_market_keyboard(market_id, market_link, scope="reminder_1h")
                        broadcast_to_all(
                            notification, market_id, keyboard,
                            theme=raw_theme, notification_key=f"1h_{market_id}",
                            rich_html=build_rich_reminder(title, mins_left, ai_message, urgent=False),
                        )
                        update_market_flag(market_id, "notified_1h")
                        logger.info(f"1 hour reminder sent for: {title}")

                    elif minutes_until <= 10.0 and not market_data.get("notified_5m"):
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
                            notification, market_id, keyboard,
                            theme=raw_theme, notification_key=f"10m_{market_id}",
                            rich_html=build_rich_reminder(title, 10, ai_message, urgent=True),
                            premium_only=not ENABLE_PUBLIC_10_MIN_REMINDER,
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
                    capture_market_resolution(market_id, market_data)
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

        if data.startswith("admin_"):
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


@bot.message_handler(commands=['summary'])
def summary_command(message):
    try:
        reply_temp(message, "Digest is currently disabled.", parse_mode="HTML")
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
        reply_temp(message, "Premium digest is currently disabled.", parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in premium_digest: {e}")


@bot.message_handler(commands=['premiumlead'])
def premiumlead_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "Permission denied.")
            return
        reply_temp(message, get_premium_lead_report_text(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in premiumlead: {e}")
        reply_temp(message, f"Error: {e}")


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


@bot.message_handler(commands=['recalc'])
def recalc_command(message):
    try:
        if not is_admin(message.from_user.id):
            reply_temp(message, "❌ Permission Denied. Admin Only Command")
            return
        reply_temp(message, "Recalculating creator totals from latest market state...")
        _force_recalc()
        reply_temp(message, "✅ Creator totals recalculated.")
    except Exception as e:
        logger.error(f"error in recalc command: {e}")
        reply_temp(message, f"❌ Error: {e}")


def _force_recalc():
    """Force recalculation bypassing rate limit."""
    global _last_recalc_run
    _last_recalc_run = 0
    recalculate_creator_totals()
    recalculate_creator_categories()


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
                f"Markets: <b>{markets}</b> | Volume: <b>${volume:,.2f}</b> | Best: <b>${best:,.2f}</b>"
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
                           sc.volume_percentile, sc.composite_score, sc.badge
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
            badge = row[8] or ""
            badge_display = f" {badge}" if badge else ""
            lines.append(
                f"\n<b>{escape_text(title)}</b>{badge_display}\n"
                f"Theme: {escape_text(theme)} | Vol: ${volume:,.2f} | "
                f"Participants: {participants} | Controversy: {controversy:.2f}\n"
                f"Score: <b>{score:.1f}</b>"
            )
        reply_temp(message, "\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error(f"error in marketstats command: {e}")
        reply_temp(message, f"❌ Error: {e}")


@bot.message_handler(commands=['featured'])
def show_featured(message):
    try:
        if not FEATURED_WALLETS:
            reply_temp(message, "No featured creators configured yet.")
            return
        markets = fetch_b4_markets()
        featured = [
            m for m in markets
            if is_valid_market(m) and is_market_active(m) and is_featured_creator(m)
        ]
        if not featured:
            reply_temp(message, "No active featured markets right now.")
            return
        lines = ["<b>⭐ Featured Creator Markets</b>"]
        for m in featured[:10]:
            title = str(m.get("title", "")[:45]).strip()
            market_id = str(m.get("market_id", "")).strip()
            market_link = build_market_link(market_id)
            yes_pool = int(m.get("yes_pool") or 0) / USDC_DIVISOR
            no_pool = int(m.get("no_pool") or 0) / USDC_DIVISOR
            volume = yes_pool + no_pool
            yes_votes = int(m.get("yes_votes") or 0)
            no_votes = int(m.get("no_votes") or 0)
            participants = yes_votes + no_votes
            end_time_unix = m.get("end_time")
            end_time = datetime.fromtimestamp(int(end_time_unix), tz=timezone.utc).replace(tzinfo=None)
            time_left = end_time - now_utc()
            hours_left = max(0, time_left.total_seconds() / 3600)
            promo = build_market_promo_text(m)
            promo_line = f"\n{promo}" if promo else ""
            lines.append(
                f"\n⭐ <b>{escape_text(title)}</b>\n"
                f"Vol: <b>${volume:,.2f}</b> | People: <b>{participants}</b> | "
                f"Time: <b>{hours_left:.1f}h</b>{promo_line}\n"
                f"<a href=\"{market_link}\">Vote Now</a>"
            )
        reply_temp(message, "\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"error in featured command: {e}")
        reply_temp(message, f"❌ Error: {e}")


@bot.message_handler(commands=['trending'])
def show_trending(message):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT m.market_id, m.title, m.theme, m.end_time,
                           s.total_volume, s.total_participants, s.controversy_score,
                           sc.composite_score, sc.badge,
                           (SELECT COUNT(*) FROM market_events me WHERE me.market_id = m.market_id
                            AND me.recorded_at > NOW() - INTERVAL '6 hours') as recent_events
                    FROM markets m
                    LEFT JOIN market_snapshots s ON m.market_id = s.market_id
                        AND s.snapshot_at = (
                            SELECT MAX(snapshot_at) FROM market_snapshots WHERE market_id = m.market_id
                        )
                    LEFT JOIN market_scores sc ON m.market_id = sc.market_id
                    WHERE m.resolved = FALSE AND m.hidden = FALSE
                    ORDER BY sc.composite_score DESC NULLS LAST
                    LIMIT 12
                """)
                rows = cur.fetchall()
        if not rows:
            reply_temp(message, "No trending markets yet. Check back soon.")
            return
        lines = ["<b>Trending Markets</b>\nRanked by intelligence score"]
        for row in rows:
            title = str(row[1] or "")[:42]
            theme = row[2] or "?"
            end_time_str = row[3]
            volume = float(row[4] or 0)
            participants = int(row[5] or 0)
            controversy = float(row[6] or 0)
            score = float(row[7] or 0)
            badge = row[8] or ""
            recent_events = int(row[9] or 0)
            market_id = str(row[0]).strip()
            market_link = build_market_link(market_id)

            badge_display = f" {badge}" if badge else ""
            time_display = ""
            if end_time_str:
                try:
                    end_dt = datetime.fromisoformat(end_time_str)
                    time_left = (end_dt - now_utc()).total_seconds() / 3600
                    if time_left > 0:
                        time_display = f" | {time_left:.1f}h left"
                except Exception:
                    pass

            lines.append(
                f"\n<b>{escape_text(title)}</b>{badge_display}\n"
                f"${volume:,.2f} vol | {participants} people | {controversy:.2f} controversy"
                f"{time_display}\n"
                f"<a href=\"{market_link}\">Vote</a>"
            )
        reply_temp(message, "\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"error in trending command: {e}")
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
        telebot.types.BotCommand("summary", "Daily summary (disabled)"),
        telebot.types.BotCommand("preferences", "Choose market categories"),
        telebot.types.BotCommand("getmyid", "Get your telegram id"),
        telebot.types.BotCommand("featured", "Featured creator markets"),
        telebot.types.BotCommand("trending", "Trending markets by momentum"),
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
        telebot.types.BotCommand("preview", "Preview latest market alert"),
        telebot.types.BotCommand("health", "Show bot health"),
        telebot.types.BotCommand("premium_add", "Add premium user or chat"),
        telebot.types.BotCommand("setpremiumwallet", "Set USDC Solana payment address"),
        telebot.types.BotCommand("premium_remove", "Remove premium user or chat"),
        telebot.types.BotCommand("premium_users", "List premium users or chats"),
        telebot.types.BotCommand("premium_digest", "Premium digest (disabled)"),
        telebot.types.BotCommand("premiumlead", "Show premium lead-time report"),
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
        telebot.types.BotCommand("intel", "Intelligence engine status"),
        telebot.types.BotCommand("recalc", "Recalculate creator totals"),
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
