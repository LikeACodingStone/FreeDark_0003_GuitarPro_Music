import argparse
import asyncio
import getpass
import os
import random
import re
import shutil
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import ApiIdInvalidError, SessionPasswordNeededError


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == "DownloadScript" else Path.cwd()

DEFAULT_PLAYLIST_FILE = PROJECT_ROOT / "2026-07-24_一部只有金属乐和乡村布鲁斯的听歌机器.txt"
DEFAULT_EXISTING_LIST_FILE = PROJECT_ROOT / "halp_path_list_1779699672.txt"
DEFAULT_PENDING_FILE = PROJECT_ROOT / "DownloadScript" / "NewDownload.txt"
DEFAULT_FAILED_FILE = PROJECT_ROOT / "DownloadScript" / "FailedDownload.txt"
DEFAULT_REQUEST_FAILED_FILE = PROJECT_ROOT / "DownloadScript" / "RequestFailedDownload.txt"
DEFAULT_ENGINE_STATE_FILE = PROJECT_ROOT / "DownloadScript" / "LastDownloadEngine.txt"
DEFAULT_BLOCKED_FILE = PROJECT_ROOT / "DownloadScript" / "SendBlockedUntil.txt"
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "config.ini"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

AUDIO_EXTENSIONS = {
    ".aac",
    ".aiff",
    ".ape",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}

MUSIC_HUNTERS_CLICK_ATTEMPTS = 3
MUSIC_HUNTERS_INITIAL_CLICK_DELAY = (3.0, 5.0)
MUSIC_HUNTERS_MEDIA_GRACE_SECONDS = 8.0
MUSIC_HUNTERS_RETRY_DELAY = (4.0, 7.0)

REQUEST_FAILURE_TEXT_PATTERNS = (
    "no results found",
    "no matching track result",
    "no matching musicn result",
    "did not acknowledge the numbered button",
    "timeout waiting for media",
    "timed out waiting for media",
    "telegram returned an empty download path",
    "downloaded media did not match request",
    "downloaded path does not exist",
    "duplicate filename already exists",
)

SAFETY_STOP_TEXT_PATTERNS = (
    "a wait of",
    "abuse",
    "automated",
    "automation",
    "banned from sending messages",
    "captcha",
    "flood",
    "internal server error",
    "limit",
    "peer flood",
    "rate limit",
    "restricted",
    "rpc call fail",
    "rpc_call_fail",
    "server error",
    "slow mode",
    "spam",
    "suspicious",
    "too many requests",
    "try again later",
    "wait before",
    "write forbidden",
    "you can't write in this chat",
)

SAFETY_STOP_CLASS_PATTERNS = (
    "AuthKey",
    "ChatWriteForbidden",
    "Flood",
    "InternalServer",
    "PeerFlood",
    "PhoneNumberBanned",
    "RpcCallFail",
    "ServerError",
    "SlowMode",
    "UserBanned",
    "UserRestricted",
)

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "i",
    "if",
    "in",
    "is",
    "it",
    "its",
    "me",
    "my",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "you",
    "your",
}


@dataclass(frozen=True)
class BotPreset:
    name: str
    username: str
    strategy: str


BOT_PRESETS = {
    "sqmp3": BotPreset("SQMP3", "t.me/SQMP3", "sqmp3"),
    "deezermusicbot": BotPreset("DeezerMusicBot", "@DeezerMusicBot", "deezer"),
    "musicshuntersbot": BotPreset("MusicsHuntersbot", "@MusicsHuntersbot", "music_hunters"),
}


class TrackRequestError(RuntimeError):
    """A single track could not be requested or delivered, but the run can continue."""


class SafetyStopError(RuntimeError):
    """Telegram or the bot returned a response that should stop automation quickly."""


@dataclass(frozen=True)
class AppConfig:
    download_platform: str
    download_bot: str
    download_channel: str
    reset_account: bool
    playlist_id: str
    playlist_file: Path
    existing_list_file: Path


@dataclass(frozen=True)
class Track:
    artist: str
    title: str
    source: str = ""
    line_no: int = 0

    @property
    def query(self) -> str:
        return f"{self.artist} - {self.title}"

    @property
    def key(self) -> tuple[str, str]:
        return normalize_piece(self.artist), normalize_piece(self.title)


@dataclass(frozen=True)
class Config:
    api_id: int | None
    api_hash: str | None
    phone: str | None
    bot_name: str
    bot_strategy: str
    bot_username: str
    playlist_file: Path
    existing_list_file: Path
    pending_file: Path
    failed_file: Path
    request_failed_file: Path
    engine_state_file: Path
    blocked_file: Path
    log_file: Path
    download_dir: Path
    incoming_dir: Path
    rejected_dir: Path
    session_dir: Path
    session_name: str
    session_ttl_hours: int
    min_delay: int
    max_delay: int
    response_timeout: int
    long_rest_every_minutes: int
    long_rest_min_minutes: int
    long_rest_max_minutes: int
    stop_on_send_blocked: bool
    send_blocked_cooldown_hours: int
    success_confirm_every: int


def load_dotenv() -> None:
    """Load simple KEY=VALUE entries without adding a runtime dependency."""
    paths = []
    for candidate in (Path.cwd() / ".env", PROJECT_ROOT / ".env", SCRIPT_DIR / ".env"):
        if candidate not in paths:
            paths.append(candidate)

    for path in paths:
        if not path.is_file():
            continue

        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def env_value(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.getenv(name, default)
    if value is not None:
        value = value.strip().strip('"').strip("'")
    if required and not value:
        raise SystemExit(f"[CONFIG] Missing required setting: {name}")
    return value


def env_int(name: str, default: int | None = None, required: bool = False) -> int:
    raw = env_value(name, str(default) if default is not None else None, required=required)
    if raw is None:
        raise SystemExit(f"[CONFIG] Missing required setting: {name}")
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"[CONFIG] {name} must be an integer.") from exc


def env_bool(name: str, default: bool = False) -> bool:
    raw = env_value(name)
    if raw is None or raw == "":
        return default
    return parse_bool(raw, default=default)


def parse_bool(value: str, default: bool = False) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def normalized_text(value: str) -> str:
    return " ".join(value.lower().split())


def text_matches_any(value: str, patterns: tuple[str, ...]) -> bool:
    text = normalized_text(value)
    return any(pattern in text for pattern in patterns)


def text_indicates_request_failure(value: str) -> bool:
    return text_matches_any(value, REQUEST_FAILURE_TEXT_PATTERNS)


def text_indicates_safety_stop(value: str) -> bool:
    return text_matches_any(value, SAFETY_STOP_TEXT_PATTERNS)


def exception_text_chain(exc: BaseException) -> str:
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(type(current).__name__)
        message = str(current).strip()
        if message:
            parts.append(message)
        current = current.__cause__ or current.__context__
    return " | ".join(parts)


def exception_class_indicates_safety_stop(exc: BaseException) -> bool:
    names: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        names.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    return any(pattern in name for name in names for pattern in SAFETY_STOP_CLASS_PATTERNS)


def normalize_bot_choice(value: str) -> str:
    username_match = re.match(
        r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/([^/?#]+)",
        value.strip(),
        flags=re.IGNORECASE,
    )
    candidate = username_match.group(1) if username_match else value
    return re.sub(r"[^a-z0-9]", "", candidate.lower())


def resolve_bot_preset(value: str) -> BotPreset:
    preset = BOT_PRESETS.get(normalize_bot_choice(value))
    if preset is None:
        choices = ", ".join(item.name for item in BOT_PRESETS.values())
        raise SystemExit(f"[CONFIG] DownloadBot must be one of: {choices}.")
    return preset


def resolve_bot_selection(app_config: AppConfig) -> BotPreset:
    if app_config.download_bot:
        return resolve_bot_preset(app_config.download_bot)

    configured_channel = app_config.download_channel or env_value("TG_BOT_USERNAME", "SQMP3") or "SQMP3"
    normalized_channel = normalize_bot_choice(configured_channel)
    return BOT_PRESETS.get(
        normalized_channel,
        BotPreset("Custom", configured_channel, "sqmp3"),
    )


def resolve_path(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def resolve_config_path(raw: str | Path, config_path: Path) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return (config_path.parent / path).resolve()


def load_app_config(path: Path | None = None) -> tuple[AppConfig, Path]:
    config_path = resolve_path(path or os.getenv("CONFIG_FILE", str(DEFAULT_CONFIG_FILE)))
    default_playlist_file = config_path.parent / DEFAULT_PLAYLIST_FILE.name
    default_existing_list_file = config_path.parent / DEFAULT_EXISTING_LIST_FILE.name
    if not config_path.exists():
        write_app_config(
            config_path,
            AppConfig(
                download_platform="Telegram",
                download_bot="SQMP3",
                download_channel="t.me/SQMP3",
                reset_account=False,
                playlist_id="17961590701",
                playlist_file=default_playlist_file,
                existing_list_file=default_existing_list_file,
            ),
        )

    values = {
        "DownloadPlatform": "Telegram",
        "DownloadBot": "",
        "DownloadChannel": "",
        "ResetAccount": "False",
        "PlaylistId": "17961590701",
        "PlaylistFile": str(default_playlist_file),
        "ExistingListFile": str(default_existing_list_file),
    }
    for raw_line in config_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("[") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if key in values:
            values[key] = value.strip()

    platform = values["DownloadPlatform"].strip()
    normalized_platform = platform.lower()
    if normalized_platform == "telegram":
        platform = "Telegram"
    else:
        raise SystemExit("[CONFIG] DownloadPlatform must be Telegram.")

    return (
        AppConfig(
            download_platform=platform,
            download_bot=values["DownloadBot"].strip(),
            download_channel=values["DownloadChannel"].strip(),
            reset_account=parse_bool(values["ResetAccount"]),
            playlist_id=values["PlaylistId"].strip(),
            playlist_file=resolve_config_path(values["PlaylistFile"], config_path),
            existing_list_file=resolve_config_path(values["ExistingListFile"], config_path),
        ),
        config_path,
    )


def write_app_config(path: Path, app_config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"DownloadPlatform={app_config.download_platform}\n"
        f"DownloadBot={app_config.download_bot}\n"
        f"DownloadChannel={app_config.download_channel}\n"
        f"ResetAccount={'True' if app_config.reset_account else 'False'}\n"
        f"PlaylistId={app_config.playlist_id}\n"
        f"PlaylistFile={app_config.playlist_file}\n"
        f"ExistingListFile={app_config.existing_list_file}\n",
        encoding="utf-8",
    )


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    existing_lines: list[str] = []
    if path.is_file():
        existing_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    seen: set[str] = set()
    output_lines: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output_lines.append(line)
            continue

        key, _value = line.split("=", 1)
        key = key.strip()
        if key in updates:
            output_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output_lines.append(line)

    for key, value in updates.items():
        if key not in seen:
            output_lines.append(f"{key}={value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")

    for key, value in updates.items():
        os.environ[key] = value


def set_config_reset_account_false(path: Path) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []
    output_lines: list[str] = []
    updated = False

    for line in lines:
        if line.strip().startswith("ResetAccount") and "=" in line:
            output_lines.append("ResetAccount=False")
            updated = True
        else:
            output_lines.append(line)

    if not updated:
        output_lines.append("ResetAccount=False")

    path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")


def prompt_telegram_account_values() -> dict[str, str]:
    api_id_raw = input("New TG_API_ID: ").strip()
    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise SystemExit("[CONFIG] TG_API_ID must be a numeric api_id from https://my.telegram.org/apps.") from exc

    api_hash = getpass.getpass("New TG_API_HASH: ").strip()
    validate_telegram_api_config(api_id, api_hash)

    phone = input("New TG_PHONE (include country code): ").strip()
    bot_username = input("TG_BOT_USERNAME [SQMP3]: ").strip() or "SQMP3"

    return {
        "TG_API_ID": str(api_id),
        "TG_API_HASH": api_hash,
        "TG_PHONE": phone,
        "TG_BOT_USERNAME": bot_username,
    }


def reset_telegram_account(config_path: Path, app_config: AppConfig) -> None:
    load_dotenv()

    session_dir = resolve_path(env_value("SESSION_DIR", str(PROJECT_ROOT / "telegram_session")) or PROJECT_ROOT / "telegram_session")
    session_name = env_value("SESSION_NAME", "music_session") or "music_session"
    blocked_file = resolve_path(env_value("BLOCKED_FILE", str(DEFAULT_BLOCKED_FILE)) or DEFAULT_BLOCKED_FILE)
    env_file = resolve_path(env_value("ENV_FILE", str(DEFAULT_ENV_FILE)) or DEFAULT_ENV_FILE)

    for path in session_dir.glob(f"{session_name}.session*"):
        if path.is_file():
            path.unlink()

    blocked_file.unlink(missing_ok=True)

    updates = prompt_telegram_account_values()
    update_env_file(env_file, updates)
    set_config_reset_account_false(config_path)

    print(f"[ACCOUNT] Telegram session removed from: {session_dir}")
    print(f"[ACCOUNT] Telegram account settings updated in: {env_file}")
    print(f"[ACCOUNT] ResetAccount has been changed back to False in: {config_path}")


def load_config(app_config: AppConfig) -> Config:
    load_dotenv()

    api_id = env_int("TG_API_ID", required=True)
    api_hash = env_value("TG_API_HASH", required=True)
    assert api_hash is not None
    validate_telegram_api_config(api_id, api_hash)

    min_delay = env_int("MIN_DELAY", 60)
    max_delay = env_int("MAX_DELAY", 150)
    if min_delay > max_delay:
        raise SystemExit("[CONFIG] MIN_DELAY cannot be greater than MAX_DELAY.")

    long_rest_min = env_int("LONG_REST_MIN_MINUTES", 30)
    long_rest_max = env_int("LONG_REST_MAX_MINUTES", 90)
    if long_rest_min > long_rest_max:
        raise SystemExit("[CONFIG] LONG_REST_MIN_MINUTES cannot be greater than LONG_REST_MAX_MINUTES.")

    download_dir = resolve_path(env_value("DOWNLOAD_DIR", str(PROJECT_ROOT / "TMDownload")) or PROJECT_ROOT / "TMDownload")
    bot_preset = resolve_bot_selection(app_config)

    return Config(
        api_id=api_id,
        api_hash=api_hash,
        phone=env_value("TG_PHONE"),
        bot_name=bot_preset.name,
        bot_strategy=bot_preset.strategy,
        bot_username=bot_preset.username,
        playlist_file=app_config.playlist_file,
        existing_list_file=app_config.existing_list_file,
        pending_file=resolve_path(env_value("PENDING_FILE", str(DEFAULT_PENDING_FILE)) or DEFAULT_PENDING_FILE),
        failed_file=resolve_path(env_value("FAILED_FILE", str(DEFAULT_FAILED_FILE)) or DEFAULT_FAILED_FILE),
        request_failed_file=resolve_path(
            env_value("REQUEST_FAILED_FILE", str(DEFAULT_REQUEST_FAILED_FILE)) or DEFAULT_REQUEST_FAILED_FILE
        ),
        engine_state_file=resolve_path(
            env_value("ENGINE_STATE_FILE", str(DEFAULT_ENGINE_STATE_FILE)) or DEFAULT_ENGINE_STATE_FILE
        ),
        blocked_file=resolve_path(env_value("BLOCKED_FILE", str(DEFAULT_BLOCKED_FILE)) or DEFAULT_BLOCKED_FILE),
        log_file=resolve_path(env_value("LOG_FILE", str(PROJECT_ROOT / "download_summary.log")) or PROJECT_ROOT / "download_summary.log"),
        download_dir=download_dir,
        incoming_dir=resolve_path(env_value("INCOMING_DIR", str(download_dir / "_incoming")) or download_dir / "_incoming"),
        rejected_dir=resolve_path(env_value("REJECTED_DIR", str(download_dir / "rejected")) or download_dir / "rejected"),
        session_dir=resolve_path(env_value("SESSION_DIR", str(PROJECT_ROOT / "telegram_session")) or PROJECT_ROOT / "telegram_session"),
        session_name=env_value("SESSION_NAME", "music_session") or "music_session",
        session_ttl_hours=env_int("SESSION_TTL_HOURS", 72),
        min_delay=min_delay,
        max_delay=max_delay,
        response_timeout=env_int("RESPONSE_TIMEOUT", 120),
        long_rest_every_minutes=env_int("LONG_REST_EVERY_MINUTES", 120),
        long_rest_min_minutes=long_rest_min,
        long_rest_max_minutes=long_rest_max,
        stop_on_send_blocked=env_bool("STOP_ON_SEND_BLOCKED", True),
        send_blocked_cooldown_hours=env_int("SEND_BLOCKED_COOLDOWN_HOURS", 6),
        success_confirm_every=env_int("SUCCESS_CONFIRM_EVERY", 500),
    )


def validate_telegram_api_config(api_id: int, api_hash: str) -> None:
    if api_id <= 0:
        raise SystemExit("[CONFIG] TG_API_ID must be a positive number from my.telegram.org/apps.")

    if not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        raise SystemExit(
            "[CONFIG] TG_API_HASH should be the 32-character API hash from my.telegram.org/apps, "
            "not a bot token or Telegram password."
        )


def safe_filename(value: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", value).strip()
    return cleaned or "Untitled Playlist"


def ensure_playlist_file(app_config: AppConfig) -> Path:
    if app_config.playlist_file.is_file():
        print(f"[PLAYLIST] Using existing playlist file: {app_config.playlist_file}")
        return app_config.playlist_file

    if not app_config.playlist_id:
        raise SystemExit("[CONFIG] PlaylistFile does not exist and PlaylistId is empty.")

    print(f"[PLAYLIST] Playlist file not found: {app_config.playlist_file}")
    print(f"[PLAYLIST] Fetching NetEase playlist ID: {app_config.playlist_id}")
    return export_netease_playlist(app_config.playlist_id, app_config.playlist_file)


def export_netease_playlist(playlist_id: str, output_file: Path) -> Path:
    try:
        from pyncm import apis
    except ImportError as exc:
        raise SystemExit(
            "[PLAYLIST] Missing dependency pyncm. Rebuild the Docker image or install requirements.txt."
        ) from exc

    response = apis.playlist.GetPlaylistInfo(playlist_id)
    playlist_res = response.json() if hasattr(response, "json") else response

    if playlist_res.get("code") != 200:
        raise SystemExit(f"[PLAYLIST] Failed to fetch playlist info for ID: {playlist_id}")

    playlist_data = playlist_res.get("playlist", {})
    playlist_name = playlist_data.get("name", "Untitled Playlist")
    track_ids = [item["id"] for item in playlist_data.get("trackIds", []) if "id" in item]

    if not track_ids:
        raise SystemExit(f"[PLAYLIST] No tracks found in playlist ID: {playlist_id}")

    target_file = output_file
    if not str(target_file).strip():
        today = datetime.now().strftime("%Y-%m-%d")
        target_file = PROJECT_ROOT / f"{today}_{safe_filename(playlist_name)}.txt"

    target_file.parent.mkdir(parents=True, exist_ok=True)
    print(f"[PLAYLIST] Reading playlist '{playlist_name}', found {len(track_ids)} tracks.")

    songs = []
    chunk_size = 300
    for index in range(0, len(track_ids), chunk_size):
        batch_ids = track_ids[index:index + chunk_size]
        detail_response = apis.track.GetTrackDetail(batch_ids)
        detail_json = detail_response.json() if hasattr(detail_response, "json") else detail_response
        songs.extend(detail_json.get("songs", []))

    with target_file.open("w", encoding="utf-8") as handle:
        handle.write(f"Playlist ID: {playlist_id}\n")
        handle.write(f"Playlist Name: {playlist_name}\n")
        handle.write(f"Total Tracks: {len(songs)}\n")
        handle.write("=" * 40 + "\n\n")

        for index, song in enumerate(songs, start=1):
            song_name = song.get("name", "Unknown Song")
            artists_data = song.get("ar", [])
            artists = " / ".join(artist.get("name", "Unknown Artist") for artist in artists_data)
            handle.write(f"{index}. {artists} - {song_name}\n")

    print(f"[PLAYLIST] Exported {len(songs)} tracks to: {target_file}")
    return target_file


def normalize_piece(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = unicodedata.normalize("NFKC", value).lower()

    normalized_chars = []
    for ch in value.replace("_", " "):
        normalized_chars.append(ch if ch.isalnum() else " ")

    return " ".join("".join(normalized_chars).split())


def meaningful_tokens(value: str) -> set[str]:
    return {token for token in normalize_piece(value).split() if len(token) > 1 and token not in STOP_WORDS}


def track_matches_text(track: Track, text: str) -> bool:
    search_tokens = meaningful_tokens(text)
    if not search_tokens:
        return False

    title_tokens = meaningful_tokens(track.title)
    artist_tokens = meaningful_tokens(track.artist)

    if not title_tokens:
        return False

    matched_title_count = len(title_tokens.intersection(search_tokens))
    if len(title_tokens) == 1:
        needed_title_count = 1
    elif len(title_tokens) <= 4:
        needed_title_count = 2
    else:
        needed_title_count = 3

    if matched_title_count < needed_title_count:
        return False

    if artist_tokens and artist_tokens.isdisjoint(search_tokens):
        return matched_title_count >= max(3, needed_title_count)

    return True


def deezer_track_matches_text(track: Track, text: str) -> bool:
    if track_matches_text(track, text):
        return True

    search_tokens = meaningful_tokens(text)
    artist_tokens = meaningful_tokens(track.artist)
    if not search_tokens or (artist_tokens and artist_tokens.isdisjoint(search_tokens)):
        return False

    title_segments = re.split(r"\s*(?:-|:|\||/|\u2013|\u2014)\s*", track.title)
    for segment in title_segments:
        segment_tokens = meaningful_tokens(segment)
        if len(segment_tokens) >= 2 and segment_tokens.issubset(search_tokens):
            return True
    return False


def strip_number_prefix(value: str) -> str:
    return re.sub(r"^\s*\d+[.)]\s*", "", value).strip()


def basename_from_any_path(value: str) -> str:
    return re.split(r"[\\/]", value.strip())[-1]


def split_artist_title(value: str, source: str = "", line_no: int = 0) -> Track | None:
    text = strip_number_prefix(value)
    text = re.sub(r"\s*\(\d+\)\s*$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None

    # Prefer the common "Artist - Title" separator, then fall back to the first hyphen.
    if re.search(r"\s+-\s+", text):
        artist, title = re.split(r"\s+-\s+", text, maxsplit=1)
    elif "-" in text:
        artist, title = text.split("-", 1)
    else:
        return None

    artist = artist.strip()
    title = title.strip()
    if not artist or not title:
        return None

    return Track(artist=artist, title=title, source=source, line_no=line_no)


def parse_playlist(path: Path) -> list[Track]:
    if not path.is_file():
        raise SystemExit(f"[INPUT] Playlist file not found: {path}")

    tracks: list[Track] = []
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        track = split_artist_title(line, source=str(path), line_no=line_no)
        if track:
            tracks.append(track)

    return tracks


def parse_optional_track_file(path: Path) -> dict[tuple[str, str], Track]:
    tracks: dict[tuple[str, str], Track] = {}
    if not path.is_file():
        return tracks

    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        track = split_artist_title(line, source=str(path), line_no=line_no)
        if track:
            tracks[track.key] = track

    return tracks


def parse_existing_list(path: Path) -> dict[tuple[str, str], Track]:
    existing: dict[tuple[str, str], Track] = {}
    if not path.is_file():
        print(f"[WARN] Existing-list file not found, continuing without it: {path}")
        return existing

    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        filename = basename_from_any_path(line)
        stem = Path(filename).stem
        track = split_artist_title(stem, source=str(path), line_no=line_no)
        if track:
            existing[track.key] = track

    return existing


def scan_download_dir(path: Path) -> dict[tuple[str, str], Track]:
    existing: dict[tuple[str, str], Track] = {}
    if not path.is_dir():
        return existing

    for file_path in path.iterdir():
        if not file_path.is_file() or file_path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue

        track = split_artist_title(file_path.stem, source=str(file_path))
        if track:
            existing[track.key] = track

    return existing


def parse_status_log(path: Path) -> dict[tuple[str, str], tuple[str, Track]]:
    statuses: dict[tuple[str, str], tuple[str, Track]] = {}
    if not path.is_file():
        return statuses

    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        match = re.match(r"^\[(SUCCESS|FAILED|REJECTED|REQUEST_FAILED)\]\s+(.+)$", raw_line.strip())
        if not match:
            continue

        status = match.group(1)
        payload = match.group(2).strip()
        details = ""
        if payload.endswith(")") and " (" in payload:
            payload, details = payload.rsplit(" (", 1)
            details = details.rstrip(")")

        track = split_artist_title(payload, source=str(path), line_no=line_no)
        if track:
            saved_match = re.search(r"Saved as:\s*(.+)$", details)
            if status == "SUCCESS" and saved_match and not track_matches_text(track, saved_match.group(1)):
                status = "REJECTED"
            if status == "FAILED" and text_indicates_request_failure(details):
                status = "REQUEST_FAILED"
            statuses[track.key] = (status, track)

    return statuses


def build_download_queue(config: Config) -> tuple[list[Track], dict[tuple[str, str], Track]]:
    playlist_tracks = parse_playlist(config.playlist_file)
    known_tracks = parse_existing_list(config.existing_list_file)
    known_tracks.update(scan_download_dir(config.download_dir))
    log_statuses = parse_status_log(config.log_file)
    request_failed_tracks = parse_optional_track_file(config.request_failed_file)
    current_engine = download_engine_key(config)
    previous_engine = read_download_engine(config.engine_state_file)
    engine_changed = bool(previous_engine and previous_engine != current_engine)

    failed_from_log: dict[tuple[str, str], Track] = {}
    for key, (status, track) in log_statuses.items():
        if status == "SUCCESS":
            request_failed_tracks.pop(key, None)
        elif status == "REQUEST_FAILED":
            request_failed_tracks[key] = track
        elif status != "SUCCESS":
            failed_from_log[key] = track

    engine_retry_queue: list[Track] = []
    retry_queue: list[Track] = []
    new_queue: list[Track] = []
    unresolved_failures: dict[tuple[str, str], Track] = {}
    seen_in_playlist: set[tuple[str, str]] = set()
    skipped_existing = 0
    skipped_duplicate = 0
    skipped_request_failed = 0
    retry_request_failed = 0

    for track in playlist_tracks:
        key = track.key
        if key in seen_in_playlist:
            skipped_duplicate += 1
            continue
        seen_in_playlist.add(key)

        if key in known_tracks:
            skipped_existing += 1
            continue

        if key in request_failed_tracks:
            if engine_changed:
                engine_retry_queue.append(track)
                retry_request_failed += 1
                continue
            skipped_request_failed += 1
            continue

        if key in failed_from_log:
            retry_queue.append(track)
            unresolved_failures[key] = track
        else:
            new_queue.append(track)

    queue = engine_retry_queue + retry_queue + new_queue

    write_pending_file(config.pending_file, queue)
    write_track_file(config.failed_file, retry_queue)
    write_track_file(
        config.request_failed_file,
        sorted(request_failed_tracks.values(), key=lambda item: normalize_piece(item.query)),
    )

    print(f"[PLAN] Playlist tracks parsed: {len(playlist_tracks)}")
    print(f"[PLAN] Known existing tracks: {len(known_tracks)}")
    print(f"[PLAN] Skipped already existing: {skipped_existing}")
    print(f"[PLAN] Skipped duplicate rows: {skipped_duplicate}")
    print(f"[PLAN] Skipped previous request failures: {skipped_request_failed}")
    if engine_changed:
        print(f"[PLAN] Download engine changed: {previous_engine} -> {current_engine}")
        print(f"[PLAN] Retrying request-failed tracks first for the new engine: {retry_request_failed}")
    print(f"[PLAN] Retry failed tracks first: {len(retry_queue)}")
    print(f"[PLAN] Pending downloads: {len(queue)}")
    print(f"[PLAN] Pending list written to: {config.pending_file}")
    print(f"[PLAN] Failed retry list written to: {config.failed_file}")
    print(f"[PLAN] Request-failed list written to: {config.request_failed_file}")

    return queue, unresolved_failures


def write_pending_file(path: Path, tracks: list[Track]) -> None:
    write_track_file(path, tracks)


def write_track_file(path: Path, tracks: list[Track]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(track.query for track in tracks)
    if content:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def download_engine_key(config: Config) -> str:
    return f"{config.bot_strategy}:{normalize_bot_choice(config.bot_username)}"


def read_download_engine(path: Path) -> str:
    if not path.is_file():
        return ""

    first_line = path.read_text(encoding="utf-8", errors="replace").splitlines()[0:1]
    return first_line[0].strip() if first_line else ""


def write_download_engine(config: Config) -> None:
    config.engine_state_file.parent.mkdir(parents=True, exist_ok=True)
    config.engine_state_file.write_text(download_engine_key(config) + "\n", encoding="utf-8")


def ensure_directories(config: Config) -> None:
    config.download_dir.mkdir(parents=True, exist_ok=True)
    config.incoming_dir.mkdir(parents=True, exist_ok=True)
    config.rejected_dir.mkdir(parents=True, exist_ok=True)
    config.session_dir.mkdir(parents=True, exist_ok=True)
    config.request_failed_file.parent.mkdir(parents=True, exist_ok=True)
    config.engine_state_file.parent.mkdir(parents=True, exist_ok=True)
    config.log_file.parent.mkdir(parents=True, exist_ok=True)


def remove_expired_session(config: Config) -> None:
    if config.session_ttl_hours <= 0:
        return

    session_file = config.session_dir / f"{config.session_name}.session"
    if not session_file.exists():
        return

    age_seconds = time.time() - session_file.stat().st_mtime
    ttl_seconds = config.session_ttl_hours * 60 * 60
    if age_seconds <= ttl_seconds:
        return

    for path in config.session_dir.glob(f"{config.session_name}.session*"):
        if path.is_file():
            path.unlink()

    print(f"[SESSION] Removed local Telegram session older than {config.session_ttl_hours} hours.")


def mark_send_blocked(config: Config) -> None:
    if config.send_blocked_cooldown_hours <= 0:
        return

    blocked_until = time.time() + config.send_blocked_cooldown_hours * 60 * 60
    config.blocked_file.parent.mkdir(parents=True, exist_ok=True)
    config.blocked_file.write_text(
        f"{blocked_until:.0f}\n"
        f"Telegram refused message sending. Retry after the timestamp above, or rerun with --force.\n",
        encoding="utf-8",
    )


def send_blocked_cooldown_active(config: Config) -> bool:
    if not config.blocked_file.is_file():
        return False

    first_line = config.blocked_file.read_text(encoding="utf-8", errors="replace").splitlines()[0:1]
    if not first_line:
        return False

    try:
        blocked_until = float(first_line[0])
    except ValueError:
        return False

    remaining_seconds = blocked_until - time.time()
    if remaining_seconds <= 0:
        config.blocked_file.unlink(missing_ok=True)
        return False

    remaining_minutes = max(1, int(remaining_seconds // 60))
    print(f"[BLOCKED] Telegram message sending was refused recently. Retry in about {remaining_minutes} minutes.")
    print(f"[BLOCKED] Manual override after you confirm Telegram can send again: add --force.")
    print(f"[BLOCKED] Marker file: {config.blocked_file}")
    return True


def write_log(config: Config, status: str, track: Track, details: str = "") -> None:
    with config.log_file.open("a", encoding="utf-8") as handle:
        handle.write(f"[{status}] {track.query} {details}\n")


def write_deezer_log(config: Config, track: Track, details: str) -> None:
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    label = {
        "deezer": "DEEZER",
        "music_hunters": "MUSIC_HUNTERS",
    }.get(config.bot_strategy, "BOT")
    line = f"[{label}] {track.query} | {details}"
    print(line)
    with config.log_file.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {line}\n")


def write_failed_file(config: Config, failed_tracks: dict[tuple[str, str], Track]) -> None:
    ordered = sorted(failed_tracks.values(), key=lambda item: normalize_piece(item.query))
    write_track_file(config.failed_file, ordered)


def write_request_failed_file(config: Config, request_failed_tracks: dict[tuple[str, str], Track]) -> None:
    ordered = sorted(request_failed_tracks.values(), key=lambda item: normalize_piece(item.query))
    write_track_file(config.request_failed_file, ordered)


async def ensure_authorized(client: TelegramClient, config: Config) -> None:
    if await client.is_user_authorized():
        print("[SESSION] Reusing existing Telegram session.")
        return

    phone = config.phone or input("Telegram phone number: ").strip()
    try:
        await client.send_code_request(phone)
    except ApiIdInvalidError as exc:
        raise SystemExit(
            "[CONFIG] Telegram rejected TG_API_ID/TG_API_HASH. "
            "Open .env and replace them with the matching api_id and api_hash from https://my.telegram.org/apps. "
            "Do not use a bot token here."
        ) from exc
    code = input("Telegram login code: ").strip()

    try:
        await client.sign_in(phone=phone, code=code)
    except SessionPasswordNeededError:
        password = getpass.getpass("Telegram 2FA password: ")
        await client.sign_in(password=password)

    print("[SESSION] Telegram login completed.")


def message_matches_track(message, track: Track, matcher=track_matches_text) -> bool:
    text_parts = [message.message or ""]

    document = getattr(message.media, "document", None) if message.media else None
    if document:
        for attr in document.attributes:
            for field in ("file_name", "title", "performer"):
                value = getattr(attr, field, "")
                if value:
                    text_parts.append(value)

    return matcher(track, " ".join(text_parts))


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    for index in range(1, 10_000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not find a free filename for: {path.name}")


def move_to_rejected(config: Config, downloaded_path: Path) -> Path:
    config.rejected_dir.mkdir(parents=True, exist_ok=True)
    target = unique_path(config.rejected_dir / downloaded_path.name)
    shutil.move(str(downloaded_path), str(target))
    return target


def accept_downloaded_file(
    config: Config,
    track: Track,
    downloaded_path_text: str,
    matcher=track_matches_text,
) -> str:
    downloaded_path = Path(downloaded_path_text)
    if not downloaded_path.exists():
        raise RuntimeError(f"Downloaded path does not exist: {downloaded_path_text}")

    if not matcher(track, downloaded_path.name):
        rejected_path = move_to_rejected(config, downloaded_path)
        raise RuntimeError(f"Downloaded media did not match request; moved to rejected/{rejected_path.name}")

    target_path = config.download_dir / downloaded_path.name
    if target_path.exists():
        rejected_path = move_to_rejected(config, downloaded_path)
        raise RuntimeError(f"Duplicate filename already exists; moved to rejected/{rejected_path.name}")

    shutil.move(str(downloaded_path), str(target_path))
    return target_path.name


def telegram_chat_ref(value: str) -> str:
    text = value.strip()
    url_match = re.match(
        r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/([^/?#]+)",
        text,
        flags=re.IGNORECASE,
    )
    if url_match:
        return f"@{url_match.group(1)}"

    username = text.lstrip("@").strip()
    if not username:
        raise RuntimeError("DownloadChannel/TG_BOT_USERNAME cannot be empty.")
    return f"@{username}"


def is_deezer_music_bot(value: str) -> bool:
    return telegram_chat_ref(value).lstrip("@").lower() == "deezermusicbot"


def message_button_rows(message):
    return getattr(message, "buttons", None) or []


def describe_deezer_panel(message) -> str:
    labels = []
    for row in message_button_rows(message):
        for button in row:
            text = " ".join((getattr(button, "text", "") or "").split())
            if text:
                labels.append(text[:120])
    return f"message_id={getattr(message, 'id', '?')} buttons={len(labels)} labels={labels}"


def panel_indicates_no_results(message) -> bool:
    for row in message_button_rows(message):
        for button in row:
            text = getattr(button, "text", "") or ""
            if "no results" in normalize_piece(text):
                return True
    return False


def find_button_by_label(message, expected_label: str):
    normalized_expected = normalize_piece(expected_label)
    for row_index, row in enumerate(message_button_rows(message)):
        for column_index, button in enumerate(row):
            text = getattr(button, "text", "") or ""
            if normalize_piece(text) == normalized_expected:
                return row_index, column_index, text
    return None


def find_deezer_track_button(message, track: Track):
    filter_labels = {"tracks", "albums", "artists", "deezer", "soundcloud", "vk", "close"}
    for row_index, row in enumerate(message_button_rows(message)):
        for column_index, button in enumerate(row):
            text = getattr(button, "text", "") or ""
            if normalize_piece(text) in filter_labels:
                continue
            if deezer_track_matches_text(track, text):
                return row_index, column_index, text
    return None


def find_numbered_track_button(message, track: Track):
    message_text = getattr(message, "message", "") or ""
    for line in message_text.splitlines():
        match = re.match(r"^\s*(\d+)[.)]\s+(.+?)\s*$", line)
        if not match:
            continue

        result_number = match.group(1)
        candidate_text = re.sub(r"\s+\(\d{1,3}:\d{2}\)\s*$", "", match.group(2)).strip()
        if not deezer_track_matches_text(track, candidate_text):
            continue

        button_location = find_button_by_label(message, result_number)
        if button_location is not None:
            return button_location, candidate_text
    return None


def button_is_selected(text: str) -> bool:
    return "\u2705" in text


def callback_answer_text(callback_answer) -> str:
    return " ".join((getattr(callback_answer, "message", "") or "").split())


def callback_confirms_download(callback_answer) -> bool:
    text = callback_answer_text(callback_answer).lower()
    return any(
        phrase in text
        for phrase in (
            "download started",
            "downloading",
            "download is starting",
        )
    )


async def click_message_button(config: Config, track: Track, message, button_location, purpose: str):
    row_index, column_index, text = button_location
    button = message_button_rows(message)[row_index][column_index]
    if getattr(button, "url", None):
        raise RuntimeError(f"{purpose} is a URL button and cannot trigger a Telegram bot callback: {text}")

    write_deezer_log(
        config,
        track,
        f"Clicking {purpose}: row={row_index} column={column_index} text={text!r}",
    )
    try:
        callback_answer = await message.click(row_index, column_index)
    except Exception as exc:
        write_deezer_log(
            config,
            track,
            f"Click failed for {purpose}: {type(exc).__name__}: {exc}",
        )
        raise

    callback_text = callback_answer_text(callback_answer)
    details = f"Callback completed for {purpose}"
    if callback_text:
        details += f"; bot reply={callback_text[:200]!r}"
    else:
        details += "; bot reply=<empty>"
    write_deezer_log(config, track, details)

    if callback_text and text_indicates_safety_stop(callback_text):
        raise SafetyStopError(f"{config.bot_name} returned a safety-stop callback: {callback_text[:200]}")
    if callback_text and text_indicates_request_failure(callback_text):
        raise TrackRequestError(f"{config.bot_name} could not request this track: {callback_text[:200]}")

    return callback_answer


async def request_and_download_sqmp3(
    client: TelegramClient,
    config: Config,
    track: Track,
    bot_chat: str,
) -> str:
    loop = asyncio.get_running_loop()
    download_future = loop.create_future()
    safety_future = loop.create_future()

    @client.on(events.NewMessage(chats=bot_chat))
    async def handle_new_message(event):
        if event.message.media and (event.message.audio or event.message.document):
            if message_matches_track(event.message, track) and not download_future.done():
                download_future.set_result(event.message)
            return

        if message_button_rows(event.message):
            return

        message_text = " ".join((event.message.message or "").split())
        if message_text and text_indicates_safety_stop(message_text) and not safety_future.done():
            safety_future.set_exception(
                SafetyStopError(f"{config.bot_name} returned a safety-stop message: {message_text[:200]}")
            )
            return
        if message_text and text_indicates_request_failure(message_text) and not safety_future.done():
            safety_future.set_exception(
                TrackRequestError(f"{config.bot_name} could not request this track: {message_text[:200]}")
            )

    try:
        await client.send_message(bot_chat, f"/music {track.query}")
        done, pending = await asyncio.wait(
            {download_future, safety_future},
            timeout=config.response_timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for pending_future in pending:
            pending_future.cancel()

        if safety_future in done:
            raise safety_future.exception()
        if download_future not in done:
            raise asyncio.TimeoutError

        matched_message = download_future.result()
        downloaded_path = await client.download_media(matched_message, file=str(config.incoming_dir))
        if not downloaded_path:
            raise RuntimeError("Telegram returned an empty download path.")
        return accept_downloaded_file(config, track, downloaded_path)
    finally:
        client.remove_event_handler(handle_new_message)


async def request_and_download_deezer(
    client: TelegramClient,
    config: Config,
    track: Track,
    bot_chat: str,
    allow_tracks_filter: bool,
) -> str:
    loop = asyncio.get_running_loop()
    download_future = loop.create_future()
    safety_future = loop.create_future()
    panel_event = asyncio.Event()
    panel_message = None
    panel_version = 0
    deadline = loop.time() + config.response_timeout
    stage = "registering bot event handlers"

    async def handle_bot_message(event):
        nonlocal panel_message, panel_version
        message = event.message
        if message.media and (message.audio or message.document):
            if message_matches_track(message, track, deezer_track_matches_text):
                file_name = getattr(getattr(message, "file", None), "name", "") or "unknown"
                write_deezer_log(
                    config,
                    track,
                    f"Matched media from {type(event).__name__}: message_id={message.id} file={file_name!r}",
                )
                if not download_future.done():
                    download_future.set_result(message)
            else:
                file_name = getattr(getattr(message, "file", None), "name", "") or "unknown"
                write_deezer_log(
                    config,
                    track,
                    f"Ignored non-matching media from {type(event).__name__}: message_id={message.id} file={file_name!r}",
                )
            return

        if message_button_rows(message):
            panel_message = message
            panel_version += 1
            panel_event.set()
            write_deezer_log(
                config,
                track,
                f"Received {type(event).__name__} result panel version={panel_version}: "
                f"{describe_deezer_panel(message)}",
            )
            return

        message_text = " ".join((message.message or "").split())
        if message_text and text_indicates_safety_stop(message_text) and not safety_future.done():
            write_deezer_log(
                config,
                track,
                f"Received safety-stop text from {type(event).__name__}: {message_text[:200]!r}",
            )
            safety_future.set_exception(
                SafetyStopError(f"{config.bot_name} returned a safety-stop message: {message_text[:200]}")
            )
            return
        if message_text and text_indicates_request_failure(message_text) and not safety_future.done():
            write_deezer_log(
                config,
                track,
                f"Received request-failure text from {type(event).__name__}: {message_text[:200]!r}",
            )
            safety_future.set_exception(
                TrackRequestError(f"{config.bot_name} could not request this track: {message_text[:200]}")
            )

    async def wait_for_media_or_panel(after_panel_version: int):
        nonlocal panel_message, panel_version
        while True:
            if safety_future.done():
                raise safety_future.exception()
            if download_future.done():
                return "media", download_future.result(), panel_version
            if panel_version > after_panel_version and panel_message is not None:
                return "panel", panel_message, panel_version

            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError

            panel_event.clear()
            if panel_version > after_panel_version:
                continue

            panel_wait = asyncio.create_task(panel_event.wait())
            done, _ = await asyncio.wait(
                {download_future, safety_future, panel_wait},
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if safety_future in done:
                panel_wait.cancel()
                await asyncio.gather(panel_wait, return_exceptions=True)
                raise safety_future.exception()
            if download_future in done:
                panel_wait.cancel()
                await asyncio.gather(panel_wait, return_exceptions=True)
                return "media", download_future.result(), panel_version
            if panel_wait not in done:
                panel_wait.cancel()
                await asyncio.gather(panel_wait, return_exceptions=True)
                raise asyncio.TimeoutError

    async def wait_for_matching_media(timeout_seconds: float):
        if download_future.done():
            return download_future.result()
        if safety_future.done():
            raise safety_future.exception()

        done, _ = await asyncio.wait(
            {download_future, safety_future},
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if safety_future in done:
            raise safety_future.exception()
        if download_future in done:
            return download_future.result()
        return None

    async def click_music_hunters_result(message, button_location):
        initial_delay = random.uniform(*MUSIC_HUNTERS_INITIAL_CLICK_DELAY)
        write_deezer_log(
            config,
            track,
            f"Waiting {initial_delay:.1f}s before the first automatic click",
        )
        await asyncio.sleep(initial_delay)

        if download_future.done():
            return download_future.result()

        for attempt in range(1, MUSIC_HUNTERS_CLICK_ATTEMPTS + 1):
            if download_future.done():
                return download_future.result()
            write_deezer_log(
                config,
                track,
                f"Automatic click attempt {attempt}/{MUSIC_HUNTERS_CLICK_ATTEMPTS}",
            )
            callback_answer = await click_message_button(
                config,
                track,
                message,
                button_location,
                "track result",
            )

            if download_future.done():
                return download_future.result()
            if callback_confirms_download(callback_answer):
                write_deezer_log(config, track, "Bot confirmed that the download started")
                return None

            write_deezer_log(
                config,
                track,
                f"Click attempt {attempt} was not acknowledged; waiting "
                f"{MUSIC_HUNTERS_MEDIA_GRACE_SECONDS:.0f}s for delayed media",
            )
            delayed_media = await wait_for_matching_media(MUSIC_HUNTERS_MEDIA_GRACE_SECONDS)
            if delayed_media is not None:
                return delayed_media

            if attempt < MUSIC_HUNTERS_CLICK_ATTEMPTS:
                retry_delay = random.uniform(*MUSIC_HUNTERS_RETRY_DELAY)
                write_deezer_log(
                    config,
                    track,
                    f"Retrying the same numbered button after {retry_delay:.1f}s",
                )
                await asyncio.sleep(retry_delay)

        raise TrackRequestError(
            f"{config.bot_name} did not acknowledge the numbered button after "
            f"{MUSIC_HUNTERS_CLICK_ATTEMPTS} attempts."
        )

    new_message_event = events.NewMessage(chats=bot_chat)
    edited_message_event = events.MessageEdited(chats=bot_chat)
    client.add_event_handler(handle_bot_message, new_message_event)
    client.add_event_handler(handle_bot_message, edited_message_event)

    try:
        write_deezer_log(
            config,
            track,
            f"Registered message listeners for {bot_chat}; response timeout={config.response_timeout}s",
        )
        stage = "sending search query"
        write_deezer_log(config, track, f"Sending search query to {bot_chat}")
        await client.send_message(bot_chat, track.query)
        stage = "waiting for the search result panel"
        write_deezer_log(config, track, "Search query sent; waiting for a result panel or matching media")
        response_kind, response, current_panel_version = await wait_for_media_or_panel(0)
        write_deezer_log(
            config,
            track,
            f"Search response received: kind={response_kind} panel_version={current_panel_version}",
        )

        if response_kind == "panel":
            result_button = find_deezer_track_button(response, track)
            if result_button is None and config.bot_strategy == "music_hunters":
                numbered_result = find_numbered_track_button(response, track)
                if numbered_result is not None:
                    result_button, candidate_text = numbered_result
                    write_deezer_log(
                        config,
                        track,
                        f"Mapped numbered result {result_button[2]!r} to matching candidate {candidate_text!r}",
                    )

            if result_button is None:
                if not allow_tracks_filter:
                    write_deezer_log(
                        config,
                        track,
                        f"{config.bot_name} returned no matching track button",
                    )
                    if panel_indicates_no_results(response):
                        raise TrackRequestError(f'{config.bot_name} returned no results for "{track.query}".')
                    raise TrackRequestError(
                        f'{config.bot_name} returned no matching track result for "{track.query}".'
                    )

                tracks_button = find_button_by_label(response, "Tracks")
                if tracks_button is None:
                    write_deezer_log(config, track, "No matching track button and no Tracks filter were found")
                    raise TrackRequestError(
                        f'{config.bot_name} returned buttons but no matching track result for "{track.query}".'
                    )
                if button_is_selected(tracks_button[2]):
                    write_deezer_log(
                        config,
                        track,
                        "No matching track button was found while Tracks is already selected",
                    )
                    raise TrackRequestError(
                        f'{config.bot_name} returned no matching track result for "{track.query}" '
                        "while the Tracks filter was already selected."
                    )

                stage = "clicking the Tracks filter"
                await click_message_button(config, track, response, tracks_button, "Tracks filter")
                stage = "waiting for the Tracks-filtered result panel"
                write_deezer_log(config, track, "Tracks filter clicked; waiting for the updated result panel")
                response_kind, response, current_panel_version = await wait_for_media_or_panel(
                    current_panel_version
                )
                if response_kind == "panel":
                    result_button = find_deezer_track_button(response, track)
                    if result_button is None:
                        raise TrackRequestError(
                            f'{config.bot_name} returned no matching track result for "{track.query}" '
                            "after selecting Tracks."
                        )

            if response_kind == "panel":
                write_deezer_log(
                    config,
                    track,
                    f"Selected matching track button: row={result_button[0]} column={result_button[1]} "
                    f"text={result_button[2]!r}",
                )
                stage = "clicking the track result"
                media_after_click = None
                if config.bot_strategy == "music_hunters":
                    media_after_click = await click_music_hunters_result(response, result_button)
                    deadline = loop.time() + config.response_timeout
                else:
                    await click_message_button(config, track, response, result_button, "track result")

                if media_after_click is not None:
                    response = media_after_click
                else:
                    stage = "waiting for the selected track media"
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    write_deezer_log(
                        config,
                        track,
                        f"Confirmed callback; waiting up to {remaining:.1f}s for media",
                    )
                    response = await wait_for_matching_media(remaining)
                    if response is None:
                        raise asyncio.TimeoutError

        stage = "downloading Telegram media"
        source_name = getattr(getattr(response, "file", None), "name", "") or "unknown"
        write_deezer_log(config, track, f"Downloading matched Telegram media: {source_name!r}")
        downloaded_path = await client.download_media(response, file=str(config.incoming_dir))
        if not downloaded_path:
            raise RuntimeError("Telegram returned an empty download path.")
        write_deezer_log(config, track, f"Telegram media downloaded to: {downloaded_path}")
        stage = "validating and moving the downloaded file"
        saved_name = accept_downloaded_file(
            config,
            track,
            downloaded_path,
            deezer_track_matches_text,
        )
        write_deezer_log(config, track, f"SUCCESS: saved as {saved_name!r}")
        return saved_name
    except Exception as exc:
        write_deezer_log(config, track, f"FAILED during {stage}: {type(exc).__name__}: {exc}")
        raise
    finally:
        client.remove_event_handler(handle_bot_message)
        write_deezer_log(config, track, f"Removed {config.bot_name} event handlers")


async def request_and_download(client: TelegramClient, config: Config, track: Track) -> str:
    bot_chat = telegram_chat_ref(config.bot_username)
    if config.bot_strategy == "deezer":
        return await request_and_download_deezer(client, config, track, bot_chat, allow_tracks_filter=True)
    if config.bot_strategy == "music_hunters":
        return await request_and_download_deezer(client, config, track, bot_chat, allow_tracks_filter=False)
    return await request_and_download_sqmp3(client, config, track, bot_chat)


def is_send_blocked_error(exc: Exception) -> bool:
    text = exception_text_chain(exc).lower()
    return (
        "banned from sending messages" in text
        or "write forbidden" in text
        or "you can't write in this chat" in text
        or "sendmessagerequest" in text and "banned" in text
    )


def is_safety_stop_error(exc: Exception) -> bool:
    if isinstance(exc, SafetyStopError):
        return True
    if isinstance(exc, (TrackRequestError, asyncio.TimeoutError)):
        return False
    return exception_class_indicates_safety_stop(exc) or text_indicates_safety_stop(exception_text_chain(exc))


def is_recoverable_track_error(exc: Exception) -> bool:
    if isinstance(exc, TrackRequestError):
        return True
    if isinstance(exc, asyncio.TimeoutError):
        return True
    return text_indicates_request_failure(exception_text_chain(exc))


async def sleep_before_next_request(config: Config, window_started_at: float) -> float:
    if config.long_rest_every_minutes > 0:
        elapsed_minutes = (time.monotonic() - window_started_at) / 60
        if elapsed_minutes >= config.long_rest_every_minutes:
            rest_minutes = random.randint(config.long_rest_min_minutes, config.long_rest_max_minutes)
            print(f"[REST] Request window reached {elapsed_minutes:.1f} minutes. Resting {rest_minutes} minutes.")
            await asyncio.sleep(rest_minutes * 60)
            return time.monotonic()

    delay = random.randint(config.min_delay, config.max_delay)
    print(f"[WAIT] Sleeping {delay} seconds before the next request.")
    await asyncio.sleep(delay)
    return window_started_at


def confirm_after_success_batch(config: Config, success_count: int) -> bool:
    if config.success_confirm_every <= 0:
        return True
    if success_count == 0 or success_count % config.success_confirm_every != 0:
        return True

    answer = input(f"[CONFIRM] 已连续成功下载 {success_count} 首，输入 yes 继续: ").strip().lower()
    if answer == "yes":
        print("[CONFIRM] Continuing download.")
        return True

    print("[STOP] Manual confirmation was not received. Stop this run.")
    return False


def is_track_in_current_download_folder(config: Config, track: Track) -> bool:
    return track.key in scan_download_dir(config.download_dir)


def record_request_failed_track(
    config: Config,
    failed_tracks: dict[tuple[str, str], Track],
    request_failed_tracks: dict[tuple[str, str], Track],
    track: Track,
    reason: str,
) -> None:
    write_log(config, "REQUEST_FAILED", track, f"({reason})")
    failed_tracks.pop(track.key, None)
    write_failed_file(config, failed_tracks)
    request_failed_tracks[track.key] = track
    write_request_failed_file(config, request_failed_tracks)


async def download_tracks(config: Config, queue: list[Track], failed_tracks: dict[tuple[str, str], Track]) -> None:
    if not queue:
        print("[DONE] Nothing to download.")
        return

    session_base = config.session_dir / config.session_name
    client = TelegramClient(str(session_base), config.api_id, config.api_hash)

    await client.connect()
    try:
        await ensure_authorized(client, config)

        total = len(queue)
        request_failed_tracks = parse_optional_track_file(config.request_failed_file)
        request_window_started = time.monotonic()
        consecutive_successes = 0
        for index, track in enumerate(queue, start=1):
            if is_track_in_current_download_folder(config, track):
                print(f"[{index}/{total}] SKIPPED (Already downloaded now): {track.query}")
                failed_tracks.pop(track.key, None)
                request_failed_tracks.pop(track.key, None)
                write_failed_file(config, failed_tracks)
                write_request_failed_file(config, request_failed_tracks)
                continue

            print(f"[{index}/{total}] Requesting: {track.query}")
            try:
                filename = await request_and_download(client, config, track)
                print(f"[{index}/{total}] SUCCESS: {filename}")
                write_log(config, "SUCCESS", track, f"(Saved as: {filename})")
                failed_tracks.pop(track.key, None)
                request_failed_tracks.pop(track.key, None)
                write_failed_file(config, failed_tracks)
                write_request_failed_file(config, request_failed_tracks)
                consecutive_successes += 1

                if not confirm_after_success_batch(config, consecutive_successes):
                    break
            except asyncio.TimeoutError:
                reason = "Timeout waiting for media"
                print(f"[{index}/{total}] REQUEST FAILED: {track.query} ({reason})")
                record_request_failed_track(config, failed_tracks, request_failed_tracks, track, reason)
                print(f"[SKIP] Recorded request failure and continuing: {config.request_failed_file}")
            except Exception as exc:
                reason = " ".join((str(exc) or type(exc).__name__).split())
                if is_safety_stop_error(exc):
                    print(f"[{index}/{total}] SAFETY STOP: {track.query} ({reason})")
                    write_log(config, "FAILED", track, f"({reason})")
                    failed_tracks[track.key] = track
                    write_failed_file(config, failed_tracks)
                    if config.stop_on_send_blocked and is_send_blocked_error(exc):
                        mark_send_blocked(config)
                        print("[STOP] Telegram refused message sending. Stop this run and retry later.")
                        print(f"[STOP] Cooldown marker written to: {config.blocked_file}")
                    else:
                        print("[STOP] Server/rate-limit/automation-risk response detected. Stop this run.")
                    print(f"[STOP] Remaining tracks are still listed in: {config.pending_file}")
                    break

                if is_recoverable_track_error(exc):
                    print(f"[{index}/{total}] REQUEST FAILED: {track.query} ({reason})")
                    record_request_failed_track(config, failed_tracks, request_failed_tracks, track, reason)
                    print(f"[SKIP] Recorded request failure and continuing: {config.request_failed_file}")
                else:
                    print(f"[{index}/{total}] FAILED: {track.query} ({reason})")
                    write_log(config, "FAILED", track, f"({reason})")
                    failed_tracks[track.key] = track
                    write_failed_file(config, failed_tracks)

                    if config.stop_on_send_blocked and is_send_blocked_error(exc):
                        mark_send_blocked(config)
                        print("[STOP] Telegram refused message sending. Stop this run and retry later.")
                        print(f"[STOP] Cooldown marker written to: {config.blocked_file}")
                        print(f"[STOP] Remaining tracks are still listed in: {config.pending_file}")
                        break

                    print(f"[STOP] Request failed with an unclassified error. Remaining tracks are still listed in: {config.pending_file}")
                    break

            if index < total:
                request_window_started = await sleep_before_next_request(config, request_window_started)
    finally:
        await client.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download playlist tracks from a Telegram music bot.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only build DownloadScript/NewDownload.txt; do not log in or download.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore the send-blocked cooldown marker and try sending again.",
    )
    parser.add_argument(
        "--reset-account",
        action="store_true",
        help="Reset local Telegram session and rewrite Telegram account settings in .env.",
    )
    parser.add_argument(
        "--reset-account-only",
        action="store_true",
        help="Reset local Telegram account settings and exit without downloading.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    app_config, app_config_path = load_app_config()

    if args.reset_account or args.reset_account_only or app_config.reset_account:
        reset_telegram_account(app_config_path, app_config)
        if args.reset_account_only:
            return

    if app_config.download_platform != "Telegram":
        raise SystemExit("[CONFIG] DownloadPlatform must be Telegram.")

    playlist_file = ensure_playlist_file(app_config)
    app_config = AppConfig(
        download_platform=app_config.download_platform,
        download_bot=app_config.download_bot,
        download_channel=app_config.download_channel,
        reset_account=app_config.reset_account,
        playlist_id=app_config.playlist_id,
        playlist_file=playlist_file,
        existing_list_file=app_config.existing_list_file,
    )

    config = load_config(app_config)
    ensure_directories(config)
    remove_expired_session(config)

    queue, failed_tracks = build_download_queue(config)
    if args.dry_run:
        print("[DRY-RUN] Skipping Telegram login and download.")
        return

    if not args.force and send_blocked_cooldown_active(config):
        return

    await download_tracks(config, queue, failed_tracks)
    write_download_engine(config)


if __name__ == "__main__":
    asyncio.run(main())
