import logging
import os
import yt_dlp # type: ignore
import asyncio
import re # Import regex module
import subprocess # For ffmpeg
from concurrent.futures import ThreadPoolExecutor # Import ThreadPoolExecutor
import uuid # Import uuid for generating unique IDs
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile # type: ignore # Import for inline keyboards
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler # type: ignore # Import CallbackQueryHandler

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8163802324:AAGE7AG_PTCMtlAeg4XMA1GT_lTuWdNonoQ")
DOWNLOAD_DIR = "downloads"
TELEGRAM_FILE_LIMIT_MB = 2000 # Telegram bot API limit is 2GB
LOCAL_SAVE_LIMIT_MB = 50 # Files larger than this will be saved locally and not uploaded

# Create a ThreadPoolExecutor for running blocking I/O operations (like yt-dlp and ffmpeg)
executor = ThreadPoolExecutor(max_workers=5) # You can adjust max_workers as needed

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(
        f"Hi {user.mention_html()}! I'm your media downloader bot. Send me a link to download!",
    )

def _blocking_download_video(url: str) -> tuple[str, int]:
    """Synchronously downloads a video using yt-dlp and returns its path and size."""
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
        'noplaylist': True,
        'restrictfilenames': True,
        'socket_timeout': 60,  # Set timeout to 60 seconds
        'no_warnings': True,   # Suppress warnings
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)
        file_size = os.path.getsize(filepath)
        return filepath, file_size

def _blocking_convert_media(original_filepath: str, action: str) -> tuple[str, int]:
    """Synchronously converts media using ffmpeg and returns the converted file's path and size."""
    output_filepath = None
    if action == "mp3":
        output_filepath = original_filepath + ".mp3"
        command = ["ffmpeg", "-i", original_filepath, "-vn", "-ab", "128k", "-ar", "44100", "-y", output_filepath]
    elif action == "mp4_low":
        output_filepath = original_filepath + ".low.mp4"
        command = ["ffmpeg", "-i", original_filepath, "-vf", "scale=640:-1", "-crf", "28", "-y", output_filepath]
    else:
        raise ValueError("Unsupported conversion type.")

    result = subprocess.run(command, check=True, capture_output=True)
    logger.info(f"FFmpeg stdout: {result.stdout.decode()}")
    logger.info(f"FFmpeg stderr: {result.stderr.decode()}")
    if not output_filepath or not os.path.exists(output_filepath):
        raise RuntimeError(f"Conversion failed or output file not found. FFmpeg output: {result.stderr.decode()}")
    
    file_size = os.path.getsize(output_filepath)
    return output_filepath, file_size

def _blocking_reencode_video(original_filepath: str, resolution: str | None = None) -> tuple[str, int]:
    """Synchronously re-encodes a video to a Telegram-friendly MP4 format using ffmpeg."""
    base, ext = os.path.splitext(original_filepath)
    reencoded_filepath = base + "_telegram.mp4"
    command = [
        "ffmpeg",
        "-i", original_filepath,
        "-c:v", "libx264",  # H.264 video codec
        "-preset", "fast", # Encoding preset (e.g., ultrafast, superfast, fast, medium, slow, slower, veryslow)
        "-crf", "28",       # Constant Rate Factor (lower is higher quality, 23 is good default)
        "-maxrate", "1000k", # Maximum video bitrate
        "-bufsize", "2000k", # Buffer size for maxrate
        "-c:a", "aac",      # AAC audio codec
        "-b:a", "64k",     # Audio bitrate
        "-movflags", "faststart", # Optimize for streaming
        "-y", reencoded_filepath
    ]
    if resolution:
        command.insert(-1, "-vf")
        command.insert(-1, f"scale={resolution}")
    result = subprocess.run(command, check=True, capture_output=True)
    logger.info(f"FFmpeg stdout: {result.stdout.decode()}")
    logger.info(f"FFmpeg stderr: {result.stderr.decode()}")
    if not os.path.exists(reencoded_filepath):
        raise RuntimeError(f"Re-encoding failed or output file not found. FFmpeg output: {result.stderr.decode()}")
    file_size = os.path.getsize(reencoded_filepath)
    return reencoded_filepath, file_size

async def handle_url_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles messages that contain a URL and attempts to download the video."""
    text = update.message.text
    logger.info(f"Received message text: {text}")
    url_match = re.search(r"https?://\S+", text)
    if url_match:
        url = url_match.group(0)
        logger.info(f"Detected URL: {url}")
        await update.message.reply_text(f"Attempting to download: {url}")

        try:
            # Run the blocking download in the executor
            filepath, file_size = await asyncio.get_running_loop().run_in_executor(
                executor, _blocking_download_video, url
            )

            # Re-encode the video for Telegram compatibility
            reencoded_filepath, reencoded_file_size = await asyncio.get_running_loop().run_in_executor(
                executor, _blocking_reencode_video, filepath
            )

            file_size_mb = reencoded_file_size / (1024 * 1024)
            logger.info(f"File: {reencoded_filepath}, Size: {reencoded_file_size} bytes ({file_size_mb:.2f} MB)")

            # If the re-encoded file is still too large, try re-encoding at a lower resolution
            if file_size_mb > TELEGRAM_FILE_LIMIT_MB:
                logger.info(f"Re-encoded file still too large ({file_size_mb:.2f} MB). Attempting lower resolution re-encode.")
                os.remove(reencoded_filepath) # Clean up the first re-encoded file
                reencoded_filepath, reencoded_file_size = await asyncio.get_running_loop().run_in_executor(
                    executor, _blocking_reencode_video, filepath, "640:-1" # Re-encode to 640px width
                )
                file_size_mb = reencoded_file_size / (1024 * 1024)
                logger.info(f"File (low res): {reencoded_filepath}, Size: {reencoded_file_size} bytes ({file_size_mb:.2f} MB)")

            logger.info(f"Checking file size: {file_size_mb:.2f} MB vs limit {TELEGRAM_FILE_LIMIT_MB} MB and local save limit {LOCAL_SAVE_LIMIT_MB} MB")
            if file_size_mb > LOCAL_SAVE_LIMIT_MB:
                await update.message.reply_text(
                    f"File size ({file_size_mb:.2f} MB) exceeds the upload limit of {LOCAL_SAVE_LIMIT_MB} MB. "
                    f"File saved to local storage: {reencoded_filepath}"
                )
                # Clean up original file after re-encoding
                if os.path.exists(filepath):
                    os.remove(filepath)
                # Do NOT remove reencoded_filepath as it's saved locally
            else:
                # Store filepath in bot_data and create unique IDs for callback_data
                file_id = str(uuid.uuid4())
                context.bot_data[file_id] = reencoded_filepath

                # Create inline keyboard for conversion options
                keyboard = [
                    [InlineKeyboardButton("Convert to MP3", callback_data=f"mp3:{file_id}")],
                    [InlineKeyboardButton("Convert to MP4 (Low Quality)", callback_data=f"mp4_low:{file_id}")],
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                logger.info(f"Attempting to send document: {reencoded_filepath}")
                try:
                    with open(reencoded_filepath, 'rb') as f:
                        await update.message.reply_document(document=InputFile(f, filename=os.path.basename(reencoded_filepath)), reply_markup=reply_markup, read_timeout=600, write_timeout=600)
                    logger.info(f"Document sent successfully: {reencoded_filepath}")
                    await update.message.reply_text("Download complete and file sent! Choose a conversion option or ignore.")
                except Exception as upload_e:
                    logger.error(f"Error uploading document {reencoded_filepath}: {upload_e}")
                    await update.message.reply_text(f"Failed to upload file. Error: {upload_e}")
                finally:
                    # Clean up original file after re-encoding
                    if os.path.exists(filepath):
                        os.remove(filepath)
                    # The re-encoded file and its file_id are kept until conversion is attempted

        except Exception as e:
            logger.error(f"Error downloading {url}: {e}")
            await update.message.reply_text(f"Failed to download {url}. Error: {e}")
    else:
        await update.message.reply_text("Please send a valid URL to download.")

async def convert_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Entered convert_media function.")
    query = update.callback_query
    logger.info(f"Query object: {query}")
    await query.answer() # Acknowledge the callback query
    logger.info("Callback query answered. Processing data...")

    logger.info(f"Callback query data: {query.data}")

    data = query.data.split(":")
    action = data[0]
    file_id = data[1]
    original_filepath = context.bot_data.get(file_id)

    logger.info(f"Action: {action}, File ID: {file_id}, Original Filepath from bot_data: {original_filepath}")

    if not original_filepath:
        logger.error(f"Original filepath not found for file_id: {file_id}. Attempting to edit message.")
        await query.edit_message_text("Original file path not found. It might have expired or been removed.")
        return

    logger.info(f"Attempting to edit message to 'Converting...'")
    await query.edit_message_text(f"Converting {os.path.basename(original_filepath)} to {action.upper()}...")
    logger.info(f"Message edited to 'Converting...'")

    try:
        logger.info(f"Starting blocking conversion for {original_filepath} to {action}")
        converted_filepath, converted_file_size = await asyncio.get_running_loop().run_in_executor(
            executor, _blocking_convert_media, original_filepath, action
        )
        logger.info(f"Finished blocking conversion. Converted file: {converted_filepath}, Size: {converted_file_size}")

        converted_file_size_mb = converted_file_size / (1024 * 1024)

        if converted_file_size_mb > LOCAL_SAVE_LIMIT_MB:
            await query.edit_message_text(
                f"Converted file size ({converted_file_size_mb:.2f} MB) exceeds the upload limit of {LOCAL_SAVE_LIMIT_MB} MB. "
                f"File saved to local storage: {converted_filepath}"
            )
            logger.info(f"Converted file saved locally: {converted_filepath}")
        else:
            logger.info(f"Attempting to send converted document: {converted_filepath}")
            try:
                with open(converted_filepath, 'rb') as f:
                    await query.message.reply_document(document=InputFile(f, filename=os.path.basename(converted_filepath)))
                logger.info(f"Converted document sent successfully: {converted_filepath}")
                await query.edit_message_text("Conversion complete and file sent!")
            except Exception as upload_e:
                logger.error(f"Error uploading converted document {converted_filepath}: {upload_e}")
                await query.edit_message_text(f"Failed to upload converted file. Error: {upload_e}")
            finally:
                # Clean up converted file after sending or failed upload
                if os.path.exists(converted_filepath):
                    os.remove(converted_filepath)
                    logger.info(f"Cleaned up converted file: {converted_filepath}")

    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg error: {e.stderr.decode()}")
        await query.edit_message_text(f"Conversion failed: {e.stderr.decode()}")
    except Exception as e:
        logger.error(f"An unexpected error occurred during conversion: {e}")
        await query.edit_message_text(f"An unexpected error occurred during conversion: {e}")
    finally:
        # Clean up original file after conversion is attempted
        if original_filepath and os.path.exists(original_filepath):
            os.remove(original_filepath)
            logger.info(f"Cleaned up original file: {original_filepath}")
        # Clean up file_id from bot_data
        if file_id in context.bot_data:
            del context.bot_data[file_id]
            logger.info(f"Cleaned up file_id from bot_data: {file_id}")

def download_progress_hook(d, update: Update):
    if d['status'] == 'downloading':
        # This hook is called very frequently, so we should avoid sending too many updates
        # For now, we'll keep it simple, but later we can add rate limiting
        pass
    elif d['status'] == 'finished':
        pass # Download finished, will send file

def main() -> None:
    """Start the bot."""
    # Create download directory if it doesn't exist
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url_message))
    application.add_handler(CallbackQueryHandler(convert_media)) # Handle inline keyboard callbacks

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
