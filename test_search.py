import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import main


def make_entry(index: int) -> dict:
    video_id = f"videoidx{index:03d}"
    return {
        "id": video_id,
        "title": f"Result {index}",
        "uploader": f"Channel {index}",
        "duration": 120 + index,
        "thumbnail": f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        "description": f"Description for result {index}",
    }


def make_playback_info(video_id: str) -> dict:
    return {
        "id": video_id,
        "title": f"Playable {video_id}",
        "uploader": "Preview Channel",
        "duration": 245,
        "description": f"Playback description for {video_id}",
        "formats": [
            {
                "format_id": "18",
                "ext": "mp4",
                "protocol": "https",
                "acodec": "mp4a.40.2",
                "vcodec": "avc1.42001E",
                "height": 360,
                "tbr": 800,
                "url": f"https://media.example.com/{video_id}-360.mp4",
            },
            {
                "format_id": "22",
                "ext": "mp4",
                "protocol": "https",
                "acodec": "mp4a.40.2",
                "vcodec": "avc1.64001F",
                "height": 720,
                "tbr": 1800,
                "url": f"https://media.example.com/{video_id}-720.mp4",
            },
        ],
    }


class FakeYoutubeDL:
    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, query, download=False):
        if query.startswith("ytsearch10:"):
            return {"entries": [make_entry(index) for index in range(10)]}
        if query.startswith("https://www.youtube.com/watch?v="):
            return make_playback_info(query.split("v=", 1)[1])
        raise AssertionError(f"Unexpected query: {query}")


class FlatSearchYoutubeDL(FakeYoutubeDL):
    last_options = None

    def __init__(self, options):
        super().__init__(options)
        type(self).last_options = options

    def extract_info(self, query, download=False):
        if query.startswith("ytsearch10:"):
            if not self.options.get("extract_flat"):
                raise RuntimeError("Sign in to confirm you're not a bot")
            return {
                "entries": [
                    {
                        "id": "videoidx111",
                        "title": "Flat result",
                        "channel": "Flat Channel",
                        "duration": 187,
                        "url": "https://www.youtube.com/watch?v=videoidx111",
                    }
                ]
            }
        return super().extract_info(query, download=download)


class BotCheckYoutubeDL(FakeYoutubeDL):
    def extract_info(self, query, download=False):
        raise RuntimeError(
            "Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies."
        )


class SearchTests(unittest.TestCase):
    def setUp(self):
        fd, self.state_path = tempfile.mkstemp(prefix="podify-search-", suffix=".json")
        os.close(fd)
        os.environ["PODIFY_STATE_PATH"] = self.state_path
        main.save_state(main.clone_default_state())

    def tearDown(self):
        os.environ.pop("PODIFY_STATE_PATH", None)
        if os.path.exists(self.state_path):
            os.remove(self.state_path)

    @patch("main.yt_dlp.YoutubeDL", FakeYoutubeDL)
    def test_search_returns_results_with_blocklist_filtering(self):
        state = main.load_state()
        state["blocked_videos"].append(
            {
                "video_id": "videoidx005",
                "title": "Blocked result",
                "video_url": "https://www.youtube.com/watch?v=videoidx005",
                "reason": "DMCA notice received",
                "source": "dmca_notice",
                "blocked_at": "2026-03-25T00:00:00+00:00",
            }
        )
        main.save_state(state)

        results = asyncio.run(main.search("focus mode"))

        self.assertEqual(len(results), 9)
        self.assertTrue(all(item["video_id"] != "videoidx005" for item in results))
        self.assertEqual(results[0]["title"], "Result 0")
        self.assertEqual(results[0]["duration"], "2:00")
        self.assertIn("embed", results[0]["embed_url"])
        self.assertEqual(results[0]["playback_url"], "/playback/videoidx000")
        self.assertIn("Description for result 0", results[0]["description"])

    def test_search_rejects_non_youtube_urls(self):
        with self.assertRaises(HTTPException) as invalid_url:
            asyncio.run(main.search("https://example.com/watch?v=videoidx000"))

        self.assertEqual(invalid_url.exception.status_code, 422)

    @patch("main.yt_dlp.YoutubeDL", FlatSearchYoutubeDL)
    def test_search_uses_flat_yt_dlp_results_for_query_searches(self):
        results = asyncio.run(main.search("hello"))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["video_id"], "videoidx111")
        self.assertEqual(results[0]["duration"], "3:07")
        self.assertEqual(FlatSearchYoutubeDL.last_options["extract_flat"], "in_playlist")
        self.assertTrue(FlatSearchYoutubeDL.last_options["lazy_playlist"])

    @patch("main.yt_dlp.YoutubeDL", BotCheckYoutubeDL)
    def test_search_returns_operator_guidance_on_youtube_bot_check(self):
        with self.assertRaises(HTTPException) as blocked:
            asyncio.run(main.search("hello"))

        self.assertEqual(blocked.exception.status_code, 503)
        self.assertIn("PODIFY_YTDLP_COOKIE_FILE", blocked.exception.detail)

    @patch("main.yt_dlp.YoutubeDL", FakeYoutubeDL)
    def test_playback_resolves_best_browser_stream(self):
        playback = asyncio.run(main.get_playback("videoidx000"))

        self.assertEqual(playback["video_id"], "videoidx000")
        self.assertEqual(playback["stream_url"], "https://media.example.com/videoidx000-720.mp4")
        self.assertEqual(playback["mime_type"], "video/mp4")
        self.assertEqual(playback["playback_url"], "/playback/videoidx000")
        self.assertEqual(len(playback["sources"]), 2)


if __name__ == "__main__":
    unittest.main()
