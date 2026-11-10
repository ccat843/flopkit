# Copyright 2026 ccat843
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Testnet-open watcher — read-only.

Polls the official status surfaces and detects the planned → live flip.
No signing, no chain writes: it is safe to run from a cron job daily.
When it detects a live testnet it prints an ARMING directive: pin the
published network identity with `flopkit arm-testnet`, verify, then run
the first cycle interactively before automating.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request

from .core import Cfg, ledger_append

LIVE_STATUS = ("live", "open", "launched", "started")
# Prose pages (FAQs, docs) contain "Testnet is open — no whitelist" as a
# RULE, not a status. Only the Status: line on the machine status page is
# authoritative; prose hits are flagged for review, never auto-live.
PROSE_REVIEW = ("testnet is now", "testnet went live", "testnet launched", "testnet is live")
PRIMARY = "https://ask.flop.finance/docs/network-testnet"


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "flopkit/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def status_line(text: str) -> str | None:
    m = re.search(r"Status:\s*([a-z]+)", text, re.IGNORECASE)
    return m.group(0).lower() if m else None


def watch(cfg: Cfg) -> dict:
    urls = cfg.raw.get("watch", {}).get("status_urls", [])
    if PRIMARY not in urls:
        urls = [PRIMARY, *urls]
    reports: list[dict] = []
    primary: dict | None = None
    for url in urls:
        try:
            body = fetch(url)
        except Exception as e:
            reports.append({"url": url, "ok": False, "error": f"{type(e).__name__}: {e}"})
            continue
        digest = hashlib.sha256(body.encode()).hexdigest()
        entry = {
            "url": url,
            "ok": True,
            "sha256": digest[:16],
            "status_line": status_line(body),
            "prose_review": any(mk in body.lower() for mk in PROSE_REVIEW),
        }
        reports.append(entry)
        ledger_append({"op": "watch", "ok": True, "url": url, "sha256": digest[:16],
                       "status_line": entry["status_line"], "prose_review": entry["prose_review"]})
        if url == PRIMARY:
            primary = entry
    # Authoritative live signal = the Status: line on the machine status page.
    live = bool(primary and primary.get("ok")
                and primary.get("status_line")
                and primary["status_line"].split(":", 1)[-1].strip() in LIVE_STATUS)
    review = bool(primary and primary.get("ok") and not live and (primary.get("prose_review") or primary.get("status_line") is None))
    return {"reports": reports, "testnet_live": live, "needs_review": review}


def render_watch(w: dict) -> str:
    out = []
    for r in w["reports"]:
        if not r.get("ok"):
            out.append(f"  ✖ {r['url']}: {r.get('error')}")
        else:
            tag = "LIVE" if r.get("status_line") and r["status_line"].split(":", 1)[-1].strip() in LIVE_STATUS else \
                  ("review" if r.get("prose_review") else "planned/unknown")
            out.append(f"  {'✔' if r['ok'] else '✖'} {r['url']} [{tag}] (sha {r.get('sha256')}, status: {r.get('status_line')})")
    out.append(f"\n  testnet_live_detected: {w['testnet_live']}   needs_review: {w['needs_review']}")
    if w["testnet_live"]:
        out.append(
            "\n  >>> ARMING DIRECTIVE: the machine status page says live.\n"
            "      1. Read the published testnet identity (genesis, explorer, faucet, rpc, gateway).\n"
            "      2. Run:  flopkit arm-testnet <genesis> <explorer> <faucet> <rpc> <gateway>\n"
            "      3. Run:  flopkit cycle --dry-run     (verify reads + pins)\n"
            "      4. Run:  flopkit cycle               (first real cycle, interactively)\n"
            "      5. Only then schedule the daily cron cycle."
        )
    elif w["needs_review"]:
        out.append("\n  >>> A page shows a possible-live marker or no Status line. An operator/agent should read the source and confirm before arming.")
    return "\n".join(out)
