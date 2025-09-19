import os


def _get_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "t", "yes", "y", "on")


# Secret key for session signing
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")

# Simple admin password (Basic Auth). For dev, default to "admin".
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

# Optional debug flag (used when running via `python app.py`)
DEBUG = _get_bool("FLASK_DEBUG", False) or _get_bool("DEBUG", False)

# Data directory and SQLAlchemy settings
# Place DB under top-level ./data so it persists via Docker volume mount.
DATA_DIR = os.getenv("DATA_DIR", os.path.abspath(os.path.join(os.getcwd(), "data")))
os.makedirs(DATA_DIR, exist_ok=True)

# SQLite file stored at ./data/game.db
SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(DATA_DIR, "game.db")
SQLALCHEMY_TRACK_MODIFICATIONS = False

# CSRF and proxy tolerance:
# By default, relax strict HTTPS referrer checking (better for LAN reverse-proxy with self-signed certs).
# Set WTF_CSRF_SSL_STRICT=1 in the environment to enforce strict referer/host checks in production.
WTF_CSRF_SSL_STRICT = _get_bool("WTF_CSRF_SSL_STRICT", False)

# Optionally set a comma-separated list of trusted origins (host[:port]) if you want strict mode with a proxy.
_WTF_TRUSTED = os.getenv("WTF_CSRF_TRUSTED_ORIGINS")
if _WTF_TRUSTED:
    WTF_CSRF_TRUSTED_ORIGINS = [h.strip() for h in _WTF_TRUSTED.split(",") if h.strip()]

# Game settings exposed to templates (env overrides supported)
GAME_SETTINGS = {
    "FIRST_CLUE_ID": int(os.getenv("FIRST_CLUE_ID", 1)),
    "FINAL_CLUE_ID": int(os.getenv("FINAL_CLUE_ID", 6)),
    "HINT_DELAY_SECONDS": int(os.getenv("HINT_DELAY_SECONDS", 20)),
}
