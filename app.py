from __future__ import annotations

import base64
import hashlib
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional, Tuple

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    Response,
)

from models import db, Team, Clue, Progress, init_app_db

# App setup
app = Flask(__name__)
# Load configuration from config.py (SECRET_KEY, ADMIN_PASSWORD, GAME_SETTINGS, SQLAlchemy)
app.config.from_object("config")

# Ensure data/ exists for SQLite volume mapping
os.makedirs("data", exist_ok=True)

# Initialize database and seed default clues
init_app_db(app)


# Utilities and helpers
def choose_variant(team_token: str, clue_id: int) -> str:
    """
    Deterministic variant chooser using team token and clue id.
    Returns "A" if even, else "B".
    """
    key = f"{team_token}:{clue_id}".encode("utf-8")
    h = hashlib.sha256(key).hexdigest()
    return "A" if int(h, 16) % 2 == 0 else "B"


def get_game_settings() -> dict:
    return app.config.get("GAME_SETTINGS", {})


def _get_first_clue() -> Optional[Clue]:
    return Clue.query.order_by(Clue.order_index.asc()).first()


def _get_final_clue() -> Optional[Clue]:
    return Clue.query.filter_by(is_final=True).order_by(Clue.order_index.desc()).first()


def _get_next_clue(current: Clue) -> Optional[Clue]:
    return (
        Clue.query.filter(Clue.order_index > current.order_index)
        .order_by(Clue.order_index.asc())
        .first()
    )


def get_current_team_record() -> Optional[Team]:
    team_id = session.get("team_id")
    token = session.get("team_token")
    if not team_id or not token:
        return None
    team = Team.query.get(team_id)
    if team and team.token == token:
        return team
    return None


def get_current_team_name() -> Optional[str]:
    team = get_current_team_record()
    if team:
        return team.name
    # fallback to any stored name (pre-Step2)
    return session.get("team_name")


def unauthorized_response() -> Response:
    # Prompt browser for Basic Auth credentials
    return Response(
        "Unauthorized",
        401,
        {"WWW-Authenticate": 'Basic realm="Admin"'},
    )


def extract_basic_auth_password(req) -> Optional[str]:
    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return None
    try:
        encoded = auth_header.split(" ", 1)[1]
        decoded = base64.b64decode(encoded).decode("utf-8")
        # Expect "username:password"
        if ":" in decoded:
            return decoded.split(":", 1)[1]
        return None
    except Exception:
        return None


@app.context_processor
def inject_globals():
    return {
        "GAME_SETTINGS": get_game_settings(),
        "current_team": get_current_team_name(),
    }


def _ensure_progress(team: Team, clue: Clue) -> Progress:
    """Get or create a Progress row for the given team and clue."""
    prog = Progress.query.filter_by(team_id=team.id, clue_id=clue.id).first()
    if prog:
        return prog
    variant = choose_variant(team.token, clue.id)
    prog = Progress(
        team_id=team.id,
        clue_id=clue.id,
        variant=variant,
        started_at=datetime.utcnow(),
        used_hint=False,
        skipped=False,
    )
    db.session.add(prog)
    db.session.commit()
    return prog


def _format_duration(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _compute_score(team: Team) -> Tuple[int, int, int, int, Optional[timedelta]]:
    """Returns (score, solved_count, hint_count, skip_count, elapsed)."""
    entries = Progress.query.filter_by(team_id=team.id).all()
    solved_count = sum(1 for p in entries if p.solved_at is not None)
    hint_count = sum(1 for p in entries if p.used_hint)
    skip_count = sum(1 for p in entries if p.skipped)
    base = 10 * solved_count - 3 * hint_count - 8 * skip_count
    elapsed: Optional[timedelta] = None
    if team.completed_at:
        elapsed = team.completed_at - team.created_at
        # -1 per 2 full minutes elapsed
        penalty = int(elapsed.total_seconds() // 120)
        base -= penalty
    return base, solved_count, hint_count, skip_count, elapsed


# Routes
@app.get("/")
def index():
    return render_template("index.html")


@app.post("/start")
def start():
    team_name = (request.form.get("team_name") or "").strip()
    if not team_name:
        flash("Please enter a team name.", "danger")
        return redirect(url_for("index"))

    # Create new team with unique token (even if name collides, token differentiates)
    token = uuid.uuid4().hex
    team = Team(name=team_name, token=token, created_at=datetime.utcnow())
    db.session.add(team)
    db.session.commit()

    # Persist identity in session
    session["team_id"] = team.id
    session["team_token"] = team.token
    session["team_name"] = team.name  # for header display

    first = _get_first_clue()
    if not first:
        flash("No clues configured.", "warning")
        return redirect(url_for("index"))
    return redirect(url_for("clue", id=first.id))


@app.get("/clue/<int:id>")
def clue(id: int):
    team = get_current_team_record()
    if not team:
        flash("Pick a team name to start.", "warning")
        return redirect(url_for("index"))

    clue_obj = Clue.query.get(id)
    if not clue_obj:
        # If clue doesn't exist, finish the game
        return redirect(url_for("finish"))

    prog = _ensure_progress(team, clue_obj)

    # Body based on assigned variant
    body_text = clue_obj.body_variant_a if prog.variant == "A" else clue_obj.body_variant_b

    # If hint was already used, show it again via flash for visibility in current templates
    if prog.used_hint and clue_obj.hint_text:
        flash(f"Hint: {clue_obj.hint_text}", "warning")

    return render_template(
        "clue.html",
        clue_id=clue_obj.id,
        variant=prog.variant,
        title=clue_obj.title,
        body_text=body_text,
        hint_text=clue_obj.hint_text,
        answer_type=clue_obj.answer_type,
        hint_revealed=prog.used_hint,
    )


@app.post("/submit/<int:id>")
def submit(id: int):
    team = get_current_team_record()
    if not team:
        flash("Pick a team name to start.", "warning")
        return redirect(url_for("index"))

    clue_obj = Clue.query.get(id)
    if not clue_obj:
        return redirect(url_for("finish"))

    prog = _ensure_progress(team, clue_obj)

    if clue_obj.answer_type == "text":
        submitted = (request.form.get("answer") or "").strip().lower()
        expected = (clue_obj.answer_payload or "").strip().lower()
        if submitted != expected:
            flash("Try again.", "danger")
            return redirect(url_for("clue", id=clue_obj.id))

    # For tap or correct text, mark solved
    if not prog.solved_at:
        prog.solved_at = datetime.utcnow()
    db.session.commit()

    if clue_obj.is_final:
        if not team.completed_at:
            team.completed_at = datetime.utcnow()
            db.session.commit()
        return redirect(url_for("finish"))

    # Next clue by order
    next_clue = _get_next_clue(clue_obj)
    if not next_clue:
        # No next -> finish
        if not team.completed_at:
            team.completed_at = datetime.utcnow()
            db.session.commit()
        return redirect(url_for("finish"))

    return redirect(url_for("clue", id=next_clue.id))


@app.post("/hint/<int:id>")
def hint(id: int):
    team = get_current_team_record()
    if not team:
        flash("Pick a team name to start.", "warning")
        return redirect(url_for("index"))

    clue_obj = Clue.query.get(id)
    if not clue_obj:
        return redirect(url_for("finish"))

    prog = _ensure_progress(team, clue_obj)
    if not prog.used_hint:
        prog.used_hint = True
        db.session.commit()

    # Surface the hint via flash so current template shows it
    if clue_obj.hint_text:
        flash(f"Hint: {clue_obj.hint_text}", "warning")
    flash(f"Hint used for Clue {id}", "info")
    return redirect(url_for("clue", id=id))


@app.post("/skip/<int:id>")
def skip(id: int):
    team = get_current_team_record()
    if not team:
        flash("Pick a team name to start.", "warning")
        return redirect(url_for("index"))

    clue_obj = Clue.query.get(id)
    if not clue_obj:
        return redirect(url_for("finish"))

    prog = _ensure_progress(team, clue_obj)
    prog.skipped = True
    if not prog.solved_at:
        prog.solved_at = datetime.utcnow()
    db.session.commit()

    if clue_obj.is_final:
        if not team.completed_at:
            team.completed_at = datetime.utcnow()
            db.session.commit()
        return redirect(url_for("finish"))

    next_clue = _get_next_clue(clue_obj)
    if not next_clue:
        if not team.completed_at:
            team.completed_at = datetime.utcnow()
            db.session.commit()
        return redirect(url_for("finish"))

    return redirect(url_for("clue", id=next_clue.id))


@app.get("/leaderboard")
def leaderboard():
    teams = Team.query.order_by(Team.created_at.asc()).all()
    total_clues = Clue.query.count()
    rows = []
    for team in teams:
        score, solved_count, hint_count, skip_count, elapsed = _compute_score(team)
        if team.completed_at and elapsed is not None:
            time_display = _format_duration(elapsed)
        else:
            # Show current progress for unfinished teams
            time_display = f"Clue {min(solved_count + 1, total_clues)} of {total_clues}"
        rows.append(
            {
                "team": team.name,
                "score": score,
                "time": time_display,
                "completed_at": team.completed_at,
                "elapsed": elapsed.total_seconds() if elapsed is not None else None,
            }
        )

    # Sort by score desc, then by fastest completion time for finished teams; unfinished go after
    def sort_key(row):
        completed = row["completed_at"] is not None
        elapsed_key = row.get("elapsed", None)
        if not (completed and elapsed_key is not None):
            elapsed_key = float("inf")
        return (-row["score"], 0 if completed else 1, elapsed_key)

    rows.sort(key=sort_key)

    # Assign ranks
    for idx, row in enumerate(rows, start=1):
        row["rank"] = idx

    note = None
    return render_template("leaderboard.html", rows=rows, note=note)


@app.get("/finish")
def finish():
    return render_template("finish.html")


@app.get("/admin")
def admin():
    admin_password_expected = app.config.get("ADMIN_PASSWORD", "")
    provided_password = extract_basic_auth_password(request)

    if not admin_password_expected:
        return unauthorized_response()
    if provided_password != admin_password_expected:
        return unauthorized_response()

    # Live progress summary (passed to template; current template shows basic info)
    team_rows = []
    total_clues = Clue.query.count()
    for t in Team.query.order_by(Team.created_at.asc()).all():
        _, solved_count, hint_count, skip_count, elapsed = _compute_score(t)
        current_clue_num = min(solved_count + 1, total_clues)
        team_rows.append(
            {
                "name": t.name,
                "current": f"{current_clue_num}/{total_clues}" if not t.completed_at else "Finished",
                "hints": hint_count,
                "skips": skip_count,
                "started_at": t.created_at,
                "completed_at": t.completed_at,
            }
        )

    info = {
        "admin_password_set": bool(admin_password_expected),
        "active_teams": Team.query.count(),
        "teams": team_rows,
    }
    return render_template("admin.html", **info)


@app.post("/admin/reset")
def admin_reset():
    admin_password_expected = app.config.get("ADMIN_PASSWORD", "")
    provided_password = extract_basic_auth_password(request)
    if not admin_password_expected or provided_password != admin_password_expected:
        return unauthorized_response()

    # Clear Teams and Progress, keep Clues
    Progress.query.delete()
    Team.query.delete()
    db.session.commit()
    flash("Game reset. All teams and progress cleared.", "warning")
    return redirect(url_for("admin"))


@app.get("/setup")
def setup():
    return render_template("setup.html")


@app.get("/healthz")
def healthz():
    return Response("ok", status=200, mimetype="text/plain")


# Allow running with `python app.py` (optional; flask run is preferred)
if __name__ == "__main__":
    # Default to 0.0.0.0:8080 to match README instructions
    app.run(host="0.0.0.0", port=8080, debug=bool(app.config.get("DEBUG", False)))
