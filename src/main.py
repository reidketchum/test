"""Main entry point for the GoCanvas Production Verification App.

Modes:
    python -m src.main                     # Start Flask webhook server + daily scheduler
    python -m src.main --check-date 2026-04-02  # Run all checks for a specific date
    python -m src.main --server-only       # Start webhook server without scheduler
"""

import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask

from src.config import (
    load_settings,
    get_db_connection_string,
    get_gocanvas_credentials,
    get_anthropic_api_key,
    get_smtp_config,
    get_flask_config,
    setup_logging,
    ROOT_DIR,
)
from src.clients.sqlserver import SqlServerClient
from src.clients.gocanvas import GoCanvasClient
from src.clients.claude_vision import ClaudeVisionAnalyzer
from src.checks.quality_images import QualityImagesCheck
from src.checks.first_piece import FirstPieceCheck
from src.checks.line_clearance import LineClearanceCheck
from src.reporting.models import VerificationReport
from src.reporting.html_report import generate_html_report
from src.reporting.csv_report import generate_csv_report
from src.notifications.email import EmailNotifier
from src.webhook.routes import webhook_bp, init_submission_db

logger = logging.getLogger(__name__)


def create_app(settings: dict) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)

    flask_config = get_flask_config()
    app.secret_key = flask_config["secret_key"]
    app.config["SETTINGS"] = settings

    # Register webhook blueprint
    app.register_blueprint(webhook_bp)

    # Health check endpoint
    @app.route("/health")
    def health():
        return {"status": "ok"}, 200

    return app


def create_clients(settings: dict) -> tuple:
    """Instantiate all API clients."""
    # SQL Server
    db_client = SqlServerClient(get_db_connection_string())

    # GoCanvas
    gc_creds = get_gocanvas_credentials()
    gc_client = GoCanvasClient(
        username=gc_creds["username"],
        api_key=gc_creds["api_key"],
        base_url=settings.get("gocanvas", {}).get("base_url", "https://www.gocanvas.com/apiv2"),
    )

    # Claude Vision
    vision_config = settings.get("claude_vision", {})
    vision_client = ClaudeVisionAnalyzer(
        api_key=get_anthropic_api_key(),
        model=vision_config.get("model", "claude-sonnet-4-20250514"),
        max_tokens=vision_config.get("max_tokens", 1024),
    )

    return db_client, gc_client, vision_client


def create_checks(db_client, gc_client, vision_client, settings: dict) -> list:
    """Instantiate all verification checks."""
    return [
        QualityImagesCheck(gocanvas=gc_client, vision=vision_client, settings=settings),
        FirstPieceCheck(db=db_client, gocanvas=gc_client, settings=settings),
        LineClearanceCheck(db=db_client, gocanvas=gc_client, settings=settings),
    ]


def run_checks(checks: list, target_date: date) -> VerificationReport:
    """Run all verification checks and return a report."""
    report = VerificationReport(target_date=target_date)

    for check in checks:
        logger.info("Running check: %s for date %s", check.name, target_date)
        try:
            results = check.run(target_date)
            report.results.extend(results)
            logger.info(
                "Check '%s' complete: %d results",
                check.name, len(results),
            )
        except Exception as e:
            logger.error("Check '%s' failed with error: %s", check.name, e)
            from src.checks.base import CheckResult
            report.results.append(CheckResult(
                check_name=check.name,
                status="ERROR",
                details=f"Check failed with unexpected error: {e}",
            ))

    return report


def generate_and_send_report(report: VerificationReport, settings: dict):
    """Generate report files and send email notification."""
    # Output directory
    output_dir = ROOT_DIR / "reports" / report.target_date.isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate reports
    html_path = output_dir / "report.html"
    csv_path = output_dir / "report.csv"

    html_content = generate_html_report(report, html_path)
    generate_csv_report(report, csv_path)

    logger.info("Reports generated in %s", output_dir)

    # Log summary
    logger.info(
        "Report summary for %s: Total=%d, Pass=%d, Fail=%d, Error=%d, Skipped=%d",
        report.target_date,
        report.total_checks,
        report.passed,
        report.failed,
        report.errors,
        report.skipped,
    )

    # Send email
    try:
        smtp_config = get_smtp_config()
        if smtp_config.get("user") and smtp_config.get("password"):
            notifier = EmailNotifier(smtp_config)
            notification_config = settings.get("notifications", {})
            recipients = notification_config.get("recipients", [])
            subject = notification_config.get("subject_template", "Quality Report - {date}").format(
                date=report.target_date.isoformat()
            )

            notifier.send_report(
                subject=subject,
                html_body=html_content,
                recipients=recipients,
                attachments=[csv_path],
            )
        else:
            logger.warning("SMTP credentials not configured, skipping email notification")
    except Exception as e:
        logger.error("Failed to send email notification: %s", e)


def run_daily_check(settings: dict):
    """Run the daily compliance check (called by scheduler or CLI)."""
    schedule_config = settings.get("schedule", {})
    lookback_days = schedule_config.get("lookback_days", 1)
    target_date = date.today() - timedelta(days=lookback_days)

    logger.info("Starting daily compliance check for %s", target_date)

    db_client, gc_client, vision_client = create_clients(settings)
    checks = create_checks(db_client, gc_client, vision_client, settings)
    report = run_checks(checks, target_date)
    generate_and_send_report(report, settings)

    logger.info("Daily compliance check complete for %s", target_date)


def main():
    """Main entry point."""
    setup_logging()

    parser = argparse.ArgumentParser(description="GoCanvas Production Verification App")
    parser.add_argument(
        "--check-date",
        type=str,
        help="Run checks for a specific date (YYYY-MM-DD). Runs and exits.",
    )
    parser.add_argument(
        "--server-only",
        action="store_true",
        help="Start the webhook server without the daily scheduler.",
    )
    args = parser.parse_args()

    settings = load_settings()

    # Initialize submission database
    db_path = settings.get("webhook", {}).get("submission_db", "data/submissions.db")
    init_submission_db(db_path)

    # Mode 1: One-shot check for a specific date
    if args.check_date:
        target = date.fromisoformat(args.check_date)
        logger.info("Running one-shot checks for %s", target)

        db_client, gc_client, vision_client = create_clients(settings)
        checks = create_checks(db_client, gc_client, vision_client, settings)
        report = run_checks(checks, target)
        generate_and_send_report(report, settings)
        return

    # Mode 2/3: Start Flask server (with or without scheduler)
    app = create_app(settings)

    # Wire up the quality checker for immediate webhook analysis
    try:
        _, gc_client, vision_client = create_clients(settings)
        quality_checker = QualityImagesCheck(gocanvas=gc_client, vision=vision_client, settings=settings)
        app.config["QUALITY_CHECKER"] = quality_checker
    except Exception as e:
        logger.warning("Could not initialize quality checker for webhook mode: %s", e)

    if not args.server_only:
        # Start APScheduler for daily checks
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
            import pytz

            scheduler = BackgroundScheduler()
            schedule_config = settings.get("schedule", {})
            run_time = schedule_config.get("daily_run_time", "06:00")
            tz = schedule_config.get("timezone", "America/New_York")
            hour, minute = run_time.split(":")

            scheduler.add_job(
                run_daily_check,
                trigger=CronTrigger(hour=int(hour), minute=int(minute), timezone=pytz.timezone(tz)),
                args=[settings],
                id="daily_compliance_check",
                name="Daily Compliance Check",
            )
            scheduler.start()
            logger.info("Scheduler started: daily check at %s %s", run_time, tz)
        except Exception as e:
            logger.error("Failed to start scheduler: %s", e)

    # Start Flask server
    flask_config = get_flask_config()
    logger.info("Starting webhook server on %s:%d", flask_config["host"], flask_config["port"])
    app.run(
        host=flask_config["host"],
        port=flask_config["port"],
        debug=False,
    )


if __name__ == "__main__":
    main()
