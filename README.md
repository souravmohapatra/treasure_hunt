# Treasure Hunt (Flask Scaffold)

A minimal, server-rendered Flask app scaffolding for a treasure-hunt game. This step provides a runnable server with stub routes, in-memory placeholders (no database yet), Jinja templates, and Bootstrap styling via CDN. It includes a simple session-based team name flow, deterministic clue variants, and a protected admin placeholder.

## Project structure

- app.py
- config.py
- requirements.txt
- .gitignore
- README.md
- templates/
  - base.html
  - index.html
  - clue.html
  - leaderboard.html
  - finish.html
  - admin.html
  - setup.html
- static/
  - main.css

## Quick start

- Prerequisites: Python 3.10+ recommended.

- Create and activate a virtual environment:
    - macOS/Linux:
        - python -m venv venv
        - source venv/bin/activate
    - Windows (PowerShell):
        - python -m venv venv
        - .\venv\Scripts\Activate.ps1
      or Windows (CMD):
        - venv\Scripts\activate.bat

- Install dependencies:
    - pip install -r requirements.txt

- Set environment variables:
    - macOS/Linux:
        - export FLASK_APP=app.py
        - export FLASK_DEBUG=1        (optional)
        - export SECRET_KEY="dev-secret"
        - export ADMIN_PASSWORD="admin"
    - Windows (PowerShell):
        - $env:FLASK_APP="app.py"
        - $env:FLASK_DEBUG="1"        (optional)
        - $env:SECRET_KEY="dev-secret"
        - $env:ADMIN_PASSWORD="admin"

- Run the server:
    - flask run --host=0.0.0.0 --port=8080

- Visit:
    - http://localhost:8080

## Routes overview

- GET / — Landing page with a team name form. Stores `team_name` in a signed session cookie when you start.
- POST /start — Validates and stores `team_name`; redirects to the first clue.
- GET /clue/<id> — Placeholder clue page. Variant is chosen deterministically from `team_name` and `id` (A or B). Valid ids: 1–6; outside redirects to /finish.
- POST /submit/<id> — Advances to the next clue, or to /finish when `id` is 6.
- POST /hint/<id> — Flashes “Hint used…” and returns to the same clue.
- GET /leaderboard — Placeholder table (no real scoring yet).
- GET /finish — Celebratory placeholder. Notes that scoring arrives in Step 2.
- GET /admin — Basic Auth protected using `ADMIN_PASSWORD`. Shows a read-only placeholder dashboard. If password is unset or wrong, returns 401 and the browser will prompt.
- GET /setup — Read-only placeholder page; editable setup arrives in Step 4.
- GET /healthz — Returns 200 OK with body “ok”.

## Behavior notes

- Session and security: Uses Flask’s built-in session with `SECRET_KEY` for signing. `team_name` is stored in the session.
- Deterministic variant function (in `app.py`):
  - Uses SHA-256 of `f"{team_name}:{clue_id}"` and picks “A” if the hex integer is even, else “B”.
- If `team_name` isn’t set and a clue is visited, you’ll be redirected to `/` with a flash message.
- Valid clues are 1..6; out-of-range redirects to `/finish`.
- No database yet—everything is in-memory.

## Configuration

- `SECRET_KEY`: Secret for session signing (default: “dev-secret” for dev).
- `ADMIN_PASSWORD`: Basic Auth password for `/admin` (default: “admin” for dev).
- `GAME_SETTINGS` (in `config.py`, template-readable):
  - `FIRST_CLUE_ID` = 1
  - `FINAL_CLUE_ID` = 6
  - `HINT_DELAY_SECONDS` = 20

You can override any of these via environment variables.

## Next steps

Step 2 will add SQLite models and real scoring.

## Step 2: Database migration

The app auto-creates the SQLite database and seeds dummy clues on startup. If you prefer to run this manually:

```
flask shell
from models import db
db.create_all()
```

## Docker

Prerequisites
- Docker and Docker Compose installed.

Build and run
```
docker compose up -d --build
# visit http://localhost:18080
```

Environment variables
- SECRET_KEY: required in production (used for session signing)
- ADMIN_PASSWORD: required to access /admin
- Optional:
  - FIRST_CLUE_ID (default 1)
  - FINAL_CLUE_ID (default 6)
  - HINT_DELAY_SECONDS (default 20)

Persisted data
- SQLite database is stored on the host at ./data/game.db (mounted into the container at /app/data).

Stop and remove
```
docker compose down
```

Health check
```
curl http://localhost:18080/healthz
# should return: ok
```

## Setup UI

Admin-only route: /setup

- Protected by the same Basic Auth as /admin (use your ADMIN_PASSWORD). Your browser will prompt for credentials.

What you can do

- Manage clues:
  - List all clues in order (ID, Title, Order, Final).
  - Add, Edit, Delete clues.
  - Reorder by editing the “Order index” field.
  - Preview Variant A or B via buttons (opens /clue/<id>?variant=A or B in a new tab; does not affect DB).
- Global settings:
  - Hint delay (seconds), Points per solve, Penalty per hint, Penalty per skip.
  - Time penalty: window size in seconds and points lost per window.
  - Values are stored in the Config table; when missing, defaults are used.
- Import/Export configuration:
  - Export downloads a JSON document with all clues and config settings.
  - Import uploads a JSON file and overwrites existing clues and config.

JSON format

```
{
  "clues": [
    {
      "id": 1,
      "title": "Clue 1",
      "body_variant_a": "This is Clue 1 (A)",
      "body_variant_b": "This is Clue 1 (B)",
      "answer_type": "tap | text | mcq",
      "answer_payload": "",            // text: comma-separated; mcq: JSON array; tap: empty
      "hint_text": "Hint for Clue 1",
      "order_index": 1,
      "is_final": false
    }
  ],
  "config": {
    "HINT_DELAY_SECONDS": 20,
    "POINTS_SOLVE": 10,
    "PENALTY_HINT": 3,
    "PENALTY_SKIP": 8,
    "TIME_PENALTY_WINDOW_SECONDS": 120,
    "TIME_PENALTY_POINTS": 1
  }
}
```

Notes

- CSRF protection is enabled for setup forms (Flask-WTF).
- Scoring logic is unchanged; settings simply control penalties and delay values.
- Preview links do not record progress or modify the database.