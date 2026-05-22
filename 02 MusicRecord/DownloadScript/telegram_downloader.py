import asyncio
import re
import os
import random
from telethon import TelegramClient, events

# --- CONFIGURATION ---
API_ID = 32477142        
API_HASH = '430f46a0a677da5820ab317a54a0375a'  
BOT_USERNAME = 'SQMP3'   
FILE_NAME = 'NewDownload.txt'
LOG_FILE = 'download_summary.log'

# --- DOWNLOAD DIRECTORY ---
DOWNLOAD_DIR = os.path.join(os.getcwd(), 'TMDownload')

# --- ANTI-FLOOD TIMEOUTS ---
MIN_DELAY = 60          
MAX_DELAY = 150         
RESPONSE_TIMEOUT = 120  

# Regular expression to match "Artist - Song" format
SONG_REGEX = re.compile(r'^\s*\*?\s*(.+?\s*-\s*.+?)\s*$')

def ensure_download_dir():
    """Creates TMDownload directory if it does not exist."""
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)
        print(f"[SYSTEM] Created download directory: {DOWNLOAD_DIR}")
    else:
        print(f"[SYSTEM] Download directory already exists: {DOWNLOAD_DIR}")

def get_downloaded_keywords():
    """Scans the download directory to extract keywords of already downloaded songs."""
    if not os.path.exists(DOWNLOAD_DIR):
        return set()
    downloaded_files = os.listdir(DOWNLOAD_DIR)
    keywords = set()
    for file in downloaded_files:
        name_without_ext = os.path.splitext(file)[0].lower()
        clean_name = re.sub(r'[^a-zA-Z0-9\s\u4e00-\u9fa5]', '', name_without_ext)
        if clean_name.strip():
            keywords.add(clean_name.strip())
    return keywords

def is_already_downloaded(song_info, downloaded_keywords):
    """Checks if the song already exists in the local directory."""
    clean_song = re.sub(r'[^a-zA-Z0-9\s\u4e00-\u9fa5]', '', song_info.lower()).strip()
    if clean_song in downloaded_keywords:
        return True
    for kw in downloaded_keywords:
        if kw in clean_song or clean_song in kw:
            return True
    return False

def parse_songs(file_path):
    """Reads the text file and extracts valid song rows cleanly."""
    songs = []
    if not os.path.exists(file_path):
        print(f"[ERROR] File not found: '{file_path}'")
        return songs

    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            cleaned_line = line.strip()
            if not cleaned_line or cleaned_line.startswith('#'):
                continue
            match = SONG_REGEX.match(cleaned_line)
            if match:
                songs.append(match.group(1).strip())
    return songs

def write_log(status, song_name, details=""):
    """Writes download status to a local log file."""
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"[{status}] {song_name} {details}\n")

async def main():
    ensure_download_dir()
    downloaded_keywords = get_downloaded_keywords()
    
    if downloaded_keywords:
        print(f"[SYSTEM] Detected {len(downloaded_keywords)} existing tracks in local folder.")

    songs = parse_songs(FILE_NAME)
    total_songs = len(songs)
    print(f"[SYSTEM] Parsed {total_songs} songs from {FILE_NAME}.")
    
    if total_songs == 0:
        print("[INFO] No valid songs found. Exiting.")
        return

    # Initialize Telethon Client
    async with TelegramClient('music_session', API_ID, API_HASH) as client:
        print("======== Connected to Telegram successfully ========")
        print("[INFO] Starting two-way request and download loop...\n")

        for index, song in enumerate(songs, start=1):
            # 1. Check if already downloaded
            if is_already_downloaded(song, downloaded_keywords):
                print(f"[{index}/{total_songs}] SKIPPED (Already Exists): {song}")
                write_log("SKIPPED", song, "(File already exists in TMDownload)")
                continue

            print(f"[{index}/{total_songs}] Requesting: {song}")
            
            # Create a Future object to handle the async response from the group
            loop = asyncio.get_running_loop()
            download_future = loop.create_future()

            # 2. Define an inline event handler to intercept the bot's media response in the group
            @client.on(events.NewMessage(chats=BOT_USERNAME))
            async def handle_new_message(event):
                if event.message.media and (event.message.audio or event.message.document):
                    song_pieces = [p for p in re.split(r'[-_\s]', song.lower()) if len(p) > 1]
                    msg_text = (event.message.message or "").lower()
                    file_name_attr = getattr(event.message.media, 'document', None)
                    file_name_str = ""
                    
                    if file_name_attr:
                        for attr in file_name_attr.attributes:
                            if hasattr(attr, 'file_name'):
                                file_name_str = attr.file_name.lower()

                    if any(piece in msg_text or piece in file_name_str for piece in song_pieces) or not song_pieces:
                        if not download_future.done():
                            download_future.set_result(event.message)

            # 3. Send the command to the group
            command = f"/music {song}"
            try:
                await client.send_message(BOT_USERNAME, command)
            except Exception as e:
                print(f" └─ [ERROR] Failed to send command: {e}")
                write_log("FAILED", song, f"(Command send error: {e})")
                client.remove_event_handler(handle_new_message)
                await asyncio.sleep(10)
                continue

            # 4. Wait for the bot to output the file within the timeout window
            try:
                print(" └─ Waiting for bot response...")
                matched_message = await asyncio.wait_for(download_future, timeout=RESPONSE_TIMEOUT)
                
                # Bot responded! Start downloading the file to disk
                print(" └─ Bot file detected! Downloading to TMDownload...")
                
                # --- FIXED HERE: Changed 'music_dir' to 'file' to resolve Telethon API mismatch ---
                path = await client.download_media(matched_message, file=DOWNLOAD_DIR)
                
                actual_filename = os.path.basename(path) if path else "Unknown File"
                print(f" └─ [SUCCESS] Saved as: {actual_filename}")
                write_log("SUCCESS", song, f"(Saved as: {actual_filename})")
                
                # Refresh downloaded keywords to dynamically catch duplicates later
                downloaded_keywords.add(re.sub(r'[^a-zA-Z0-9\s\u4e00-\u9fa5]', '', song.lower()).strip())

            except asyncio.TimeoutError:
                print(f" └─ [TIMEOUT] Bot did not respond with a file within {RESPONSE_TIMEOUT}s.")
                write_log("FAILED", song, "(Timeout: Bot did not respond with media)")
            except Exception as e:
                print(f" └─ [ERROR] Download process failed: {e}")
                write_log("FAILED", song, f"(Download error: {e})")
            finally:
                # Always remove the handler before moving to the next song
                client.remove_event_handler(handle_new_message)

            # 5. Long anti-flood safe delay before moving to the next song
            current_delay = random.randint(MIN_DELAY, MAX_DELAY)
            print(f" └─ Cooling down for {current_delay} seconds...\n")
            await asyncio.sleep(current_delay)

if __name__ == '__main__':
    asyncio.run(main())