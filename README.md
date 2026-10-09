# One-Ayah-Daily
# 🌙 Quran Verse of the Day Bot

A Telegram bot that sends one randomly selected Quranic verse every day,
including the original Arabic text, English meaning, and verse reference.

## Features

- Daily Quran verse delivery
- Original Arabic text
- English meaning
- Surah name and verse number
- Subscribe and unsubscribe commands
- Local JSON dataset
- No external Quran API required

## Commands

- `/start` — Subscribe to daily verses
- `/today` — Read today's verse
- `/stop` — Unsubscribe
- `/help` — Show available commands

## Project files

- `bot.py` — Main bot application
- `quran.json` — Local Quran verse dataset
- `requirements.txt` — Python dependencies
- `README.md` — Project documentation

## Requirements

- Python 3.10 or later
- A Telegram bot token
- Persistent host storage for subscriber data

## Environment variables

Configure these in your hosting dashboard:

| Variable | Default | Purpose |
|---|---|---|
| `BOT_TOKEN` | Required | Telegram bot token |
| `TIMEZONE` | `Africa/Addis_Ababa` | Daily scheduling timezone |
| `DAILY_HOUR` | `8` | Daily delivery hour, 0–23 |
| `DAILY_MINUTE` | `0` | Daily delivery minute, 0–59 |
| `DATA_DIR` | `data` directory | Directory for subscriber and state files |

Never commit your bot token to GitHub.

## Run locally

Install dependencies:

    pip install -r requirements.txt

Set your bot token as an environment variable.

Linux/macOS:

    export BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"

Windows PowerShell:

    $env:BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"

Start the bot:

    python bot.py

## Deployment

1. Upload these files to your GitHub repository.
2. Connect the repository to your Python hosting provider.
3. Configure the required environment variables.
4. Install dependencies using `pip install -r requirements.txt`.
5. Set the startup command to `python bot.py`.
6. Ensure the service runs continuously and has persistent storage.
7. Open your Telegram bot and send `/start`.

## Quran text accuracy

The included JSON file is only a small demonstration dataset.

Before public release, replace it with a complete, verified Quran dataset
and a reliable English translation. Verify every Arabic verse, surah number,
ayah number, and translation.

Retain the required attribution and comply with the license of any dataset
or translation you use.

## Persistence

The bot stores subscribers in `data/subscribers.json` and the current daily
verse in `data/state.json`.

If the host replaces the filesystem during redeployment or restarts, use a
persistent volume and set `DATA_DIR` to its mounted path.

## Important scheduling note

The bot uses an in-process daily scheduler. It must remain running for
scheduled delivery to occur. Do not run multiple replicas of the same bot,
because each replica could send duplicate messages.

## License

Choose a license appropriate for your code and the Quran dataset you use.
Dataset and translation licenses may differ from the code license.
