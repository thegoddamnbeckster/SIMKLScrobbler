# -*- coding: utf-8 -*-
"""
AUTH V2 (SIMKL changelog 2026-09-18): the V1 PIN login is expected to stop working around April 2027.

What these pin down:
* a V2 token and a V1 token each travel with their own client id (a V2 id fails on V1 endpoints and the reverse);
* the device flow asks for write access (scope "media:read media:write") - without it the token is read-only;
* an access token lasts 7 days and is renewed BEFORE it runs out; renewing invalidates the old token at once, so when
  several Kodi processes want a new one only the first refreshes and the others adopt its token;
* a refused refresh token (revoked, or unused for 180 days) clears the tokens so the user is asked to connect again,
  while a network or server failure keeps them and tries again later;
* every request says which app and version it is.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tests.kodi_stubs  # noqa: F401 -- side-effect: stubs xbmc & friends

from resources.lib import oauth2

V2_TOKEN = "simkl_at_" + "a" * 34
V1_TOKEN = "f" * 64


class FakeSettings:
    """Settings shared by 'processes' (two stores over one dict stand for two Kodi processes)."""

    def __init__(self, **initial):
        self.values = dict(initial)

    def get(self, key):
        return self.values.get(key, "")

    def set(self, key, value):
        self.values[key] = value

    def store(self, now):
        return oauth2.TokenStore(self.get, self.set, now=now)


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakePost:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, path, body):
        self.calls.append((path, dict(body)))
        return self.responses.pop(0)


class TokenKinds(unittest.TestCase):
    def test_a_v2_token_is_recognised_and_uses_the_v2_client_id(self):
        self.assertTrue(oauth2.is_v2_token(V2_TOKEN))
        self.assertEqual(oauth2.CLIENT_ID_V2, oauth2.client_id_for(V2_TOKEN))

    def test_an_existing_v1_token_keeps_the_v1_client_id_until_simkl_retires_v1(self):
        self.assertFalse(oauth2.is_v2_token(V1_TOKEN))
        self.assertEqual(oauth2.CLIENT_ID_V1, oauth2.client_id_for(V1_TOKEN))

    def test_no_token_yet_means_a_new_login_which_is_v2(self):
        self.assertEqual(oauth2.CLIENT_ID_V2, oauth2.client_id_for(""))
        self.assertEqual(oauth2.CLIENT_ID_V2, oauth2.client_id_for(None))

    def test_the_two_client_ids_differ(self):
        self.assertNotEqual(oauth2.CLIENT_ID_V1, oauth2.CLIENT_ID_V2)

    def test_every_request_identifies_the_app_and_its_version(self):
        self.assertEqual({"app-name": "kodi-simkl-scrobbler", "app-version": "7.9.27"}, oauth2.identity_params("7.9.27"))
        self.assertEqual("kodi-simkl-scrobbler/7.9.27", oauth2.user_agent("7.9.27"))


class DeviceFlow(unittest.TestCase):
    def test_the_code_request_asks_for_read_and_write_scope_with_the_v2_client_id(self):
        post = FakePost((200, {"device_code": "dc", "user_code": "ABCD-EFGH", "verification_uri": "https://simkl.com/pin",
                               "verification_uri_complete": "https://simkl.com/pin?user_code=ABCD-EFGH", "expires_in": 900, "interval": 5}))

        data = oauth2.request_device_code(post)

        path, body = post.calls[0]
        self.assertEqual("/oauth2/device", path)
        self.assertEqual("media:read media:write", body["scope"])
        self.assertEqual(oauth2.CLIENT_ID_V2, body["client_id"])
        self.assertEqual("ABCD-EFGH", data["user_code"])
        self.assertEqual(5, data["interval"])

    def test_a_failed_code_request_raises(self):
        with self.assertRaises(oauth2.OAuthError):
            oauth2.request_device_code(FakePost((401, {"error": "invalid_client"})))

    def test_polling_maps_every_answer_the_service_gives(self):
        cases = [
            ((200, {"access_token": V2_TOKEN, "refresh_token": "simkl_rt_x", "expires_in": 604800}), "ok"),
            ((400, {"error": "authorization_pending"}), "pending"),
            ((400, {"error": "slow_down"}), "slow_down"),
            ((400, {"error": "expired_token"}), "expired"),
            ((400, {"error": "access_denied"}), "denied"),
            ((503, None), "pending"),       # the service hiccuped: keep waiting
            ((0, None), "pending"),         # no network: keep waiting
        ]
        for response, expected in cases:
            with self.subTest(response=response):
                state, _ = oauth2.poll_device_token(FakePost(response), "dc")
                self.assertEqual(expected, state)

    def test_polling_uses_the_device_code_grant(self):
        post = FakePost((400, {"error": "authorization_pending"}))
        oauth2.poll_device_token(post, "the-device-code")
        self.assertEqual("urn:ietf:params:oauth:grant-type:device_code", post.calls[0][1]["grant_type"])
        self.assertEqual("the-device-code", post.calls[0][1]["device_code"])


class SavingTokens(unittest.TestCase):
    def test_a_token_response_is_saved_with_its_expiry_and_under_the_legacy_setting_too(self):
        clock = FakeClock()
        settings = FakeSettings()

        settings.store(clock).save({"access_token": V2_TOKEN, "refresh_token": "simkl_rt_x", "expires_in": 604800})

        self.assertEqual(V2_TOKEN, settings.get("access_token"))
        self.assertEqual(V2_TOKEN, settings.get("simkl_token"))
        self.assertEqual("simkl_rt_x", settings.get("refresh_token"))
        self.assertEqual(str(int(clock.t + 604800)), settings.get("token_expires_at"))

    def test_clear_removes_every_token(self):
        settings = FakeSettings(access_token=V2_TOKEN, simkl_token=V2_TOKEN, refresh_token="r", token_expires_at="1")
        settings.store(FakeClock()).clear()
        self.assertEqual("", settings.get("access_token") + settings.get("simkl_token")
                         + settings.get("refresh_token") + settings.get("token_expires_at"))


class WhenToRefresh(unittest.TestCase):
    def test_only_a_v2_token_with_a_refresh_token_that_is_near_expiry_is_refreshed(self):
        now = 1000.0
        soon = now + oauth2.REFRESH_MARGIN_SECONDS - 1
        later = now + oauth2.REFRESH_MARGIN_SECONDS + 1000
        self.assertTrue(oauth2.needs_refresh(V2_TOKEN, "r", soon, now))
        self.assertTrue(oauth2.needs_refresh(V2_TOKEN, "r", now - 5, now))       # already expired
        self.assertFalse(oauth2.needs_refresh(V2_TOKEN, "r", later, now))        # plenty left
        self.assertFalse(oauth2.needs_refresh(V1_TOKEN, "r", now - 5, now))      # a V1 token never expires on a schedule
        self.assertFalse(oauth2.needs_refresh(V2_TOKEN, "", now - 5, now))       # nothing to refresh with


class Refreshing(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.expired = str(int(self.clock.t - 10))

    def test_an_expired_token_is_renewed_and_the_new_tokens_are_saved(self):
        settings = FakeSettings(access_token=V2_TOKEN, refresh_token="simkl_rt_old", token_expires_at=self.expired)
        new_token = "simkl_at_" + "b" * 34
        post = FakePost((200, {"access_token": new_token, "refresh_token": "simkl_rt_old", "expires_in": 604800}))

        outcome = oauth2.refresh_access_token(settings.store(self.clock), post, oauth2.NoLock(), now=self.clock)

        self.assertEqual("fresh", outcome)
        self.assertEqual(new_token, settings.get("access_token"))
        path, body = post.calls[0]
        self.assertEqual("/oauth2/token", path)
        self.assertEqual("refresh_token", body["grant_type"])
        self.assertEqual("simkl_rt_old", body["refresh_token"])

    def test_a_token_with_time_left_is_left_alone(self):
        settings = FakeSettings(access_token=V2_TOKEN, refresh_token="r", token_expires_at=str(int(self.clock.t + 100000)))
        post = FakePost()

        self.assertEqual("unneeded", oauth2.refresh_access_token(settings.store(self.clock), post, oauth2.NoLock(), now=self.clock))
        self.assertEqual([], post.calls)

    def test_a_refresh_can_be_forced_for_a_token_the_service_rejected(self):
        settings = FakeSettings(access_token=V2_TOKEN, refresh_token="r", token_expires_at=str(int(self.clock.t + 100000)))
        post = FakePost((200, {"access_token": "simkl_at_" + "c" * 34, "expires_in": 604800}))

        outcome = oauth2.refresh_access_token(settings.store(self.clock), post, oauth2.NoLock(), now=self.clock, force=True)

        self.assertEqual("fresh", outcome)
        self.assertEqual("r", settings.get("refresh_token"))      # a response without one keeps the refresh token we had

    def test_a_process_that_lost_the_race_adopts_the_winners_token_instead_of_refreshing_again(self):
        # Refreshing invalidates the previous access token at once: a second refresh would cut the first process off.
        winner_token = "simkl_at_" + "d" * 34
        settings = FakeSettings(access_token=V2_TOKEN, refresh_token="simkl_rt_old", token_expires_at=self.expired)
        loser = settings.store(self.clock)

        class WinnerRefreshesWhileTheLoserWaitsForTheLock(oauth2.NoLock):
            def acquire(inner, timeout=15.0):
                settings.store(self.clock).save({"access_token": winner_token, "refresh_token": "simkl_rt_old", "expires_in": 604800})
                return True

        post = FakePost()
        outcome = oauth2.refresh_access_token(loser, post, WinnerRefreshesWhileTheLoserWaitsForTheLock(), now=self.clock)

        self.assertEqual("fresh", outcome)
        self.assertEqual([], post.calls)                          # no second refresh
        self.assertEqual(winner_token, settings.get("access_token"))

    def test_a_refused_refresh_token_clears_the_tokens_so_the_user_connects_again(self):
        settings = FakeSettings(access_token=V2_TOKEN, simkl_token=V2_TOKEN, refresh_token="revoked", token_expires_at=self.expired)

        outcome = oauth2.refresh_access_token(settings.store(self.clock), FakePost((400, {"error": "invalid_grant"})),
                                              oauth2.NoLock(), now=self.clock)

        self.assertEqual("reauth", outcome)
        self.assertEqual("", settings.get("access_token") + settings.get("refresh_token"))

    def test_a_network_or_server_failure_keeps_the_tokens_and_tries_again_later(self):
        for response in [(0, None), (503, None), (500, {"error": "server_error"})]:
            with self.subTest(response=response):
                settings = FakeSettings(access_token=V2_TOKEN, refresh_token="keep", token_expires_at=self.expired)

                outcome = oauth2.refresh_access_token(settings.store(self.clock), FakePost(response), oauth2.NoLock(), now=self.clock)

                self.assertEqual("failed", outcome)
                self.assertEqual("keep", settings.get("refresh_token"))
                self.assertEqual(V2_TOKEN, settings.get("access_token"))

    def test_a_bad_client_id_does_not_wipe_the_users_tokens(self):
        settings = FakeSettings(access_token=V2_TOKEN, refresh_token="keep", token_expires_at=self.expired)

        outcome = oauth2.refresh_access_token(settings.store(self.clock), FakePost((401, {"error": "invalid_client"})),
                                              oauth2.NoLock(), now=self.clock)

        self.assertEqual("failed", outcome)
        self.assertEqual("keep", settings.get("refresh_token"))


class FileLockBetweenProcesses(unittest.TestCase):
    def test_the_second_taker_waits_until_the_first_releases(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "token.lock")
            first = oauth2.FileLock(path, sleep=lambda s: None)
            second = oauth2.FileLock(path, sleep=lambda s: None)

            self.assertTrue(first.acquire(timeout=1))
            self.assertFalse(second.acquire(timeout=0))          # held: gives up when its time is up
            first.release()
            self.assertTrue(second.acquire(timeout=1))
            second.release()
            self.assertFalse(os.path.exists(path))

    def test_the_lock_of_a_crashed_process_is_taken_over(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "token.lock")
            open(path, "w").close()
            os.utime(path, (1, 1))                                # modified in 1970: certainly stale

            lock = oauth2.FileLock(path, stale_seconds=60, sleep=lambda s: None)

            self.assertTrue(lock.acquire(timeout=1))
            lock.release()


if __name__ == "__main__":
    unittest.main()
