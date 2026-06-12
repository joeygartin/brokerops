#!/usr/bin/env python3
"""Point the live Vapi assistant at this machine via a cloudflared quick tunnel.

Quick tunnels are ephemeral — the trycloudflare.com hostname dies with the
cloudflared process — so every dev session that wants real webhooks must mint a
fresh tunnel and re-patch the assistant's server.url. This script does both:

    1. start `cloudflared tunnel --url http://localhost:<port>`
    2. scrape the https://*.trycloudflare.com URL from its log
    3. PATCH the assistant so server.url = <tunnel>/webhooks/vapi and
       server.secret = VAPI_WEBHOOK_SECRET (delivered back as x-vapi-secret)
    4. GET the assistant back and print the now-active webhook URL

Credentials come from the environment or the repo-root .env (gitignored):
VAPI_API_KEY, VAPI_ASSISTANT_ID, VAPI_WEBHOOK_SECRET.

Foreground by default (Ctrl-C tears the tunnel down); --detach leaves
cloudflared running and prints its PID so you can kill it later. Management
calls always go to api.vapi.ai — VAPI_BASE_URL may point at the demo stub and
is deliberately ignored here.

Usage: scripts/vapi_assistant_sync.py [--port 8000] [--detach]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

VAPI_API_BASE = "https://api.vapi.ai"
TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
TUNNEL_LOG = Path("/tmp/brokerops-vapi-tunnel.log")
TUNNEL_READY_TIMEOUT = 30.0

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """Fill os.environ from KEY=VALUE lines without overriding the real environment."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"error: {name} is not set (environment or {REPO_ROOT / '.env'})")
    return value


def vapi_request(method: str, path: str, api_key: str, body: dict[str, Any] | None = None) -> Any:
    request = urllib.request.Request(
        f"{VAPI_API_BASE}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # api.vapi.ai sits behind Cloudflare, which 403s urllib's default
            # Python-urllib User-Agent (error 1010).
            "User-Agent": "brokerops-vapi-assistant-sync/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        sys.exit(f"error: Vapi {method} {path} -> {exc.code}: {detail}")


def start_tunnel(port: int, detach: bool) -> tuple[subprocess.Popen[bytes], str]:
    """Launch a cloudflared quick tunnel and return (process, public URL)."""
    TUNNEL_LOG.write_text("")
    with TUNNEL_LOG.open("ab") as log:
        try:
            process = subprocess.Popen(
                ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=detach,
            )
        except FileNotFoundError:
            sys.exit("error: cloudflared not found on PATH (brew install cloudflared)")

    deadline = time.monotonic() + TUNNEL_READY_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            sys.exit(f"error: cloudflared exited early — see {TUNNEL_LOG}")
        match = TUNNEL_URL_RE.search(TUNNEL_LOG.read_text(errors="replace"))
        if match:
            return process, match.group(0)
        time.sleep(0.5)
    process.terminate()
    sys.exit(f"error: no trycloudflare URL within {TUNNEL_READY_TIMEOUT:.0f}s — see {TUNNEL_LOG}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000, help="local api port (default 8000)")
    parser.add_argument("--detach", action="store_true", help="leave cloudflared running and exit")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    api_key = require_env("VAPI_API_KEY")
    assistant_id = require_env("VAPI_ASSISTANT_ID")
    webhook_secret = require_env("VAPI_WEBHOOK_SECRET")

    process, tunnel_url = start_tunnel(args.port, args.detach)
    webhook_url = f"{tunnel_url}/webhooks/vapi"
    print(f"tunnel up: {tunnel_url} -> http://localhost:{args.port} (log: {TUNNEL_LOG})")

    try:
        # The webhook handler only acts on end-of-call-report; subscribing to just
        # that keeps Vapi from posting per-utterance noise through the tunnel.
        vapi_request(
            "PATCH",
            f"/assistant/{assistant_id}",
            api_key,
            {
                "server": {"url": webhook_url, "secret": webhook_secret},
                "serverMessages": ["end-of-call-report"],
            },
        )
        assistant = vapi_request("GET", f"/assistant/{assistant_id}", api_key)
        active_url = (assistant.get("server") or {}).get("url", "")
        if active_url != webhook_url:
            sys.exit(f"error: patch did not stick — assistant server.url is {active_url!r}")
    except SystemExit:
        process.terminate()
        raise
    print(f"assistant {assistant.get('name', assistant_id)} now posts to {active_url}")

    if args.detach:
        print(f"cloudflared detached (pid {process.pid}); kill {process.pid} to stop")
        return
    print("tunnel stays up while this runs — Ctrl-C to stop")
    try:
        process.wait()
        sys.exit(f"cloudflared exited unexpectedly — see {TUNNEL_LOG}")
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        print("tunnel closed")


if __name__ == "__main__":
    main()
