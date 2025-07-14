print(">>>> SCRIPT EXECUTION STARTED <<<<") # Prominent marker
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

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_FALLBACK_TOKEN_IF_ANY")
DOWNLOAD_DIR = "downloads"
TELEGRAM_FILE_LIMIT_MB = 2000
LOCAL_SAVE_LIMIT_MB = 50

executor = ThreadPoolExecutor(max_workers=5) # Reverted to a more reasonable default

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_html(
        f"Hi {user.mention_html()}! I'm your media downloader bot. Send me a link to download!",
    )

last_progress_update_time = {}

def _blocking_download_video(url: str, update: Update, context: ContextTypes.DEFAULT_TYPE, message_id: int, main_loop: asyncio.AbstractEventLoop) -> tuple[str, int]:
    chat_id = update.effective_chat.id
    last_progress_update_time[chat_id] = 0
    cookies_file_path = "/app/persistent_data/cookies.txt" # Path for Railway Volume

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
            current_time = main_loop.time()

            if message_id and \
               (current_time - last_progress_update_time.get(chat_id, 0) > 5 or
                (numeric_percentage is not None and
                 (numeric_percentage % 5 < 0.1 or numeric_percentage > 99.0))):
                progress_message = (
                    f"Downloading: {percentage}\n"
                    f"ETA: {eta}\n"
                    f"Speed: {speed}\n"
                    f"Downloaded: {yt_dlp.utils.format_bytes(downloaded_bytes)} / {yt_dlp.utils.format_bytes(total_bytes)}"
                )
                try:
                    asyncio.run_coroutine_threadsafe(
                        context.bot.edit_message_text(
                            text=progress_message,
                            chat_id=chat_id,
                            message_id=message_id
                        ),
                        main_loop
                    )
                    last_progress_update_time[chat_id] = current_time
                except Exception as e_progress:
                    logger.warning(f"Failed to edit progress message: {e_progress}")
        elif d['status'] == 'finished':
            try:
                asyncio.run_coroutine_threadsafe(
                    context.bot.edit_message_text(
                        text="Download finished. Processing...",
                        chat_id=chat_id,
                        message_id=message_id
                    ),
                    main_loop
                )
            except Exception as e_finish:
                logger.warning(f"Failed to edit 'Download finished' message: {e_finish}")

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
        'verbose': False,
        'log_warnings': True,
        'logger': logger,
        'prefer_https': False,
        'force_ipv4': True,
        'sleep_interval_requests': 1,
        'max_sleep_interval': 5,
        'no_cache_dir': True,
        'progress_hooks': [progress_hook],
        'cookies': cookies_file_path, # Use the defined path
    }
    logger.info(f"yt-dlp attempting to extract info for URL: {url} with progress hook and cookies from {cookies_file_path}.")
    if not os.path.exists(cookies_file_path):
        logger.warning(f"{cookies_file_path} not found. Downloads requiring authentication may fail.")
    else:
        logger.info(f"{cookies_file_path} found. Size: {os.path.getsize(cookies_file_path)} bytes.")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise ValueError("yt-dlp failed to extract video information.")
        filepath = ydl.prepare_filename(info)
        logger.info(f"yt-dlp prepared filename: {filepath}")
        if not os.path.exists(filepath):
            if '_filepath' in info:
                filepath = info['_filepath']
                logger.info(f"Using _filepath from info dictionary: {filepath}")
            else:
                if os.path.exists(DOWNLOAD_DIR):
                    logger.error(f"Expected file {filepath} not found. Files in {DOWNLOAD_DIR}: {os.listdir(DOWNLOAD_DIR)}")
                else:
                    logger.error(f"Expected file {filepath} not found. {DOWNLOAD_DIR} does not exist.")
                raise FileNotFoundError(f"Downloaded file not found at expected path: {filepath}")
        file_size = os.path.getsize(filepath)
        return filepath, file_size

def _blocking_convert_media(input_filepath: str, action: str) -> tuple[str, int]:
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
    base, ext = os.path.splitext(original_filepath)
    reencoded_filepath = base + "_telegram.mp4"
    command = [
        "ffmpeg", "-i", original_filepath,
        "-vf", "scale=1280:-1", # Downscale to 1280p width, keep aspect ratio
        "-c:v", "libx264", "-preset", "fast",
        "-crf", "28", "-maxrate", "1000k", "-bufsize", "2000k", "-c:a", "aac",
        "-b:a", "64k", "-movflags", "faststart", "-y", reencoded_filepath
    ]
    if resolution:
        # This is a bit of a hack, but it works.
        # The scale filter is already in the command, so we just need to replace the value.
        try:
            index_of_vf = command.index("-vf")
            command[index_of_vf+1] = f"scale={resolution}"
        except ValueError:
            # If -vf is not in the command for some reason, add it.
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
    text = update.message.text
    logger.info(f"Received message text: {text}")
    url_match = re.search(r"https?://\S+", text)
    if url_match:
        url = url_match.group(0)
        logger.info(f"Detected URL: {url}")
        progress_message = await update.message.reply_text(f"Initializing download for: {url}")
        progress_message_id = progress_message.message_id
        try:
            main_loop = asyncio.get_running_loop()
            filepath, file_size = await main_loop.run_in_executor(
                executor, _blocking_download_video, url, update, context, progress_message_id, main_loop
            )
            reencoded_filepath, reencoded_file_size = await asyncio.get_running_loop().run_in_executor(
                executor, _blocking_reencode_video, filepath
            )
            file_size_mb = reencoded_file_size / (1024 * 1024)
            logger.info(f"File: {reencoded_filepath}, Size: {reencoded_file_size} bytes ({file_size_mb:.2f} MB)")
            if file_size_mb > TELEGRAM_FILE_LIMIT_MB:
                logger.info(f"Re-encoded file still too large ({file_size_mb:.2f} MB). Attempting lower resolution re-encode.")
                if os.path.exists(reencoded_filepath):
                    os.remove(reencoded_filepath)
                reencoded_filepath, reencoded_file_size = await asyncio.get_running_loop().run_in_executor(
                    executor, _blocking_reencode_video, filepath, "640:-1"
                )
                file_size_mb = reencoded_file_size / (1024 * 1024)
                logger.info(f"File (low res): {reencoded_filepath}, Size: {reencoded_file_size} bytes ({file_size_mb:.2f} MB)")
            
            logger.info(f"Checking file size: {file_size_mb:.2f} MB vs limit {TELEGRAM_FILE_LIMIT_MB} MB and local save limit {LOCAL_SAVE_LIMIT_MB} MB")
            if file_size_mb > LOCAL_SAVE_LIMIT_MB:
                await update.message.reply_text(
                    f"File size ({file_size_mb:.2f} MB) exceeds upload limit of {LOCAL_SAVE_LIMIT_MB} MB. "
                    f"File saved to local storage: {os.path.basename(reencoded_filepath)} (Note: This path is on the server)"
                )
                if os.path.exists(filepath):
                    os.remove(filepath)
            else:
                file_id = str(uuid.uuid4())
                context.bot_data[file_id] = reencoded_filepath
                keyboard = [
                    [InlineKeyboardButton("Convert to MP3", callback_data=f"mp3:{file_id}")],
                    [InlineKeyboardButton("Convert to MP4 (Low Quality)", callback_data=f"mp4_low:{file_id}")],
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                logger.info(f"Attempting to send document: {reencoded_filepath}")
                try:
                    with open(reencoded_filepath, 'rb') as f:
                        unique_filename = f"{os.path.basename(reencoded_filepath)}?v={uuid.uuid4()}"
                        await update.message.reply_video(video=InputFile(f, filename=unique_filename), reply_markup=reply_markup, read_timeout=600, write_timeout=600)
                    logger.info(f"Document sent successfully: {unique_filename}")
                    await update.message.reply_text("Download complete! Choose a conversion option or ignore.")
                except Exception as upload_e:
                    logger.error(f"Error uploading document {reencoded_filepath}: {upload_e}")
                    await update.message.reply_text(f"Failed to upload file. Error: {upload_e}")
                finally:
                    if os.path.exists(filepath):
                        os.remove(filepath)
        except Exception as e:
            logger.error(f"Error downloading {url}: {e}", exc_info=True)
            await update.message.reply_text(f"Failed to download {url}. Error: {e}")
    else:
        await update.message.reply_text("Please send a valid URL to download.")

async def convert_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Entered convert_media function.")
    query = update.callback_query
    await query.answer()
    logger.info(f"Callback query data: {query.data}")
    data = query.data.split(":")
    action = data[0]
    file_id = data[1]
    original_filepath_from_botdata = context.bot_data.get(file_id)

    if not original_filepath_from_botdata or not os.path.exists(original_filepath_from_botdata):
        logger.error(f"Original file not found for file_id: {file_id}. Path: {original_filepath_from_botdata}")
        message_text = "Original file not found. It might have been removed or the request is old. Please try downloading again."
        try:
            if hasattr(query.message, 'caption') and query.message.caption is not None:
                await query.message.edit_caption(caption=message_text, reply_markup=query.message.reply_markup)
            else:
                await query.edit_message_text(text=message_text)
        except Exception as e_edit:
            logger.error(f"Error updating message for file not found: {e_edit}")
            await query.edit_message_text(text=message_text) # Fallback
        return

    temp_source_for_conversion = ""
    converted_filepath = ""
    converted_file_size_mb = 0 
    final_text = "An error occurred during conversion processing."
    edit_target_message = query.message

    try:
        base, ext = os.path.splitext(os.path.basename(original_filepath_from_botdata))
        temp_source_for_conversion = os.path.join(DOWNLOAD_DIR, f"{base}_{uuid.uuid4()}_tempcopy{ext}")
        shutil.copy(original_filepath_from_botdata, temp_source_for_conversion)
        logger.info(f"Created temporary copy for conversion: {temp_source_for_conversion}")
        
        converting_message_text = f"⏳ Converting {os.path.basename(original_filepath_from_botdata)} to {action.upper()}..."
        if hasattr(edit_target_message, 'caption') and edit_target_message.caption is not None:
            await edit_target_message.edit_caption(caption=converting_message_text, reply_markup=edit_target_message.reply_markup)
        else:
            await edit_target_message.edit_text(text=converting_message_text, reply_markup=edit_target_message.reply_markup)
        logger.info(f"Message edited to 'Converting...' for {os.path.basename(original_filepath_from_botdata)}")

        converted_filepath, converted_file_size = await asyncio.get_running_loop().run_in_executor(
            executor, _blocking_convert_media, temp_source_for_conversion, action
        )
        logger.info(f"Finished blocking conversion. Converted file: {converted_filepath}, Size: {converted_file_size}")
        converted_file_size_mb = converted_file_size / (1024 * 1024)

        if converted_file_size_mb > LOCAL_SAVE_LIMIT_MB:
            final_text = (
                f"⚠️ Converted file size ({converted_file_size_mb:.2f} MB) exceeds upload limit.\n"
                f"File saved to local storage: {os.path.basename(converted_filepath)} (Note: This path is on the server)"
            )
            logger.info(f"Converted file saved locally: {converted_filepath}")
        else:
            logger.info(f"Attempting to send converted document: {converted_filepath}")
            try:
                await query.message.reply_document(document=InputFile(open(converted_filepath, 'rb'), filename=os.path.basename(converted_filepath)))
                logger.info(f"Converted document sent successfully: {converted_filepath}")
                final_text = f"✅ Conversion to {action.upper()} complete! New file sent."
            except Exception as upload_e:
                logger.error(f"Error uploading converted document {converted_filepath}: {upload_e}", exc_info=True)
                final_text = f"❌ Failed to upload converted file. Error: {upload_e}"
        
        if hasattr(edit_target_message, 'caption') and edit_target_message.caption is not None:
            await edit_target_message.edit_caption(caption=final_text, reply_markup=edit_target_message.reply_markup)
        else:
            await edit_target_message.edit_text(text=final_text, reply_markup=edit_target_message.reply_markup)

    except Exception as e_conv:
        logger.error(f"Error during conversion processing: {e_conv}", exc_info=True)
        error_text = f"❌ Error during {action} conversion."
        if isinstance(e_conv, subprocess.CalledProcessError):
            error_text = f"❌ Conversion failed: FFmpeg error. Details in logs."
        elif isinstance(e_conv, FileNotFoundError):
             error_text = f"❌ Error during conversion: A file was not found."
        
        try:
            if hasattr(edit_target_message, 'caption') and edit_target_message.caption is not None:
                await edit_target_message.edit_caption(caption=error_text, reply_markup=edit_target_message.reply_markup)
            else:
                await edit_target_message.edit_text(text=error_text, reply_markup=edit_target_message.reply_markup)
        except Exception as e_report:
            logger.error(f"Failed to report conversion error to user: {e_report}")
    finally:
        if converted_filepath and os.path.exists(converted_filepath):
            if not (converted_file_size_mb > LOCAL_SAVE_LIMIT_MB and final_text.startswith("⚠️")):
                 os.remove(converted_filepath)
                 logger.info(f"Cleaned up converted file: {converted_filepath}")
        if temp_source_for_conversion and os.path.exists(temp_source_for_conversion):
            os.remove(temp_source_for_conversion)
            logger.info(f"Cleaned up temporary source copy: {temp_source_for_conversion}")

def main() -> None:
    logger.info(">>>> MAIN FUNCTION STARTED <<<<")
    logger.info(f"Current working directory: {os.getcwd()}")
    download_dir_absolute_path = os.path.abspath(DOWNLOAD_DIR)
    logger.info(f"Download directory target (relative): {DOWNLOAD_DIR}")
    logger.info(f"Attempting to ensure download directory exists at absolute path: {download_dir_absolute_path}")
    try:
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        logger.info(f"os.makedirs call for '{DOWNLOAD_DIR}' completed.")
        if os.path.exists(DOWNLOAD_DIR) and os.path.isdir(DOWNLOAD_DIR):
            logger.info(f"SUCCESS: Directory '{DOWNLOAD_DIR}' exists at '{download_dir_absolute_path}'.")
            try:
                logger.info(f"Contents of '{DOWNLOAD_DIR}': {os.listdir(DOWNLOAD_DIR)}")
            except Exception as e_list_dir:
                logger.warning(f"Could not list contents of '{DOWNLOAD_DIR}', but it exists. Error: {e_list_dir}")
        else:
            logger.error(f"FAILURE: Directory '{DOWNLOAD_DIR}' does NOT exist or is not a directory at '{download_dir_absolute_path}' after os.makedirs call.")
    except Exception as e_makedirs:
        logger.error(f"CRITICAL FAILURE: Error during os.makedirs for '{DOWNLOAD_DIR}': {e_makedirs}", exc_info=True)

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).concurrent_updates(1).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url_message))
    application.add_handler(CallbackQueryHandler(convert_media))

    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()