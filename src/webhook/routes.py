"""Flask routes for receiving GoCanvas Workflow Handoff Notification Webhooks."""

import json
import logging
import sqlite3
from datetime import datetime, date
from pathlib import Path

from flask import Blueprint, request, jsonify, current_app

logger = logging.getLogger(__name__)

webhook_bp = Blueprint("webhook", __name__)


def init_submission_db(db_path: str):
    """Initialize the local SQLite database for storing webhook submissions."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL,
            tag TEXT NOT NULL,
            form_name TEXT,
            submission_date TEXT,
            line_id TEXT,
            job_number TEXT,
            lot_code TEXT,
            item_number TEXT,
            raw_payload TEXT NOT NULL,
            processed INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()
    logger.info("Submission database initialized at %s", db_path)


def store_submission(db_path: str, tag: str, payload: dict, extracted_fields: dict):
    """Store a webhook submission in the local SQLite database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO submissions (received_at, tag, form_name, submission_date,
                                 line_id, job_number, lot_code, item_number, raw_payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.utcnow().isoformat(),
            tag,
            extracted_fields.get("form_name", ""),
            extracted_fields.get("submission_date", date.today().isoformat()),
            extracted_fields.get("line_id", ""),
            extracted_fields.get("job_number", ""),
            extracted_fields.get("lot_code", ""),
            extracted_fields.get("item_number", ""),
            json.dumps(payload),
        ),
    )
    conn.commit()
    submission_id = cursor.lastrowid
    conn.close()
    logger.info("Stored submission id=%d, tag=%s", submission_id, tag)
    return submission_id


def get_submissions_by_tag_and_date(db_path: str, tag: str, target_date: date) -> list[dict]:
    """Retrieve stored submissions by tag and date."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM submissions
        WHERE tag = ? AND submission_date = ?
        ORDER BY received_at
        """,
        (tag, target_date.isoformat()),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def extract_fields_from_payload(payload: dict, form_config: dict) -> dict:
    """Extract relevant fields from a GoCanvas webhook JSON payload.

    The exact structure depends on GoCanvas's Workflow Handoff format.
    This extracts fields based on the configured field mappings in settings.yaml.

    Args:
        payload: The raw JSON payload from GoCanvas.
        form_config: The form configuration from settings.yaml (e.g., forms.first_piece_approval).

    Returns:
        Dict with normalized field names (line_id, job_number, lot_code, etc.).
    """
    extracted = {
        "form_name": "",
        "submission_date": date.today().isoformat(),
        "line_id": "",
        "job_number": "",
        "lot_code": "",
        "item_number": "",
    }

    # Try to extract form name from common payload locations
    for key in ("FormName", "form_name", "AppName", "app_name", "Name", "name"):
        if key in payload:
            extracted["form_name"] = str(payload[key])
            break

    # Try to extract submission date
    for key in ("SubmissionDate", "submission_date", "Date", "date", "Timestamp", "timestamp"):
        if key in payload:
            extracted["submission_date"] = str(payload[key])[:10]  # YYYY-MM-DD
            break

    # Extract mapped fields from the form config
    match_fields = form_config.get("match_fields", form_config.get("fields", {}))
    for target_field, source_field in match_fields.items():
        value = _deep_get(payload, source_field)
        if value is not None:
            # Map config field names to our normalized names
            field_map = {"line": "line_id", "job": "job_number", "lot": "lot_code", "item": "item_number"}
            normalized = field_map.get(target_field, target_field)
            extracted[normalized] = str(value)

    return extracted


def _deep_get(data: dict, key: str):
    """Search for a key in a nested dict, checking top-level and nested structures."""
    if key in data:
        return data[key]

    # Search in nested Response/Entries structures (common GoCanvas patterns)
    for value in data.values():
        if isinstance(value, dict):
            result = _deep_get(value, key)
            if result is not None:
                return result
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    result = _deep_get(item, key)
                    if result is not None:
                        return result
    return None


@webhook_bp.route("/webhook/gocanvas", methods=["POST"])
def receive_gocanvas_webhook():
    """Receive and process a GoCanvas Workflow Handoff Notification Webhook.

    GoCanvas sends JSON payloads with a Tag field identifying the form type.
    Routes to appropriate handler based on the tag.
    """
    # Parse incoming JSON payload
    payload = request.get_json(silent=True)
    if payload is None:
        # Try form-encoded or raw body
        try:
            payload = json.loads(request.get_data(as_text=True))
        except (json.JSONDecodeError, ValueError):
            logger.warning("Received non-JSON webhook payload")
            return jsonify({"error": "Invalid JSON payload"}), 400

    logger.info("Received GoCanvas webhook: %s", json.dumps(payload)[:500])

    # Extract the tag to identify form type
    tag = payload.get("Tag", payload.get("tag", "unknown"))
    settings = current_app.config.get("SETTINGS", {})
    db_path = settings.get("webhook", {}).get("submission_db", "data/submissions.db")
    forms_config = settings.get("gocanvas", {}).get("forms", {})

    # Route based on tag
    if tag == forms_config.get("quality_check", {}).get("webhook_tag", "quality"):
        return _handle_quality_submission(payload, db_path, forms_config.get("quality_check", {}))
    elif tag == forms_config.get("first_piece_approval", {}).get("webhook_tag", "first-piece"):
        return _handle_first_piece_submission(payload, db_path, forms_config.get("first_piece_approval", {}))
    elif tag == forms_config.get("line_clearance", {}).get("webhook_tag", "line-clearance"):
        return _handle_line_clearance_submission(payload, db_path, forms_config.get("line_clearance", {}))
    else:
        logger.warning("Received webhook with unknown tag: %s", tag)
        # Store anyway for audit
        store_submission(db_path, tag, payload, {"form_name": "unknown"})
        return jsonify({"status": "received", "tag": tag, "action": "stored_unknown"}), 200


def _handle_quality_submission(payload: dict, db_path: str, form_config: dict):
    """Handle a quality form submission - trigger immediate image analysis."""
    extracted = extract_fields_from_payload(payload, form_config)
    submission_id = store_submission(db_path, form_config.get("webhook_tag", "quality"), payload, extracted)

    # Queue image analysis (the actual analysis is triggered by the check module)
    # For now, mark as needing processing
    logger.info(
        "Quality submission received (id=%d): line=%s, job=%s",
        submission_id, extracted.get("line_id"), extracted.get("job_number"),
    )

    # Trigger immediate quality check if configured
    quality_checker = current_app.config.get("QUALITY_CHECKER")
    if quality_checker:
        try:
            results = quality_checker.analyze_webhook_submission(payload, form_config)
            # If any failures, the checker will send immediate alerts
            failures = [r for r in results if r.status == "FAIL"]
            if failures:
                logger.warning("Quality check found %d failures for submission %d", len(failures), submission_id)
        except Exception as e:
            logger.error("Error running immediate quality check: %s", e)

    return jsonify({"status": "received", "submission_id": submission_id, "action": "quality_analysis_queued"}), 200


def _handle_first_piece_submission(payload: dict, db_path: str, form_config: dict):
    """Handle a first piece approval submission - store for daily cross-reference."""
    extracted = extract_fields_from_payload(payload, form_config)
    submission_id = store_submission(db_path, form_config.get("webhook_tag", "first-piece"), payload, extracted)

    logger.info(
        "First piece approval stored (id=%d): line=%s, job=%s, lot=%s",
        submission_id, extracted.get("line_id"), extracted.get("job_number"), extracted.get("lot_code"),
    )

    return jsonify({"status": "received", "submission_id": submission_id, "action": "stored"}), 200


def _handle_line_clearance_submission(payload: dict, db_path: str, form_config: dict):
    """Handle a line clearance submission - store for daily cross-reference."""
    extracted = extract_fields_from_payload(payload, form_config)
    submission_id = store_submission(db_path, form_config.get("webhook_tag", "line-clearance"), payload, extracted)

    logger.info(
        "Line clearance stored (id=%d): line=%s",
        submission_id, extracted.get("line_id"),
    )

    return jsonify({"status": "received", "submission_id": submission_id, "action": "stored"}), 200
