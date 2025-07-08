# Telegram Media Downloader Bot

This is a Telegram bot that allows you to download media from various sources (e.g., YouTube, Tiktok, X, Facebool,Instagram and 1000+ more sites) by simply sending a URL. It supports downloading videos, re-encoding them for Telegram compatibility, and converting them to MP3 or lower quality MP4 formats.

## Features

- **Media Download:** Download videos from supported platforms using `yt-dlp`.
- **Telegram Compatibility:** Automatically re-encodes downloaded videos to a Telegram-friendly MP4 format.
- **File Size Handling:** Handles large files by saving them locally if they exceed Telegram's upload limit.
- **Media Conversion:** Convert downloaded media to:
    - MP3 (audio only)
    - Lower quality MP4 (reduced resolution)
- **Asynchronous Operations:** Uses `asyncio` and `ThreadPoolExecutor` for efficient handling of downloads and conversions.

## Technologies Used

- **Python:** The core programming language.
- **`python-telegram-bot`:** For interacting with the Telegram Bot API.
- **`yt-dlp`:** A powerful command-line program to download videos from YouTube.com and other video sites.
- **`ffmpeg`:** For media re-encoding and conversion.
- **`asyncio`:** For asynchronous programming.
- **`ThreadPoolExecutor`:** For running blocking I/O operations in a separate thread pool.

## Setup

Follow these steps to set up and run the bot:

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/downloaderBot.git
cd downloaderBot
```

### 2. Create a Virtual Environment (Recommended)

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Install FFmpeg

FFmpeg is required for media processing. You can download it from the official website or install it via your system's package manager.

**On Debian/Ubuntu:**
```bash
sudo apt update
sudo apt install ffmpeg
```

**On macOS (using Homebrew):**
```bash
brew install ffmpeg
```

**On Windows:**
Download the executables from the [FFmpeg website](https://ffmpeg.org/download.html) and add them to your system's PATH.

### 5. Configure Environment Variables

Create a `.env` file in the root directory of the project and add your Telegram Bot Token:

```
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN_HERE
```

**How to get your `TELEGRAM_BOT_TOKEN`:**
1. Open Telegram and search for `@BotFather`.
2. Start a chat with `@BotFather` and send `/newbot`.
3. Follow the instructions to choose a name and username for your bot.
4. `@BotFather` will give you an API token. Copy this token and paste it into your `.env` file.

### 6. Run the Bot

```bash
python3 bot.py
```

The bot should now be running and listening for messages.

## Usage

1. **Start the bot:** Send the `/start` command to your bot in Telegram.
2. **Send a URL:** Send a message containing a URL of the media you want to download (e.g., a YouTube video link).
3. **Download and Convert:** The bot will download the media, re-encode it for Telegram, and then offer options to convert it to MP3 or a lower quality MP4 via inline keyboard buttons.

## Project Structure

```
.env
.gitignore
bot.py
requirements.txt
downloads/
```

- `.env`: Stores environment variables like your Telegram Bot Token.
- `.gitignore`: Specifies intentionally untracked files to ignore.
- `bot.py`: The main script containing the bot's logic.
- `requirements.txt`: Lists the Python dependencies.
- `downloads/`: Directory where downloaded and converted media files are temporarily stored.

## Contributing

Feel free to fork the repository, open issues, or submit pull requests if you have suggestions or improvements.

## License

This project is open-source and available under the [MIT License](LICENSE).