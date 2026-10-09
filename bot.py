import json
import logging
import os
import random
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =========================
# CONFIGURATION
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Change this to your preferred timezone.
TIMEZONE = os.getenv("TIMEZONE", "Africa/Addis_Ababa")

# Daily delivery time in 24-hour format.
DAILY_HOUR = int(os.getenv("DAILY_HOUR", "8"))
DAILY_MINUTE = int(os.getenv("DAILY_MINUTE", "0"))

BASE_DIR = Path(__file__).resolve().parent
QURAN_FILE = BASE_DIR / "quran.json"
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
SUBSCRIBERS_FILE = DATA_DIR / "subscribers.json"
STATE_FILE = DATA_DIR / "state.json"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# =========================
# FILE MANAGEMENT
# =========================

def load_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError):
        logger.exception("Could not read %s", path)
        return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = path.with_suffix(path.suffix + ".tmp")

    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    temporary_path.replace(path)


def load_quran():
    verses = load_json(QURAN_FILE, [])

    if not isinstance(verses, list) or not verses:
        raise RuntimeError(
            "quran.json is missing, empty, or invalid."
        )

    required_fields = {
        "surah",
        "surah_number",
        "ayah_number",
        "arabic",
        "english",
    }

    for verse in verses:
        if not isinstance(verse, dict):
            raise RuntimeError("Invalid verse entry in quran.json.")

        missing = required_fields - verse.keys()

        if missing:
            raise RuntimeError(
                f"Verse entry is missing fields: {missing}"
            )

        if not verse["arabic"].strip() or not verse["english"].strip():
            raise RuntimeError("A verse has empty Arabic or English text.")

    return verses


def load_subscribers():
    data = load_json(SUBSCRIBERS_FILE, [])

    if not isinstance(data, list):
        return set()

    return {int(chat_id) for chat_id in data}


def save_subscribers(subscribers):
    save_json(
        SUBSCRIBERS_FILE,
        sorted(subscribers),
    )


def get_state():
    state = load_json(STATE_FILE, {})

    if not isinstance(state, dict):
        return {}

    return state


def save_state(state):
    save_json(STATE_FILE, state)


# =========================
# VERSE SELECTION
# =========================

def get_today():
    return datetime_now().date().isoformat()


def datetime_now():
    return __import__("datetime").datetime.now(
        ZoneInfo(TIMEZONE)
    )


def choose_daily_verse(verses):
    """
    Pick one verse per local calendar day.
    Keep the selected verse in state.json so all subscribers
    receive the same verse, including after a restart.
    """
    today = get_today()
    state = get_state()

    if state.get("date") == today:
        previous_verse = state.get("verse")

        if isinstance(previous_verse, dict):
            return previous_verse

    previous_key = state.get("last_verse_key")

    candidates = [
        verse for verse in verses
        if (
            f"{verse['surah_number']}:{verse['ayah_number']}"
            != previous_key
        )
    ]

    if not candidates:
        candidates = verses

    verse = random.choice(candidates)

    state = {
        "date": today,
        "last_verse_key": (
            f"{verse['surah_number']}:{verse['ayah_number']}"
        ),
        "verse": verse,
    }

    save_state(state)

    return verse


def format_verse(verse):
    return (
        "🌙 <b>VERSE OF THE DAY | آية اليوم</b>\n\n"
        f"📖 <b>Surah {verse['surah']} "
        f"({verse['surah_number']}:{verse['ayah_number']})</b>\n\n"
        f"<b>Arabic:</b>\n"
        f"<tg-emoji emoji-id=\"5368324170671202286\">📖</tg-emoji>"
        f"{verse['arabic']}\n\n"
        f"<b>English meaning:</b>\n"
        f"{verse['english']}\n\n"
        f"— Quran {verse['surah_number']}:"
        f"{verse['ayah_number']}\n\n"
        "🤍 <i>A daily reminder for your heart.</i>"
    )


# =========================
# TELEGRAM COMMANDS
# =========================

WELCOME_MESSAGE = (
    "🌙 <b>Welcome to Quran Verse of the Day!</b>\n\n"
    "Receive a daily Quranic verse with its original Arabic "
    "text and English meaning.\n\n"
    "📖 One verse every day.\n"
    "🤍 A reminder for your heart.\n\n"
    "Use /today to read today's verse.\n"
    "Use /stop to unsubscribe."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribers = load_subscribers()

    already_subscribed = chat_id in subscribers

    if not already_subscribed:
        subscribers.add(chat_id)
        save_subscribers(subscribers)

    await update.effective_message.reply_text(
        WELCOME_MESSAGE,
        parse_mode="HTML",
    )

    if not already_subscribed:
        await update.effective_message.reply_text(
            "✅ You are now subscribed to the daily Quran reminder!"
        )

        # Send today's verse immediately to new subscribers.
        await send_verse_to_chat(
            context.bot,
            chat_id,
            choose_daily_verse(load_quran()),
        )


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribers = load_subscribers()

    if chat_id in subscribers:
        subscribers.remove(chat_id)
        save_subscribers(subscribers)

        message = (
            "You have unsubscribed from daily verses. 🤍\n"
            "Use /start whenever you want to subscribe again."
        )
    else:
        message = "You are not currently subscribed. Use /start to join."

    await update.effective_message.reply_text(message)


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    verse = choose_daily_verse(load_quran())

    await update.effective_message.reply_text(
        format_verse(verse),
        parse_mode="HTML",
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        "🌙 <b>Quran Verse of the Day — Help</b>\n\n"
        "/start — Subscribe to daily verses\n"
        "/today — Read today's verse\n"
        "/stop — Unsubscribe\n"
        "/help — Show this help message\n\n"
        "May every verse bring you closer to guidance. 🤲",
        parse_mode="HTML",
    )


# =========================
# DAILY DELIVERY
# =========================

async def send_verse_to_chat(bot, chat_id, verse):
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=format_verse(verse),
            parse_mode="HTML",
        )
        return True

    except Exception:
        logger.exception("Failed to send a verse to chat %s", chat_id)
        return False


async def daily_broadcast(context: ContextTypes.DEFAULT_TYPE):
    subscribers = load_subscribers()

    if not subscribers:
        logger.info("No subscribers yet.")
        return

    verse = choose_daily_verse(load_quran())

    logger.info(
        "Sending daily verse %s:%s to %s subscribers",
        verse["surah_number"],
        verse["ayah_number"],
        len(subscribers),
    )

    for chat_id in list(subscribers):
        success = await send_verse_to_chat(
            context.bot,
            chat_id,
            verse,
        )

        if not success:
            # Remove chats that can no longer receive messages,
            # such as users who blocked the bot.
            try:
                from telegram.error import Forbidden

                # Delivery failures are logged. Subscriber removal
                # can be configured more narrowly if desired.
            except ImportError:
                pass


# =========================
# STARTUP
# =========================

async def post_init(application: Application):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    load_quran()
    subscribers = load_subscribers()

    logger.info("Bot started successfully.")
    logger.info("Loaded %s subscribers.", len(subscribers))
    logger.info(
        "Daily delivery scheduled for %02d:%02d (%s).",
        DAILY_HOUR,
        DAILY_MINUTE,
        TIMEZONE,
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is not configured. Set it in your host's "
            "environment variables."
        )

    if not 0 <= DAILY_HOUR <= 23:
        raise ValueError("DAILY_HOUR must be between 0 and 23.")

    if not 0 <= DAILY_MINUTE <= 59:
        raise ValueError("DAILY_MINUTE must be between 0 and 59.")

    timezone = ZoneInfo(TIMEZONE)

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("today", today))
    application.add_handler(CommandHandler("help", help_command))

    application.job_queue.run_daily(
        daily_broadcast,
        time=time(
            hour=DAILY_HOUR,
            minute=DAILY_MINUTE,
            tzinfo=timezone,
        ),
        name="daily_quran_verse",
    )

    logger.info("Starting polling...")
    application.run_polling()


if __name__ == "__main__":
    main()
