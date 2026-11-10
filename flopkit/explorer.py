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

"""Key-free on-chain credit reads (the source of truth for participation).

The airdrop is scored from settled compute-channel spend by the agent's OWN
account. These reads hit the public explorer API only — no key, no signing —
and are what every cycle verifies at the end. Activity is not
participation; credited, settled spend is.

Endpoints mirror the published FLOP devnet explorer API:
  GET {explorer}/accounts/{address}
  GET {explorer}/accounts/{address}/inference-spend
  GET {explorer}/compute-channels?agent={address}&limit=N
  GET {explorer}/compute-channels/{channel_id}
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from .core import Cfg, FlopkitError, ledger_append, utc_day


def _get(cfg: Cfg, path: str, retries: int = 3) -> dict:
    base = cfg.net_profile(cfg.active_network).get("explorer")
    if not base:
        raise FlopkitError(f"no explorer API pinned for network '{cfg.active_network}'")
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    last = "no attempt"
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "flopkit/0.1"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # flaky RPC-backed explorer; retry, then fail-closed
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 * attempt)
    raise FlopkitError(f"explorer read failed for {path}: {last}")


def inference_spend(cfg: Cfg, address: str | None = None) -> dict:
    address = address or cfg.identity["address"]
    body = _get(cfg, f"accounts/{address}/inference-spend")
    out = {
        "network": cfg.active_network,
        "address": address,
        "day": utc_day(),
        "total_spent_flop": _flop(body.get("totalSpentFlop") or body.get("total_spent_flop")),
        "settled_channels": int(body.get("settledChannels") or body.get("settled_channels") or 0),
        "channel_count": int(body.get("channelCount") or body.get("channel_count") or 0),
    }
    ledger_append({"op": "credit", "ok": True, "net": out["network"], **out, "source": "explorer/inference-spend"})
    return out


def account(cfg: Cfg, address: str | None = None) -> dict:
    address = address or cfg.identity["address"]
    return _get(cfg, f"accounts/{address}")


def my_channels(cfg: Cfg, limit: int = 20, address: str | None = None) -> list[dict]:
    address = address or cfg.identity["address"]
    query = urllib.parse.urlencode({"agent": address, "limit": limit})
    body = _get(cfg, f"compute-channels?{query}")
    items = body.get("channels") or body.get("items") or (body if isinstance(body, list) else [])
    return items[:limit]


def channel(cfg: Cfg, channel_id: str) -> dict:
    return _get(cfg, f"compute-channels/{channel_id}")


def _flop(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def credit_snapshot(cfg: Cfg) -> dict:
    """The per-cycle verification artifact: credited spend + open channels."""
    spend = inference_spend(cfg)
    channels = my_channels(cfg, limit=10)
    pending = [
        c for c in channels
        if str(c.get("status", "")).lower() not in ("settled", "closed", "refunded")
    ]
    spend["pending_channels"] = len(pending)
    spend["pending_ids"] = [c.get("id") or c.get("channelId") for c in pending][:10]
    return spend
