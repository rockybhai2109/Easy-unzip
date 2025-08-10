# ────────────────────────────────────────────────────────────────
# Health-check server — goes above everything
import os, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pyrogram import Client, filters
from pyrogram.types import Message
import logging
import ffmpeg
import tempfile
import re
import logging
import sys

# Ensure stdout/stderr use UTF-8
if sys.stdout.encoding != 'utf-8':
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
if sys.stderr.encoding != 'utf-8':
    sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handles GET requests, which a browser or user might make."""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        """
        Handles HEAD requests for Render's health check.
        It sends the same headers as a GET request but no body.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()



def sanitize_filename(filename: str) -> str:
    # Replace @tags like @abc with @ii_LevelUP_ii
    return re.sub(r'@[\w_]+', '@ii_LevelUP_ii', filename)

def _run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    server.serve_forever()

# Start server in background thread
threading.Thread(target=_run_health_server, daemon=True).start()
# ────────────────────────────────────────────────────────────────

import asyncio
import os
import time
import logging
import py7zr
import zipfile
import rarfile # For .rar file support
import shutil
import sys
from datetime import datetime
from typing import Optional, List, Dict, Any, Set
from pathlib import Path
from dotenv import load_dotenv
from pyrogram import Client, types, filters, enums
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import FloodWait, RPCError

# --- Load environment variables ---
env_path = Path('.') / '.env'
load_dotenv(dotenv_path=env_path)

# Ensure environment variables are loaded, raise an error if not found
try:
    API_ID = int(os.getenv("TG_API_ID"))
    API_HASH = os.getenv("TG_API_HASH")
    BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
except (TypeError, ValueError) as e:
    raise ValueError(f"Missing or invalid Telegram API credentials in .env file: {e}")

# --- Proxy Configuration (optional) ---
USE_PROXY = False # Set to True if you want to use a proxy
PROXY_CONFIG = {
    "hostname": "95.217.135.241",
    "port": 443,
    "type": "mtproto",
    "secret": "7pJZSUjIp-43RmluNUkNmWNkbnMuZ21vZ2xlLmNvbQ=="
}

# --- Set up logging with UTF-8 encoding for console ---
# Set console output encoding to UTF-8


bot = Client(
    name="my_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=4,  # Number of worker threads
    sleep_threshold=60  # Flood wait threshold
)

# Alternative method using session string (if you have one)
"""
bot = Client(
    name="my_bot",
    session_string="your_session_string",
    api_id=API_ID,
    api_hash=API_HASH
)
"""

# Start command handler
@bot.on_message(filters.command("start") & (filters.private | filters.group | filters.channel))
async def start(bot, m: Message):
    user_name = m.from_user.first_name if m.from_user else "User"
    
    welcome_text = f"""
👋 Hello {user_name}!

Welcome to the bot! Here's what I can do:

🔹 /help - Show available commands
🔹 /info - Get bot information
🔹 /settings - Configure bot settings

Feel free to explore and let me know if you need any assistance!
    """
    
    try:
        await m.reply_text(
            text=welcome_text,
            disable_web_page_preview=True,
            reply_to_message_id=m.id
        )
    except Exception as e:
        logging.error(f"Error in start command: {e}")
        await m.reply_text("Welcome to the bot!")


# --- Configuration for file selection ---
FILES_PER_PAGE = 10 # Number of files to show per page for multi-selection


def format_progress_bar(current: int, total: int) -> str:
    """Formats a simple text-based progress bar."""
    if total <= 0: # Handle cases where total is 0 or unknown
        return "[□□□□□□□□□□□□□□□□□□□□] 0.0%"
    
    percentage = current * 100 / total
    # Clamp percentage between 0 and 100
    percentage = max(0.0, min(100.0, percentage))

    filled_blocks = int(percentage // 5)
    empty_blocks = 20 - filled_blocks

    progress_bar = "■" * filled_blocks + "□" * empty_blocks
    return f"[{progress_bar}] {percentage:.1f}%"

# --- Async progress callback function ---
async def progress_callback_func(current: int, total: int, client_data: Dict[str, Any]):
    """
    Callback function for pyrogram's download/upload operations to update message.
    Updates the message every 3-5 seconds or when percentage changes significantly.
    """
    message_to_edit = client_data['message']
    file_name = client_data['file_name']
    start_time = client_data['start_time']
    last_update_time = client_data.get('last_update_time', start_time)
    last_updated_percentage = client_data.get('last_updated_percentage', 0)

    # Prioritize explicit_total_size from client_data if available
    effective_total = client_data.get('explicit_total_size')
    if effective_total is None or effective_total <= 0:
        effective_total = total # Fallback to the 'total' passed by Pyrogram if our explicit one is bad

    elapsed_time = time.time() - start_time
    download_speed = current / elapsed_time if elapsed_time > 0 else 0

    status_text = ""
    percentage = 0

    if effective_total is not None and effective_total > 0:
        percentage = current * 100 / effective_total
        bar = format_progress_bar(current, effective_total)
        speed_mbps = download_speed / (1024 * 1024) # Convert bytes/sec to MB/s
        
        status_text = (
            f"⬇️ Downloading `{file_name}`...\n"
            f"{bar}\n"
            f"Speed: `{speed_mbps:.2f} MB/s` | Progress: `{current / (1024*1024):.2f} / {effective_total / (1024*1024):.2f} MB`"
        )
        
        should_update = (last_updated_percentage == 0 and percentage > 0) or \
                        (percentage - last_updated_percentage >= 5 and time.time() - last_update_time >= 3) or \
                        (percentage == 100)
    else: # Effective Total size is unknown or zero
        status_text = (
            f"⬇️ Downloading `{file_name}`...\n"
            f"Progress: `{current / (1024*1024):.2f} MB` / Unknown size\n"
            f"Speed: N/A"
        )
        should_update = time.time() - last_update_time >= 5
    
    if should_update:
        try:
            await message_to_edit.edit_text(
                status_text,
                parse_mode=enums.ParseMode.MARKDOWN
            )
            client_data['last_update_time'] = time.time()
            client_data['last_updated_percentage'] = percentage if effective_total is not None and effective_total > 0 else 0
        except Exception as e:
            logger.warning(f"Error updating progress message for {file_name} (msg ID: {message_to_edit.id}): {e}")




# --- Helper to recursively list all files in a directory ---
def _recursively_list_files(directory_path: Path) -> List[Path]:
    """Recursively lists all files within a given directory and its subdirectories."""
    file_list = []
    for item in directory_path.iterdir():
        if item.is_file():
            file_list.append(item)
        elif item.is_dir():
            file_list.extend(_recursively_list_files(item)) # Recurse into subdirectories
    return file_list


class UltimateBot:
    def __init__(self):
        self.client: Optional[Client] = None
        self.active_downloads: Set[str] = set()
        self.user_extraction_sessions: Dict[int, Dict[str, Any]] = {}

    async def _get_client(self) -> Client:
        """Initializes and returns the Pyrogram client."""
        if self.client:
            return self.client

        session_name = "ultimate_unzipper_bot"
        if USE_PROXY:
            logger.info(f"Connecting via MTProto Proxy: {PROXY_CONFIG['hostname']}:{PROXY_CONFIG['port']}")
            self.client = Client(
                name=session_name,
                api_id=API_ID,
                api_hash=API_HASH,
                bot_token=BOT_TOKEN,
                proxy=PROXY_CONFIG,
                sleep_threshold=30
            )
        else:
            logger.info("Connecting without proxy...")
            self.client = Client(
                name=session_name,
                api_id=API_ID,
                api_hash=API_HASH,
                bot_token=BOT_TOKEN,
                sleep_threshold=30
            )
        return self.client

    def _generate_file_selection_keyboard(self, chat_id: int) -> InlineKeyboardMarkup:
        """
        Generates the inline keyboard for file selection, including pagination and
        visual indication of selected files.
        """
        session_data = self.user_extraction_sessions.get(chat_id)
        if not session_data:
            logger.error(f"Attempted to generate keyboard for non-existent session: {chat_id}")
            return InlineKeyboardMarkup([[InlineKeyboardButton("Session Expired", callback_data="session_expired")]])

        all_files = session_data['files']
        selected_indices = session_data['selected_file_indices']
        current_page = session_data['current_page']
        temp_dir = session_data['temp_dir']
        total_pages = session_data['total_pages']

        buttons = []
        
        start_index = current_page * FILES_PER_PAGE
        end_index = min(start_index + FILES_PER_PAGE, len(all_files))
        
        if not all_files:
            buttons.append([InlineKeyboardButton("No files found in archive.", callback_data="no_files_info")])
        else:
            for i in range(start_index, end_index):
                file_path = all_files[i]
                try:
                    relative_path_str = os.path.relpath(file_path, temp_dir)
                    display_path = Path(relative_path_str)
                except ValueError:
                    display_path = file_path.name
                
                button_text = str(display_path)
                if len(button_text) > 50:
                    button_text = "..." + button_text[-47:]
                
                if i in selected_indices:
                    button_text = f"✅ {button_text}"
                
                buttons.append([InlineKeyboardButton(button_text, callback_data=f"toggle_file_{i}")])

        navigation_row = []
        if current_page > 0:
            navigation_row.append(InlineKeyboardButton("⬅️ Previous", callback_data="prev_page"))
        
        navigation_row.append(InlineKeyboardButton(f"{current_page + 1}/{total_pages}", callback_data="page_indicator_no_action"))
        
        if current_page < total_pages - 1:
            navigation_row.append(InlineKeyboardButton("Next ➡️", callback_data="next_page"))
        
        if navigation_row:
            buttons.append(navigation_row)

        action_row = []
        if selected_indices and all_files:
            action_row.append(InlineKeyboardButton(f"⬆️ Upload Selected ({len(selected_indices)})", callback_data="upload_selected_files"))
        action_row.append(InlineKeyboardButton("❌ Cancel", callback_data="cancel_selection"))
        buttons.append(action_row)

        return InlineKeyboardMarkup(buttons)

    async def start_command_handler(self, client: Client, message: types.Message):
        """Handles the /start command."""
        await self.send_text(message.chat.id, (
            "🎀 Hoi! Send me a `.zip`, `.7z`, or `.rar` archive and I'll unzip it and let you choose which files to download."
        ))

    async def document_handler(self, client: Client, message: types.Message):
        """Handles incoming document messages."""
        if message.document:
            file_name = message.document.file_name
            remote_file_unique_id = message.document.file_unique_id
            
            logger.info(f"Received document from chat {message.chat.id}: Name='{file_name}', Unique ID='{remote_file_unique_id}', Size='{message.document.file_size}' bytes")

            if not remote_file_unique_id or not file_name:
                logger.warning(f"Received document with no file_unique_id or file_name in chat {message.chat.id}")
                await self.send_text(message.chat.id, "❌ Unable to process: File details missing.")
                return

            if not (
                file_name.lower().endswith(('.zip', '.7z', '.rar'))
                or file_name.lower().endswith(tuple([f".zip.{str(i).zfill(3)}" for i in range(1, 1000)]))
            ):
                return await self.send_text(message.chat.id, "❌ Only `.zip`, `.zip.###`, `.7z`, and `.rar` files are supported.")

            if remote_file_unique_id in self.active_downloads:
                return await self.send_text(message.chat.id, "⏳ Already processing this file. Please wait.")
            
            if message.chat.id in self.user_extraction_sessions:
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Yes, cancel current", callback_data="cancel_selection")],
                    [InlineKeyboardButton("No, continue current", callback_data="no_action")]
                ])
                await self.send_text(
                    message.chat.id, 
                    "⚠️ You already have an active extraction session. Do you want to cancel it and start a new one?",
                    reply_markup=reply_markup
                )
                return

            self.active_downloads.add(remote_file_unique_id)
            asyncio.create_task(self.process_file(message.chat.id, message.document, remote_file_unique_id, client))
            self.active_downloads.add(remote_file_unique_id)
            asyncio.create_task(self.process_file(message.chat.id, message.document, remote_file_unique_id, client))

    async def process_file(self, chat_id: int, document: types.Document, remote_file_unique_id: str, client: Client):
        """ Handles the download, extraction, and presentation of files for selection. """
        file_name = document.file_name
        file_size_from_tg = document.file_size
        file_name = sanitize_filename(file_name)
        
        status_message = await self.send_text(chat_id, f"⬇️ Downloading `{file_name}`...\n[□□□□□□□□□□□□□□□□□□□□] 0.0%")

        if not status_message:
            self.active_downloads.remove(remote_file_unique_id)
            logger.error(f"Failed to send initial status message for chat {chat_id}.")
            return

        status_id = status_message.id
        start_time = time.time()

        download_dir = None
        try:
            download_dir = Path(f"downloads/{datetime.now().strftime('%Y%m%d_%H%M%S')}_{chat_id}")
            os.makedirs(download_dir, exist_ok=True)
            local_file_path = download_dir / file_name

            if not document.file_id:
                raise ValueError("Missing file ID for download.")

            progress_client_data = {
                'message': status_message,
                'file_name': file_name,
                'start_time': start_time,
                'explicit_total_size': file_size_from_tg
            }

            logger.info(f"Starting actual download for {file_name} (local path: {local_file_path})...")
            await client.download_media(
                document.file_id,
                file_name=str(local_file_path),
                progress=progress_callback_func,
                progress_args=(progress_client_data,) 
            )
            logger.info(f"✅ Downloaded {file_name} successfully in {time.time() - start_time:.2f}s")

            await self.edit_text(chat_id, status_id, f"📦 Unzipping `{file_name}`...")

            if file_name.lower().endswith(tuple([f".zip.{str(i).zfill(3)}" for i in range(1, 1000)])):
    # No extraction — just send as video
                await self.edit_text(chat_id, status_id, f"📤 Sending `{file_name}` as video...")
                try:
                    await self.send_streamable_video(chat_id, str(local_file_path), caption=f"`{file_name}`")
                    self.active_downloads.remove(remote_file_unique_id)
                    return
                except Exception as e:
                    logger.error(f"Failed to send {file_name} as video: {e}")
                    await self.edit_text(chat_id, status_id, f"❌ Failed to send `{file_name}` as video.")
                    return
            elif file_name.lower().endswith('.zip'):
                with zipfile.ZipFile(local_file_path, 'r') as z:
                    z.extractall(download_dir)

            elif file_name.lower().endswith('.7z'):
                with py7zr.SevenZipFile(local_file_path, 'r') as z:
                    z.extractall(path=download_dir)
            elif file_name.lower().endswith('.rar'):
                try:
                    with rarfile.RarFile(local_file_path, 'r') as z:
                        z.extractall(path=download_dir)
                except rarfile.RarCannotExec:
                    logger.error("RAR EXTRACTION FAILED: The 'unrar' command was not found on the server.")
                    raise RuntimeError("Server is not configured to handle RAR files (the 'unrar' command is missing). Please contact the bot administrator.")

            extracted_files = _recursively_list_files(download_dir)
            extracted_files = [f for f in extracted_files if f != local_file_path]

            if not extracted_files:
                await self.edit_text(chat_id, status_id, "🤔 Archive is empty or contains no extractable files.")
                shutil.rmtree(download_dir, ignore_errors=True)
                self.active_downloads.remove(remote_file_unique_id)
                return

            total_pages = (len(extracted_files) + FILES_PER_PAGE - 1) // FILES_PER_PAGE
            total_pages = max(1, total_pages)

            self.user_extraction_sessions[chat_id] = {
                'temp_dir': download_dir,
                'files': extracted_files,
                'selected_file_indices': set(),
                'main_message_id': status_id,
                'current_page': 0,
                'total_pages': total_pages
            }
            
            keyboard = self._generate_file_selection_keyboard(chat_id)
            await self.edit_text(chat_id, status_id, 
                                 f"📦 Extracted {len(extracted_files)} file(s) from `{file_name}`.\n"
                                 "Please select files to download (tap again to deselect):",
                                 reply_markup=keyboard)

        except Exception as e:
            logger.error(f"❌ Error processing {file_name} for chat {chat_id}: {e}", exc_info=True)
            error_message_to_user = f"❌ An error occurred during processing:\n`{str(e)}`"
            try:
                await self.edit_text(chat_id, status_id, error_message_to_user)
            except Exception:
                await self.send_text(chat_id, error_message_to_user)
            
            if download_dir and download_dir.exists():
                shutil.rmtree(download_dir, ignore_errors=True)
                logger.info(f"Cleaned up directory due to error: {download_dir}")
            
            if chat_id in self.user_extraction_sessions:
                del self.user_extraction_sessions[chat_id]
        finally:
            if remote_file_unique_id in self.active_downloads:
                self.active_downloads.remove(remote_file_unique_id)

    async def callback_query_handler(self, client: Client, callback_query: types.CallbackQuery):
        """Handles inline keyboard button presses for file selection."""
        chat_id = callback_query.message.chat.id
        message_id = callback_query.message.id
        data = callback_query.data

        await callback_query.answer() 

        if chat_id not in self.user_extraction_sessions:
            logger.warning(f"Callback from expired/non-existent session for chat {chat_id}, data: {data}")
            try:
                await self.edit_text(chat_id, message_id, "⚠️ Session expired or not found. Please send a new archive.")
            except Exception:
                await self.send_text(chat_id, "⚠️ Your previous session has expired. Please send a new archive.")
            return

        session_data = self.user_extraction_sessions[chat_id]
        files_to_upload = session_data['files']
        selected_indices = session_data['selected_file_indices']
        
        perform_cleanup = False

        try:
            if data.startswith("toggle_file_"):
                file_index = int(data.split('_')[2])
                if 0 <= file_index < len(files_to_upload):
                    if file_index in selected_indices:
                        selected_indices.remove(file_index)
                    else:
                        selected_indices.add(file_index)
                    
                    keyboard = self._generate_file_selection_keyboard(chat_id)
                    await self.edit_text(chat_id, message_id, 
                                         f"📦 Extracted {len(files_to_upload)} file(s).\n"
                                         "Please select files to download (tap again to deselect):",
                                         reply_markup=keyboard)
            
            elif data == "upload_selected_files":
                if not selected_indices:
                    await self.edit_text(chat_id, message_id, "⚠️ No files selected for upload. Please select some files or cancel.")
                    return

                await self.edit_text(chat_id, message_id, f"⬆️ Uploading {len(selected_indices)} selected file(s)...")
                files_uploaded_count = 0
                
                sorted_selected_indices = sorted(list(selected_indices))

                for index in sorted_selected_indices:
                    file_path = files_to_upload[index]
                    if file_path.is_file():
                        max_retries = 3
                        for attempt in range(max_retries):
                            try:
                                logger.info(f"Chat {chat_id}: Uploading file: {file_path.name} (Attempt {attempt + 1}/{max_retries})")
                                await self.send_streamable_video(chat_id, str(file_path), caption=f"`{file_path.name}`")
                                files_uploaded_count += 1
                                break
                            except ConnectionResetError as upload_e:
                                logger.warning(f"ConnectionResetError during upload of {file_path.name} (Attempt {attempt + 1}): {upload_e}")
                                if attempt < max_retries - 1:
                                    await asyncio.sleep(2 ** attempt)
                                    logger.info(f"Retrying upload for {file_path.name}...")
                                else:
                                    logger.error(f"Failed to upload {file_path.name} after {max_retries} attempts: {upload_e}", exc_info=True)
                                    await self.send_text(chat_id, f"❌ Failed to upload `{file_path.name}` after multiple retries.")
                            except FloodWait as flood_e:
                                logger.warning(f"FloodWait during upload of {file_path.name}: {flood_e.value} seconds. Waiting...")
                                await self.send_text(chat_id, f"⏳ Telegram flood wait. Waiting {flood_e.value} seconds before continuing upload for `{file_path.name}`...")
                                await asyncio.sleep(flood_e.value + 5)
                            except RPCError as rpc_e:
                                logger.error(f"RPCError during upload of {file_path.name}: {rpc_e}", exc_info=True)
                                await self.send_text(chat_id, f"❌ Telegram API error during upload of `{file_path.name}`: `{str(rpc_e)}`")
                                break
                            except Exception as upload_e:
                                logger.error(f"Unhandled error during upload of {file_path.name}: {upload_e}", exc_info=True)
                                await self.send_text(chat_id, f"❌ An unexpected error occurred during upload of `{file_path.name}`: `{str(upload_e)}`")
                                break
                
                await self.edit_text(chat_id, message_id, f"✅ Done! Uploaded {files_uploaded_count} selected file(s). Archive cleaned up.")
                perform_cleanup = True

            elif data == "prev_page":
                if session_data['current_page'] > 0:
                    session_data['current_page'] -= 1
                    keyboard = self._generate_file_selection_keyboard(chat_id)
                    await self.edit_text(chat_id, message_id, 
                                         f"📦 Extracted {len(files_to_upload)} file(s).\n"
                                         "Please select files to download (tap again to deselect):",
                                         reply_markup=keyboard)
            
            elif data == "next_page":
                if session_data['current_page'] < session_data['total_pages'] - 1:
                    session_data['current_page'] += 1
                    keyboard = self._generate_file_selection_keyboard(chat_id)
                    await self.edit_text(chat_id, message_id, 
                                         f"📦 Extracted {len(files_to_upload)} file(s).\n"
                                         "Please select files to download (tap again to deselect):",
                                         reply_markup=keyboard)
            
            elif data == "page_indicator_no_action":
                await callback_query.answer(f"Page {session_data['current_page'] + 1} of {session_data['total_pages']}. Use arrows to navigate.")
            
            elif data == "no_files_info":
                await callback_query.answer("No files were found in the archive to select.")

            elif data == "cancel_selection":
                logger.info(f"Chat {chat_id}: User cancelled file selection.")
                await self.edit_text(chat_id, message_id, "🚫 File selection cancelled. Archive cleaned up.")
                perform_cleanup = True
            
            elif data == "no_action":
                pass

            elif data == "session_expired":
                pass

            else:
                logger.warning(f"Unknown callback data received: {data} for chat {chat_id}")
                await self.edit_text(chat_id, message_id, "❌ Unknown action. Please try again or send a new archive.")

        except Exception as e:
            logger.error(f"Unhandled error in callback query handler for chat {chat_id}, data: {data}: {e}", exc_info=True)
            await self.edit_text(chat_id, message_id, f"❌ An unexpected error occurred: `{str(e)}`")
            perform_cleanup = True

        finally:
            if perform_cleanup:
                self._cleanup_session(chat_id)

    def _cleanup_session(self, chat_id: int):
        """Removes session data and temporary files for a given chat_id."""
        if chat_id in self.user_extraction_sessions:
            session_data = self.user_extraction_sessions.pop(chat_id)
            temp_dir = session_data.get('temp_dir')
            if temp_dir and temp_dir.exists():
                logger.info(f"Cleaning up directory for chat {chat_id}: {temp_dir}")
                shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info(f"Session for chat {chat_id} has been cleared.")

    async def send_text(self, chat_id: int, text: str, **kwargs):
        try:
            return await self.client.send_message(chat_id, text, **kwargs)
        except RPCError as e:
            logger.error(f"Failed to send message to {chat_id}: {e}")
        return None

    async def edit_text(self, chat_id: int, message_id: int, text: str, **kwargs):
        try:
            return await self.client.edit_message_text(chat_id, message_id, text, **kwargs)
        except RPCError as e:
            logger.warning(f"Failed to edit message {message_id} in {chat_id}: {e}")
        return None


    async def send_streamable_video(self, chat_id: int, video: str, **kwargs):
        try:
        # --- Generate thumbnail ---
            thumb_path = tempfile.mktemp(suffix=".jpg")
            try:
            # Extract a frame at 1 second
                (
                    ffmpeg
                    .input(video, ss=1)
                    .filter('scale', 1080, 720)
                    .output(thumb_path, vframes=1)
                    .overwrite_output()
                    .run(quiet=True)
                )
            except Exception as e:
                logger.warning(f"Thumbnail generation failed for {video}: {e}")
                thumb_path = None

        # --- Get duration in seconds ---
            duration_sec = None
            try:
                probe = ffmpeg.probe(video)
                duration_sec = int(float(probe['format']['duration']))
            except Exception as e:
                logger.warning(f"Duration check failed for {video}: {e}")

            return await self.client.send_video(
                chat_id,
                video,
                supports_streaming=True,
                duration=duration_sec,
                thumb=thumb_path if thumb_path else None,
                **kwargs
            )
        except RPCError as e:
            logger.error(f"Failed to send streamable video to {chat_id}: {e}")





    def register_handlers(self):
        """Registers all the necessary handlers with the client."""
        if not self.client:
            raise RuntimeError("Client not initialized. Call _get_client first.")
        
        # Separate handlers for better organization
        self.client.add_handler(
            MessageHandler(
                self.start_command_handler,
                filters=filters.command("start")
            )
        )
        self.client.add_handler(
            MessageHandler(
                self.document_handler,
                filters=filters.document
            )
        )
        self.client.add_handler(
            CallbackQueryHandler(self.callback_query_handler)
        )
        logger.info("Message and callback handlers have been registered.")
    async def run(self):
        """Initializes the client, registers handlers, and starts the bot."""
        self.client = await self._get_client()
        self.register_handlers()
        
        logger.info("Bot is starting up...")
        await self.client.start()
        
        user_info = await self.client.get_me()
        logger.info(f"Bot started as @{user_info.username} (ID: {user_info.id})")
        
        await asyncio.Event().wait()

        logger.info("Bot is shutting down...")
        await self.client.stop()

# --- Main execution block ---
async def main():
    bot = UltimateBot()
    await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped manually.")
    except Exception as e:
        logger.critical("💥 Critical startup error", exc_info=True)

