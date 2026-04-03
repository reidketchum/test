"""HTML report generator using Jinja2 templates."""

import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.reporting.models import VerificationReport

logger = logging.getLogger(__name__)

# Template directory
TEMPLATE_DIR = Path(__file__).parent.parent.parent / "templates"


def generate_html_report(report: VerificationReport, output_path: Path | None = None) -> str:
    """Generate an HTML report from verification results.

    Args:
        report: The VerificationReport to render.
        output_path: Optional path to write the HTML file. If None, returns HTML string only.

    Returns:
        The rendered HTML string.
    """
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("report.html.j2")

    html = template.render(
        report=report,
        summary=report.summary_by_check(),
        results_by_check=report.results_by_check(),
        failures=report.failures_only(),
    )

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html)
        logger.info("HTML report written to %s", output_path)

    return html
