"""First Piece Approval verification check.

Verifies that a First Piece Approval form was submitted in GoCanvas
for every production line/job/lot that had positive production transactions
in SQL Server on the target date.
"""

import logging
from datetime import date

from src.checks.base import BaseCheck, CheckResult
from src.clients.gocanvas import GoCanvasClient
from src.clients.sqlserver import SqlServerClient
from src.webhook.routes import get_submissions_by_tag_and_date

logger = logging.getLogger(__name__)


class FirstPieceCheck(BaseCheck):
    """Verify first piece approvals exist for all production runs."""

    def __init__(
        self,
        db: SqlServerClient,
        gocanvas: GoCanvasClient,
        settings: dict,
    ):
        self.db = db
        self.gocanvas = gocanvas
        self.settings = settings
        self.form_config = settings.get("gocanvas", {}).get("forms", {}).get("first_piece_approval", {})
        self.db_queries = settings.get("database", {}).get("queries", {})
        self.db_path = settings.get("webhook", {}).get("submission_db", "data/submissions.db")

    @property
    def name(self) -> str:
        return "First Piece Approval"

    def run(self, target_date: date) -> list[CheckResult]:
        """Check that all production runs have matching first piece approvals."""
        results = []

        # Step 1: Get positive production transactions from SQL Server
        try:
            query = self.db_queries.get("positive_transactions", "")
            if not query:
                return [CheckResult(
                    check_name=self.name,
                    status="ERROR",
                    details="No positive_transactions query configured in settings.yaml",
                )]

            transactions = self.db.execute_query(query, (target_date.isoformat(),))
            logger.info("Found %d production transactions for %s", len(transactions), target_date)
        except Exception as e:
            logger.error("Error querying production transactions: %s", e)
            return [CheckResult(
                check_name=self.name,
                status="ERROR",
                details=f"Database query error: {e}",
            )]

        if not transactions:
            return [CheckResult(
                check_name=self.name,
                status="SKIPPED",
                details=f"No positive production transactions found for {target_date}",
            )]

        # Step 2: Get unique (line, job, lot) combos from production data
        production_runs = set()
        for txn in transactions:
            line = str(txn.get("line_id", "")).strip()
            job = str(txn.get("job_number", "")).strip()
            lot = str(txn.get("lot_code", "")).strip()
            if line:
                production_runs.add((line, job, lot))

        logger.info("Found %d unique production runs (line/job/lot)", len(production_runs))

        # Step 3: Get first piece approval submissions (webhook + API)
        approvals = self._get_approvals(target_date)
        logger.info("Found %d first piece approval submissions", len(approvals))

        # Step 4: Cross-reference - find production runs missing approvals
        match_fields = self.form_config.get("match_fields", {})
        line_field = match_fields.get("line", "production_line")
        job_field = match_fields.get("job", "job_number")
        lot_field = match_fields.get("lot", "lot_code")

        approved_runs = set()
        for approval in approvals:
            a_line = str(approval.get("line_id", approval.get(line_field, ""))).strip()
            a_job = str(approval.get("job_number", approval.get(job_field, ""))).strip()
            a_lot = str(approval.get("lot_code", approval.get(lot_field, ""))).strip()
            if a_line:
                approved_runs.add((a_line, a_job, a_lot))

        # Step 5: Report results
        for line, job, lot in production_runs:
            if (line, job, lot) in approved_runs:
                results.append(CheckResult(
                    check_name=self.name,
                    status="PASS",
                    details=f"First piece approval found for Line {line}, Job {job}, Lot {lot}",
                    metadata={"line_id": line, "job_number": job, "lot_code": lot},
                ))
            else:
                results.append(CheckResult(
                    check_name=self.name,
                    status="FAIL",
                    details=f"MISSING first piece approval: Line {line}, Job {job}, Lot {lot} had production but no approval form",
                    metadata={"line_id": line, "job_number": job, "lot_code": lot},
                ))

        return results

    def _get_approvals(self, target_date: date) -> list[dict]:
        """Get first piece approvals from both webhook store and GoCanvas API."""
        approvals = []

        # From webhook submissions store
        try:
            webhook_subs = get_submissions_by_tag_and_date(
                self.db_path,
                self.form_config.get("webhook_tag", "first-piece"),
                target_date,
            )
            approvals.extend(webhook_subs)
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
                        field_map = {"line": "line_id", "job": "job_number", "lot": "lot_code"}
                        normalized[field_map.get(target, target)] = value or ""
                    approvals.append(normalized)
        except Exception as e:
            logger.error("Error fetching first piece approvals from API: %s", e)

        return approvals
