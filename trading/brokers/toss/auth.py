"""Getting and keeping a Toss access token.

One line in the Toss docs shapes this entire module:

    client 당 유효한 access token 은 1 개입니다.
    재발급 시 이전에 발급된 token 은 즉시 무효화됩니다.

So a token is a shared, singular resource, and a naive "fetch when I need one"
is actively harmful: PRISM fans orders out across accounts, and if two of them
request a token at once the second issue silently kills the first, so whichever
caller was mid-request starts getting 401s. Worse, the obvious fix — retry on
401 by fetching a new token — turns that into a loop where each retry
invalidates the token the other caller just obtained.

The token is therefore cached and guarded twice: a thread lock within the
process, and a file lock across processes, because the batch, the tracking
agent and the stance server run separately but share one client_id. Both locks
re-read the cache after acquiring, so a caller that waited gets the token the
winner just fetched instead of fetching another one.

Refresh is proactive. `expires_in` comes back with the token (86400s in the
published example), and renewing on expiry alone would guarantee that at least
one in-flight request meets the moment the old token dies.

The file-lock class is a trimmed copy of the one in `trading/kis_auth.py`.
Importing that module would read `kis_devlp.yaml` at import time and make Toss
depend on KIS configuration, which is the coupling this whole effort exists to
remove.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests

from trading.brokers.base import BrokerUnavailable
from trading.brokers.toss import ratelimit
from trading.brokers.toss.errors import TossAuthError, parse_error_response

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openapi.tossinvest.com"
TOKEN_PATH = "/oauth2/token"

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

# Renew this many seconds before expiry. Generous because the cost of renewing
# early is one extra call a day, while the cost of renewing late is a failed
# order at the only moment that matters.
_RENEW_MARGIN_SECONDS = 300

_LOCK_TIMEOUT = 30.0
_STALE_LOCK_SECONDS = 300


class TossCredentials:
    """client_id / client_secret, and where the cached token lives."""

    def __init__(self, client_id: str, client_secret: str, *, base_url: str = DEFAULT_BASE_URL):
        if not client_id or not client_secret:
            raise ValueError("client_id and client_secret are both required")
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")

    @property
    def cache_key(self) -> str:
        """Stable per-client filename component.

        Hashed so the client_id never appears in a path that might be logged,
        listed, or pasted into an issue.
        """
        return hashlib.sha256(self.client_id.encode()).hexdigest()[:16]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"TossCredentials(client_id={self.client_id[:6]}…, base_url={self.base_url})"


class _FileLock:
    """Atomic-create lock, portable across Windows and Unix.

    Trimmed from `kis_auth.CrossPlatformFileLock`; see the module docstring for
    why it is copied rather than imported.
    """

    def __init__(self, path: Path, timeout: float = _LOCK_TIMEOUT):
        self.path = path
        self.timeout = timeout
        self._fd: int | None = None

    def acquire(self) -> bool:
        start = time.time()
        while time.time() - start < self.timeout:
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, str(os.getpid()).encode())
                return True
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                    if age > _STALE_LOCK_SECONDS:
                        logger.warning("[TOSS_AUTH] removing stale lock %s", self.path)
                        self.path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                time.sleep(0.1)
            except OSError as exc:
                logger.warning("[TOSS_AUTH] lock error %s: %s", self.path, exc)
                time.sleep(0.1)
        return False

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            self.path.unlink()
        except OSError:
            pass

    def __enter__(self) -> "_FileLock":
        if not self.acquire():
            raise BrokerUnavailable(f"could not acquire Toss token lock: {self.path}")
        return self

    def __exit__(self, *args: Any) -> None:
        self.release()


class TossAuth:
    """Issues and caches the access token for one client."""

    def __init__(
        self,
        credentials: TossCredentials,
        *,
        config_dir: Path | None = None,
        session: requests.Session | None = None,
        timeout: float = 10.0,
    ):
        self._credentials = credentials
        self._config_dir = Path(config_dir) if config_dir is not None else CONFIG_DIR
        self._session = session or requests.Session()
        self._timeout = timeout
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at: float = 0.0

    @property
    def base_url(self) -> str:
        return self._credentials.base_url

    @property
    def _token_file(self) -> Path:
        return self._config_dir / f"toss_token_{self._credentials.cache_key}.json"

    @property
    def _lock_file(self) -> Path:
        return self._config_dir / f"toss_token_{self._credentials.cache_key}.lock"

    # ── Public ────────────────────────────────────────────────────────────────

    def access_token(self, *, force_refresh: bool = False) -> str:
        """A usable token, issuing one only if nobody else already has."""
        if not force_refresh:
            cached = self._usable_memory_token()
            if cached is not None:
                return cached

        with self._lock:
            # Another thread may have fetched while we waited.
            if not force_refresh:
                cached = self._usable_memory_token()
                if cached is not None:
                    return cached
                disk = self._read_token_file()
                if disk is not None:
                    self._token, self._expires_at = disk
                    return self._token

            return self._issue_under_file_lock(force_refresh=force_refresh)

    def invalidate(self) -> None:
        """Forget the current token, in memory and on disk.

        Called after a 401, because the token Toss rejected is worthless to
        every other process sharing this cache too.
        """
        with self._lock:
            self._token = None
            self._expires_at = 0.0
            try:
                self._token_file.unlink()
            except OSError:
                pass

    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token()}"}

    # ── Internals ─────────────────────────────────────────────────────────────

    def _usable_memory_token(self) -> str | None:
        if self._token and time.time() < self._expires_at:
            return self._token
        return None

    def _issue_under_file_lock(self, *, force_refresh: bool) -> str:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        with _FileLock(self._lock_file):
            # Re-read inside the lock: the process we queued behind has very
            # likely just written a token, and issuing another would revoke it.
            if not force_refresh:
                disk = self._read_token_file()
                if disk is not None:
                    self._token, self._expires_at = disk
                    return self._token

            token, expires_at = self._request_token()
            self._token, self._expires_at = token, expires_at
            self._write_token_file(token, expires_at)
            logger.info(
                "[TOSS_AUTH] issued access token client=%s… ttl=%ds",
                self._credentials.client_id[:6],
                int(expires_at - time.time()),
            )
            return token

    def _request_token(self) -> tuple[str, float]:
        ratelimit.bucket_for(ratelimit.AUTH).acquire()
        url = f"{self._credentials.base_url}{TOKEN_PATH}"
        try:
            response = self._session.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._credentials.client_id,
                    "client_secret": self._credentials.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise BrokerUnavailable(f"Toss token request failed: {exc}") from exc

        if response.status_code != 200:
            error = parse_error_response(
                response.status_code, _safe_json(response), response.headers
            )
            _explain_auth_failure(error)
            raise error

        payload = _safe_json(response)
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise BrokerUnavailable(
                f"Toss token response missing access_token (HTTP {response.status_code})"
            )

        # Treat a missing or absurd expires_in as a short life rather than
        # trusting it — a wrong long TTL means using a dead token all day.
        try:
            ttl = int(payload.get("expires_in") or 0)
        except (TypeError, ValueError):
            ttl = 0
        if ttl <= 0:
            logger.warning("[TOSS_AUTH] no usable expires_in; assuming 300s")
            ttl = 300

        expires_at = time.time() + max(ttl - _RENEW_MARGIN_SECONDS, 30)
        return str(payload["access_token"]), expires_at

    def _read_token_file(self) -> tuple[str, float] | None:
        try:
            raw = json.loads(self._token_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        token = raw.get("access_token")
        expires_at = raw.get("expires_at")
        if not token or not isinstance(expires_at, (int, float)):
            return None
        if time.time() >= expires_at:
            return None
        return str(token), float(expires_at)

    def _write_token_file(self, token: str, expires_at: float) -> None:
        payload = json.dumps({"access_token": token, "expires_at": expires_at})
        temp = self._token_file.with_suffix(".tmp")
        try:
            temp.write_text(payload, encoding="utf-8")
            os.chmod(temp, 0o600)
            os.replace(temp, self._token_file)
        except OSError as exc:
            # A cache we cannot persist is a performance problem, not a
            # correctness one — the in-memory token still works.
            logger.warning("[TOSS_AUTH] could not cache token: %s", exc)
            try:
                temp.unlink()
            except OSError:
                pass


def _safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _explain_auth_failure(error: Exception) -> None:
    """Say what to actually do about it.

    The IP allowlist is the one that costs people an afternoon: every call
    fails identically, and nothing in a 403 body points at the WTS setting that
    caused it.
    """
    if not isinstance(error, TossAuthError):
        return
    if error.status == 403:
        logger.error("=" * 60)
        logger.error("❌ TOSS API REJECTED THIS IP")
        logger.error("=" * 60)
        logger.error("Every Toss call from this host will fail until the IP is allowed.")
        logger.error("📋 HOW TO FIX:")
        logger.error("   토스증권 WTS > 설정 > Open API > 허용 IP 관리")
        logger.error("   에서 이 서버의 공인 IP를 등록하세요.")
        logger.error("   확인: curl -s https://api.ipify.org")
        logger.error("=" * 60)
    elif error.status == 401:
        logger.error("=" * 60)
        logger.error("❌ TOSS CLIENT AUTHENTICATION FAILED")
        logger.error("=" * 60)
        logger.error("📋 POSSIBLE CAUSES:")
        logger.error("   - client_id / client_secret 오타 또는 만료")
        logger.error("   - 토스증권 WTS에서 해당 클라이언트가 비활성 상태")
        logger.error("=" * 60)
