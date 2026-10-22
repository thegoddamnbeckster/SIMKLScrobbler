# -*- coding: utf-8 -*-
"""
SIMKL AUTH V2 (OAuth 2.0 device flow) for the scrobbler.

Why this exists (SIMKL changelog, 2026-09-18): AUTH V1 - the old /oauth/pin login whose token lasts five years - is
expected to stop working around April 2027. AUTH V2 differs in ways that touch every request:

* a NEW client id (a V1 id cannot be upgraded), and every user authorises it once - there is no token exchange;
* the code is requested with POST /oauth2/device and exchanged with POST /oauth2/token (RFC 8628);
* write access must be asked for (scope "media:read media:write"), otherwise the token is read-only;
* the access token lasts 7 days; a refresh token (180 days, renewed on use) gets a new one;
* refreshing invalidates the previous access token IMMEDIATELY, so two processes refreshing at once cut each other off.

Kodi runs the service, scripts and the rating dialog as separate Python processes that share these settings, so a
refresh is taken under a lock and the saved token is re-read after the lock is won: whoever lost the race uses the
winner's token instead of refreshing again.

Everything here is plain Python with the HTTP call, the settings and the clock passed in, so it can be tested without Kodi.

Professional code - suitable for public distribution
Attribution: Claude.ai with assistance from Michael Beck
"""

import os
import time

# Module version
__version__ = '7.9.27'

BASE_URL = "https://api.simkl.com"

# Registered AUTH V2 application (not secret: the device flow needs no client secret).
CLIENT_ID_V2 = "357bede39082cc927e5f99cab0df428681743677119516b73614c77144ed37a7"
# The original AUTH V1 application. Kept so people who have not reconnected yet keep working until SIMKL retires V1.
CLIENT_ID_V1 = "ab02f10030b0d629ffada90e2bf6236c57f42256a9e94d243255392af7b391e7"

# SIMKL asks every request to say which app and version it is (changelog 2026-04-22).
APP_NAME = "kodi-simkl-scrobbler"

SCOPE = "media:read media:write"

# A V2 access token is 43 characters starting simkl_at_; a V1 token is 64 lowercase hex characters.
V2_TOKEN_PREFIX = "simkl_at_"

# Refresh this long before the token runs out, so a request never goes out with one that is about to expire.
REFRESH_MARGIN_SECONDS = 600

# A lock older than this is a crashed process's, not a running refresh.
LOCK_STALE_SECONDS = 60


class OAuthError(Exception):
    """A device-flow or token request that could not be completed."""


def is_v2_token(token):
    return bool(token) and str(token).startswith(V2_TOKEN_PREFIX)


def client_id_for(token):
    """The client id that goes with a token: V2 for a V2 token or no token yet, V1 only for an existing V1 token."""
    return CLIENT_ID_V1 if token and not is_v2_token(token) else CLIENT_ID_V2


def identity_params(app_version):
    """The parameters SIMKL wants on every request, besides the client id."""
    return {"app-name": APP_NAME, "app-version": str(app_version)}


def user_agent(app_version):
    return f"{APP_NAME}/{app_version}"


# ---------------------------------------------------------------------------------------------------------------------
# device flow
# ---------------------------------------------------------------------------------------------------------------------

def request_device_code(post):
    """
    Starts the device flow. `post(path, body)` returns (status_code, parsed_json_or_None).
    Returns {device_code, user_code, verification_uri, verification_uri_complete, expires_in, interval}.
    """
    status, body = post("/oauth2/device", {"client_id": CLIENT_ID_V2, "scope": SCOPE})
    if status != 200 or not isinstance(body, dict) or not body.get("device_code") or not body.get("user_code"):
        raise OAuthError(f"SIMKL did not give a login code (HTTP {status})")
    return {
        "device_code": body["device_code"],
        "user_code": body["user_code"],
        "verification_uri": body.get("verification_uri") or "https://simkl.com/pin",
        "verification_uri_complete": body.get("verification_uri_complete") or "",
        "expires_in": int(body.get("expires_in") or 900),
        "interval": max(int(body.get("interval") or 5), 1),
    }


def poll_device_token(post, device_code):
    """
    One poll of POST /oauth2/token. Returns (state, payload):
      ("ok", token_payload) - authorised;     ("pending", None)  - the user has not approved yet;
      ("slow_down", None)   - poll less often (add 5 seconds);
      ("expired", None)     - the code ran out - start again;   ("denied", None) - refused or any other final error.
    """
    status, body = post("/oauth2/token", {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": CLIENT_ID_V2,
        "device_code": device_code,
    })
    if status == 200 and isinstance(body, dict) and body.get("access_token"):
        return "ok", body
    error = (body or {}).get("error") if isinstance(body, dict) else None
    if error == "authorization_pending":
        return "pending", None
    if error == "slow_down":
        return "slow_down", None
    if error == "expired_token":
        return "expired", None
    if status >= 500 or status == 0:
        return "pending", None            # the service or the network hiccuped: keep waiting
    return "denied", None


# ---------------------------------------------------------------------------------------------------------------------
# tokens at rest
# ---------------------------------------------------------------------------------------------------------------------

class TokenStore:
    """
    The tokens as saved in the addon settings. `get`/`set` are the settings accessors; `get_fresh` reads the value as
    another process last saved it (Kodi caches settings per Addon object), defaulting to `get`.
    """

    def __init__(self, get, set_, get_fresh=None, now=time.time):
        self._get = get
        self._set = set_
        self._get_fresh = get_fresh or get
        self._now = now

    # -- reading
    def access_token(self, fresh=False):
        return (self._get_fresh if fresh else self._get)("access_token") or ""

    def refresh_token(self, fresh=False):
        return (self._get_fresh if fresh else self._get)("refresh_token") or ""

    def expires_at(self, fresh=False):
        try:
            return float((self._get_fresh if fresh else self._get)("token_expires_at") or 0)
        except (TypeError, ValueError):
            return 0.0

    # -- writing
    def save(self, payload):
        """Saves a token response (device grant or refresh grant)."""
        access = payload["access_token"]
        self._set("access_token", access)
        self._set("simkl_token", access)            # the legacy setting id the rest of the addon also reads
        if payload.get("refresh_token"):
            self._set("refresh_token", payload["refresh_token"])
        self._set("token_expires_at", str(int(self._now() + int(payload.get("expires_in") or 604800))))

    def clear(self):
        for key in ("access_token", "simkl_token", "refresh_token", "token_expires_at"):
            self._set(key, "")


def needs_refresh(access_token, refresh_token, expires_at, now):
    """True when a V2 token is expired or about to be and there is a refresh token to renew it with."""
    if not is_v2_token(access_token) or not refresh_token:
        return False
    return expires_at <= now + REFRESH_MARGIN_SECONDS


# ---------------------------------------------------------------------------------------------------------------------
# refreshing, once, across processes
# ---------------------------------------------------------------------------------------------------------------------

class FileLock:
    """A lock between processes: the file's existence is the lock. A stale file (a crashed holder) is taken over."""

    def __init__(self, path, stale_seconds=LOCK_STALE_SECONDS, sleep=time.sleep, now=time.time):
        self.path = path
        self._stale = stale_seconds
        self._sleep = sleep
        self._now = now
        self._held = False

    def acquire(self, timeout=15.0):
        deadline = self._now() + timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                self._held = True
                return True
            except FileExistsError:
                try:
                    if self._now() - os.path.getmtime(self.path) > self._stale:
                        os.remove(self.path)
                        continue
                except OSError:
                    continue                  # it vanished between the checks: try to take it again
                if self._now() >= deadline:
                    return False
                self._sleep(0.25)
            except OSError:
                return False                  # cannot lock (no writable folder): refresh without it rather than not at all

    def release(self):
        if self._held:
            try:
                os.remove(self.path)
            except OSError:
                pass
            self._held = False


class NoLock:
    """Stands in for a lock where there is nowhere to put one."""
    def acquire(self, timeout=15.0):
        return True

    def release(self):
        pass


def refresh_access_token(store, post, lock, now=time.time, force=False):
    """
    Gets a new access token with the saved refresh token, if one is needed (or `force`).

    Returns "fresh" when a good token is saved (refreshed here, or by another process while we waited for the lock),
    "unneeded" when nothing was due, "reauth" when SIMKL refused the refresh token (the user must connect again - the
    saved tokens are cleared), and "failed" when the service or network could not be reached (try again later).
    """
    access, refresh, expires = store.access_token(), store.refresh_token(), store.expires_at()
    if not force and not needs_refresh(access, refresh, expires, now()):
        return "unneeded"
    if not refresh:
        return "reauth" if is_v2_token(access) else "unneeded"

    acquired = lock.acquire()
    try:
        # Another process may have refreshed while we waited: use its token, do not refresh a second time (that would
        # invalidate the one it just got).
        saved_access, saved_refresh = store.access_token(fresh=True), store.refresh_token(fresh=True)
        saved_expires = store.expires_at(fresh=True)
        already_renewed = saved_access and saved_access != access
        if already_renewed and not needs_refresh(saved_access, saved_refresh, saved_expires, now()):
            return "fresh"
        if saved_refresh:
            refresh = saved_refresh

        status, body = post("/oauth2/token", {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID_V2,
            "refresh_token": refresh,
        })
        if status == 200 and isinstance(body, dict) and body.get("access_token"):
            body.setdefault("refresh_token", refresh)
            store.save(body)
            return "fresh"
        error = body.get("error") if isinstance(body, dict) else None
        if status in (400, 401) and error == "invalid_grant":
            # the refresh token was revoked, expired (180 days unused) or already replaced: only a new login helps
            store.clear()
            return "reauth"
        return "failed"
    finally:
        if acquired:
            lock.release()
