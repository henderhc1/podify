from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent
CODE_DIR = ROOT / "podify"


FORBIDDEN_PATTERNS = (
    "googleapiclient",
    "youtube.googleapis.com",
    "youtube/v3",
    "google-api-python-client",
)


class ArchitectureTests(unittest.TestCase):
    def test_no_youtube_api_patterns_in_source(self) -> None:
        for path in CODE_DIR.rglob("*.py"):
            content = path.read_text(encoding="utf-8").lower()
            for pattern in FORBIDDEN_PATTERNS:
                self.assertNotIn(
                    pattern,
                    content,
                    f"Found forbidden YouTube API pattern '{pattern}' in {path}",
                )

    def test_no_youtube_api_client_dependency(self) -> None:
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertNotIn("google-api-python-client", requirements)


if __name__ == "__main__":
    unittest.main()
