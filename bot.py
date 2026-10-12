import asyncio
import json
import logging
import os
import random
from datetime import time, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Forbidden, BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BASE_DIR = Path(__file__).resolve().parent
QURAN_FILE = BASE_DIR / "quran.json"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TIMEZONE_NAME = os.getenv("TIMEZONE", "Africa/Addis_Ababa").strip()
DAILY_HOUR = int(os.getenv("DAILY_HOUR", "8"))
DAILY_MINUTE = int(os.getenv("DAILY_MINUTE", "0"))
BOT_OWNER_ID = os.getenv("BOT_OWNER_ID", "").strip()

# These files are saved beside bot.py. For durable storage, configure persistent
# storage on your hosting provider if its filesystem is temporary.
SUBSCRIBERS_FILE = BASE_DIR / "subscribers.json"
MEMBERS_FILE = BASE_DIR / "members.json"
SCHEDULE_FILE = BASE_DIR / "schedule.json"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def load_quran() -> list[dict]:
    """Read the combined Quran JSON and flatten it into individual verses."""
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
AMHARIC_API_BASE = "https://quranenc.com/api/v1/translation/sura"
AMHARIC_CACHE: dict[int, dict[int, str]] = {}


def verse_keyboard(index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔊 Audio", callback_data=f"audio:{index}"),
        InlineKeyboardButton("➡️ Next", callback_data=f"next:{index}"),
        InlineKeyboardButton("🎲 Random", callback_data=f"random:{index}"),
    ]])


async def get_amharic_translation(surah_number: int, ayah_number: int) -> str | None:
    if surah_number not in AMHARIC_CACHE:
        url = f"{AMHARIC_API_BASE}/{AMHARIC_TRANSLATION_KEY}/{surah_number}"
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()

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
        verse["english"],
    ]
    if amharic:
        parts.append(amharic)
    else:
        parts.append("🇪🇹 የአማርኛ ትርጉም ለጊዜው አልተገኘም። እባክዎ ቆይተው ይሞክሩ።")
    return "\n\n".join(parts)


def verse_audio_url(verse: dict) -> str:
    filename = f"{verse['surah_number']:03d}{verse['ayah_number']:03d}.mp3"
    return f"https://everyayah.com/data/Alafasy_128kbps/{filename}"


async def send_verse_audio(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, verse: dict
) -> None:
    try:
        await context.bot.send_audio(
            chat_id=chat_id,
            audio=verse_audio_url(verse),
            title=f"Surah {verse['surah_number']}, Ayah {verse['ayah_number']}",
            performer="Mishary Rashid Alafasy",
            caption=f"🎧 {verse['surah']} — Ayah {verse['ayah_number']}",
        )
    except TelegramError:
        logger.exception(
            "Could not send audio for Surah %s Ayah %s",
            verse["surah_number"],
            verse["ayah_number"],
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text="I couldn't load the recitation audio just now. Please try again later.",
        )


def save_json(path: Path, data) -> None:
    """Save JSON using a temporary file first to reduce the chance of a partial write."""
    try:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp_path.replace(path)
    except OSError:
        logger.exception("Could not save %s", path.name)


def is_owner(update: Update) -> bool:
    user = update.effective_user
    return bool(BOT_OWNER_ID and user and str(user.id) == BOT_OWNER_ID)


async def track_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Record users whenever they send a message or press a verse button."""
    user = update.effective_user
    chat = update.effective_chat
    if not user:
        return

    try:
        timezone = ZoneInfo(TIMEZONE_NAME)
    except (ZoneInfoNotFoundError, ValueError):
        timezone = ZoneInfo("UTC")

    members: dict = context.application.bot_data.setdefault("members", {})
    members[str(user.id)] = {
        "id": user.id,
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "username": user.username or "",
        "last_seen": datetime.now(timezone).isoformat(timespec="seconds"),
    }
    save_json(MEMBERS_FILE, members)

    # Only private chats are eligible for private daily messages/broadcasts.
    if chat and chat.type == "private":
        subscribers: set[int] = context.application.bot_data.setdefault("subscribers", set())
        subscribers.add(chat.id)
        save_json(SUBSCRIBERS_FILE, sorted(subscribers))


def get_schedule() -> tuple[int, int]:
    try:
        if SCHEDULE_FILE.exists():
            data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
            hour, minute = int(data["hour"]), int(data["minute"])
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour, minute
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        logger.exception("Could not read saved schedule; using environment defaults.")
    return DAILY_HOUR, DAILY_MINUTE


async def show_verse(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    index: int,
    edit: bool = False,
) -> None:
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

    # Record the person pressing Audio, Next, or Random.
    await track_member(update, context)
    await query.answer()

    try:
        action, raw_index = query.data.split(":", 1)
        current_index = int(raw_index)
    except (ValueError, TypeError):
        return

    current_index = max(0, min(current_index, len(VERSES) - 1))
    if action == "next":
        await show_verse(update, context, (current_index + 1) % len(VERSES))
    elif action == "random":
        await show_verse(update, context, random.randrange(len(VERSES)))
    elif action == "audio" and update.effective_chat:
        await send_verse_audio(context, update.effective_chat.id, VERSES[current_index])


def schedule_daily_post(application: Application, hour: int, minute: int) -> None:
    if application.job_queue is None:
        raise RuntimeError(
            "JobQueue is unavailable. Set requirements.txt to "
            "'python-telegram-bot[job-queue]==22.5'."
        )
    for job in application.job_queue.get_jobs_by_name("daily_quran_verse"):
        job.schedule_removal()
    timezone = ZoneInfo(TIMEZONE_NAME)
    application.job_queue.run_daily(
        send_daily_verse,
        time=time(hour=hour, minute=minute, tzinfo=timezone),
        name="daily_quran_verse",
    )


async def settime_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        await update.effective_message.reply_text("Sorry, this command is only available to the bot owner.")
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "Usage: /settime HH:MM\nExample: /settime 06:30"
        )
        return
    try:
        hour_text, minute_text = context.args[0].split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
        if (
            len(hour_text) != 2
            or len(minute_text) != 2
            or not (0 <= hour <= 23 and 0 <= minute <= 59)
        ):
            raise ValueError
    except (ValueError, TypeError):
        await update.effective_message.reply_text(
            "Please use 24-hour time like /settime 06:30 or /settime 20:15."
        )
        return

    schedule_daily_post(context.application, hour, minute)
    save_json(SCHEDULE_FILE, {"hour": hour, "minute": minute, "timezone": TIMEZONE_NAME})
    await update.effective_message.reply_text(
        f"✅ Daily Quran posting time changed to {hour:02d}:{minute:02d} "
        f"({TIMEZONE_NAME}).\nThis setting is saved and will remain after a bot restart "
        "if the hosting filesystem preserves these files."
    )


async def mytime_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        await update.effective_message.reply_text("Sorry, this command is only available to the bot owner.")
        return
    hour, minute = get_schedule()
    await update.effective_message.reply_text(
        f"🕒 Daily posting time: {hour:02d}:{minute:02d} ({TIMEZONE_NAME})."
    )


async def members_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        await update.effective_message.reply_text("Sorry, this command is only available to the bot owner.")
        return

    members = context.application.bot_data.get("members", {})
    if not members:
        await update.effective_message.reply_text(
            "No members have been recorded yet. Users are recorded when they message "
            "the bot or press a verse button."
        )
        return

    ordered = sorted(
        members.values(),
        key=lambda m: (m.get("first_name", "").casefold(), int(m.get("id", 0))),
    )
    await update.effective_message.reply_text(f"👥 Recorded users: {len(ordered)}")
    chunk = ""
    for member in ordered:
        name = " ".join(
            x for x in (member.get("first_name", ""), member.get("last_name", "")) if x
        ).strip() or "Unknown"
        username = f" (@{member['username']})" if member.get("username") else ""
        line = f"• {name}{username} — ID: {member.get('id')}\n"
        if len(chunk) + len(line) > 3500:
            await update.effective_message.reply_text(chunk)
            chunk = ""
        chunk += line
    if chunk:
        await update.effective_message.reply_text(chunk)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        await update.effective_message.reply_text("Sorry, this command is only available to the bot owner.")
        return
    members = context.application.bot_data.get("members", {})
    subscribers = context.application.bot_data.get("subscribers", set())
    hour, minute = get_schedule()
    await update.effective_message.reply_text(
        f"📊 Bot statistics\n"
        f"Registered users recorded: {len(members)}\n"
        f"Private chats recorded: {len(subscribers)}\n"
        f"Daily posting time: {hour:02d}:{minute:02d} ({TIMEZONE_NAME})"
    )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only: /broadcast Your message. Sends to recorded private chats."""
    if not is_owner(update):
        await update.effective_message.reply_text(
            "Sorry, this command is only available to the bot owner."
        )
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n/broadcast Your message here\n\n"
            "This sends a text message to recorded private users who have started "
            "the bot and can still receive messages."
        )
        return

    message_text = " ".join(context.args).strip()
    if not message_text:
        await update.effective_message.reply_text("Please include a message after /broadcast.")
        return

    # Union of subscriber chats and member IDs, deduplicated.
    subscribers: set[int] = context.application.bot_data.setdefault("subscribers", set())
    members: dict = context.application.bot_data.setdefault("members", {})
    recipients = set(subscribers)
    for key, member in members.items():
        try:
            recipients.add(int(member.get("id", key)))
        except (TypeError, ValueError):
            continue

    recipients.discard(update.effective_chat.id if update.effective_chat else 0)
    if not recipients:
        await update.effective_message.reply_text(
            "No saved users to message yet. Users are added when they interact with the bot."
        )
        return

    await update.effective_message.reply_text(
        f"📣 Starting broadcast to {len(recipients)} saved private chats..."
    )
    sent = 0
    failed = 0
    blocked = 0

    for chat_id in recipients:
        try:
            await context.bot.send_message(chat_id=chat_id, text=message_text)
            sent += 1
        except Forbidden:
            # Usually the user blocked the bot or the chat is no longer reachable.
            blocked += 1
            subscribers.discard(chat_id)
        except BadRequest as exc:
            failed += 1
            logger.warning("Broadcast to %s failed: %s", chat_id, exc)
        except TelegramError:
            failed += 1
            logger.exception("Broadcast to %s failed", chat_id)

        # Small pause to reduce the risk of hitting Telegram rate limits.
        await asyncio.sleep(0.05)

    save_json(SUBSCRIBERS_FILE, sorted(subscribers))
    await update.effective_message.reply_text(
        f"✅ Broadcast finished.\n"
        f"Sent: {sent}\n"
        f"Blocked/unreachable: {blocked}\n"
        f"Other failures: {failed}"
    )


async def remember_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat or chat.type != "private":
        return
    subscribers: set[int] = context.application.bot_data.setdefault("subscribers", set())
    subscribers.add(chat.id)
    save_json(SUBSCRIBERS_FILE, sorted(subscribers))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_member(update, context)
    await remember_chat(update, context)
    if update.effective_message:
        await update.effective_message.reply_text(
            "السلام عليكم ورحمة الله وبركاته\n\n"
            "Welcome! I share Quran verses in Arabic, English (Saheeh International), "
            "and Amharic.\n\n"
            "Amharic translation: Africa Academy, Muhammad Zain Zahreddin. "
            "Source: QuranEnc.com · V1.0.1.\n\n"
            "Commands:\n"
            "/verse — receive a random Quran verse\n"
            "/daily — receive a verse now\n"
            "/search SURAH:AYAH — look up a verse (example: /search 2:255)\n"
            "/help — show this message\n\n"
            "Daily verses are scheduled automatically for private chats that have started "
            "the bot."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_member(update, context)
    await start(update, context)


async def verse_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_member(update, context)
    await show_verse(update, context, random.randrange(len(VERSES)))


async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_member(update, context)
    await verse_command(update, context)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_member(update, context)
    if not context.args or len(context.args) != 1 or ":" not in context.args[0]:
        await update.effective_message.reply_text(
            "Usage: /search SURAH:AYAH\nExample: /search 2:255"
        )
        return
    try:
        surah_num, ayah_num = (int(part) for part in context.args[0].split(":", 1))
    except ValueError:
        await update.effective_message.reply_text("Use a reference like /search 2:255.")
        return

    index = next(
        (
            i for i, verse in enumerate(VERSES)
            if verse["surah_number"] == surah_num and verse["ayah_number"] == ayah_num
        ),
        None,
    )
    if index is None:
        await update.effective_message.reply_text(
            "I couldn't find that verse. Check the surah and ayah numbers."
        )
        return
    await show_verse(update, context, index)


async def send_daily_verse(context: ContextTypes.DEFAULT_TYPE) -> None:
    subscribers: set[int] = context.application.bot_data.get("subscribers", set())
    if not subscribers:
        logger.info("No subscribed chats yet; skipping daily message.")
        return

    index = random.randrange(len(VERSES))
    message = await format_verse(VERSES[index])
    for chat_id in list(subscribers):
        try:
            await context.bot.send_message(
                chat_id=chat_id, text=message, reply_markup=verse_keyboard(index)
            )
        except Forbidden:
            logger.warning("Removing unreachable subscriber chat %s", chat_id)
            subscribers.discard(chat_id)
        except TelegramError:
            logger.exception("Could not send daily verse to chat %s", chat_id)
    save_json(SUBSCRIBERS_FILE, sorted(subscribers))


async def post_init(application: Application) -> None:
    try:
        if SUBSCRIBERS_FILE.exists():
            stored = json.loads(SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
            application.bot_data["subscribers"] = {int(chat_id) for chat_id in stored}
        else:
            application.bot_data["subscribers"] = set()
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        logger.exception("Could not load subscribers.json; starting with no saved chats.")
        application.bot_data["subscribers"] = set()

    try:
        if MEMBERS_FILE.exists():
            stored_members = json.loads(MEMBERS_FILE.read_text(encoding="utf-8"))
            application.bot_data["members"] = (
                stored_members if isinstance(stored_members, dict) else {}
            )
        else:
            application.bot_data["members"] = {}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        logger.exception("Could not load members.json; starting with no saved members.")
        application.bot_data["members"] = {}

    try:
        ZoneInfo(TIMEZONE_NAME)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RuntimeError(
            f"Unknown timezone '{TIMEZONE_NAME}'. Check the TIMEZONE setting."
        ) from exc

    if application.job_queue is None:
        raise RuntimeError(
            "JobQueue is unavailable. Set requirements.txt to "
            "'python-telegram-bot[job-queue]==22.5'."
        )

    hour, minute = get_schedule()
    schedule_daily_post(application, hour, minute)
    logger.info(
        "Loaded %s members and %s private subscribers.",
        len(application.bot_data["members"]),
        len(application.bot_data["subscribers"]),
    )
    logger.info("Daily Quran verse scheduled for %02d:%02d (%s).", hour, minute, TIMEZONE_NAME)


async def post_shutdown(application: Application) -> None:
    save_json(
        SUBSCRIBERS_FILE,
        sorted(application.bot_data.get("subscribers", set())),
    )
    save_json(MEMBERS_FILE, application.bot_data.get("members", {}))


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing. Add your Telegram bot token in FadeHost environment variables."
        )
    if not BOT_OWNER_ID.isdigit():
        raise RuntimeError(
            "BOT_OWNER_ID is missing or invalid. Add your numeric Telegram user ID "
            "in FadeHost environment variables to enable owner commands."
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
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("settime", settime_command))
    application.add_handler(CommandHandler("mytime", mytime_command))
    application.add_handler(CommandHandler("members", members_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))

    # Track normal messages and commands. The explicit calls above ensure key
    # commands work even if handler ordering is changed later.
    application.add_handler(MessageHandler(filters.ALL, track_member), group=-1)
    application.add_handler(
        CallbackQueryHandler(
            verse_button_callback,
            pattern=r"^(next|random|audio):\d+$",
        )
    )

    logger.info("Bot is starting.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
