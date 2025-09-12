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

# Game settings exposed to templates (env overrides supported)
GAME_SETTINGS = {
    "FIRST_CLUE_ID": int(os.getenv("FIRST_CLUE_ID", 1)),
    "FINAL_CLUE_ID": int(os.getenv("FINAL_CLUE_ID", 6)),
    "HINT_DELAY_SECONDS": int(os.getenv("HINT_DELAY_SECONDS", 20)),
}
