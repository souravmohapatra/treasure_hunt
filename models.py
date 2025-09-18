"""
SQLAlchemy models and database initialization/seed logic for the Treasure Hunt app.

Usage (from a Flask context):
    from models import db, init_app_db, Team, Clue, Progress
    init_app_db(app)  # ensures data dir, creates tables on first run, seeds clues if empty

This module intentionally keeps app coupling minimal by exposing `init_app_db(app)`
to be called from the Flask app at startup. It also supports `flask shell` workflows:

    flask shell
    >>> from models import db
    >>> db.create_all()  # creates tables within current app context

On first run, we seed 6 placeholder clues (IDs 1..6, final=True for ID 6).
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional
import uuid
import random

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, Boolean, Integer, String, Text, DateTime, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

# SQLAlchemy handle (init with app via `init_app_db(app)`)
db = SQLAlchemy()


# Models
class Team(db.Model):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    progress_entries: Mapped[list["Progress"]] = relationship(
        "Progress",
        back_populates="team",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug utility
        return f"<Team id={self.id} name={self.name!r} token={self.token[:8]}...>"


class Clue(db.Model):
    __tablename__ = "clues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body_variant_a: Mapped[str] = mapped_column(Text, nullable=False)
    body_variant_b: Mapped[str] = mapped_column(Text, nullable=False)
    answer_type: Mapped[str] = mapped_column(String(16), nullable=False, default="tap")  # "tap" or "text"
    answer_payload: Mapped[str] = mapped_column(Text, nullable=False, default="")
    hint_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    slug: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True, index=True)
    # Optional image fields
    image_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    image_alt: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    image_caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    order_index: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    progress_entries: Mapped[list["Progress"]] = relationship(
        "Progress",
        back_populates="clue",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug utility
        return f"<Clue id={self.id} title={self.title!r} order={self.order_index} final={self.is_final}>"


class Progress(db.Model):
    __tablename__ = "progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    clue_id: Mapped[int] = mapped_column(ForeignKey("clues.id", ondelete="CASCADE"), nullable=False, index=True)

    # "A" or "B" (deterministic per team token + clue id)
    variant: Mapped[str] = mapped_column(String(1), nullable=False)

    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    solved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    used_hint: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    skipped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    team: Mapped[Team] = relationship("Team", back_populates="progress_entries")
    clue: Mapped[Clue] = relationship("Clue", back_populates="progress_entries")

    __table_args__ = (
        UniqueConstraint("team_id", "clue_id", name="uq_progress_team_clue"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug utility
        return f"<Progress team_id={self.team_id} clue_id={self.clue_id} variant={self.variant}>"


class Config(db.Model):
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug utility
        return f"<Config {self.key!r}={self.value!r}>"

# Initialization and seeding helpers
def _sqlite_db_path_from_uri(uri: str) -> Optional[str]:
    """
    Extract filesystem path from a SQLite URI of the form sqlite:///path/to/db.sqlite
    Works for absolute paths that produce 'sqlite:////abs/path' as well.
    Returns None if not a SQLite URI.
    """
    prefix = "sqlite:///"
    if not isinstance(uri, str) or not uri.startswith(prefix):
        return None
    return uri[len(prefix):]


def generate_readable_slug(existing: set[str]) -> str:
    adjectives = [
        "amber", "aqua", "azure", "coral", "ivory", "jade", "lilac", "mint",
        "peach", "plum", "rose", "sage", "sunny", "violet", "silver", "golden"
    ]
    nouns = [
        "banana", "caterpillar", "comet", "river", "meadow", "maple", "pebble", "harbor",
        "lantern", "puzzle", "galaxy", "marble", "sunrise", "breeze", "willow", "orchid"
    ]
    while True:
        slug = f"{random.choice(adjectives)}-{random.choice(nouns)}-{uuid.uuid4().hex[:4]}"
        if slug not in existing:
            return slug

def init_app_db(app) -> None:
    """
    Bind SQLAlchemy to the Flask app, ensure the data directory exists,
    create the database file on first run, and seed default clues if needed.
    """
    db.init_app(app)

    # Ensure data directory is present (defensive; config also tries to create it)
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    db_path = _sqlite_db_path_from_uri(uri) or ""
    data_dir = os.path.dirname(db_path) if db_path else None
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)

    with app.app_context():
        # Create tables if DB file doesn't exist yet
        create_needed = bool(db_path) and not os.path.exists(db_path)
        if create_needed:
            db.create_all()
        else:
            # Even if file exists, ensure tables are present (idempotent)
            db.create_all()

        # Lightweight migration: ensure clue image/slug columns exist (SQLite)
        try:
            cols = {row[1] for row in db.session.execute(text("PRAGMA table_info('clues')")).fetchall()}
            if 'image_filename' not in cols:
                db.session.execute(text("ALTER TABLE clues ADD COLUMN image_filename VARCHAR(255)"))
            if 'image_alt' not in cols:
                db.session.execute(text("ALTER TABLE clues ADD COLUMN image_alt VARCHAR(255)"))
            if 'image_caption' not in cols:
                db.session.execute(text("ALTER TABLE clues ADD COLUMN image_caption TEXT"))
            if 'slug' not in cols:
                db.session.execute(text("ALTER TABLE clues ADD COLUMN slug VARCHAR(64)"))
            # Enforce uniqueness at DB level where possible
            db.session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_clues_slug ON clues(slug)"))
            db.session.commit()

            # Backfill missing slugs
            existing = {
                s for (s,) in db.session.execute(text("SELECT slug FROM clues WHERE slug IS NOT NULL AND slug != ''")).fetchall()
            }
            missing = db.session.execute(
                text("SELECT id FROM clues WHERE slug IS NULL OR slug = ''")
            ).fetchall()
            for (clue_id,) in missing:
                slug = generate_readable_slug(existing)
                db.session.execute(
                    text("UPDATE clues SET slug = :slug WHERE id = :id"),
                    {"slug": slug, "id": clue_id},
                )
                existing.add(slug)
            db.session.commit()
        except Exception:
            db.session.rollback()

        # Seed clues if empty
        seed_default_clues()


def seed_default_clues() -> None:
    """
    Insert 6 dummy clues if the Clue table is empty:
      - IDs 1–5: body_variant_a/b: "This is Clue X (A/B)", answer_type="tap", hint "Hint for Clue X", order_index=X
      - Clue 6: same as above but is_final=True
    """
    if Clue.query.count() > 0:
        return

    to_add: list[Clue] = []
    existing_slugs: set[str] = set()
    for i in range(1, 7):
        slug = generate_readable_slug(existing_slugs)
        existing_slugs.add(slug)
        to_add.append(
            Clue(
                id=i,  # set explicit ids so routes /clue/<id> map cleanly to 1..6
                title=f"Clue {i}",
                body_variant_a=f"This is Clue {i} (A)",
                body_variant_b=f"This is Clue {i} (B)",
                answer_type="tap",
                answer_payload="",
                hint_text=f"Hint for Clue {i}",
                slug=slug,
                order_index=i,
                is_final=(i == 6),
            )
        )
    db.session.bulk_save_objects(to_add)
    db.session.commit()


__all__ = [
    "db",
    "Team",
    "Clue",
    "Progress",
    "Config",
    "init_app_db",
    "seed_default_clues",
]
