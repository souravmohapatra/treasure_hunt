from __future__ import annotations

import json
from typing import Any

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import (
    StringField,
    TextAreaField,
    SelectField,
    IntegerField,
    BooleanField,
    SubmitField,
)
from wtforms.validators import DataRequired, Optional, NumberRange, Length, ValidationError


# Optional constants for config keys used elsewhere in the app
CONFIG_KEY_HINT_DELAY_SECONDS = "HINT_DELAY_SECONDS"
CONFIG_KEY_POINTS_SOLVE = "POINTS_SOLVE"
CONFIG_KEY_PENALTY_HINT = "PENALTY_HINT"
CONFIG_KEY_PENALTY_SKIP = "PENALTY_SKIP"
CONFIG_KEY_TIME_PENALTY_WINDOW_SECONDS = "TIME_PENALTY_WINDOW_SECONDS"
CONFIG_KEY_TIME_PENALTY_POINTS = "TIME_PENALTY_POINTS"


class ClueForm(FlaskForm):
    """
    Form for creating/updating a Clue.
    - answer_type:
        * "tap" -> no answer payload required
        * "text" -> 'answer_payload' is a comma-separated string of accepted answers
        * "mcq"  -> 'answer_payload' is a JSON array of strings (choices or accepted values)
    """
    title = StringField(
        "Title",
        validators=[DataRequired(), Length(min=1, max=200)],
        render_kw={"placeholder": "Clue title"},
    )
    body_variant_a = TextAreaField(
        "Variant A text",
        validators=[DataRequired(), Length(min=1)],
        render_kw={"rows": 4, "placeholder": "Body text for Variant A"},
    )
    body_variant_b = TextAreaField(
        "Variant B text",
        validators=[DataRequired(), Length(min=1)],
        render_kw={"rows": 4, "placeholder": "Body text for Variant B"},
    )
    answer_type = SelectField(
        "Answer type",
        choices=[("tap", "Tap"), ("text", "Text"), ("mcq", "Multiple Choice")],
        validators=[DataRequired()],
    )
    answer_payload = TextAreaField(
        "Options",
        description=(
            "For Text: comma-separated list (e.g., 'alpha, beta'). "
            "For MCQ: JSON array of strings (e.g., [\"A\",\"B\",\"C\"]). "
            "For Tap: leave blank."
        ),
        validators=[Optional()],
        render_kw={"rows": 3, "placeholder": "Depends on answer type"},
    )
    answer_correct = StringField(
        "Correct answer",
        description="For MCQ: must match one of the Options exactly.",
        validators=[Optional(), Length(max=255)],
        render_kw={"placeholder": "One of the options (MCQ only)"},
    )
    hint_text = TextAreaField(
        "Hint text",
        validators=[Optional()],
        render_kw={"rows": 3, "placeholder": "Optional hint shown when requested"},
    )
    order_index = IntegerField(
        "Order index",
        validators=[DataRequired(), NumberRange(min=1, message="Order index must be >= 1")],
        render_kw={"min": 1},
    )
    # Optional image fields
    image = FileField(
        "Image",
        validators=[Optional(), FileAllowed(["png", "jpg", "jpeg", "webp", "gif"], "Images only!")],
        render_kw={"accept": "image/png,image/jpeg,image/webp,image/gif"}
    )
    image_alt = StringField(
        "Image alt",
        validators=[Optional(), Length(max=255)],
        render_kw={"placeholder": "Alternative text for accessibility"}
    )
    image_caption = StringField(
        "Image caption",
        validators=[Optional(), Length(max=255)],
        render_kw={"placeholder": "Short caption shown under the image"}
    )
    remove_image = BooleanField("Remove image")
    is_final = BooleanField("Is final clue?")
    submit = SubmitField("Save")

    def validate_answer_payload(self, field: TextAreaField) -> None:
        """Validate payload format based on selected answer_type."""
        atype = (self.answer_type.data or "").strip().lower()
        payload = (field.data or "").strip()

        if atype in ("text", "mcq") and not payload:
            raise ValidationError("Options are required for Text or MCQ types.")

        if atype == "mcq" and payload:
            try:
                parsed: Any = json.loads(payload)
            except json.JSONDecodeError as e:
                raise ValidationError(f"Invalid JSON: {e.msg}") from e

            if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
                raise ValidationError("MCQ options must be a JSON array of strings.")

        # For 'text', free-form comma-separated string is fine.
        # For 'tap', payload can be empty; if provided, we accept it but it is unused.


    def validate_answer_correct(self, field: StringField) -> None:
        """Ensure MCQ has a correct answer present and that it is among the options."""
        atype = (self.answer_type.data or "").strip().lower()
        if atype != "mcq":
            return  # Only enforce for MCQ
        correct = (field.data or "").strip()
        if not correct:
            raise ValidationError("Correct answer is required for MCQ.")
        # Parse options from answer_payload
        try:
            options: Any = json.loads(self.answer_payload.data or "[]")
        except json.JSONDecodeError:
            raise ValidationError("Provide valid MCQ options first (JSON array of strings).")
        if not isinstance(options, list) or not all(isinstance(x, str) for x in options):
            raise ValidationError("MCQ options must be a JSON array of strings.")
        if correct not in options:
            raise ValidationError("Correct answer must match one of the options exactly.")

class SettingsForm(FlaskForm):
    """
    Global game settings form.
    These values are stored in the Config table as key/value pairs.
    """
    hint_delay_seconds = IntegerField(
        "Hint delay (seconds)",
        validators=[DataRequired(), NumberRange(min=0, message="Must be >= 0")],
        render_kw={"min": 0, "placeholder": "e.g., 20"},
    )
    points_solve = IntegerField(
        "Points per solve",
        validators=[DataRequired()],
        render_kw={"placeholder": "e.g., 10"},
    )
    penalty_hint = IntegerField(
        "Penalty per hint",
        validators=[DataRequired()],
        render_kw={"placeholder": "e.g., 3"},
    )
    penalty_skip = IntegerField(
        "Penalty per skip",
        validators=[DataRequired()],
        render_kw={"placeholder": "e.g., 8"},
    )
    time_penalty_window_seconds = IntegerField(
        "Time penalty window (seconds)",
        description="Number of seconds per time penalty window (e.g., 120 for every 2 minutes).",
        validators=[DataRequired(), NumberRange(min=1, message="Must be >= 1")],
        render_kw={"min": 1, "placeholder": "e.g., 120"},
    )
    time_penalty_points = IntegerField(
        "Time penalty points (per window)",
        description="Points lost per time window elapsed.",
        validators=[DataRequired()],
        render_kw={"placeholder": "e.g., 1"},
    )
    submit = SubmitField("Save settings")
