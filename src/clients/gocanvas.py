"""GoCanvas REST API client for fetching form submissions and images."""

import logging
import xml.etree.ElementTree as ET
from datetime import date

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)


class GoCanvasClient:
    """Client for the GoCanvas API v2 (XML-based)."""

    def __init__(self, username: str, api_key: str, base_url: str = "https://www.gocanvas.com/apiv2"):
        self.username = username
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def _auth_params(self) -> dict:
        """Return authentication parameters for API requests."""
        return {
            "username": self.username,
        }

    def _auth_headers(self) -> dict:
        """Return authentication headers for API requests."""
        return {
            "Authorization": f"Bearer {self.api_key}",
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    )
    def get_submissions(
        self, form_name: str, begin_date: date, end_date: date | None = None
    ) -> list[dict]:
        """Fetch form submissions from GoCanvas for a date range.

        Args:
            form_name: Name of the GoCanvas form/app.
            begin_date: Start date for submissions.
            end_date: End date (defaults to begin_date for single-day queries).

        Returns:
            List of submission dicts with normalized field names.
        """
        if end_date is None:
            end_date = begin_date

        all_submissions = []
        page = 1

        while True:
            params = {
                **self._auth_params(),
                "form_name": form_name,
                "begin_date": begin_date.isoformat(),
                "end_date": end_date.isoformat(),
                "page_number": page,
            }

            logger.debug("Fetching GoCanvas submissions: form=%s, page=%d", form_name, page)
            response = self.session.get(
                f"{self.base_url}/submissions.xml",
                params=params,
                headers=self._auth_headers(),
                timeout=30,
            )
            response.raise_for_status()

            submissions, total_pages = self._parse_submissions_xml(response.text)
            all_submissions.extend(submissions)

            logger.info(
                "Fetched page %d/%d for form '%s': %d submissions",
                page, total_pages, form_name, len(submissions),
            )

            if page >= total_pages:
                break
            page += 1

        logger.info(
            "Total submissions for '%s' (%s to %s): %d",
            form_name, begin_date, end_date, len(all_submissions),
        )
        return all_submissions

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    )
    def get_submission_by_id(self, submission_id: int) -> dict | None:
        """Fetch a single submission by its GoCanvas submission ID.

        Args:
            submission_id: The GoCanvas submission ID from the webhook notification.

        Returns:
            Parsed submission dict, or None if not found.
        """
        params = {
            **self._auth_params(),
            "submission_id": submission_id,
        }

        logger.debug("Fetching GoCanvas submission by ID: %d", submission_id)
        response = self.session.get(
            f"{self.base_url}/submissions.xml",
            params=params,
            headers=self._auth_headers(),
            timeout=30,
        )
        response.raise_for_status()

        submissions, _ = self._parse_submissions_xml(response.text)
        if submissions:
            logger.info("Fetched submission %d: %d fields", submission_id, len(submissions[0]))
            return submissions[0]

        logger.warning("Submission %d not found in API response", submission_id)
        return None

    def _parse_submissions_xml(self, xml_text: str) -> tuple[list[dict], int]:
        """Parse GoCanvas submissions XML response into list of dicts.

        Returns:
            Tuple of (submissions list, total page count).
        """
        root = ET.fromstring(xml_text)
        submissions = []
        total_pages = 1

        # Try to find page count
        page_count_el = root.find(".//TotalPages")
        if page_count_el is not None and page_count_el.text:
            total_pages = int(page_count_el.text)

        # Parse each submission
        for submission_el in root.findall(".//Submission"):
            submission = self._element_to_dict(submission_el)
            submissions.append(submission)

        return submissions, total_pages

    def _element_to_dict(self, element: ET.Element) -> dict:
        """Recursively convert an XML element and its children to a dict."""
        result = {}

        # Add element attributes
        result.update(element.attrib)

        for child in element:
            tag = child.tag
            if len(child) > 0:
                # Child has sub-elements, recurse
                child_dict = self._element_to_dict(child)
                if tag in result:
                    # Multiple children with same tag -> list
                    if not isinstance(result[tag], list):
                        result[tag] = [result[tag]]
                    result[tag].append(child_dict)
                else:
                    result[tag] = child_dict
            else:
                # Leaf element
                result[tag] = child.text or ""

        # If element has text content and no children
        if element.text and element.text.strip() and not list(element):
            result["_text"] = element.text.strip()

        return result

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    )
    def get_image(self, image_url: str) -> bytes:
        """Download an image from a GoCanvas submission.

        Args:
            image_url: URL of the image attachment.

        Returns:
            Raw image bytes.
        """
        logger.debug("Downloading image: %s", image_url)
        response = self.session.get(
            image_url,
            headers=self._auth_headers(),
            timeout=60,
        )
        response.raise_for_status()
        logger.info("Downloaded image: %d bytes", len(response.content))
        return response.content

    def get_submission_field(self, submission: dict, field_name: str) -> str | None:
        """Extract a field value from a parsed submission dict.

        Searches recursively through the submission structure for the field name.

        Args:
            submission: Parsed submission dict from get_submissions().
            field_name: The field/question name to look for.

        Returns:
            The field value as a string, or None if not found.
        """
        return self._find_field(submission, field_name)

    def _find_field(self, data: dict | list | str, field_name: str) -> str | None:
        """Recursively search for a field name in a nested dict/list structure."""
        if isinstance(data, dict):
            # Check if this dict has a Name/Label matching the field
            for key in ("Name", "Label", "name", "label"):
                if data.get(key) == field_name:
                    # Return the value from Value/Response/value keys
                    for val_key in ("Value", "Response", "value", "response", "_text"):
                        if val_key in data:
                            return data[val_key]

            # Check direct key match
            if field_name in data:
                val = data[field_name]
                if isinstance(val, str):
                    return val
                if isinstance(val, dict):
                    return val.get("_text") or val.get("Value") or val.get("value")

            # Recurse into values
            for value in data.values():
                result = self._find_field(value, field_name)
                if result is not None:
                    return result

        elif isinstance(data, list):
            for item in data:
                result = self._find_field(item, field_name)
                if result is not None:
                    return result

        return None

    def get_image_urls(self, submission: dict, image_fields: list[str]) -> list[str]:
        """Extract image URLs from a submission for the given field names.

        Args:
            submission: Parsed submission dict.
            image_fields: List of field names that contain image attachments.

        Returns:
            List of image URLs found.
        """
        urls = []
        for field_name in image_fields:
            url = self.get_submission_field(submission, field_name)
            if url and (url.startswith("http://") or url.startswith("https://")):
                urls.append(url)
        return urls

    def test_connection(self) -> bool:
        """Test that GoCanvas API credentials work."""
        try:
            params = {
                **self._auth_params(),
                "page_number": 1,
            }
            response = self.session.get(
                f"{self.base_url}/submissions.xml",
                params=params,
                headers=self._auth_headers(),
                timeout=15,
            )
            # A 200 or even a 400 (no form specified) means auth works
            if response.status_code in (200, 400):
                logger.info("GoCanvas connection test successful")
                return True
            logger.warning("GoCanvas returned status %d", response.status_code)
            return False
        except Exception as e:
            logger.error("GoCanvas connection test failed: %s", e)
            return False
