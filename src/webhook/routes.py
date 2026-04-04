"""Flask routes for receiving GoCanvas Workflow Handoff Notification Webhooks.

GoCanvas webhook payload structure (actual):
{
    "type": "submission_create",
    "submission": {"id": 1, "guid": "SUBMISSION_GUID"},
    "form": {"id": 1, "name": "FORM_NAME", "guid": "FORM_GUID", "tag": "F6 – Line Clearance"},
    "dispatch_item": {"id": 1}
}

The webhook only provides submission metadata. The full submission data
(field values, images) must be fetched via the GoCanvas Submissions API
using the submission ID/GUID.
"""

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
            form_id INTEGER,
            submission_guid TEXT,
            gocanvas_submission_id INTEGER,
            submission_date TEXT,
            line_id TEXT,
            job_number TEXT,
            lot_code TEXT,
            item_number TEXT,
            raw_payload TEXT NOT NULL,
            full_submission_data TEXT,
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
        INSERT INTO submissions (received_at, tag, form_name, form_id,
                                 submission_guid, gocanvas_submission_id,
                                 submission_date, line_id, job_number,
                                 lot_code, item_number, raw_payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.utcnow().isoformat(),
            tag,
            extracted_fields.get("form_name", ""),
            extracted_fields.get("form_id"),
            extracted_fields.get("submission_guid", ""),
            extracted_fields.get("gocanvas_submission_id"),
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


def update_submission_data(db_path: str, local_id: int, full_data: dict, extracted_fields: dict):
    """Update a stored submission with full data fetched from GoCanvas API."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE submissions
        SET full_submission_data = ?,
            line_id = COALESCE(NULLIF(?, ''), line_id),
            job_number = COALESCE(NULLIF(?, ''), job_number),
            lot_code = COALESCE(NULLIF(?, ''), lot_code),
            item_number = COALESCE(NULLIF(?, ''), item_number),
            submission_date = COALESCE(NULLIF(?, ''), submission_date)
        WHERE id = ?
        """,
        (
            json.dumps(full_data),
            extracted_fields.get("line_id", ""),
            extracted_fields.get("job_number", ""),
            extracted_fields.get("lot_code", ""),
            extracted_fields.get("item_number", ""),
            extracted_fields.get("submission_date", ""),
            local_id,
        ),
    )
    conn.commit()
    conn.close()


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


def extract_webhook_metadata(payload: dict) -> dict:
    """Extract metadata from the GoCanvas webhook notification payload.

    The webhook payload only contains IDs, not field data. We extract
    identifiers needed to fetch the full submission from the API.
    """
    form_info = payload.get("form", {})
    submission_info = payload.get("submission", {})

    return {
        "form_name": form_info.get("name", ""),
        "form_id": form_info.get("id"),
        "form_guid": form_info.get("guid", ""),
        "tag": form_info.get("tag", ""),
        "submission_guid": submission_info.get("guid", ""),
        "gocanvas_submission_id": submission_info.get("id"),
        "submission_date": date.today().isoformat(),
        "line_id": "",
        "job_number": "",
        "lot_code": "",
        "item_number": "",
    }


def identify_form_type(tag: str, forms_config: dict) -> str | None:
    """Match a GoCanvas form tag to a configured form type.

    Tags from GoCanvas look like "F6 – Line Clearance".
    We match by checking if the configured webhook_tag appears in the actual tag.

    Returns the form config key (e.g. "quality_check", "first_piece_approval")
    or None if no match.
    """
    tag_lower = tag.lower()
    for form_key, form_conf in forms_config.items():
        configured_tag = form_conf.get("webhook_tag", "").lower()
        if configured_tag and configured_tag in tag_lower:
            return form_key
    return None


def fetch_full_submission(gocanvas_client, submission_id: int) -> dict | None:
    """Fetch the full submission data from GoCanvas API by submission ID.

    Returns the parsed submission dict or None on failure.
    """
    try:
        submissions = gocanvas_client.get_submission_by_id(submission_id)
        if submissions:
            return submissions[0] if isinstance(submissions, list) else submissions
    except Exception as e:
        logger.error("Failed to fetch full submission %d from GoCanvas: %s", submission_id, e)
    return None


def extract_fields_from_submission(submission_data: dict, form_config: dict, gocanvas_client) -> dict:
    """Extract field values from a full GoCanvas submission.

    Uses the configured field mappings to find relevant values.
    """
    extracted = {
        "submission_date": date.today().isoformat(),
        "line_id": "",
        "job_number": "",
        "lot_code": "",
        "item_number": "",
    }

    match_fields = form_config.get("match_fields", form_config.get("fields", {}))
    for target_field, source_field in match_fields.items():
        value = gocanvas_client.get_submission_field(submission_data, source_field)
        if value is not None:
            field_map = {"line": "line_id", "job": "job_number", "lot": "lot_code", "item": "item_number"}
            normalized = field_map.get(target_field, target_field)
            extracted[normalized] = str(value)

    return extracted


@webhook_bp.route("/webhook/gocanvas", methods=["POST"])
def receive_gocanvas_webhook():
    """Receive and process a GoCanvas Workflow Handoff Notification Webhook.

    GoCanvas sends a lightweight JSON notification with submission ID and
    form metadata. The full submission data must be fetched via the API.

    Payload structure:
    {
        "type": "submission_create",
        "submission": {"id": 1, "guid": "..."},
        "form": {"id": 1, "name": "...", "guid": "...", "tag": "F6 – Line Clearance"},
        "dispatch_item": {"id": 1}
    }
    """
    # Parse incoming JSON payload
    payload = request.get_json(silent=True)
    if payload is None:
        try:
            payload = json.loads(request.get_data(as_text=True))
        except (json.JSONDecodeError, ValueError):
            logger.warning("Received non-JSON webhook payload")
            return jsonify({"error": "Invalid JSON payload"}), 400

    logger.info("Received GoCanvas webhook: %s", json.dumps(payload))

    # Extract metadata from the notification
    metadata = extract_webhook_metadata(payload)
    tag = metadata["tag"]

    settings = current_app.config.get("SETTINGS", {})
    db_path = settings.get("webhook", {}).get("submission_db", "data/submissions.db")
    forms_config = settings.get("gocanvas", {}).get("forms", {})

    # Identify which form type this is based on the tag
    form_type = identify_form_type(tag, forms_config)
    form_config = forms_config.get(form_type, {}) if form_type else {}

    # Store the webhook notification immediately
    local_id = store_submission(db_path, tag, payload, metadata)

    # Fetch full submission data from GoCanvas API
    gocanvas_client = current_app.config.get("GOCANVAS_CLIENT")
    gc_submission_id = metadata.get("gocanvas_submission_id")
    full_data = None

    if gocanvas_client and gc_submission_id:
        full_data = fetch_full_submission(gocanvas_client, gc_submission_id)
        if full_data and form_config:
            extracted = extract_fields_from_submission(full_data, form_config, gocanvas_client)
            update_submission_data(db_path, local_id, full_data, extracted)
            logger.info("Fetched and stored full submission data for id=%d", local_id)

    # Route to form-specific handler
    if form_type == "quality_check":
        return _handle_quality_submission(local_id, full_data, form_config, payload)
    elif form_type == "first_piece_approval":
        logger.info("First piece approval stored (id=%d): form=%s", local_id, metadata["form_name"])
        return jsonify({"status": "received", "local_id": local_id, "form_type": "first_piece", "action": "stored"}), 200
    elif form_type == "line_clearance":
        logger.info("Line clearance stored (id=%d): form=%s", local_id, metadata["form_name"])
        return jsonify({"status": "received", "local_id": local_id, "form_type": "line_clearance", "action": "stored"}), 200
    else:
        logger.warning("Received webhook with unrecognized tag: '%s'", tag)
        return jsonify({"status": "received", "local_id": local_id, "tag": tag, "action": "stored_unknown"}), 200


def _handle_quality_submission(local_id: int, full_data: dict | None, form_config: dict, payload: dict):
    """Handle a quality form submission - trigger immediate image analysis if possible."""
    if not full_data:
        logger.warning("No full submission data for quality check id=%d, will process in scheduled run", local_id)
        return jsonify({"status": "received", "local_id": local_id, "action": "stored_pending_analysis"}), 200

    quality_checker = current_app.config.get("QUALITY_CHECKER")
    if quality_checker:
        try:
            results = quality_checker.analyze_webhook_submission(full_data, form_config)
            failures = [r for r in results if r.status == "FAIL"]
            if failures:
                logger.warning("Quality check found %d failures for submission %d", len(failures), local_id)
                # Send immediate alert
                notifier = current_app.config.get("EMAIL_NOTIFIER")
                settings = current_app.config.get("SETTINGS", {})
                if notifier and settings.get("notifications", {}).get("immediate_quality_alerts"):
                    recipients = settings.get("notifications", {}).get("recipients", [])
                    for failure in failures:
                        notifier.send_immediate_alert(
                            subject=f"Quality Alert - {failure.details[:50]}",
                            message=failure.details,
                            recipients=recipients,
                        )
            return jsonify({
                "status": "received",
                "local_id": local_id,
                "action": "analyzed",
                "results": len(results),
                "failures": len(failures),
            }), 200
        except Exception as e:
            logger.error("Error running immediate quality check: %s", e)

    return jsonify({"status": "received", "local_id": local_id, "action": "quality_stored"}), 200
