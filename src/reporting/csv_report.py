"""CSV report generator for verification results."""

import csv
import io
import json
import logging
from pathlib import Path

from src.reporting.models import VerificationReport

logger = logging.getLogger(__name__)


def generate_csv_report(report: VerificationReport, output_path: Path | None = None) -> str:
    """Generate a CSV report from verification results.

    Args:
        report: The VerificationReport to render.
        output_path: Optional path to write the CSV file.

    Returns:
        The CSV content as a string.
    """
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Check Name",
        "Status",
        "Details",
        "Metadata",
        "Report Date",
        "Generated At",
    ])

    # Data rows
    for result in report.results:
        writer.writerow([
            result.check_name,
            result.status,
            result.details,
            json.dumps(result.metadata) if result.metadata else "",
            report.target_date.isoformat(),
            report.generated_at.isoformat(),
        ])

    csv_content = output.getvalue()

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(csv_content)
        logger.info("CSV report written to %s", output_path)

    return csv_content
