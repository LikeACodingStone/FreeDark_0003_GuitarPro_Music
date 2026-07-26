import argparse
import asyncio
import getpass
import json
import os
import random
import re
import shutil
import subprocess
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
DEFAULT_BLOCKED_FILE = PROJECT_ROOT / "DownloadScript" / "SendBlockedUntil.txt"
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "config.ini"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_MUSICN_SCRIPT = SCRIPT_DIR / "musicn_auto_downloader.mjs"

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

MUSICN_ALL_SERVICES = ("migu", "wangyi", "kuwo", "kugou")


@dataclass(frozen=True)
class AppConfig:
    download_platform: str
    reset_account: bool
    playlist_id: str
    playlist_file: Path
    existing_list_file: Path
    musicn_service: str
    musicn_services: tuple[str, ...]
    musicn_search_size: int


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
    bot_username: str
    playlist_file: Path
    existing_list_file: Path
    pending_file: Path
    failed_file: Path
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
    musicn_service: str
    musicn_services: tuple[str, ...]
    musicn_search_size: int
    musicn_script: Path


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


def parse_musicn_services(value: str) -> tuple[str, ...]:
    raw = value.strip().lower()
    if raw in {"auto", "all", "*"}:
        return MUSICN_ALL_SERVICES

    services: list[str] = []
    for item in re.split(r"[,;\s]+", raw):
        service = item.strip().lower()
        if not service:
            continue
        if service not in MUSICN_ALL_SERVICES:
            raise SystemExit(
                f"[CONFIG] Unsupported Musicn service: {service}. "
                f"Use one or more of: {', '.join(MUSICN_ALL_SERVICES)}."
            )
        if service not in services:
            services.append(service)

    if not services:
        raise SystemExit("[CONFIG] MusicnServices cannot be empty.")
    return tuple(services)


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
                reset_account=False,
                playlist_id="17961590701",
                playlist_file=default_playlist_file,
                existing_list_file=default_existing_list_file,
                musicn_service="migu",
                musicn_services=MUSICN_ALL_SERVICES,
                musicn_search_size=10,
            ),
        )

    values = {
        "DownloadPlatform": "Telegram",
        "ResetAccount": "False",
        "PlaylistId": "17961590701",
        "PlaylistFile": str(default_playlist_file),
        "ExistingListFile": str(default_existing_list_file),
        "MusicnService": "migu",
        "MusicnServices": "",
        "MusicnSearchSize": "10",
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
    elif normalized_platform == "musicn":
        platform = "Musicn"
    elif normalized_platform == "other":
        platform = "Other"
    else:
        raise SystemExit("[CONFIG] DownloadPlatform must be Telegram, Musicn, or Other.")

    musicn_services_raw = values["MusicnServices"].strip() or values["MusicnService"].strip()
    musicn_services = parse_musicn_services(musicn_services_raw)
    musicn_service = musicn_services[0]

    try:
        musicn_search_size = int(values["MusicnSearchSize"])
    except ValueError as exc:
        raise SystemExit("[CONFIG] MusicnSearchSize must be an integer.") from exc
    if musicn_search_size <= 0:
        raise SystemExit("[CONFIG] MusicnSearchSize must be greater than 0.")

    return (
        AppConfig(
            download_platform=platform,
            reset_account=parse_bool(values["ResetAccount"]),
            playlist_id=values["PlaylistId"].strip(),
            playlist_file=resolve_config_path(values["PlaylistFile"], config_path),
            existing_list_file=resolve_config_path(values["ExistingListFile"], config_path),
            musicn_service=musicn_service,
            musicn_services=musicn_services,
            musicn_search_size=musicn_search_size,
        ),
        config_path,
    )


def write_app_config(path: Path, app_config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"DownloadPlatform={app_config.download_platform}\n"
        f"ResetAccount={'True' if app_config.reset_account else 'False'}\n"
        f"PlaylistId={app_config.playlist_id}\n"
        f"PlaylistFile={app_config.playlist_file}\n"
        f"ExistingListFile={app_config.existing_list_file}\n"
        f"MusicnService={app_config.musicn_service}\n"
        f"MusicnServices={','.join(app_config.musicn_services)}\n"
        f"MusicnSearchSize={app_config.musicn_search_size}\n",
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

    api_id: int | None = None
    api_hash: str | None = None
    if app_config.download_platform == "Telegram":
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

    return Config(
        api_id=api_id,
        api_hash=api_hash,
        phone=env_value("TG_PHONE"),
        bot_username=env_value("TG_BOT_USERNAME", "SQMP3") or "SQMP3",
        playlist_file=app_config.playlist_file,
        existing_list_file=app_config.existing_list_file,
        pending_file=resolve_path(env_value("PENDING_FILE", str(DEFAULT_PENDING_FILE)) or DEFAULT_PENDING_FILE),
        failed_file=resolve_path(env_value("FAILED_FILE", str(DEFAULT_FAILED_FILE)) or DEFAULT_FAILED_FILE),
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
        musicn_service=app_config.musicn_service,
        musicn_services=app_config.musicn_services,
        musicn_search_size=app_config.musicn_search_size,
        musicn_script=resolve_path(env_value("MUSICN_SCRIPT", str(DEFAULT_MUSICN_SCRIPT)) or DEFAULT_MUSICN_SCRIPT),
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
        match = re.match(r"^\[(SUCCESS|FAILED|REJECTED)\]\s+(.+)$", raw_line.strip())
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
            statuses[track.key] = (status, track)

    return statuses


def build_download_queue(config: Config) -> tuple[list[Track], dict[tuple[str, str], Track]]:
    playlist_tracks = parse_playlist(config.playlist_file)
    known_tracks = parse_existing_list(config.existing_list_file)
    known_tracks.update(scan_download_dir(config.download_dir))
    log_statuses = parse_status_log(config.log_file)

    failed_from_log: dict[tuple[str, str], Track] = {}
    for key, (status, track) in log_statuses.items():
        if status != "SUCCESS":
            failed_from_log[key] = track

    retry_queue: list[Track] = []
    new_queue: list[Track] = []
    unresolved_failures: dict[tuple[str, str], Track] = {}
    seen_in_playlist: set[tuple[str, str]] = set()
    skipped_existing = 0
    skipped_duplicate = 0

    for track in playlist_tracks:
        key = track.key
        if key in seen_in_playlist:
            skipped_duplicate += 1
            continue
        seen_in_playlist.add(key)

        if key in known_tracks:
            skipped_existing += 1
            continue

        if key in failed_from_log:
            retry_queue.append(track)
            unresolved_failures[key] = track
        else:
            new_queue.append(track)

    queue = retry_queue + new_queue

    write_pending_file(config.pending_file, queue)
    write_track_file(config.failed_file, retry_queue)

    print(f"[PLAN] Playlist tracks parsed: {len(playlist_tracks)}")
    print(f"[PLAN] Known existing tracks: {len(known_tracks)}")
    print(f"[PLAN] Skipped already existing: {skipped_existing}")
    print(f"[PLAN] Skipped duplicate rows: {skipped_duplicate}")
    print(f"[PLAN] Retry failed tracks first: {len(retry_queue)}")
    print(f"[PLAN] Pending downloads: {len(queue)}")
    print(f"[PLAN] Pending list written to: {config.pending_file}")
    print(f"[PLAN] Failed retry list written to: {config.failed_file}")

    return queue, unresolved_failures


def write_pending_file(path: Path, tracks: list[Track]) -> None:
    write_track_file(path, tracks)


def write_track_file(path: Path, tracks: list[Track]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(track.query for track in tracks)
    if content:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def ensure_directories(config: Config) -> None:
    config.download_dir.mkdir(parents=True, exist_ok=True)
    config.incoming_dir.mkdir(parents=True, exist_ok=True)
    config.rejected_dir.mkdir(parents=True, exist_ok=True)
    config.session_dir.mkdir(parents=True, exist_ok=True)
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


def write_failed_file(config: Config, failed_tracks: dict[tuple[str, str], Track]) -> None:
    ordered = sorted(failed_tracks.values(), key=lambda item: normalize_piece(item.query))
    write_track_file(config.failed_file, ordered)


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


def message_matches_track(message, track: Track) -> bool:
    text_parts = [message.message or ""]

    document = getattr(message.media, "document", None) if message.media else None
    if document:
        for attr in document.attributes:
            for field in ("file_name", "title", "performer"):
                value = getattr(attr, field, "")
                if value:
                    text_parts.append(value)

    return track_matches_text(track, " ".join(text_parts))


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


def accept_downloaded_file(config: Config, track: Track, downloaded_path_text: str) -> str:
    downloaded_path = Path(downloaded_path_text)
    if not downloaded_path.exists():
        raise RuntimeError(f"Downloaded path does not exist: {downloaded_path_text}")

    if not track_matches_text(track, downloaded_path.name):
        rejected_path = move_to_rejected(config, downloaded_path)
        raise RuntimeError(f"Downloaded media did not match request; moved to rejected/{rejected_path.name}")

    target_path = config.download_dir / downloaded_path.name
    if target_path.exists():
        rejected_path = move_to_rejected(config, downloaded_path)
        raise RuntimeError(f"Duplicate filename already exists; moved to rejected/{rejected_path.name}")

    shutil.move(str(downloaded_path), str(target_path))
    return target_path.name


async def request_and_download(client: TelegramClient, config: Config, track: Track) -> str:
    loop = asyncio.get_running_loop()
    download_future = loop.create_future()

    @client.on(events.NewMessage(chats=config.bot_username))
    async def handle_new_message(event):
        if not event.message.media:
            return
        if not (event.message.audio or event.message.document):
            return
        if not message_matches_track(event.message, track):
            return
        if not download_future.done():
            download_future.set_result(event.message)

    try:
        await client.send_message(config.bot_username, f"/music {track.query}")
        matched_message = await asyncio.wait_for(download_future, timeout=config.response_timeout)
        downloaded_path = await client.download_media(matched_message, file=str(config.incoming_dir))
        if not downloaded_path:
            raise RuntimeError("Telegram returned an empty download path.")
        return accept_downloaded_file(config, track, downloaded_path)
    finally:
        client.remove_event_handler(handle_new_message)


def is_send_blocked_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "banned from sending messages" in text
        or "write forbidden" in text
        or "you can't write in this chat" in text
        or "sendmessagerequest" in text and "banned" in text
    )


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
        request_window_started = time.monotonic()
        consecutive_successes = 0
        for index, track in enumerate(queue, start=1):
            if is_track_in_current_download_folder(config, track):
                print(f"[{index}/{total}] SKIPPED (Already downloaded now): {track.query}")
                failed_tracks.pop(track.key, None)
                write_failed_file(config, failed_tracks)
                continue

            print(f"[{index}/{total}] Requesting: {track.query}")
            try:
                filename = await request_and_download(client, config, track)
                print(f"[{index}/{total}] SUCCESS: {filename}")
                write_log(config, "SUCCESS", track, f"(Saved as: {filename})")
                failed_tracks.pop(track.key, None)
                write_failed_file(config, failed_tracks)
                consecutive_successes += 1

                if not confirm_after_success_batch(config, consecutive_successes):
                    break
            except asyncio.TimeoutError:
                print(f"[{index}/{total}] TIMEOUT: {track.query}")
                write_log(config, "FAILED", track, "(Timeout waiting for media)")
                failed_tracks[track.key] = track
                write_failed_file(config, failed_tracks)
                print(f"[STOP] Request failed. Remaining tracks are still listed in: {config.pending_file}")
                break
            except Exception as exc:
                print(f"[{index}/{total}] FAILED: {track.query} ({exc})")
                write_log(config, "FAILED", track, f"({exc})")
                failed_tracks[track.key] = track
                write_failed_file(config, failed_tracks)

                if config.stop_on_send_blocked and is_send_blocked_error(exc):
                    mark_send_blocked(config)
                    print("[STOP] Telegram refused message sending. Stop this run and retry later.")
                    print(f"[STOP] Cooldown marker written to: {config.blocked_file}")
                    print(f"[STOP] Remaining tracks are still listed in: {config.pending_file}")
                    break

                print(f"[STOP] Request failed. Remaining tracks are still listed in: {config.pending_file}")
                break

            if index < total:
                request_window_started = await sleep_before_next_request(config, request_window_started)
    finally:
        await client.disconnect()


def request_and_download_musicn_service(config: Config, track: Track, service: str) -> str:
    if not config.musicn_script.is_file():
        raise RuntimeError(f"musicn helper script not found: {config.musicn_script}")

    command = [
        "node",
        str(config.musicn_script),
        "--service",
        service,
        "--size",
        str(config.musicn_search_size),
        "--incoming-dir",
        str(config.incoming_dir),
        "--artist",
        track.artist,
        "--title",
        track.title,
        "--query",
        track.query,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(120, config.response_timeout + 60),
    )

    output_lines = [line for line in result.stdout.splitlines() if line.strip()]
    payload = output_lines[-1] if output_lines else "{}"
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        details = result.stderr.strip() or result.stdout.strip() or "No output from musicn helper."
        raise RuntimeError(f"musicn helper returned invalid output: {details}") from exc

    if result.returncode != 0 or not data.get("ok"):
        error = data.get("error") or result.stderr.strip() or "musicn helper failed."
        raise RuntimeError(error)

    return accept_downloaded_file(config, track, data["path"])


def request_and_download_musicn(config: Config, track: Track) -> tuple[str, str]:
    errors: list[str] = []
    for service in config.musicn_services:
        print(f"[MUSICN] Trying {service}: {track.query}")
        try:
            filename = request_and_download_musicn_service(config, track, service)
            return filename, service
        except Exception as exc:
            message = f"{service}: {exc}"
            print(f"[MUSICN] {message}")
            errors.append(message)

    raise RuntimeError("All Musicn services failed: " + " | ".join(errors))


async def download_tracks_musicn(config: Config, queue: list[Track], failed_tracks: dict[tuple[str, str], Track]) -> None:
    if not queue:
        print("[DONE] Nothing to download.")
        return

    total = len(queue)
    request_window_started = time.monotonic()
    consecutive_successes = 0

    for index, track in enumerate(queue, start=1):
        if is_track_in_current_download_folder(config, track):
            print(f"[{index}/{total}] SKIPPED (Already downloaded now): {track.query}")
            failed_tracks.pop(track.key, None)
            write_failed_file(config, failed_tracks)
            continue

        print(f"[{index}/{total}] Musicn requesting: {track.query}")
        try:
            filename, service = request_and_download_musicn(config, track)
            print(f"[{index}/{total}] SUCCESS via {service}: {filename}")
            write_log(config, "SUCCESS", track, f"(Musicn/{service} saved as: {filename})")
            failed_tracks.pop(track.key, None)
            write_failed_file(config, failed_tracks)
            consecutive_successes += 1

            if not confirm_after_success_batch(config, consecutive_successes):
                break
        except Exception as exc:
            print(f"[{index}/{total}] FAILED: {track.query} ({exc})")
            write_log(config, "FAILED", track, f"(Musicn error: {exc})")
            failed_tracks[track.key] = track
            write_failed_file(config, failed_tracks)
            print(f"[STOP] Request failed. Remaining tracks are still listed in: {config.pending_file}")
            break

        if index < total:
            request_window_started = await sleep_before_next_request(config, request_window_started)


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

    if (args.reset_account or args.reset_account_only or app_config.reset_account) and app_config.download_platform == "Telegram":
        reset_telegram_account(app_config_path, app_config)
        if args.reset_account_only:
            return
    elif args.reset_account_only:
        print("[ACCOUNT] reset-account is only needed for Telegram. No account reset was performed.")
        return
    elif app_config.reset_account:
        print("[ACCOUNT] ResetAccount=True is ignored because DownloadPlatform is not Telegram.")
        set_config_reset_account_false(app_config_path)

    if app_config.download_platform not in {"Telegram", "Musicn", "Other"}:
        raise SystemExit("[CONFIG] DownloadPlatform must be Telegram, Musicn, or Other.")

    playlist_file = ensure_playlist_file(app_config)
    app_config = AppConfig(
        download_platform=app_config.download_platform,
        reset_account=app_config.reset_account,
        playlist_id=app_config.playlist_id,
        playlist_file=playlist_file,
        existing_list_file=app_config.existing_list_file,
        musicn_service=app_config.musicn_service,
        musicn_services=app_config.musicn_services,
        musicn_search_size=app_config.musicn_search_size,
    )

    config = load_config(app_config)
    ensure_directories(config)
    remove_expired_session(config)

    queue, failed_tracks = build_download_queue(config)
    if args.dry_run:
        print("[DRY-RUN] Skipping Telegram login and download.")
        return

    if app_config.download_platform == "Telegram":
        if not args.force and send_blocked_cooldown_active(config):
            return

        await download_tracks(config, queue, failed_tracks)
        return

    print(f"[PLATFORM] DownloadPlatform={app_config.download_platform}; using Musicn backend.")
    await download_tracks_musicn(config, queue, failed_tracks)


if __name__ == "__main__":
    asyncio.run(main())
