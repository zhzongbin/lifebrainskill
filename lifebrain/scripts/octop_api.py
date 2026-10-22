#!/usr/bin/env python3
"""Talk to the *running* Octop daemon over its HTTP API.

Why not ``octop cron run-now``
------------------------------
That CLI path boots an **embedded** ``OctopServer``. When the main daemon is
already running you end up with two gateway runtimes polling the same WeChat
bot token: duplicated long-polling, interleaved logs, and a cron run whose
environment does not match the live one. The embedded server also competes
with the live one for cron bookkeeping.

This helper instead calls the live daemon:

    POST /api/agents/{agent_id}/cron/{cron_id}/run-now

and then waits for the run to finish by polling the cron row, so the caller
can tell "delivered" from "delivery failed".

Authentication
--------------
The installation's JWT signing secret lives in the local control-plane
database (``secrets`` table, key ``jwt``). We read it and sign a short-lived
HS256 access token ourselves. The token only ever travels in an HTTP header -
never in argv, never in a log file, never in the repository. A pre-minted
token can be supplied through ``LIFEBRAIN_API_TOKEN`` instead.

Only stdlib is used, so this works with any ``python3``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

DEFAULT_PORT = 8088
DEFAULT_HOST = "127.0.0.1"


# ── paths / endpoints ───────────────────────────────────────────────────────

def octop_root() -> str:
    """Installation root (``$OCTOP_HOME`` or ``~/.octop``)."""
    return os.environ.get("OCTOP_HOME") or os.path.join(os.path.expanduser("~"), ".octop")


def db_path() -> str:
    """Path of the control-plane SQLite database."""
    env = os.environ.get("LIFEBRAIN_OCTOP_DB")
    if env:
        return env
    return os.path.join(octop_root(), "octop.db")


def base_url() -> str:
    """Base URL of the running daemon."""
    env = os.environ.get("LIFEBRAIN_API_BASE") or os.environ.get("OCTOP_API_BASE")
    if env:
        return env.rstrip("/")
    port = os.environ.get("OCTOP_PORT", "").strip()
    if not port:
        try:
            with open(os.path.join(octop_root(), "config.json"), encoding="utf-8") as fh:
                port = str(json.load(fh).get("port") or "").strip()
        except Exception:
            port = ""
    host = os.environ.get("OCTOP_BIND_HOST", "").strip() or DEFAULT_HOST
    if host in ("0.0.0.0", "::", "::0"):
        host = DEFAULT_HOST
    return f"http://{host}:{port or DEFAULT_PORT}"


# ── auth ────────────────────────────────────────────────────────────────────

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def sign_access_token(
    secret: bytes,
    *,
    sub: int,
    uname: str,
    role: str,
    ttl_seconds: int = 300,
) -> str:
    """Mint an HS256 access token compatible with ``octop.infra.auth.tokens``."""
    now = int(time.time())
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(
        json.dumps(
            {
                "sub": str(sub),
                "uname": uname,
                "role": role,
                "iat": now,
                "exp": now + ttl_seconds,
            },
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    signature = _b64(hmac.new(secret, signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def _read_only(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def load_jwt_secret(path: str) -> bytes:
    conn = _read_only(path)
    try:
        row = conn.execute("SELECT v FROM secrets WHERE k = 'jwt'").fetchone()
    finally:
        conn.close()
    if row is None:
        raise RuntimeError("jwt secret not found in control-plane db")
    return bytes(row[0])


def load_identity(path: str, agent_id: str) -> tuple[int, str, str]:
    """Return ``(user_id, username, role)`` of the agent owner."""
    conn = _read_only(path)
    try:
        row = conn.execute("SELECT user_id FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"unknown agent {agent_id}")
        user = conn.execute("SELECT id, username, role FROM users WHERE id = ?", (row[0],)).fetchone()
        if user is None:
            raise RuntimeError(f"unknown owner user for agent {agent_id}")
        return int(user[0]), str(user[1]), str(user[2])
    finally:
        conn.close()


def access_token(agent_id: str, ttl_seconds: int = 300) -> str:
    """Return a bearer token for the agent owner (env override wins)."""
    preset = os.environ.get("LIFEBRAIN_API_TOKEN", "").strip()
    if preset:
        return preset
    path = db_path()
    user_id, username, role = load_identity(path, agent_id)
    secret = load_jwt_secret(path)
    return sign_access_token(
        secret,
        sub=user_id,
        uname=username,
        role=role,
        ttl_seconds=ttl_seconds,
    )


# ── http ────────────────────────────────────────────────────────────────────

def request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    timeout: int = 30,
) -> tuple[int, object]:
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {exc.code} {method} {url}: {detail}") from exc
    except urllib.error.URLError as exc:  # pragma: no cover - network dependent
        raise RuntimeError(f"cannot reach {url}: {exc.reason}") from exc
    if not body:
        return resp.status, None
    try:
        return resp.status, json.loads(body)
    except json.JSONDecodeError:
        return resp.status, body.decode("utf-8", "replace")


def healthy(url: str | None = None) -> bool:
    """True when the daemon answers on its JWT-exempt health endpoint."""
    url = url or base_url()
    try:
        status, _ = request("GET", f"{url}/api/health", timeout=5)
        return status == 200
    except Exception:
        return False


def get_cron(agent_id: str, cron_id: str, token: str) -> dict:
    url = f"{base_url()}/api/agents/{agent_id}/cron/{cron_id}"
    _, payload = request("GET", url, token=token)
    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected cron payload: {payload!r}")
    return payload


def run_now(
    agent_id: str,
    cron_id: str,
    *,
    wait_seconds: int = 900,
    poll_interval: float = 3.0,
) -> tuple[str, str]:
    """Trigger a cron run on the live daemon and wait for its outcome.

    Returns ``(status, detail)`` where status is ``ok`` or ``error``.
    """
    token = access_token(agent_id)
    before = get_cron(agent_id, cron_id, token)
    previous_run_at = before.get("last_run_at")

    url = f"{base_url()}/api/agents/{agent_id}/cron/{cron_id}/run-now"
    request("POST", url, token=token)

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        time.sleep(poll_interval)
        row = get_cron(agent_id, cron_id, token)
        last_run_at = row.get("last_run_at")
        status = row.get("last_status")
        if last_run_at is not None and last_run_at != previous_run_at and status:
            detail = str(row.get("last_error") or "")
            return str(status), detail
    return "error", f"timed out after {wait_seconds}s waiting for the cron run"


# ── cli ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="print the daemon base URL and whether it answers")

    run = sub.add_parser("run-now", help="trigger a cron run on the live daemon")
    run.add_argument("--agent", required=True)
    run.add_argument("--cron", required=True)
    run.add_argument("--wait", type=int, default=900)

    status = sub.add_parser("status", help="print last_status of a cron job")
    status.add_argument("--agent", required=True)
    status.add_argument("--cron", required=True)

    args = parser.parse_args(argv)

    if args.cmd == "health":
        url = base_url()
        ok = healthy(url)
        print(f"{url} {'ok' if ok else 'down'}")
        return 0 if ok else 1

    if args.cmd == "status":
        token = access_token(args.agent)
        row = get_cron(args.agent, args.cron, token)
        print(row.get("last_status") or "")
        if row.get("last_error"):
            print(row["last_error"], file=sys.stderr)
        return 0

    status, detail = run_now(args.agent, args.cron, wait_seconds=args.wait)
    print(status)
    if detail:
        print(detail, file=sys.stderr)
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - CLI boundary
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
