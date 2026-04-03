"""Line Clearance verification check.

Verifies that a Line Clearance form was submitted in GoCanvas when
production transactions show two different items produced on the same
line on the same day.
"""

import logging
from datetime import date

from src.checks.base import BaseCheck, CheckResult
from src.clients.gocanvas import GoCanvasClient
from src.clients.sqlserver import SqlServerClient
from src.webhook.routes import get_submissions_by_tag_and_date

logger = logging.getLogger(__name__)


class LineClearanceCheck(BaseCheck):
    """Verify line clearance forms exist when multiple items run on the same line."""

    def __init__(
        self,
        db: SqlServerClient,
        gocanvas: GoCanvasClient,
        settings: dict,
    ):
        self.db = db
        self.gocanvas = gocanvas
        self.settings = settings
        self.form_config = settings.get("gocanvas", {}).get("forms", {}).get("line_clearance", {})
        self.db_queries = settings.get("database", {}).get("queries", {})
        self.db_path = settings.get("webhook", {}).get("submission_db", "data/submissions.db")

    @property
    def name(self) -> str:
        return "Line Clearance"

    def run(self, target_date: date) -> list[CheckResult]:
        """Check that lines with multiple items have line clearance forms."""
        results = []

        # Step 1: Find lines that produced multiple distinct items
        try:
            query = self.db_queries.get("items_per_line_per_day", "")
            if not query:
                return [CheckResult(
                    check_name=self.name,
                    status="ERROR",
                    details="No items_per_line_per_day query configured in settings.yaml",
                )]

            multi_item_lines = self.db.execute_query(query, (target_date.isoformat(),))
            logger.info(
                "Found %d lines with multiple items on %s",
                len(multi_item_lines), target_date,
            )
        except Exception as e:
            logger.error("Error querying multi-item lines: %s", e)
            return [CheckResult(
                check_name=self.name,
                status="ERROR",
                details=f"Database query error: {e}",
            )]

        if not multi_item_lines:
            return [CheckResult(
                check_name=self.name,
                status="SKIPPED",
                details=f"No lines with multiple items found for {target_date}",
            )]

        # Step 2: Get line clearance submissions
        clearances = self._get_clearances(target_date)
        logger.info("Found %d line clearance submissions", len(clearances))

        # Build set of lines that have clearance forms
        match_fields = self.form_config.get("match_fields", {})
        line_field = match_fields.get("line", "production_line")

        cleared_lines = set()
        for clearance in clearances:
            c_line = str(clearance.get("line_id", clearance.get(line_field, ""))).strip()
            if c_line:
                cleared_lines.add(c_line)

        # Step 3: Cross-reference
        for row in multi_item_lines:
            line = str(row.get("line_id", "")).strip()
            item_count = row.get("item_count", 0)

            if line in cleared_lines:
                results.append(CheckResult(
                    check_name=self.name,
                    status="PASS",
                    details=f"Line clearance found for Line {line} ({item_count} different items produced)",
                    metadata={"line_id": line, "item_count": item_count},
                ))
            else:
                results.append(CheckResult(
                    check_name=self.name,
                    status="FAIL",
                    details=f"MISSING line clearance: Line {line} produced {item_count} different items but no clearance form submitted",
                    metadata={"line_id": line, "item_count": item_count},
                ))

        return results

    def _get_clearances(self, target_date: date) -> list[dict]:
        """Get line clearance submissions from webhook store and GoCanvas API."""
        clearances = []

        # From webhook submissions store
        try:
            webhook_subs = get_submissions_by_tag_and_date(
                self.db_path,
                self.form_config.get("webhook_tag", "line-clearance"),
                target_date,
            )
            clearances.extend(webhook_subs)
        except Exception as e:
            logger.error("Error reading webhook submissions: %s", e)

        # From GoCanvas API (fallback)
        try:
            form_name = self.form_config.get("name", "")
            if form_name:
                api_subs = self.gocanvas.get_submissions(form_name, target_date)
                match_fields = self.form_config.get("match_fields", {})
                for sub in api_subs:
                    normalized = {}
                    for target, source in match_fields.items():
                        value = self.gocanvas.get_submission_field(sub, source)
                        field_map = {"line": "line_id"}
                        normalized[field_map.get(target, target)] = value or ""
                    clearances.append(normalized)
        except Exception as e:
            logger.error("Error fetching line clearances from API: %s", e)

        return clearances
