"""Claude Vision API wrapper for analyzing quality inspection images."""

import base64
import json
import logging

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class ClaudeVisionAnalyzer:
    """Analyzes images using Claude's vision capabilities."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514", max_tokens: int = 1024):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def analyze_image(self, image_data: bytes, prompt: str, media_type: str = "image/jpeg") -> dict:
        """Analyze an image using Claude Vision and return structured results.

        Args:
            image_data: Raw image bytes.
            prompt: The analysis prompt to send with the image.
            media_type: MIME type of the image (e.g., "image/jpeg", "image/png").

        Returns:
            Dict with keys: result ("PASS"/"FAIL"), confidence (0-1), details (str).
            On parse failure, returns result="ERROR" with details.
        """
        b64_image = base64.b64encode(image_data).decode("utf-8")

        logger.debug("Sending image to Claude Vision (model=%s, %d bytes)", self.model, len(image_data))

        message = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64_image,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
        )

        response_text = message.content[0].text
        logger.debug("Claude Vision response: %s", response_text)

        return self._parse_response(response_text)

    def _parse_response(self, response_text: str) -> dict:
        """Parse Claude's response text into a structured result dict."""
        # Try to extract JSON from the response
        try:
            # Look for JSON in the response (Claude might wrap it in markdown)
            text = response_text.strip()
            if "```" in text:
                # Extract from code block
                start = text.index("{")
                end = text.rindex("}") + 1
                text = text[start:end]
            elif text.startswith("{"):
                pass  # Already JSON
            else:
                # Try to find JSON object in the text
                start = text.index("{")
                end = text.rindex("}") + 1
                text = text[start:end]

            result = json.loads(text)

            # Normalize the result
            return {
                "result": result.get("result", "ERROR").upper(),
                "confidence": float(result.get("confidence", 0.0)),
                "details": str(result.get("details", "")),
            }
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Failed to parse Claude Vision response as JSON: %s", e)
            # Fall back to text analysis
            upper_text = response_text.upper()
            if "FAIL" in upper_text:
                return {"result": "FAIL", "confidence": 0.5, "details": response_text}
            elif "PASS" in upper_text:
                return {"result": "PASS", "confidence": 0.5, "details": response_text}
            return {"result": "ERROR", "confidence": 0.0, "details": f"Could not parse response: {response_text}"}
