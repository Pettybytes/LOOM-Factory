"""Apify API transport (I/O). Injected + mocked in deterministic tests.

The token is loaded by NAME only. Its value is NEVER logged, printed, or
persisted. `load_token` returns the value to the caller (who must not log it);
the error path discloses nothing.
"""

from __future__ import annotations

import json
import os
import urllib.request

TOKEN_ENV = "LOOM_APIFY_API_TOKEN"
BASE_URL = "https://api.apify.com/v2"


class ApifyError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def token_present(environ=None) -> bool:
    """True if the token is set and non-empty. Never returns the value."""
    env = environ if environ is not None else os.environ
    return bool((env.get(TOKEN_ENV) or "").strip())


def load_token(environ=None) -> str:
    """Return the token value, or raise TOKEN_MISSING (fixed, no secret)."""
    env = environ if environ is not None else os.environ
    token = (env.get(TOKEN_ENV) or "").strip()
    if not token:
        raise ApifyError("TOKEN_MISSING", "LOOM_APIFY_API_TOKEN is not configured")
    return token


def _unwrap(payload):
    """Unwrap the Apify v2 response envelope.

    Single resources come back as ``{"data": {...}}`` (so ``run["id"]`` lives at
    ``run["data"]["id"]``), while list endpoints return a bare array or
    ``{"data": [...]}``. Callers get the resource itself either way, which is why
    reading ``run["defaultDatasetId"]`` at the top level raised ``KeyError``.
    """
    if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, list)):
        return payload["data"]
    return payload


class Transport:
    """Apify transport interface (mocked in deterministic tests)."""

    def start_run(self, actor: str, input_: dict) -> dict:
        raise NotImplementedError

    def get_run_status(self, actor: str, run_id: str) -> dict:
        raise NotImplementedError

    def get_dataset_items(self, dataset_id: str) -> list:
        raise NotImplementedError


class HttpTransport(Transport):
    """Real Apify REST transport. NOT exercised by deterministic tests."""

    def __init__(self, token: str):
        self._token = token

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            BASE_URL + path,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self._token}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")

    def start_run(self, actor: str, input_: dict, *,
                  max_total_charge_usd: float | None = None) -> dict:
        """Start a run.

        `maxTotalChargeUsd` is a RUN PARAMETER, never actor input: putting it in the
        payload would make it an input field that caps nothing. It is carried here in
        the query string so Apify's billing layer enforces it.
        """
        path = f"/acts/{actor}/runs"
        if max_total_charge_usd is not None:
            if max_total_charge_usd <= 0:
                raise ApifyError("BAD_CHARGE_CAP", "maxTotalChargeUsd must be > 0")
            path += f"?maxTotalChargeUsd={max_total_charge_usd:g}"
        return _unwrap(self._post(path, input_))

    def get_run_status(self, actor: str, run_id: str) -> dict:
        req = urllib.request.Request(
            f"{BASE_URL}/acts/{actor}/runs/{run_id}",
            headers={"Authorization": f"Bearer {self._token}"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return _unwrap(json.loads(r.read().decode() or "{}"))

    def get_dataset_items(self, dataset_id: str) -> list:
        req = urllib.request.Request(
            f"{BASE_URL}/datasets/{dataset_id}/items",
            headers={"Authorization": f"Bearer {self._token}"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return _unwrap(json.loads(r.read().decode() or "[]"))
