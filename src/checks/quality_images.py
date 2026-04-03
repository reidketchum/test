"""Quality Form Image Analysis check.

Analyzes photos from quality form submissions using Claude Vision
to detect pass/fail values or visible discrepancies.
"""

import json
import logging
from datetime import date

from src.checks.base import BaseCheck, CheckResult
from src.clients.claude_vision import ClaudeVisionAnalyzer
from src.clients.gocanvas import GoCanvasClient
from src.webhook.routes import get_submissions_by_tag_and_date

logger = logging.getLogger(__name__)


class QualityImagesCheck(BaseCheck):
    """Analyze quality form images for pass/fail determination."""

    def __init__(
        self,
        gocanvas: GoCanvasClient,
        vision: ClaudeVisionAnalyzer,
        settings: dict,
    ):
        self.gocanvas = gocanvas
        self.vision = vision
        self.settings = settings
        self.form_config = settings.get("gocanvas", {}).get("forms", {}).get("quality_check", {})
        self.vision_config = settings.get("claude_vision", {})
        self.db_path = settings.get("webhook", {}).get("submission_db", "data/submissions.db")

    @property
    def name(self) -> str:
        return "Quality Image Analysis"

    def run(self, target_date: date) -> list[CheckResult]:
        """Run quality image analysis for all submissions on the target date."""
        results = []

        # First, check webhook-received submissions
        webhook_submissions = get_submissions_by_tag_and_date(
            self.db_path,
            self.form_config.get("webhook_tag", "quality"),
            target_date,
        )

        if webhook_submissions:
            logger.info("Found %d webhook quality submissions for %s", len(webhook_submissions), target_date)
            for sub in webhook_submissions:
                try:
                    payload = json.loads(sub.get("raw_payload", "{}"))
                    sub_results = self.analyze_webhook_submission(payload, self.form_config)
                    results.extend(sub_results)
                except Exception as e:
                    logger.error("Error analyzing webhook submission %s: %s", sub.get("id"), e)
                    results.append(CheckResult(
                        check_name=self.name,
                        status="ERROR",
                        details=f"Error analyzing submission: {e}",
                        metadata={"submission_id": sub.get("id")},
                    ))

        # Also check via GoCanvas API (fallback / catch any missed by webhook)
        try:
            form_name = self.form_config.get("name", "")
            if form_name:
                api_submissions = self.gocanvas.get_submissions(form_name, target_date)
                logger.info("Found %d API quality submissions for %s", len(api_submissions), target_date)
                for sub in api_submissions:
                    sub_results = self._analyze_api_submission(sub)
                    results.extend(sub_results)
        except Exception as e:
            logger.error("Error fetching quality submissions from API: %s", e)
            results.append(CheckResult(
                check_name=self.name,
                status="ERROR",
                details=f"Error fetching from GoCanvas API: {e}",
            ))

        if not results:
            results.append(CheckResult(
                check_name=self.name,
                status="SKIPPED",
                details=f"No quality form submissions found for {target_date}",
            ))

        return results

    def analyze_webhook_submission(self, payload: dict, form_config: dict) -> list[CheckResult]:
        """Analyze images from a single webhook submission.

        Called both from the webhook handler (immediate) and the scheduled run.
        """
        results = []
        image_fields = form_config.get("image_fields", [])
        prompt = self.vision_config.get("prompt_template", "Analyze this quality inspection image.")

        # Try to find image URLs in the payload
        image_urls = []
        for field_name in image_fields:
            url = self._find_in_payload(payload, field_name)
            if url and isinstance(url, str) and url.startswith("http"):
                image_urls.append((field_name, url))

        if not image_urls:
            logger.info("No image URLs found in webhook payload")
            return results

        for field_name, url in image_urls:
            try:
                image_data = self.gocanvas.get_image(url)
                # Determine media type from URL
                media_type = "image/jpeg"
                if url.lower().endswith(".png"):
                    media_type = "image/png"

                analysis = self.vision.analyze_image(image_data, prompt, media_type)

                status = "PASS" if analysis["result"] == "PASS" else "FAIL"
                results.append(CheckResult(
                    check_name=self.name,
                    status=status,
                    details=analysis.get("details", ""),
                    metadata={
                        "field": field_name,
                        "image_url": url,
                        "confidence": analysis.get("confidence", 0),
                        "vision_result": analysis["result"],
                    },
                ))
            except Exception as e:
                logger.error("Error analyzing image %s from field %s: %s", url, field_name, e)
                results.append(CheckResult(
                    check_name=self.name,
                    status="ERROR",
                    details=f"Error analyzing image from {field_name}: {e}",
                    metadata={"field": field_name, "image_url": url},
                ))

        return results

    def _analyze_api_submission(self, submission: dict) -> list[CheckResult]:
        """Analyze images from a GoCanvas API submission."""
        results = []
        image_fields = self.form_config.get("image_fields", [])
        prompt = self.vision_config.get("prompt_template", "Analyze this quality inspection image.")

        image_urls = self.gocanvas.get_image_urls(submission, image_fields)

        for url in image_urls:
            try:
                image_data = self.gocanvas.get_image(url)
                media_type = "image/png" if url.lower().endswith(".png") else "image/jpeg"

                analysis = self.vision.analyze_image(image_data, prompt, media_type)

                status = "PASS" if analysis["result"] == "PASS" else "FAIL"
                results.append(CheckResult(
                    check_name=self.name,
                    status=status,
                    details=analysis.get("details", ""),
                    metadata={
                        "image_url": url,
                        "confidence": analysis.get("confidence", 0),
                        "vision_result": analysis["result"],
                    },
                ))
            except Exception as e:
                logger.error("Error analyzing API submission image %s: %s", url, e)
                results.append(CheckResult(
                    check_name=self.name,
                    status="ERROR",
                    details=f"Error analyzing image: {e}",
                    metadata={"image_url": url},
                ))

        return results

    def _find_in_payload(self, data, key):
        """Recursively find a key in a nested dict."""
        if isinstance(data, dict):
            if key in data:
                return data[key]
            for v in data.values():
                result = self._find_in_payload(v, key)
                if result is not None:
                    return result
        elif isinstance(data, list):
            for item in data:
                result = self._find_in_payload(item, key)
                if result is not None:
                    return result
        return None
