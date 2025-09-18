from __future__ import annotations

import base64
import hashlib
import os
import uuid
import json
import io
import csv
import qrcode
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
    send_from_directory,
)

from werkzeug.utils import secure_filename
from PIL import Image
from flask_wtf import CSRFProtect
from forms import ClueForm, SettingsForm, CONFIG_KEY_HINT_DELAY_SECONDS, CONFIG_KEY_POINTS_SOLVE, CONFIG_KEY_PENALTY_HINT, CONFIG_KEY_PENALTY_SKIP, CONFIG_KEY_TIME_PENALTY_WINDOW_SECONDS, CONFIG_KEY_TIME_PENALTY_POINTS
from models import db, Team, Clue, Progress, Config, init_app_db, generate_readable_slug

# App setup
app = Flask(__name__)
# Load configuration from config.py (SECRET_KEY, ADMIN_PASSWORD, GAME_SETTINGS, SQLAlchemy)
app.config.from_object("config")
# Limit uploads to 2 MB
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

# Ensure data/ exists for SQLite volume mapping and uploads dir
os.makedirs("data", exist_ok=True)
os.makedirs(os.path.join("data", "uploads"), exist_ok=True)
csrf = CSRFProtect(app)

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
    started_cfg = Config.query.get("GAME_STARTED_AT")
    return {
        "GAME_SETTINGS": get_game_settings(),
        "current_team": get_current_team_name(),
        "game_started": bool(started_cfg and (started_cfg.value or "").strip()),
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
    wrong_attempts = sum((p.wrong_attempts or 0) for p in entries if getattr(p.clue, "answer_type", "").lower() == "mcq")
    base = 10 * solved_count - 3 * hint_count - 8 * skip_count - 2 * wrong_attempts
    elapsed: Optional[timedelta] = None
    if team.completed_at:
        # If a global game start exists, use it to compute elapsed; else fall back to team start
        start_dt: Optional[datetime] = None
        cfg = Config.query.get("GAME_STARTED_AT")
        if cfg and (cfg.value or "").strip():
            try:
                start_dt = datetime.fromisoformat(cfg.value.strip())
            except Exception:
                start_dt = None
        baseline = start_dt or team.created_at
        elapsed = team.completed_at - baseline
        # -1 per 2 full minutes elapsed
        penalty = int(elapsed.total_seconds() // 120)
        base -= penalty
    return base, solved_count, hint_count, skip_count, elapsed


# Routes
@app.get("/")
def index():
    total = Clue.query.count()
    started_cfg = Config.query.get("GAME_STARTED_AT")
    game_started = bool(started_cfg and (started_cfg.value or "").strip())
    return render_template("index.html", total_clues=total, game_started=game_started)


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

    # If the game hasn't been started by an admin, keep team on the landing page
    started_cfg = Config.query.get("GAME_STARTED_AT")
    if not (started_cfg and (started_cfg.value or "").strip()):
        flash("Waiting for the game to start. Please standby.", "info")
        return redirect(url_for("index"))

    return redirect(url_for("clue", id=first.id))


@app.get("/clue/<int:id>")
def clue(id: int):
    # Preview support: /clue/<id>?variant=A|B renders without affecting DB/session
    preview_variant = (request.args.get("variant") or "").strip().upper()
    if preview_variant in ("A", "B"):
        clue_obj = Clue.query.get(id)
        if not clue_obj:
            return redirect(url_for("finish"))
        body_text = clue_obj.body_variant_a if preview_variant == "A" else clue_obj.body_variant_b
        mcq_options = None
        try:
            if (clue_obj.answer_type or "").lower() == "mcq":
                import json as _json
                _opts = _json.loads(clue_obj.answer_payload or "[]")
                mcq_options = [str(o) for o in _opts if isinstance(o, str)]
        except Exception:
            mcq_options = []
        return render_template(
            "clue.html",
            clue_id=clue_obj.id,
            variant=preview_variant,
            title=clue_obj.title,
            body_text=body_text,
            hint_text=clue_obj.hint_text,
            answer_type=clue_obj.answer_type,
            hint_revealed=False,
            clue=clue_obj,
            mcq_options=mcq_options,
        )

    # If this is a tap-style clue with a slug, redirect to the NFC-friendly URL
    clue_obj = Clue.query.get(id)
    if clue_obj and clue_obj.answer_type == "tap" and clue_obj.slug:
        return redirect(url_for("clue_by_slug", slug=clue_obj.slug))

    team = get_current_team_record()
    if not team:
        flash("Pick a team name to start.", "warning")
        return redirect(url_for("index"))

    # Gate clues until admin starts the game
    started_cfg = Config.query.get("GAME_STARTED_AT")
    if not (started_cfg and (started_cfg.value or "").strip()):
        flash("The game has not started yet. Please wait on the landing page.", "warning")
        return redirect(url_for("index"))

    if not clue_obj:
        # If clue doesn't exist, finish the game
        return redirect(url_for("finish"))

    prog = _ensure_progress(team, clue_obj)

    # Body based on assigned variant
    body_a = (clue_obj.body_variant_a or "").strip()
    body_b = (clue_obj.body_variant_b or "").strip()
    display_variant = prog.variant
    if display_variant == "A":
        body_text = body_a if body_a else (body_b if body_b else "")
        if not body_a and body_b:
            display_variant = "B"
    else:
        body_text = body_b if body_b else (body_a if body_a else "")
        if not body_b and body_a:
            display_variant = "A"

    # If hint was already used, show it again via flash for visibility in current templates
    if prog.used_hint and clue_obj.hint_text:
        flash(f"Hint: {clue_obj.hint_text}", "warning")

    mcq_options = None
    try:
        if (clue_obj.answer_type or "").lower() == "mcq":
            import json as _json
            _opts = _json.loads(clue_obj.answer_payload or "[]")
            mcq_options = [str(o) for o in _opts if isinstance(o, str)]
    except Exception:
        mcq_options = []
    return render_template(
        "clue.html",
        clue_id=clue_obj.id,
        variant=display_variant,
        title=clue_obj.title,
        body_text=body_text,
        hint_text=clue_obj.hint_text,
        answer_type=clue_obj.answer_type,
        hint_revealed=prog.used_hint,
        clue=clue_obj,
        mcq_options=mcq_options,
    )


@app.get("/<slug>")
def clue_by_slug(slug: str):
    """
    NFC-friendly route for tap-style clues.
    Renders the clue identified by its slug (readable phrase).
    """
    # Preview support: /<slug>?variant=A|B
    preview_variant = (request.args.get("variant") or "").strip().upper()
    clue_obj = Clue.query.filter_by(slug=slug).first()
    if not clue_obj:
        return redirect(url_for("finish"))

    if preview_variant in ("A", "B"):
        body_text = clue_obj.body_variant_a if preview_variant == "A" else clue_obj.body_variant_b
        mcq_options = None
        try:
            if (clue_obj.answer_type or "").lower() == "mcq":
                import json as _json
                _opts = _json.loads(clue_obj.answer_payload or "[]")
                mcq_options = [str(o) for o in _opts if isinstance(o, str)]
        except Exception:
            mcq_options = []
        return render_template(
            "clue.html",
            clue_id=clue_obj.id,
            variant=preview_variant,
            title=clue_obj.title,
            body_text=body_text,
            hint_text=clue_obj.hint_text,
            answer_type=clue_obj.answer_type,
            hint_revealed=False,
            clue=clue_obj,
            mcq_options=mcq_options,
        )

    team = get_current_team_record()
    if not team:
        flash("Pick a team name to start.", "warning")
        return redirect(url_for("index"))

    # Gate clues until admin starts the game
    started_cfg = Config.query.get("GAME_STARTED_AT")
    if not (started_cfg and (started_cfg.value or "").strip()):
        flash("The game has not started yet. Please wait on the landing page.", "warning")
        return redirect(url_for("index"))

    # Ensure progress and render according to assigned variant
    prog = _ensure_progress(team, clue_obj)
    body_a = (clue_obj.body_variant_a or "").strip()
    body_b = (clue_obj.body_variant_b or "").strip()
    display_variant = prog.variant
    if display_variant == "A":
        body_text = body_a if body_a else (body_b if body_b else "")
        if not body_a and body_b:
            display_variant = "B"
    else:
        body_text = body_b if body_b else (body_a if body_a else "")
        if not body_b and body_a:
            display_variant = "A"

    if prog.used_hint and clue_obj.hint_text:
        flash(f"Hint: {clue_obj.hint_text}", "warning")

    mcq_options = None
    try:
        if (clue_obj.answer_type or "").lower() == "mcq":
            import json as _json
            _opts = _json.loads(clue_obj.answer_payload or "[]")
            mcq_options = [str(o) for o in _opts if isinstance(o, str)]
    except Exception:
        mcq_options = []
    return render_template(
        "clue.html",
        clue_id=clue_obj.id,
        variant=display_variant,
        title=clue_obj.title,
        body_text=body_text,
        hint_text=clue_obj.hint_text,
        answer_type=clue_obj.answer_type,
        hint_revealed=prog.used_hint,
        clue=clue_obj,
        mcq_options=mcq_options,
    )


@app.post("/submit/<int:id>")
@app.post("/submit/<slug>")
def submit(id: int = None, slug: str = None):
    team = get_current_team_record()
    if not team:
        flash("Pick a team name to start.", "warning")
        return redirect(url_for("index"))

    clue_obj = None
    if id is not None:
        clue_obj = Clue.query.get(id)
    elif slug is not None:
        clue_obj = Clue.query.filter_by(slug=slug).first()
    if not clue_obj:
        return redirect(url_for("finish"))

    prog = _ensure_progress(team, clue_obj)

    if clue_obj.answer_type == "text":
        submitted = (request.form.get("answer") or "").strip().lower()
        expected = (clue_obj.answer_payload or "").strip().lower()
        if submitted != expected:
            flash("Try again.", "danger")
            return redirect(url_for("clue", id=clue_obj.id))
    elif clue_obj.answer_type == "mcq":
        # Treat MCQ as validated only if an 'answer' is provided.
        # Wrong MCQ attempts increment a penalty counter on Progress.
        submitted = (request.form.get("answer") or "").strip().lower()
        if submitted:
            try:
                import json as _json
                answers = _json.loads(clue_obj.answer_payload or "[]")
                answers_norm = {str(a).strip().lower() for a in answers if isinstance(a, str)}
            except Exception:
                answers_norm = set()
            if submitted not in answers_norm:
                prog.wrong_attempts = (prog.wrong_attempts or 0) + 1
                db.session.commit()
                flash("Try again.", "danger")
                return redirect(url_for("clue", id=clue_obj.id))

    # For tap, correct text, or MCQ with no/valid answer, mark solved
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
@app.post("/hint/<slug>")
def hint(id: int = None, slug: str = None):
    team = get_current_team_record()
    if not team:
        flash("Pick a team name to start.", "warning")
        return redirect(url_for("index"))

    clue_obj = None
    if id is not None:
        clue_obj = Clue.query.get(id)
    elif slug is not None:
        clue_obj = Clue.query.filter_by(slug=slug).first()
    if not clue_obj:
        return redirect(url_for("finish"))

    prog = _ensure_progress(team, clue_obj)
    if not prog.used_hint:
        prog.used_hint = True
        db.session.commit()

    # Surface the hint via flash so current template shows it
    if clue_obj.hint_text:
        flash(f"Hint: {clue_obj.hint_text}", "warning")
    flash(f"Hint used for Clue {clue_obj.id}", "info")
    return redirect(url_for("clue", id=clue_obj.id))


@app.post("/skip/<int:id>")
@app.post("/skip/<slug>")
def skip(id: int = None, slug: str = None):
    team = get_current_team_record()
    if not team:
        flash("Pick a team name to start.", "warning")
        return redirect(url_for("index"))

    clue_obj = None
    if id is not None:
        clue_obj = Clue.query.get(id)
    elif slug is not None:
        clue_obj = Clue.query.filter_by(slug=slug).first()
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
        "clues": Clue.query.order_by(Clue.order_index.asc(), Clue.id.asc()).all(),
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


@app.post("/admin/rotate_slugs")
def admin_rotate_slugs():
    admin_password_expected = app.config.get("ADMIN_PASSWORD", "")
    provided_password = extract_basic_auth_password(request)
    if not admin_password_expected or provided_password != admin_password_expected:
        return unauthorized_response()

    # Collect existing slugs to ensure uniqueness
    existing = {c.slug for c in Clue.query.filter(Clue.slug.isnot(None)).all()}

    # Rotate slugs for all clues
    for c in Clue.query.order_by(Clue.order_index.asc(), Clue.id.asc()).all():
        if c.slug:
            existing.discard(c.slug)  # allow reusing pattern space
        new_slug = generate_readable_slug(existing)
        c.slug = new_slug
        existing.add(new_slug)

    db.session.commit()
    flash("Clue URLs rotated successfully.", "success")
    return redirect(url_for("admin"))


@app.post("/admin/start_game")
def admin_start_game():
    admin_password_expected = app.config.get("ADMIN_PASSWORD", "")
    provided_password = extract_basic_auth_password(request)
    if not admin_password_expected or provided_password != admin_password_expected:
        return unauthorized_response()

    # Set a global game start timestamp (ISO 8601) so all teams start together
    now_iso = datetime.utcnow().isoformat()
    row = Config.query.get("GAME_STARTED_AT")
    if row:
        row.value = now_iso
    else:
        db.session.add(Config(key="GAME_STARTED_AT", value=now_iso))
    db.session.commit()
    flash("Game started for all teams.", "success")
    return redirect(url_for("admin"))


@app.get("/admin/export_csv")
def admin_export_csv():
    admin_password_expected = app.config.get("ADMIN_PASSWORD", "")
    provided_password = extract_basic_auth_password(request)
    if not admin_password_expected or provided_password != admin_password_expected:
        return unauthorized_response()

    # Build CSV header
    clues = Clue.query.order_by(Clue.order_index.asc(), Clue.id.asc()).all()
    base_headers = [
        "team_name",
        "team_token",
        "started_at",
        "completed_at",
        "total_solved",
        "total_hints",
        "total_skips",
        "score",
    ]
    per_clue_headers = []
    for c in clues:
        per_clue_headers.extend([
            f"clue_{c.id}_variant",
            f"clue_{c.id}_hint",
            f"clue_{c.id}_skipped",
            f"clue_{c.id}_started_at",
            f"clue_{c.id}_solved_at",
        ])

    sio = io.StringIO()
    writer = csv.writer(sio)
    writer.writerow(base_headers + per_clue_headers)

    teams = Team.query.order_by(Team.created_at.asc()).all()
    for team in teams:
        score, solved_count, hint_count, skip_count, _elapsed = _compute_score(team)
        row = [
            team.name,
            team.token,
            team.created_at.isoformat() if team.created_at else "",
            team.completed_at.isoformat() if team.completed_at else "",
            solved_count,
            hint_count,
            skip_count,
            score,
        ]
        # Map progress by clue_id for quick lookup
        progresses = {
            p.clue_id: p for p in Progress.query.filter_by(team_id=team.id).all()
        }
        for c in clues:
            p = progresses.get(c.id)
            row.extend([
                (p.variant if p else ""),
                ("1" if (p and p.used_hint) else "0"),
                ("1" if (p and p.skipped) else "0"),
                (p.started_at.isoformat() if (p and p.started_at) else ""),
                (p.solved_at.isoformat() if (p and p.solved_at) else ""),
            ])
        writer.writerow(row)

    output = sio.getvalue()
    headers = {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": 'attachment; filename="results.csv"',
        "Cache-Control": "no-store",
    }
    return Response(output, headers=headers)


@app.get("/admin/qr/<int:clue_id>.png")
def admin_qr(clue_id: int):
    admin_password_expected = app.config.get("ADMIN_PASSWORD", "")
    provided_password = extract_basic_auth_password(request)
    if not admin_password_expected or provided_password != admin_password_expected:
        return unauthorized_response()

    clue = Clue.query.get_or_404(clue_id)
    url = url_for("clue", id=clue.id, _external=True)
    img = qrcode.make(url)
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    headers = {
        "Content-Type": "image/png",
        "Content-Disposition": f'attachment; filename="clue_{clue.id}.png"',
        "Cache-Control": "no-store",
    }
    return Response(bio.read(), headers=headers)


@app.route("/setup", methods=["GET", "POST"])
def setup():
    # Admin protection (Basic Auth)
    admin_password_expected = app.config.get("ADMIN_PASSWORD", "")
    provided_password = extract_basic_auth_password(request)
    if not admin_password_expected or provided_password != admin_password_expected:
        return unauthorized_response()

    # Prepare settings form with defaults from Config table or fallbacks
    def _get_cfg_int(key: str, default: int) -> int:
        cfg = Config.query.get(key)
        if not cfg:
            return default
        try:
            return int(cfg.value)
        except Exception:
            return default

    settings_defaults = {
        "hint_delay_seconds": _get_cfg_int(CONFIG_KEY_HINT_DELAY_SECONDS, get_game_settings().get("HINT_DELAY_SECONDS", 20)),
        "points_solve": _get_cfg_int(CONFIG_KEY_POINTS_SOLVE, 10),
        "penalty_hint": _get_cfg_int(CONFIG_KEY_PENALTY_HINT, 3),
        "penalty_skip": _get_cfg_int(CONFIG_KEY_PENALTY_SKIP, 8),
        "time_penalty_window_seconds": _get_cfg_int(CONFIG_KEY_TIME_PENALTY_WINDOW_SECONDS, 120),
        "time_penalty_points": _get_cfg_int(CONFIG_KEY_TIME_PENALTY_POINTS, 1),
    }
    settings_form = SettingsForm(data=settings_defaults)

    if request.method == "POST" and settings_form.validate_on_submit():
        # Save settings to Config table
        kv = {
            CONFIG_KEY_HINT_DELAY_SECONDS: str(settings_form.hint_delay_seconds.data),
            CONFIG_KEY_POINTS_SOLVE: str(settings_form.points_solve.data),
            CONFIG_KEY_PENALTY_HINT: str(settings_form.penalty_hint.data),
            CONFIG_KEY_PENALTY_SKIP: str(settings_form.penalty_skip.data),
            CONFIG_KEY_TIME_PENALTY_WINDOW_SECONDS: str(settings_form.time_penalty_window_seconds.data),
            CONFIG_KEY_TIME_PENALTY_POINTS: str(settings_form.time_penalty_points.data),
        }
        for k, v in kv.items():
            row = Config.query.get(k)
            if row:
                row.value = v
            else:
                db.session.add(Config(key=k, value=v))
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("setup"))

    # List clues ordered
    clues = Clue.query.order_by(Clue.order_index.asc(), Clue.id.asc()).all()
    return render_template("setup.html", clues=clues, settings_form=settings_form)


@app.route("/setup/add", methods=["GET", "POST"])
def setup_add():
    # Admin protection
    admin_password_expected = app.config.get("ADMIN_PASSWORD", "")
    provided_password = extract_basic_auth_password(request)
    if not admin_password_expected or provided_password != admin_password_expected:
        return unauthorized_response()

    form = ClueForm()
    if form.validate_on_submit():
        clue = Clue(
            title=form.title.data,
            body_variant_a=form.body_variant_a.data,
            body_variant_b=form.body_variant_b.data,
            answer_type=form.answer_type.data,
            answer_payload=(form.answer_payload.data or "").strip(),
            hint_text=form.hint_text.data or "",
            order_index=form.order_index.data,
            is_final=bool(form.is_final.data),
        )
        db.session.add(clue)
        db.session.commit()

        # Handle optional image upload
        file = form.image.data
        if file and getattr(file, "filename", ""):
            allowed_ext = {"png", "jpg", "jpeg", "webp", "gif"}
            filename = secure_filename(file.filename)
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext in allowed_ext and (file.mimetype or "").startswith("image/"):
                uploads_dir = os.path.join("data", "uploads")
                unique_name = f"{clue.id}-{uuid.uuid4().hex}_{filename}"
                dest_path = os.path.join(uploads_dir, unique_name)
                try:
                    data = file.read()
                    from io import BytesIO
                    bio = BytesIO(data)
                    img = Image.open(bio)
                    img.thumbnail((1600, 1600))
                    img.save(dest_path)
                    clue.image_filename = unique_name
                    clue.image_alt = (form.image_alt.data or "").strip() or None
                    clue.image_caption = (form.image_caption.data or "").strip() or None
                    db.session.commit()
                except Exception:
                    # Fallback: try saving raw if Pillow fails
                    try:
                        with open(dest_path, "wb") as f:
                            f.write(data)
                        clue.image_filename = unique_name
                        clue.image_alt = (form.image_alt.data or "").strip() or None
                        clue.image_caption = (form.image_caption.data or "").strip() or None
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                        flash("Failed to save image.", "warning")
            else:
                flash("Invalid image type. Allowed: png, jpg, jpeg, webp, gif.", "warning")

        flash("Clue added.", "success")
        return redirect(url_for("setup"))

    return render_template("setup.html", form=form)


@app.route("/setup/edit/<int:id>", methods=["GET", "POST"])
def setup_edit(id: int):
    # Admin protection
    admin_password_expected = app.config.get("ADMIN_PASSWORD", "")
    provided_password = extract_basic_auth_password(request)
    if not admin_password_expected or provided_password != admin_password_expected:
        return unauthorized_response()

    clue = Clue.query.get_or_404(id)
    form = ClueForm(obj=clue)
    if form.validate_on_submit():
        clue.title = form.title.data
        clue.body_variant_a = form.body_variant_a.data
        clue.body_variant_b = form.body_variant_b.data
        clue.answer_type = form.answer_type.data
        clue.answer_payload = (form.answer_payload.data or "").strip()
        clue.hint_text = form.hint_text.data or ""
        clue.order_index = form.order_index.data
        clue.is_final = bool(form.is_final.data)

        # Handle image removal
        uploads_dir = os.path.join("data", "uploads")
        if getattr(form, "remove_image", None) and form.remove_image.data:
            if clue.image_filename:
                try:
                    os.remove(os.path.join(uploads_dir, clue.image_filename))
                except Exception:
                    pass
            clue.image_filename = None
            clue.image_alt = None
            clue.image_caption = None

        # Handle image upload/replace
        file = form.image.data
        if file and getattr(file, "filename", ""):
            allowed_ext = {"png", "jpg", "jpeg", "webp", "gif"}
            filename = secure_filename(file.filename)
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext in allowed_ext and (file.mimetype or "").startswith("image/"):
                # Remove old file if present
                if clue.image_filename:
                    try:
                        os.remove(os.path.join(uploads_dir, clue.image_filename))
                    except Exception:
                        pass
                unique_name = f"{clue.id}-{uuid.uuid4().hex}_{filename}"
                dest_path = os.path.join(uploads_dir, unique_name)
                try:
                    data = file.read()
                    from io import BytesIO
                    bio = BytesIO(data)
                    img = Image.open(bio)
                    img.thumbnail((1600, 1600))
                    img.save(dest_path)
                    clue.image_filename = unique_name
                except Exception:
                    try:
                        with open(dest_path, "wb") as f:
                            f.write(data)
                        clue.image_filename = unique_name
                    except Exception:
                        flash("Failed to save image.", "warning")
            else:
                flash("Invalid image type. Allowed: png, jpg, jpeg, webp, gif.", "warning")

        # Always update alt/caption from form
        clue.image_alt = (form.image_alt.data or "").strip() or clue.image_alt
        clue.image_caption = (form.image_caption.data or "").strip() or clue.image_caption

        db.session.commit()
        flash("Clue updated.", "success")
        return redirect(url_for("setup"))

    return render_template("setup.html", form=form, clue=clue)


@app.post("/setup/delete/<int:id>")
def setup_delete(id: int):
    # Admin protection
    admin_password_expected = app.config.get("ADMIN_PASSWORD", "")
    provided_password = extract_basic_auth_password(request)
    if not admin_password_expected or provided_password != admin_password_expected:
        return unauthorized_response()

    clue = Clue.query.get_or_404(id)
    db.session.delete(clue)
    db.session.commit()
    flash("Clue deleted.", "warning")
    return redirect(url_for("setup"))


@app.get("/setup/export")
def setup_export():
    # Admin protection
    admin_password_expected = app.config.get("ADMIN_PASSWORD", "")
    provided_password = extract_basic_auth_password(request)
    if not admin_password_expected or provided_password != admin_password_expected:
        return unauthorized_response()

    clues = [
        {
            "id": c.id,
            "title": c.title,
            "body_variant_a": c.body_variant_a,
            "body_variant_b": c.body_variant_b,
            "answer_type": c.answer_type,
            "answer_payload": c.answer_payload,
            "hint_text": c.hint_text,
            "order_index": c.order_index,
            "is_final": bool(c.is_final),
        }
        for c in Clue.query.order_by(Clue.order_index.asc(), Clue.id.asc()).all()
    ]
    cfg = {row.key: row.value for row in Config.query.all()}
    payload = {"clues": clues, "config": cfg}
    return Response(json.dumps(payload, ensure_ascii=False, indent=2), mimetype="application/json")


@app.post("/setup/import")
def setup_import():
    # Admin protection
    admin_password_expected = app.config.get("ADMIN_PASSWORD", "")
    provided_password = extract_basic_auth_password(request)
    if not admin_password_expected or provided_password != admin_password_expected:
        return unauthorized_response()

    file = request.files.get("file")
    if not file:
        flash("No file uploaded.", "danger")
        return redirect(url_for("setup"))

    try:
        data = json.loads(file.read().decode("utf-8"))
    except Exception as e:
        flash(f"Invalid JSON: {e}", "danger")
        return redirect(url_for("setup"))

    clues = data.get("clues", [])
    config_map = data.get("config", {})

    # Overwrite clues
    Clue.query.delete()
    db.session.commit()
    for c in clues:
        obj = Clue(
            id=c.get("id"),
            title=c.get("title", ""),
            body_variant_a=c.get("body_variant_a", ""),
            body_variant_b=c.get("body_variant_b", ""),
            answer_type=c.get("answer_type", "tap"),
            answer_payload=c.get("answer_payload", ""),
            hint_text=c.get("hint_text", ""),
            order_index=int(c.get("order_index", 1)),
            is_final=bool(c.get("is_final", False)),
        )
        db.session.add(obj)
    db.session.commit()

    # Overwrite config
    Config.query.delete()
    db.session.commit()
    for k, v in config_map.items():
        db.session.add(Config(key=str(k), value=str(v)))
    db.session.commit()

    flash("Import successful.", "success")
    return redirect(url_for("setup"))


@app.get("/healthz")
def healthz():
    return Response("ok", status=200, mimetype="text/plain")


@app.get("/uploads/<path:filename>")
def serve_upload(filename: str):
    uploads_dir = os.path.join("data", "uploads")
    return send_from_directory(uploads_dir, filename)


@app.errorhandler(404)
def handle_404(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def handle_500(e):
    return render_template("500.html"), 500


# Allow running with `python app.py` (optional; flask run is preferred)
if __name__ == "__main__":
    # Default to 0.0.0.0:8080 to match README instructions
    app.run(host="0.0.0.0", port=8080, debug=bool(app.config.get("DEBUG", False)))
