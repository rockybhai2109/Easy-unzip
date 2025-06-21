# ────────────────────────────────────────────────────────────────
# Health-check server — goes above everything
import os, threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_error(404)

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
if sys.stdout.encoding != 'utf-8':
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
if sys.stderr.encoding != 'utf-8':
    sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)

logging.basicConfig(
    level=logging.INFO, # Set to INFO for less verbose logs (DEBUG for more detail)
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"), # Log to a file, ensure UTF-8
        logging.StreamHandler(sys.stdout)         # Also log to console
    ]
)
logger = logging.getLogger(__name__)

# --- Configuration for file selection ---
FILES_PER_PAGE = 10 # Number of files to show per page for multi-selection


# --- Helper function for progress bar ---
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
    # This is the key change to ensure we use the known size if Pyrogram's 'total' is unreliable
    effective_total = client_data.get('explicit_total_size')
    if effective_total is None or effective_total <= 0:
        effective_total = total # Fallback to the 'total' passed by Pyrogram if our explicit one is bad

    elapsed_time = time.time() - start_time
    download_speed = current / elapsed_time if elapsed_time > 0 else 0

    status_text = ""
    percentage = 0

    # Now use effective_total for all calculations and display logic
    if effective_total is not None and effective_total > 0:
        percentage = current * 100 / effective_total
        bar = format_progress_bar(current, effective_total)
        speed_mbps = download_speed / (1024 * 1024) # Convert bytes/sec to MB/s
        
        status_text = (
            f"⬇️ Downloading `{file_name}`...\n"
            f"{bar}\n"
            f"Speed: `{speed_mbps:.2f} MB/s` | Progress: `{current / (1024*1024):.2f} / {effective_total / (1024*1024):.2f} MB`"
        )
        
        # Update criteria for known size: 
        # 1. First update when percentage > 0
        # 2. Every 5% change *and* at least 3 seconds passed
        # 3. At 100% completion
        should_update = (last_updated_percentage == 0 and percentage > 0) or \
                        (percentage - last_updated_percentage >= 5 and time.time() - last_update_time >= 3) or \
                        (percentage == 100)
    else: # Effective Total size is unknown or zero
        status_text = (
            f"⬇️ Downloading `{file_name}`...\n"
            f"Progress: `{current / (1024*1024):.2f} MB` / Unknown size\n"
            f"Speed: N/A"
        )
        # Update criteria for unknown size: every 5 seconds
        should_update = time.time() - last_update_time >= 5
    
    if should_update:
        try:
            await message_to_edit.edit_text(
                status_text,
                parse_mode=enums.ParseMode.MARKDOWN
            )
            client_data['last_update_time'] = time.time()
            # Store percentage based on effective_total for future updates
            client_data['last_updated_percentage'] = percentage if effective_total is not None and effective_total > 0 else 0
        except Exception as e:
            # Common errors: message not found (deleted), message too old to edit
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
        # Stores unique IDs of files currently being downloaded to prevent re-processing
        self.active_downloads: Set[str] = set()
        
        # Stores user's extracted files for multi-selection and pagination
        # Format: { chat_id: {'temp_dir': Path_obj, 'files': [Path_obj,...],
        #                    'selected_file_indices': Set[int], # Indices of files selected by user
        #                    'main_message_id': int, # The message containing the selection keyboard
        #                    'current_page': int,    # Current page being displayed
        #                    'total_pages': int}     # Total number of pages
        #         }
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
                sleep_threshold=30 # Increased sleep threshold for long operations
            )
        else:
            logger.info("Connecting without proxy...")
            self.client = Client(
                name=session_name,
                api_id=API_ID,
                api_hash=API_HASH,
                bot_token=BOT_TOKEN,
                sleep_threshold=30 # Increased sleep threshold for long operations
            )

        return self.client

    # Helper to generate the inline keyboard for file selection (multi-select with pagination)
    def _generate_file_selection_keyboard(self, chat_id: int) -> InlineKeyboardMarkup:
        """
        Generates the inline keyboard for file selection, including pagination and
        visual indication of selected files.
        """
        session_data = self.user_extraction_sessions.get(chat_id)
        if not session_data:
            logger.error(f"Attempted to generate keyboard for non-existent session: {chat_id}")
            # Provide a fallback keyboard if session data is unexpectedly missing
            return InlineKeyboardMarkup([[InlineKeyboardButton("Session Expired", callback_data="session_expired")]])

        all_files = session_data['files']
        selected_indices = session_data['selected_file_indices']
        current_page = session_data['current_page']
        temp_dir = session_data['temp_dir'] # Base directory for relative path calculation
        total_pages = session_data['total_pages']

        buttons = []
        
        # Calculate files to display on the current page
        start_index = current_page * FILES_PER_PAGE
        end_index = min(start_index + FILES_PER_PAGE, len(all_files))
        
        # Add individual file buttons for the current page
        if not all_files: # Handle case where archive was empty or only contained directories
            buttons.append([InlineKeyboardButton("No files found in archive.", callback_data="no_files_info")])
        else:
            for i in range(start_index, end_index):
                file_path = all_files[i]
                
                # Use relative path to make button text more readable for nested files
                try:
                    # Use os.path.relpath for cross-platform robustness if Path.relative_to struggles
                    relative_path_str = os.path.relpath(file_path, temp_dir)
                    # Convert back to Path object for consistency, then to string for display
                    display_path = Path(relative_path_str)
                except ValueError:
                    display_path = file_path.name # Fallback to just file name if not relative
                
                button_text = str(display_path)
                if len(button_text) > 50: # Truncate long names to fit button
                    button_text = "..." + button_text[-47:]
                
                # Add a checkmark if the file is currently selected
                if i in selected_indices:
                    button_text = f"✅ {button_text}"
                
                buttons.append([InlineKeyboardButton(button_text, callback_data=f"toggle_file_{i}")])

        # Add navigation buttons (Previous, Page Indicator, Next)
        navigation_row = []
        if current_page > 0:
            navigation_row.append(InlineKeyboardButton("⬅️ Previous", callback_data="prev_page"))
        
        # Page indicator button (no action, just shows current page)
        navigation_row.append(InlineKeyboardButton(f"{current_page + 1}/{total_pages}", callback_data="page_indicator_no_action"))
        
        if current_page < total_pages - 1:
            navigation_row.append(InlineKeyboardButton("Next ➡️", callback_data="next_page"))
        
        if navigation_row: # Only add if there are navigation buttons
            buttons.append(navigation_row)

        # Add action buttons (Upload Selected, Cancel)
        action_row = []
        # Only show 'Upload Selected' if at least one file is selected AND there are actual files
        if selected_indices and all_files: 
            action_row.append(InlineKeyboardButton(f"⬆️ Upload Selected ({len(selected_indices)})", callback_data="upload_selected_files"))
        action_row.append(InlineKeyboardButton("❌ Cancel", callback_data="cancel_selection"))
        buttons.append(action_row)

        return InlineKeyboardMarkup(buttons)


    async def new_message_handler(self, client: Client, message: types.Message):
        """Handles incoming messages."""
        if message.text and message.text.lower() == '/start':
            await self.send_text(message.chat.id, (
                "👋 Hello! Send me a `.zip` or `.7z` archive and I'll unzip it and let you choose which files to download."
            ))
            return

        if message.document:
            file_name = message.document.file_name
            remote_file_unique_id = message.document.file_unique_id
            file_size_from_tg = message.document.file_size # Get file size directly from message.document

            # DEBUG: Log the file details received from Telegram
            logger.info(f"Received document from chat {message.chat.id}: Name='{file_name}', Unique ID='{remote_file_unique_id}', Size='{file_size_from_tg}' bytes")

            if not remote_file_unique_id or not file_name:
                logger.warning(f"Received document with no file_unique_id or file_name in chat {message.chat.id}")
                await self.send_text(message.chat.id, "❌ Unable to process: File details missing.")
                return

            if not (file_name.lower().endswith(('.zip', '.7z'))):
                return await self.send_text(message.chat.id, "❌ Only `.zip` and `.7z` files are supported.")

            if remote_file_unique_id in self.active_downloads:
                return await self.send_text(message.chat.id, "⏳ Already processing this file. Please wait.")
            
            # Check if there's an ongoing extraction session for this user
            if message.chat.id in self.user_extraction_sessions:
                # If a user sends a new archive while one is already active, prompt to cancel old one
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Yes, cancel current", callback_data="cancel_selection")],
                    [InlineKeyboardButton("No, continue current", callback_data="no_action")] # Keeps current session active
                ])
                await self.send_text(
                    message.chat.id, 
                    "⚠️ You already have an active extraction session. Do you want to cancel it and start a new one?",
                    reply_markup=reply_markup
                )
                return

            self.active_downloads.add(remote_file_unique_id)
            # Process the file in a background task to not block the main event loop
            asyncio.create_task(self.process_file(message.chat.id, message.document, remote_file_unique_id, client))

    async def process_file(self, chat_id: int, document: types.Document, remote_file_unique_id: str, client: Client):
        """
        Handles the download, extraction, and presentation of files for selection.
        """
        file_name = document.file_name
        file_size_from_tg = document.file_size # Get file size directly from message.document
        
        status_message = await self.send_text(chat_id, f"⬇️ Downloading `{file_name}`...\n[□□□□□□□□□□□□□□□□□□□□] 0.0%")

        if not status_message:
            # If initial message sending fails, ensure we remove from active downloads
            self.active_downloads.remove(remote_file_unique_id)
            logger.error(f"Failed to send initial status message for chat {chat_id}.")
            return

        status_id = status_message.id
        start_time = time.time()

        download_dir = None
        try:
            # Create a unique directory for each download
            download_dir = Path(f"downloads/{datetime.now().strftime('%Y%m%d_%H%M%S')}_{chat_id}")
            os.makedirs(download_dir, exist_ok=True)
            local_file_path = download_dir / file_name

            if not document.file_id:
                raise ValueError("Missing file ID for download.")

            progress_client_data = {
                'message': status_message,
                'file_name': file_name,
                'start_time': start_time,
                'explicit_total_size': file_size_from_tg # <--- IMPORTANT: Pass the known size here
            }

            logger.info(f"Starting actual download for {file_name} (local path: {local_file_path})...")
            # Download the archive. Pyrogram will automatically pass document.file_size as 'total' to progress_callback_func.
            await client.download_media(
                document.file_id,
                file_name=local_file_path,
                progress=progress_callback_func,
                progress_args=(progress_client_data,) 
            )
            logger.info(f"✅ Downloaded {file_name} successfully in {time.time() - start_time:.2f}s")

            await self.edit_text(chat_id, status_id, f"📦 Unzipping `{file_name}`...")

            # Extract the archive based on its extension
            if file_name.lower().endswith('.zip'):
                with zipfile.ZipFile(local_file_path, 'r') as z:
                    z.extractall(download_dir)
            elif file_name.lower().endswith('.7z'):
                with py7zr.SevenZipFile(local_file_path, 'r') as z:
                    z.extractall(path=download_dir)

            # Get a flattened list of all extracted files (recursive)
            extracted_files = _recursively_list_files(download_dir)
            # Ensure the original archive file itself is not included in the selectable list
            extracted_files = [f for f in extracted_files if f != local_file_path]

            if not extracted_files:
                await self.edit_text(chat_id, status_id, "🤔 Archive is empty or contains no extractable files.")
                shutil.rmtree(download_dir, ignore_errors=True) # Clean up empty dir immediately
                self.active_downloads.remove(remote_file_unique_id) # Ensure this is also removed
                return

            # Calculate total pages needed for pagination
            total_pages = (len(extracted_files) + FILES_PER_PAGE - 1) // FILES_PER_PAGE
            total_pages = max(1, total_pages) # Ensure at least 1 page even if very few files

            # Store the session data for the user
            self.user_extraction_sessions[chat_id] = {
                'temp_dir': download_dir,
                'files': extracted_files,
                'selected_file_indices': set(), # Initialize empty set for selected files
                'main_message_id': status_id,
                'current_page': 0, # Start on the first page
                'total_pages': total_pages
            }
            
            # Generate and send the initial selection keyboard for the first page
            keyboard = self._generate_file_selection_keyboard(chat_id)
            await self.edit_text(chat_id, status_id, 
                                 f"📦 Extracted {len(extracted_files)} file(s) from `{file_name}`.\n"
                                 "Please select files to download (tap again to deselect):",
                                 reply_markup=keyboard)

        except Exception as e:
            logger.error(f"❌ Error processing {file_name} for chat {chat_id}: {e}", exc_info=True)
            error_message_to_user = f"❌ An error occurred during processing:\n`{str(e)}`"
            # If the original status message still exists, try to update it. Otherwise, send a new message.
            try:
                await self.edit_text(chat_id, status_id, error_message_to_user)
            except Exception: # Message might have been deleted/too old to edit
                await self.send_text(chat_id, error_message_to_user)
            
            # Ensure cleanup if an error occurs
            if download_dir and download_dir.exists():
                shutil.rmtree(download_dir, ignore_errors=True)
                logger.info(f"Cleaned up directory due to error: {download_dir}")
            # Also clean up session data if error occurred after session was initiated
            if chat_id in self.user_extraction_sessions:
                del self.user_extraction_sessions[chat_id]
        finally:
            # Always remove the file from active downloads, whether success or fail
            if remote_file_unique_id in self.active_downloads:
                self.active_downloads.remove(remote_file_unique_id)


    async def callback_query_handler(self, client: Client, callback_query: types.CallbackQuery):
        """Handles inline keyboard button presses for file selection."""
        chat_id = callback_query.message.chat.id
        message_id = callback_query.message.id
        data = callback_query.data

        # Acknowledge the callback query immediately to remove loading state
        await callback_query.answer() 

        # Ensure there's an active session for this chat_id
        if chat_id not in self.user_extraction_sessions:
            logger.warning(f"Callback from expired/non-existent session for chat {chat_id}, data: {data}")
            # Attempt to edit the message to indicate session expiry, if still possible
            try:
                await self.edit_text(chat_id, message_id, "⚠️ Session expired or not found. Please send a new archive.")
            except Exception: # Message might have been deleted/too old to edit
                await self.send_text(chat_id, "⚠️ Your previous session has expired. Please send a new archive.")
            return

        session_data = self.user_extraction_sessions[chat_id]
        temp_dir = session_data['temp_dir']
        files_to_upload = session_data['files']
        selected_indices = session_data['selected_file_indices']
        
        # Flag to determine if cleanup should happen after this action
        perform_cleanup = False

        try:
            if data.startswith("toggle_file_"):
                file_index = int(data.split('_')[2])
                logger.info(f"Chat {chat_id}: Toggling file index {file_index}")
                if 0 <= file_index < len(files_to_upload):
                    if file_index in selected_indices:
                        selected_indices.remove(file_index) # Deselect
                        logger.debug(f"Chat {chat_id}: Deselected file {file_index}. Current selections: {sorted(list(selected_indices))}")
                    else:
                        selected_indices.add(file_index) # Select
                        logger.debug(f"Chat {chat_id}: Selected file {file_index}. Current selections: {sorted(list(selected_indices))}")
                    
                    # Re-edit the message with the updated keyboard to reflect new selection (checkmark and count)
                    keyboard = self._generate_file_selection_keyboard(chat_id)
                    await self.edit_text(chat_id, message_id, 
                                         f"📦 Extracted {len(files_to_upload)} file(s).\n"
                                         "Please select files to download (tap again to deselect):",
                                         reply_markup=keyboard)
                else:
                    logger.warning(f"Invalid file index received: {file_index} for chat {chat_id} (outside bounds)")
                    await self.edit_text(chat_id, message_id, "❌ Invalid file selection. Please try again.")
            
            elif data == "upload_selected_files":
                if not selected_indices:
                    logger.info(f"Chat {chat_id}: User tried to upload with no files selected.")
                    await self.edit_text(chat_id, message_id, "⚠️ No files selected for upload. Please select some files or cancel.")
                    return # Do not cleanup, allow user to select more

                logger.info(f"Chat {chat_id}: User chose to upload {len(selected_indices)} files.")
                await self.edit_text(chat_id, message_id, f"⬆️ Uploading {len(selected_indices)} selected file(s)...")
                files_uploaded_count = 0
                
                # Sort indices to upload files in their original order (optional, but nice)
                sorted_selected_indices = sorted(list(selected_indices))

                for index in sorted_selected_indices:
                    file_path = files_to_upload[index]
                    if file_path.is_file():
                        # Retry logic for file uploads
                        max_retries = 3
                        for attempt in range(max_retries):
                            try:
                                logger.info(f"Chat {chat_id}: Uploading file: {file_path.name} (Attempt {attempt + 1}/{max_retries})")
                                await self.send_document(chat_id, str(file_path), caption=f"`{file_path.name}`")
                                files_uploaded_count += 1
                                break # Success, break out of retry loop
                            except ConnectionResetError as upload_e: # Catch the standard ConnectionResetError
                                logger.warning(f"ConnectionResetError during upload of {file_path.name} (Attempt {attempt + 1}): {upload_e}")
                                if attempt < max_retries - 1:
                                    await asyncio.sleep(2 ** attempt) # Exponential backoff: 1s, 2s, 4s
                                    logger.info(f"Retrying upload for {file_path.name}...")
                                else:
                                    logger.error(f"Failed to upload {file_path.name} after {max_retries} attempts: {upload_e}", exc_info=True)
                                    await self.send_text(chat_id, f"❌ Failed to upload `{file_path.name}` after multiple retries.")
                            except FloodWait as flood_e:
                                logger.warning(f"FloodWait during upload of {file_path.name}: {flood_e.value} seconds. Waiting...")
                                await self.send_text(chat_id, f"⏳ Telegram flood wait. Waiting {flood_e.value} seconds before continuing upload for `{file_path.name}`...")
                                await asyncio.sleep(flood_e.value + 5) # Wait a bit longer than required
                            except RPCError as rpc_e:
                                logger.error(f"RPCError during upload of {file_path.name}: {rpc_e}", exc_info=True)
                                await self.send_text(chat_id, f"❌ Telegram API error during upload of `{file_path.name}`: `{str(rpc_e)}`")
                                break # Don't retry for RPC errors, they are usually not transient
                            except Exception as upload_e:
                                logger.error(f"Unhandled error during upload of {file_path.name}: {upload_e}", exc_info=True)
                                await self.send_text(chat_id, f"❌ An unexpected error occurred during upload of `{file_path.name}`: `{str(upload_e)}`")
                                break # Don't retry for unknown errors

                    else:
                        logger.warning(f"Attempted to upload non-file path (might be a directory): {file_path} for chat {chat_id}")
                
                await self.edit_text(chat_id, message_id, f"✅ Done! Uploaded {files_uploaded_count} selected file(s). Archive cleaned up.")
                perform_cleanup = True # Mark for cleanup

            elif data == "prev_page":
                if session_data['current_page'] > 0:
                    session_data['current_page'] -= 1
                    logger.info(f"Chat {chat_id}: Navigating to previous page {session_data['current_page'] + 1}")
                    keyboard = self._generate_file_selection_keyboard(chat_id)
                    await self.edit_text(chat_id, message_id, 
                                         f"📦 Extracted {len(files_to_upload)} file(s).\n"
                                         "Please select files to download (tap again to deselect):",
                                         reply_markup=keyboard)
                else:
                    logger.debug(f"Chat {chat_id}: Already on first page, cannot go back.")
                    # Pyrogram's callback_query.answer() implicitly handles showing "Nothing happened" if no toast is sent.
            
            elif data == "next_page":
                if session_data['current_page'] < session_data['total_pages'] - 1:
                    session_data['current_page'] += 1
                    logger.info(f"Chat {chat_id}: Navigating to next page {session_data['current_page'] + 1}")
                    keyboard = self._generate_file_selection_keyboard(chat_id)
                    await self.edit_text(chat_id, message_id, 
                                         f"📦 Extracted {len(files_to_upload)} file(s).\n"
                                         "Please select files to download (tap again to deselect):",
                                         reply_markup=keyboard)
                else:
                    logger.debug(f"Chat {chat_id}: Already on last page, cannot go next.")
                    # Pyrogram's callback_query.answer() implicitly handles showing "Nothing happened" if no toast is sent.
            
            elif data == "page_indicator_no_action":
                # User tapped the page number itself, just provide info.
                await callback_query.answer(f"Page {session_data['current_page'] + 1} of {session_data['total_pages']}. Use arrows to navigate.")
            
            elif data == "no_files_info":
                await callback_query.answer("No files were found in the archive to select.")

            elif data == "cancel_selection":
                logger.info(f"Chat {chat_id}: User cancelled file selection.")
                await self.edit_text(chat_id, message_id, "🚫 File selection cancelled. Archive cleaned up.")
                perform_cleanup = True # Mark for cleanup
            
            elif data == "no_action": # From the "cancel current session?" prompt
                logger.info(f"Chat {chat_id}: User chose to continue current session.")
                # No state change needed, just acknowledge with the initial callback_query.answer()
                pass # Explicitly do nothing else here

            elif data == "session_expired":
                logger.debug(f"Chat {chat_id}: Received session_expired callback. Already handled.")
                pass # Already handled at the start of the function

            else:
                logger.warning(f"Unknown callback data received: {data} for chat {chat_id}")
                await self.edit_text(chat_id, message_id, "❌ Unknown action. Please try again or send a new archive.")
                # No cleanup for unknown action, allow user to retry if it was a valid session

        except Exception as e:
            logger.error(f"Unhandled error in callback query handler for chat {chat_id}, data: {data}: {e}", exc_info=True)
            await self.edit_text(chat_id, message_id, f"❌ An unexpected error occurred: `{str(e)}`")
            perform_cleanup = True # Clean up on any unhandled error

        finally:
            # Perform cleanup if the action warrants it
            if perform_cleanup:
                logger.info(f"Cleaning up session data and directory for chat {chat_id}")
                if temp_dir.exists():
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    logger.debug(f"Cleaned up directory: {temp_dir}")
                if chat_id in self.user_extraction_sessions:
                    del self.user_extraction_sessions[chat_id]


    async def send_text(self, chat_id: int, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None) -> Optional[types.Message]:
        """Sends a text message to a given chat ID."""
        try:
            return await self.client.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=enums.ParseMode.MARKDOWN,
                reply_markup=reply_markup,
                disable_web_page_preview=True # Prevent link previews for cleaner messages
            )
        except Exception as e:
            logger.error(f"Failed to send text message to {chat_id}: {e}")
            return None

    async def edit_text(self, chat_id: int, message_id: int, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
        """Edits an existing text message."""
        try:
            await self.client.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=enums.ParseMode.MARKDOWN,
                reply_markup=reply_markup,
                disable_web_page_preview=True # Prevent link previews for cleaner messages
            )
        except Exception as e:
            # This can happen if the message is too old, user has already edited/deleted it, or Telegram API limits
            logger.warning(f"Failed to edit message {message_id} in chat {chat_id}: {e}")

    async def send_document(self, chat_id: int, file_path: str, caption: str):
        """Sends a document (file) to a given chat ID."""
        try:
            await self.client.send_document(
                chat_id=chat_id,
                document=file_path,
                caption=caption,
                parse_mode=enums.ParseMode.MARKDOWN,
                disable_notification=True # Send silently
            )
        except Exception as e:
            # This specific exception is caught by the retry loop in callback_query_handler
            # This 'send_document' function itself will only raise it for the retry logic to catch
            # Or if called directly outside the retry loop, it will just log here.
            logger.error(f"send_document failed for {file_path} to {chat_id}: {e}", exc_info=True)
            raise # Re-raise for the retry loop to catch

    async def run(self):
        """Starts and runs the bot."""
        self.client = await self._get_client()

        # Register handlers for different types of updates
        self.client.add_handler(MessageHandler(self.new_message_handler, filters.private & (filters.document | filters.command("start"))))
        self.client.add_handler(CallbackQueryHandler(self.callback_query_handler)) # Handles inline button presses

        logger.info("🚀 Logging in...")
        await self.client.start()
        me = await self.client.get_me()
        logger.info(f"🤖 Bot ready: @{me.username} (ID: {me.id})")

        try:
            # Keep the bot running indefinitely
            await asyncio.Future() 
        except asyncio.CancelledError:
            logger.info("Bot shutting down due to cancellation.")
        except Exception as e:
            logger.critical(f"An unhandled error occurred in the main bot loop, shutting down: {e}", exc_info=True)
        finally:
            if self.client and self.client.is_connected:
                logger.info("Disconnecting bot client...")
                await self.client.stop()
                logger.info("Bot disconnected.")
            # Ensure any remaining temporary directories are cleaned up on graceful shutdown
            if self.user_extraction_sessions:
                logger.info(f"Cleaning up {len(self.user_extraction_sessions)} remaining sessions during shutdown.")
                for chat_id, session_data in list(self.user_extraction_sessions.items()):
                    temp_dir = session_data.get('temp_dir')
                    if temp_dir and temp_dir.exists():
                        shutil.rmtree(temp_dir, ignore_errors=True)
                        logger.info(f"Cleaned up remaining directory for chat {chat_id} during shutdown: {temp_dir}")
                    del self.user_extraction_sessions[chat_id]
            logger.info("Bot shutdown complete.")


if __name__ == "__main__":
    # --- Cleanup old 'downloads' directory before starting, but preserve session ---
    logger.info("Performing pre-start cleanup: Deleting old downloads directory.")
    
    downloads_path = Path("downloads")
    if downloads_path.exists():
        try:
            shutil.rmtree(downloads_path)
            logger.info(f"Successfully deleted old downloads directory: {downloads_path}")
        except OSError as e:
            logger.error(f"Error deleting downloads directory {downloads_path}: {e}")
    # --- End Cleanup ---

    # Ensure the downloads directory exists for the new session
    # This will create it freshly if it was just deleted or didn't exist
    downloads_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Ensured fresh downloads directory: {downloads_path}")
    
    bot = UltimateBot()
    asyncio.run(bot.run())