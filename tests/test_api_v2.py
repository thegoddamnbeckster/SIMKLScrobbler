# -*- coding: utf-8 -*-
"""
The API client under AUTH V2: which client id and identification a request carries, and when the token is renewed.
(The token logic itself is in test_oauth2.py.)
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tests.kodi_stubs  # noqa: F401 -- side-effect: stubs xbmc & friends

from resources.lib import api as api_mod
from resources.lib import oauth2

V2_TOKEN = "simkl_at_" + "a" * 34
V1_TOKEN = "f" * 64


def make_api(token):
    with patch.object(api_mod, "get_setting", side_effect=lambda key: token if key == "access_token" else ""):
        return api_mod.SimklAPI()


class Identification(unittest.TestCase):
    def test_a_v2_token_sends_the_v2_client_id_the_app_name_and_version_and_a_user_agent(self):
        api = make_api(V2_TOKEN)

        self.assertEqual(oauth2.CLIENT_ID_V2, api.session.headers["simkl-api-key"])
        self.assertEqual(oauth2.CLIENT_ID_V2, api.session.params["client_id"])
        self.assertEqual("kodi-simkl-scrobbler", api.session.params["app-name"])
        self.assertEqual(api_mod.__version__, api.session.params["app-version"])
        self.assertTrue(api.session.headers["User-Agent"].startswith("kodi-simkl-scrobbler/"))
        self.assertEqual(f"Bearer {V2_TOKEN}", api.session.headers["Authorization"])

    def test_an_existing_v1_token_keeps_the_v1_client_id(self):
        api = make_api(V1_TOKEN)

        self.assertEqual(oauth2.CLIENT_ID_V1, api.session.headers["simkl-api-key"])
        self.assertEqual(oauth2.CLIENT_ID_V1, api.session.params["client_id"])

    def test_adopting_a_renewed_token_switches_the_client_id_with_it(self):
        api = make_api(V1_TOKEN)
        with patch.object(api_mod, "get_setting", return_value=V2_TOKEN):
            fresh = MagicMock()
            fresh.getSetting.return_value = V2_TOKEN
            with patch.object(api_mod.xbmcaddon, "Addon", return_value=fresh):
                api.refresh_token()

        self.assertEqual(oauth2.CLIENT_ID_V2, api.session.headers["simkl-api-key"])
        self.assertEqual(f"Bearer {V2_TOKEN}", api.session.headers["Authorization"])

    def test_the_request_params_carry_the_identification_on_the_wire(self):
        api = make_api(V2_TOKEN)
        prepared = api.session.prepare_request(__import__("requests").Request("GET", "https://api.simkl.com/sync/activities"))

        self.assertIn("app-name=kodi-simkl-scrobbler", prepared.url)
        self.assertIn(f"client_id={oauth2.CLIENT_ID_V2}", prepared.url)


class WhenTheTokenIsRenewed(unittest.TestCase):
    def test_a_v2_token_is_checked_before_a_request_goes_out(self):
        api = make_api(V2_TOKEN)
        order = []
        api.ensure_fresh_token = lambda force=False: order.append("renew") or "unneeded"
        response = MagicMock(status_code=200)
        response.json.return_value = {"ok": True}
        api._execute_request = lambda *a, **k: order.append("send") or response

        result = api._request("GET", "/sync/activities")

        self.assertEqual(["renew", "send"], order)
        self.assertEqual({"ok": True}, result)

    def test_a_v1_token_is_never_renewed(self):
        api = make_api(V1_TOKEN)
        api.ensure_fresh_token = MagicMock()
        response = MagicMock(status_code=200)
        response.json.return_value = {}
        api._execute_request = lambda *a, **k: response

        api._request("GET", "/sync/activities")

        api.ensure_fresh_token.assert_not_called()

    def test_a_401_on_a_v2_token_forces_a_renewal_and_retries_with_the_new_token(self):
        api = make_api(V2_TOKEN)
        renewed = "simkl_at_" + "b" * 34
        forced = []

        def force_renew(force=False):
            forced.append(force)
            api.access_token = renewed
            return "fresh"

        api.ensure_fresh_token = force_renew
        api.refresh_token = lambda: None                 # adoption is covered above; keep this test about the 401 path
        ok = MagicMock(status_code=200)
        ok.json.return_value = {"retried": True}
        api._execute_request = lambda *a, **k: ok

        result = api._handle_401("GET", "https://api.simkl.com/x", "/x", None, None, 30, "simkl_at", "t")

        self.assertEqual([True], forced)
        self.assertEqual({"retried": True}, result)

    def test_a_refused_renewal_stops_without_a_retry(self):
        api = make_api(V2_TOKEN)

        def refuse(force=False):
            api.access_token = ""
            return "reauth"

        api.ensure_fresh_token = refuse
        api.refresh_token = lambda: None
        api._execute_request = MagicMock()

        self.assertIsNone(api._handle_401("GET", "https://api.simkl.com/x", "/x", None, None, 30, "simkl_at", "t"))
        api._execute_request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
