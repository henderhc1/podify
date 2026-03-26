import asyncio
import os
import tempfile
import unittest
from http.cookies import SimpleCookie
from unittest.mock import patch

from fastapi import HTTPException, Response
from starlette.requests import Request

import main
from podify.auth import ACCESS_SESSION_COOKIE, require_active_user
from test_search import FakeYoutubeDL


LIBRARY_VIDEO = {
    "video_id": "videoidx111",
    "title": "Library video",
    "channel": "Preview Channel",
    "duration": "12:34",
    "thumbnail_url": "https://img.youtube.com/vi/videoidx111/hqdefault.jpg",
    "video_url": "https://www.youtube.com/watch?v=videoidx111",
    "embed_url": "https://www.youtube-nocookie.com/embed/videoidx111",
    "description": "Library preview description",
}


def build_request(
    path: str,
    *,
    method: str = "GET",
    cookies: dict[str, str] | None = None,
    scheme: str = "http",
) -> Request:
    headers = [(b"host", b"testserver")]
    if cookies:
        cookie_header = "; ".join(f"{name}={value}" for name, value in cookies.items())
        headers.append((b"cookie", cookie_header.encode("utf-8")))

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": b"",
            "headers": headers,
            "scheme": scheme,
            "client": ("127.0.0.1", 5000),
            "server": ("testserver", 80),
        }
    )


def response_cookie_value(response: Response, cookie_name: str) -> str | None:
    raw_cookie = response.headers.get("set-cookie", "")
    jar = SimpleCookie()
    jar.load(raw_cookie)
    morsel = jar.get(cookie_name)
    return morsel.value if morsel else None


class RequestFlowTests(unittest.TestCase):
    def setUp(self):
        fd, self.state_path = tempfile.mkstemp(prefix="podify-request-", suffix=".json")
        os.close(fd)
        os.environ["PODIFY_STATE_PATH"] = self.state_path
        os.environ["PODIFY_MAX_ACTIVE_USERS"] = "1"
        os.environ["PODIFY_EXPOSE_DEMO_VERIFICATION"] = "1"
        main.save_state(main.clone_default_state())

    def tearDown(self):
        os.environ.pop("PODIFY_STATE_PATH", None)
        os.environ.pop("PODIFY_MAX_ACTIVE_USERS", None)
        os.environ.pop("PODIFY_EXPOSE_DEMO_VERIFICATION", None)
        if os.path.exists(self.state_path):
            os.remove(self.state_path)

    def test_registration_cap_and_admin_approval_flow(self):
        first_request = asyncio.run(main.request_access({"email": "first@example.com"}))
        first_response = Response()
        first_verification = asyncio.run(
            main.verify_access_request(
                first_request["verification_token"],
                first_response,
                build_request("/register/verify"),
            )
        )
        self.assertEqual(first_verification["status"], "active")
        self.assertTrue(response_cookie_value(first_response, ACCESS_SESSION_COOKIE))

        second_request = asyncio.run(main.request_access({"email": "second@example.com"}))
        second_response = Response()
        second_verification = asyncio.run(
            main.verify_access_request(
                second_request["verification_token"],
                second_response,
                build_request("/register/verify"),
            )
        )
        self.assertEqual(second_verification["status"], "waitlisted")
        second_cookie = response_cookie_value(second_response, ACCESS_SESSION_COOKIE)
        self.assertTrue(second_cookie)
        with self.assertRaises(HTTPException) as waitlisted_access:
            require_active_user(
                build_request("/search", cookies={ACCESS_SESSION_COOKIE: second_cookie})
            )
        self.assertEqual(waitlisted_access.exception.status_code, 403)

        with self.assertRaises(HTTPException) as blocked_by_cap:
            asyncio.run(main.admin_approve_user({"email": "second@example.com"}))
        self.assertEqual(blocked_by_cap.exception.status_code, 409)

        delete_result = asyncio.run(main.admin_delete_user("first@example.com"))
        self.assertEqual(delete_result["status"], "deleted")

        approval_result = asyncio.run(main.admin_approve_user({"email": "second@example.com"}))
        self.assertEqual(approval_result["status"], "approved")
        self.assertEqual(approval_result["user"]["status"], "active")

    def test_dmca_notice_removes_video_from_library(self):
        add_result = asyncio.run(main.add_to_library(LIBRARY_VIDEO))
        self.assertEqual(add_result["status"], "added")
        library_items = asyncio.run(main.get_library())
        self.assertEqual(len(library_items), 1)
        self.assertEqual(
            library_items[0]["embed_url"],
            "https://www.youtube.com/embed/videoidx111",
        )
        self.assertEqual(
            library_items[0]["playback_url"],
            "/playback/videoidx111",
        )

        notice_result = asyncio.run(
            main.submit_dmca_notice(
                {
                    "reporter_name": "Rights Holder",
                    "reporter_email": "rights@example.com",
                    "video_url": LIBRARY_VIDEO["video_url"],
                    "work_description": "Original copyrighted lecture",
                    "statement": "I have a good-faith belief this preview is unauthorized.",
                }
            )
        )
        self.assertEqual(notice_result["status"], "received")
        self.assertEqual(len(asyncio.run(main.get_library())), 0)

        dmca_info = asyncio.run(main.get_dmca_info())
        self.assertEqual(dmca_info["notice_count"], 1)
        self.assertEqual(dmca_info["blocked_videos"][0]["video_id"], LIBRARY_VIDEO["video_id"])

    @patch("main.yt_dlp.YoutubeDL", FakeYoutubeDL)
    def test_service_routes_require_verified_session(self):
        with self.assertRaises(HTTPException) as unauthenticated_search:
            require_active_user(build_request("/search"))
        self.assertEqual(unauthenticated_search.exception.status_code, 401)

        request = asyncio.run(main.request_access({"email": "viewer@example.com"}))
        verify_response = Response()
        verification = asyncio.run(
            main.verify_access_request(
                request["verification_token"],
                verify_response,
                build_request("/register/verify"),
            )
        )
        self.assertEqual(verification["status"], "active")
        cookie_value = response_cookie_value(verify_response, ACCESS_SESSION_COOKIE)
        self.assertTrue(cookie_value)

        config = asyncio.run(main.get_config(build_request("/config", cookies={ACCESS_SESSION_COOKIE: cookie_value})))
        self.assertTrue(config["access"]["service_access"])
        self.assertEqual(config["access"]["user"]["email"], "viewer@example.com")

        current_user = require_active_user(
            build_request("/search", cookies={ACCESS_SESSION_COOKIE: cookie_value})
        )
        authenticated_search = asyncio.run(main.search("focus mode", current_user))
        self.assertEqual(len(authenticated_search), 10)

        logout_response = Response()
        logout = asyncio.run(
            main.logout_access_session(
                logout_response,
                build_request(
                    "/session/logout",
                    method="POST",
                    cookies={ACCESS_SESSION_COOKIE: cookie_value},
                ),
            )
        )
        self.assertEqual(logout["status"], "signed_out")

        with self.assertRaises(HTTPException) as signed_out_search:
            require_active_user(build_request("/search", cookies={ACCESS_SESSION_COOKIE: cookie_value}))
        self.assertEqual(signed_out_search.exception.status_code, 401)

    def test_admin_can_generate_test_access_link_for_active_user(self):
        asyncio.run(main.admin_add_user({"email": "tester@example.com", "status": "active"}))

        access_link = asyncio.run(main.admin_create_access_link({"email": "tester@example.com"}))
        self.assertEqual(access_link["status"], "created")
        self.assertIn("/register/verify?token=", access_link["access_url"])

        token = access_link["access_url"].split("token=", 1)[1]
        verify_response = Response()
        verification = asyncio.run(
            main.verify_access_request(
                token,
                verify_response,
                build_request("/register/verify"),
            )
        )
        self.assertEqual(verification["status"], "active")
        cookie_value = response_cookie_value(verify_response, ACCESS_SESSION_COOKIE)
        self.assertTrue(cookie_value)
        current_user = require_active_user(
            build_request("/search", cookies={ACCESS_SESSION_COOKIE: cookie_value})
        )
        self.assertEqual(current_user["email"], "tester@example.com")

    def test_secure_mode_hides_demo_verification_token(self):
        os.environ["PODIFY_EXPOSE_DEMO_VERIFICATION"] = "0"

        request = asyncio.run(main.request_access({"email": "secure@example.com"}))

        self.assertEqual(request["status"], "pending_verification")
        self.assertNotIn("verification_token", request)
        self.assertNotIn("verification_url", request)

        state = main.load_state()
        user = main.find_user(state, "secure@example.com")
        self.assertIsNotNone(user)
        self.assertIsNone(user.get("verification_token"))
        self.assertTrue(user.get("verification_token_hash"))


if __name__ == "__main__":
    unittest.main()
