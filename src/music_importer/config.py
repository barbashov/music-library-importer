import os

MB_APP_NAME = "music-library-importer"
MB_APP_VERSION = "0.1.0"
MB_CONTACT_EMAIL = os.environ.get("MUSIC_IMPORTER_EMAIL", "")
MB_RATE_LIMIT_SECONDS = 1.0

LOSSLESS_EXTS = {".flac", ".ape", ".wav", ".aiff", ".wv", ".tta"}
LOSSY_EXTS = {".mp3", ".ogg", ".opus", ".wma", ".aac"}
ALL_AUDIO_EXTS = LOSSLESS_EXTS | LOSSY_EXTS

LOSSLESS_CODECS = {
    "flac",
    "alac",
    "ape",
    "wavpack",
    "tta",
    "pcm_s16le",
    "pcm_s16be",
    "pcm_s24le",
    "pcm_s24be",
    "pcm_s32le",
    "pcm_s32be",
    "pcm_f32le",
    "pcm_f64le",
}

UNSAFE_FILENAME_CHARS = '<>:"/\\|?*'

DEFAULT_COMPILATIONS_DIR = "Compilations"
