import logging
import os
import yt_dlp # type: ignore
import asyncio
import re # Import regex module
import subprocess # For ffmpeg
from concurrent.futures import ThreadPoolExecutor # Import ThreadPoolExecutor
import uuid # Import uuid for generating unique IDs
import shutil # Import for file operations like copy
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile # type: ignore # Import for inline keyboards
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler # type: ignore # Import CallbackQueryHandler
#Downloader Bot for Telegram using yt-dlp and ffmpeg
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

# Store last update time for progress messages to avoid spamming
last_progress_update_time = {}

def _blocking_download_video(url: str, update: Update, context: ContextTypes.DEFAULT_TYPE, message_id: int, main_loop: asyncio.AbstractEventLoop) -> tuple[str, int]:
    """Synchronously downloads a video using yt-dlp and returns its path and size, updating progress."""
    chat_id = update.effective_chat.id
    last_progress_update_time[chat_id] = 0 # Initialize last update time for this chat

    def progress_hook(d):
        if d['status'] == 'downloading':
            percentage = d.get('_percent_str', 'N/A')
            try:
                numeric_percentage = float(percentage.strip().replace('%',''))
            except ValueError:
                numeric_percentage = None

            eta = d.get('_eta_str', 'N/A')
            speed = d.get('_speed_str', 'N/A')
            downloaded_bytes = d.get('downloaded_bytes', 0)
            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)

            current_time = main_loop.time() # Use main loop's time
            if message_id and \
               (current_time - last_progress_update_time.get(chat_id, 0) > 5 or
                (numeric_percentage is not None and
                 (numeric_percentage % 5 < 0.1 or numeric_percentage > 99.0))):

                progress_message = f"Downloading: {percentage}\n"
                progress_message += f"ETA: {eta}\n"
                progress_message += f"Speed: {speed}\n"
                progress_message += f"Downloaded: {yt_dlp.utils.format_bytes(downloaded_bytes)} / {yt_dlp.utils.format_bytes(total_bytes)}"

                try:
                    asyncio.run_coroutine_threadsafe(
                        context.bot.edit_message_text(
                            text=progress_message,
                            chat_id=chat_id,
                            message_id=message_id
                        ),
                        main_loop # Ensure coroutine runs on the main event loop
                    )
                    last_progress_update_time[chat_id] = current_time
                except Exception as e:
                    logger.warning(f"Failed to edit progress message: {e}")
        elif d['status'] == 'finished':
            try:
                asyncio.run_coroutine_threadsafe(
                    context.bot.edit_message_text(
                        text="Download finished. Processing...",
                        chat_id=chat_id,
                        message_id=message_id
                    ),
                    main_loop # Ensure coroutine runs on the main event loop
                )
            except Exception as e:
                logger.warning(f"Failed to edit 'Download finished' message: {e}")

    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': os.path.join(DOWNLOAD_DIR, f'{uuid.uuid4()}_%(id)s.%(ext)s'),
        'noplaylist': True,
        'restrictfilenames': True,
        'socket_timeout': 60,
        'no_warnings': True,
        'ignoreerrors': False,
        'allow_unplayable_formats': False,
        'geo_bypass': True,
        'no_check_certificate': True,
        'verbose': False, # Reduce verbosity, progress hook will handle updates
        'log_warnings': True,
        'logger': logger,
        'prefer_https': False,
        'force_ipv4': True,
        'sleep_interval_requests': 1,
        'max_sleep_interval': 5,
        'no_cache_dir': True,
        'progress_hooks': [progress_hook], # Add the progress hook
    }
    logger.info(f"yt-dlp attempting to extract info for URL: {url} with progress hook.")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise ValueError("yt-dlp failed to extract video information.")
        filepath = ydl.prepare_filename(info)
        logger.info(f"yt-dlp prepared filename: {filepath}")
        # Verify the file actually exists before getting its size
        if not os.path.exists(filepath):
            # If the file doesn't exist, it might be due to a redirect or a different final name
            # We need to find the actual downloaded file. This is a common issue with yt-dlp.
            # For now, let's try to get the actual filepath from the info dictionary if available.
            if '_filepath' in info:
                filepath = info['_filepath']
                logger.info(f"Using _filepath from info dictionary: {filepath}")
            else:
                raise FileNotFoundError(f"Downloaded file not found at expected path: {filepath}")

        file_size = os.path.getsize(filepath)
        return filepath, file_size

def _blocking_convert_media(input_filepath: str, action: str) -> tuple[str, int]:
    """Synchronously converts media using ffmpeg and returns the converted file's path and size."""
    base, orig_ext = os.path.splitext(input_filepath)
    output_filepath = ""

    if action == "mp3":
        output_filepath = f"{base}_{action}.mp3"
        command = ["ffmpeg", "-i", input_filepath, "-vn", "-ab", "128k", "-ar", "44100", "-y", output_filepath]
    elif action == "mp4_low":
        output_filepath = f"{base}_{action}.mp4"
        command = ["ffmpeg", "-i", input_filepath, "-vf", "scale=640:-1", "-crf", "28", "-y", output_filepath]
    else:
        raise ValueError(f"Unsupported conversion type: {action}")

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
        progress_message = await update.message.reply_text(f"Initializing download for: {url}")
        progress_message_id = progress_message.message_id

        try:
            # Get the current event loop to pass to the blocking function
            main_loop = asyncio.get_running_loop()
            # Run the blocking download in the executor, passing update, context, message_id, and main_loop
            filepath, file_size = await main_loop.run_in_executor(
                executor, _blocking_download_video, url, update, context, progress_message_id, main_loop
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
                logger.info(f"Generated file_id: {file_id}")
                logger.info(f"Storing reencoded_filepath in bot_data: {reencoded_filepath}")
                context.bot_data[file_id] = reencoded_filepath

                # Create inline keyboard for conversion options
                keyboard = [
                    [InlineKeyboardButton("Convert to MP3", callback_data=f"mp3:{file_id}")],
                    [InlineKeyboardButton("Convert to MP4 (Low Quality)", callback_data=f"mp4_low:{file_id}")],
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                logger.info(f"Attempting to send document: {reencoded_filepath}")
                logger.info(f"Filename for upload: {os.path.basename(reencoded_filepath)}")
                try:
                    with open(reencoded_filepath, 'rb') as f:
                        # Append a unique query parameter to the filename to bypass Telegram's caching
                        unique_filename = f"{os.path.basename(reencoded_filepath)}?v={uuid.uuid4()}"
                        await update.message.reply_video(video=InputFile(f, filename=unique_filename), reply_markup=reply_markup, read_timeout=600, write_timeout=600)
                    logger.info(f"Document sent successfully with unique filename: {unique_filename}")
                    await update.message.reply_text("Download complete and file sent! Choose a conversion option or ignore.")
                except Exception as upload_e:
                    logger.error(f"Error uploading document {reencoded_filepath}: {upload_e}")
                    await update.message.reply_text(f"Failed to upload file. Error: {upload_e}")
                finally:
                    # Clean up original file after re-encoding
                    if os.path.exists(filepath):
                        os.remove(filepath)
                    # The re-encoded file (whose path is in bot_data via file_id) is kept on disk
                    # to be available as a source for conversion operations.

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
    original_filepath_from_botdata = context.bot_data.get(file_id)

    logger.info(f"Action: {action}, File ID: {file_id}, Original Filepath from bot_data: {original_filepath_from_botdata}")

    if not original_filepath_from_botdata or not os.path.exists(original_filepath_from_botdata):
        logger.error(f"Original file not found or path invalid for file_id: {file_id}. Path: {original_filepath_from_botdata}")
        try:
            await query.message.edit_caption(caption="Original file not found. It might have been removed or the request is old. Please try downloading again.", reply_markup=query.message.reply_markup)
        except Exception as e_edit:
            logger.error(f"Error trying to edit caption for file not found: {e_edit}")
            await query.edit_message_text("Original file not found. It might have been removed or the request is old. Please try downloading again.") # Fallback if caption edit fails
        return

    temp_source_for_conversion = "" # Path to the temporary copy for this specific conversion
    converted_filepath = "" # Path to the file generated by ffmpeg

    try:
        # Create a temporary copy for this conversion operation
        base, ext = os.path.splitext(os.path.basename(original_filepath_from_botdata))
        temp_source_for_conversion = os.path.join(DOWNLOAD_DIR, f"{base}_{uuid.uuid4()}_tempcopy{ext}")
        shutil.copy(original_filepath_from_botdata, temp_source_for_conversion)
        logger.info(f"Created temporary copy for conversion: {temp_source_for_conversion} from {original_filepath_from_botdata}")

        await query.message.edit_caption(caption=f"⏳ Converting {os.path.basename(original_filepath_from_botdata)} to {action.upper()}...", reply_markup=query.message.reply_markup)
        logger.info(f"Caption edited to 'Converting...'")

        logger.info(f"Starting blocking conversion for {temp_source_for_conversion} to {action}")
        converted_filepath, converted_file_size = await asyncio.get_running_loop().run_in_executor(
            executor, _blocking_convert_media, temp_source_for_conversion, action
        )
        logger.info(f"Finished blocking conversion. Converted file: {converted_filepath}, Size: {converted_file_size}")

        converted_file_size_mb = converted_file_size / (1024 * 1024)

        if converted_file_size_mb > LOCAL_SAVE_LIMIT_MB:
            # Note: If saved locally, converted_filepath is not cleaned up here, user is given the path.
            caption_text = (
                f"⚠️ Converted file size ({converted_file_size_mb:.2f} MB) exceeds upload limit.\n"
                f"File saved to local storage: {os.path.basename(converted_filepath)}"
            )
            await query.message.edit_caption(caption=caption_text, reply_markup=query.message.reply_markup)
            logger.info(f"Converted file saved locally: {converted_filepath}")
            # No further cleanup of converted_filepath in this branch as it's meant to be kept.
        else:
            logger.info(f"Attempting to send converted document: {converted_filepath}")
            try:
                await query.message.reply_document(document=InputFile(open(converted_filepath, 'rb'), filename=os.path.basename(converted_filepath)))
                logger.info(f"Converted document sent successfully: {converted_filepath}")
                await query.message.edit_caption(caption=f"✅ Conversion to {action.upper()} complete! New file sent.", reply_markup=query.message.reply_markup)
            except Exception as upload_e:
                logger.error(f"Error uploading converted document {converted_filepath}: {upload_e}")
                await query.message.edit_caption(caption=f"❌ Failed to upload converted file. Error: {upload_e}", reply_markup=query.message.reply_markup)
            finally:
                # Clean up the successfully sent or failed-to-upload converted file
                if os.path.exists(converted_filepath):
                    os.remove(converted_filepath)
                    logger.info(f"Cleaned up converted file after processing: {converted_filepath}")

    except FileNotFoundError as fnf_e:
        logger.error(f"File not found during conversion process: {fnf_e}")
        await query.message.edit_caption(caption=f"❌ Error during conversion: File not found. {fnf_e}", reply_markup=query.message.reply_markup)
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg error: {e.stderr.decode()}") # Log full error for server admin
        await query.message.edit_caption(caption=f"❌ Conversion failed: FFmpeg error. Please check logs.", reply_markup=query.message.reply_markup)
    except Exception as e:
        logger.error(f"An unexpected error occurred during conversion: {e}", exc_info=True)
        await query.message.edit_caption(caption=f"❌ An unexpected error occurred during conversion.", reply_markup=query.message.reply_markup)
    finally:
        # Clean up the temporary source copy used for this specific conversion
        if temp_source_for_conversion and os.path.exists(temp_source_for_conversion):
            os.remove(temp_source_for_conversion)
            logger.info(f"Cleaned up temporary source copy: {temp_source_for_conversion}")

        # Note: original_filepath_from_botdata (the one from bot_data) is NOT deleted here.
        # Note: file_id is NOT removed from context.bot_data here.
        # This allows for multiple conversion attempts on the same originally downloaded video.
        # A separate mechanism would be needed for long-term cleanup of bot_data and associated files if desired.

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
