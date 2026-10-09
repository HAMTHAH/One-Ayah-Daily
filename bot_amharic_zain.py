import json
import logging
import os
import random
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
import httpx

BASE_DIR = Path(__file__).resolve().parent
QURAN_FILE = BASE_DIR / "quran.json"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TIMEZONE_NAME = os.getenv("TIMEZONE", "Africa/Addis_Ababa").strip()
DAILY_HOUR = int(os.getenv("DAILY_HOUR", "8"))
DAILY_MINUTE = int(os.getenv("DAILY_MINUTE", "0"))

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def load_quran() -> list[dict]:
    """Read the nested combined Quran JSON and flatten it into individual verses."""
    if not QURAN_FILE.exists():
        raise RuntimeError(f"quran.json not found at: {QURAN_FILE}")
    if QURAN_FILE.stat().st_size == 0:
        raise RuntimeError("quran.json is empty.")

    try:
        with QURAN_FILE.open("r", encoding="utf-8-sig") as file:
            data = json.load(file)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(data, dict) or not isinstance(data.get("surahs"), list):
        raise RuntimeError("Invalid Quran JSON: expected a top-level 'surahs' list.")

    verses = []
    for surah in data["surahs"]:
        try:
            surah_number = int(surah["number"])
            surah_name = str(surah.get("name_english") or f"Surah {surah_number}")
            surah_verses = surah["verses"]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid surah entry in quran.json: {exc}") from exc

        if not isinstance(surah_verses, list):
            raise RuntimeError(f"Invalid verses list for surah {surah_number}.")

        for verse in surah_verses:
            try:
                verses.append({
                    "surah": surah_name,
                    "surah_number": surah_number,
                    "ayah_number": int(verse["ayah"]),
                    "arabic": str(verse["arabic"]),
                    "english": str(verse["english"]),
                })
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"Invalid verse in surah {surah_number}: {exc}"
                ) from exc

    if len(verses) != 6236:
        raise RuntimeError(
            f"Expected 6236 verses, but loaded {len(verses)}. "
            "Check that quran.json is the complete dataset."
        )

    logger.info("Successfully loaded %s Quran verses.", len(verses))
    return verses


VERSES = load_quran()

# QuranEnc Amharic translation: Africa Academy, translated by Muhammad Zain Zahreddin.
AMHARIC_TRANSLATION_KEY = "amharic_zain"
AMHARIC_TRANSLATION_VERSION = "V1.0.1"
AMHARIC_API_BASE = "https://quranenc.com/api/v1/translation/sura"
AMHARIC_CACHE: dict[int, dict[int, str]] = {}


def verse_keyboard(index: int) -> InlineKeyboardMarkup:
    """Three actions: next sequential verse, random verse, or play this verse."""
    # All three buttons appear on a single row.
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("➡️ Next", callback_data=f"next:{index}"),
        InlineKeyboardButton("🎲 Random", callback_data=f"random:{index}"),
        InlineKeyboardButton("🔊 Audio", callback_data=f"audio:{index}"),
    ]])


async def get_amharic_translation(surah_number: int, ayah_number: int) -> str | None:
    """Fetch and cache one surah's Amharic translation from QuranEnc."""
    if surah_number not in AMHARIC_CACHE:
        url = f"{AMHARIC_API_BASE}/{AMHARIC_TRANSLATION_KEY}/{surah_number}"
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
            # QuranEnc normally returns a list of ayah objects.
            if isinstance(data, dict):
                data = data.get("result", data.get("data", []))
            if not isinstance(data, list):
                logger.warning("Unexpected QuranEnc response for surah %s", surah_number)
                return None
            translations: dict[int, str] = {}
            for item in data:
                if isinstance(item, dict) and item.get("aya") is not None and item.get("translation"):
                    translations[int(item["aya"])] = str(item["translation"])
            if not translations:
                logger.warning("No Amharic translations returned for surah %s", surah_number)
                return None
            AMHARIC_CACHE[surah_number] = translations
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            logger.exception("Could not retrieve Amharic translation for surah %s", surah_number)
            return None
    return AMHARIC_CACHE[surah_number].get(ayah_number)


async def format_verse(verse: dict) -> str:
    amharic = await get_amharic_translation(verse["surah_number"], verse["ayah_number"])
    parts = [
        f"📖 {verse['surah']} — Surah {verse['surah_number']}, Ayah {verse['ayah_number']}",
        verse["arabic"],
        f"🇬🇧 English (Saheeh International):\n{verse['english']}",
    ]
    if amharic:
        parts.append(f"🇪🇹 አማርኛ (የአፍሪካ አካዳሚ፣ ሙሐመድ ዘይን ዘህሩዲን):\n{amharic}")
    else:
        parts.append("🇪🇹 የአማርኛ ትርጉም ለጊዜው አልተገኘም። እባክዎ ቆይተው ይሞክሩ።")
    parts.append(f"Amharic translation source: QuranEnc.com · {AMHARIC_TRANSLATION_VERSION}")
    return "\n\n".join(parts)


def verse_audio_url(verse: dict) -> str:
    """Direct MP3 recitation from EveryAyah (Mishary Rashid Alafasy, 128 kbps)."""
    filename = f"{verse['surah_number']:03d}{verse['ayah_number']:03d}.mp3"
    return f"https://everyayah.com/data/Alafasy_128kbps/{filename}"


async def send_verse_audio(context: ContextTypes.DEFAULT_TYPE, chat_id: int, verse: dict) -> None:
    """Send audio only when the user taps the audio button."""
    try:
        await context.bot.send_audio(
            chat_id=chat_id,
            audio=verse_audio_url(verse),
            title=f"Surah {verse['surah_number']}, Ayah {verse['ayah_number']}",
            performer="Mishary Rashid Alafasy",
            caption=f"🎧 {verse['surah']} — Ayah {verse['ayah_number']}",
        )
    except Exception:
        logger.exception("Could not send audio for Surah %s Ayah %s", verse['surah_number'], verse['ayah_number'])
        await context.bot.send_message(
            chat_id=chat_id,
            text="I couldn't load the recitation audio just now. Please try again later.",
        )


async def show_verse(update: Update, context: ContextTypes.DEFAULT_TYPE, index: int, edit: bool = False) -> None:
    index = max(0, min(index, len(VERSES) - 1))
    verse = VERSES[index]
    text = await format_verse(verse)
    markup = verse_keyboard(index)
    if update.callback_query and edit:
        await update.callback_query.edit_message_text(text=text, reply_markup=markup)
    elif update.effective_message:
        await update.effective_message.reply_text(text=text, reply_markup=markup)


async def verse_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    try:
        action, raw_index = query.data.split(":", 1)
        current_index = int(raw_index)
    except (ValueError, TypeError):
        return

    current_index = max(0, min(current_index, len(VERSES) - 1))
    if action == "next":
        # Send a NEW message/panel and keep the previous verse visible.
        await show_verse(update, context, (current_index + 1) % len(VERSES))
    elif action == "random":
        # Send a NEW message/panel and keep the previous verse visible.
        await show_verse(update, context, random.randrange(len(VERSES)))
    elif action == "audio":
        if update.effective_chat:
            await send_verse_audio(context, update.effective_chat.id, VERSES[current_index])


async def remember_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remember chats that start the bot so scheduled messages can reach them."""
    if not update.effective_chat:
        return
    subscribers: set[int] = context.application.bot_data.setdefault("subscribers", set())
    subscribers.add(update.effective_chat.id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await remember_chat(update, context)
    if update.effective_message:
        await update.effective_message.reply_text(
            "السلام عليكم ورحمة الله وبركاته\n\n"
            "Welcome! I share Quran verses in Arabic, English (Saheeh International), "
            "and Amharic (Africa Academy, Muhammad Zain Zahreddin).\n\n"
            "Amharic translation source: QuranEnc.com, version V1.0.1.\n\n"
            "Commands:\n"
            "/verse — receive a random Quran verse\n"
            "/daily — receive a verse now\n"
            "/help — show this message\n\n"
            "Daily verses are scheduled automatically for chats that have started "
            "the bot."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def verse_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_verse(update, context, random.randrange(len(VERSES)))


async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await verse_command(update, context)


async def send_daily_verse(context: ContextTypes.DEFAULT_TYPE) -> None:
    subscribers: set[int] = context.application.bot_data.get("subscribers", set())
    if not subscribers:
        logger.info("No subscribed chats yet; skipping daily message.")
        return

    index = random.randrange(len(VERSES))
    message = await format_verse(VERSES[index])
    for chat_id in list(subscribers):
        try:
            await context.bot.send_message(chat_id=chat_id, text=message, reply_markup=verse_keyboard(index))
        except Exception:
            logger.exception("Could not send daily verse to chat %s", chat_id)


async def post_init(application: Application) -> None:
    subscribers_file = BASE_DIR / "subscribers.json"
    try:
        if subscribers_file.exists():
            stored = json.loads(subscribers_file.read_text(encoding="utf-8"))
            application.bot_data["subscribers"] = {int(chat_id) for chat_id in stored}
        else:
            application.bot_data["subscribers"] = set()
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        logger.exception("Could not load subscribers.json; starting with no saved chats.")
        application.bot_data["subscribers"] = set()

    try:
        timezone = ZoneInfo(TIMEZONE_NAME)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            f"Unknown timezone '{TIMEZONE_NAME}'. Check the TIMEZONE setting."
        ) from exc

    if application.job_queue is None:
        raise RuntimeError(
            "JobQueue is unavailable. Set requirements.txt to "
            "'python-telegram-bot[job-queue]==22.5'."
        )

    application.job_queue.run_daily(
        send_daily_verse,
        time=time(hour=DAILY_HOUR, minute=DAILY_MINUTE, tzinfo=timezone),
        name="daily_quran_verse",
    )
    logger.info(
        "Daily Quran verse scheduled for %02d:%02d (%s).",
        DAILY_HOUR, DAILY_MINUTE, TIMEZONE_NAME
    )


async def post_shutdown(application: Application) -> None:
    subscribers_file = BASE_DIR / "subscribers.json"
    subscribers = sorted(application.bot_data.get("subscribers", set()))
    try:
        subscribers_file.write_text(
            json.dumps(subscribers, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        logger.exception("Could not save subscribers.json.")


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing. Add your Telegram bot token in FadeHost "
            "environment variables."
        )
    if not 0 <= DAILY_HOUR <= 23 or not 0 <= DAILY_MINUTE <= 59:
        raise RuntimeError("DAILY_HOUR must be 0–23 and DAILY_MINUTE must be 0–59.")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("verse", verse_command))
    application.add_handler(CommandHandler("daily", daily_command))
    application.add_handler(CallbackQueryHandler(verse_button_callback, pattern=r"^(next|random|audio):\d+$"))

    logger.info("Bot is starting.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
