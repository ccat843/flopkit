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

"""Config + ledger + shared helpers for flopkit.

Standard library only. All FLOP chain operations are delegated to the
official @flop-labs CLI (see config.json cli.*), which owns signing via
the encrypted keystore.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOLKIT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = TOOLKIT_DIR / "config.json"
DATA_DIR = TOOLKIT_DIR / "data"
LEDGER_PATH = DATA_DIR / "ledger.jsonl"


class FlopkitError(Exception):
    """Fail-closed error: the cycle must stop and report, never guess."""


@dataclass
class Cfg:
    raw: dict
    cfg_path: Path

    @property
    def active_network(self) -> str:
        return self.raw["active_network"]

    @property
    def network(self) -> dict:
        return self.raw["networks"][self.active_network]

    @property
    def caps(self) -> dict:
        return self.raw["caps"]

    @property
    def identity(self) -> dict:
        return self.raw["identity"]

    @property
    def cli_cmd(self) -> list[str]:
        cli = self.raw["cli"]
        return [cli.get("node_bin", "node"), cli.get("cli_js")]

    def net_profile(self, name: str) -> dict:
        return self.raw["networks"].get(name, {})


def load_cfg() -> Cfg:
    if not CONFIG_PATH.exists():
        raise FlopkitError(f"missing config: {CONFIG_PATH}")
    return Cfg(json.loads(CONFIG_PATH.read_text()), CONFIG_PATH)


def save_cfg(cfg: Cfg) -> None:
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg.raw, indent=2) + "\n")
    os.replace(tmp, CONFIG_PATH)


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- ledger

def ledger_append(entry: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"ts": now_iso(), "day": utc_day(), **entry}
    with LEDGER_PATH.open("a") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")


def ledger_read(day: str | None = None, op: str | None = None, limit: int = 500) -> list[dict]:
    if not LEDGER_PATH.exists():
        return []
    out = []
    for line in LEDGER_PATH.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if day and e.get("day") != day:
            continue
        if op and e.get("op") != op:
            continue
        out.append(e)
    return out[-limit:]


def daily_spend_flop(day: str | None = None) -> float:
    """Sum of escrowed FLOP from real (non-dry-run) direct sessions, per UTC day."""
    day = day or utc_day()
    total = 0.0
    for e in ledger_read(day=day, op="session"):
        if e.get("ok") and not e.get("dry_run") and isinstance(e.get("escrow_flop"), (int, float)):
            total += float(e["escrow_flop"])
    return total


def last_credit_snapshot() -> dict | None:
    entries = [e for e in ledger_read(op="credit", limit=10)]
    return entries[-1] if entries else None


# ------------------------------------------------------------- CLI runner

def run_cli(cfg: Cfg, *args: str, timeout: int | None = None, env_extra: dict | None = None, retries: int = 2) -> dict:
    """Run the official FLOP CLI with --json; parse stdout. Fail-closed.

    The CLI logs noise to stderr; only JSON on stdout counts. RPC/WSS is
    flaky on devnet, so transient failures are retried, but a malformed
    JSON result is fatal: we never act on half-understood chain state.
    """
    cmd = list(cfg.cli_cmd) + list(args) + ["--json"]
    timeout = timeout or cfg.caps.get("cli_timeout_s", 300)
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)

    last_err = "no attempt"
    for attempt in range(1, retries + 1):
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, env=env
            )
        except subprocess.TimeoutExpired:
            last_err = f"timeout after {timeout}s (attempt {attempt})"
            time.sleep(2)
            continue
        out = proc.stdout.strip()
        # CLI may print log lines before the JSON blob; find the last {...} block
        js = _extract_json(out)
        if js is not None:
            return js
        tail = (out or proc.stderr or "").strip().splitlines()[-3:]
        last_err = f"exit={proc.returncode} no-json: {' | '.join(tail)[:400]}"
        if proc.returncode == 0 and out and not out.startswith("{") and "error" in out.lower():
            # definitive error payload from the CLI itself; do not retry
            break
        time.sleep(3 * attempt)
    raise FlopkitError(f"flop CLI failed [{ ' '.join(args[:3]) }…]: {last_err}")


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return None


def keystore_args(cfg: Cfg) -> list[str]:
    ident = cfg.identity
    ks = ident.get("keystore")
    if not ks or not Path(ks).exists():
        raise FlopkitError(f"keystore missing: {ks}")
    args = ["--keystore", ks, "--password-env", "FLOP_KEY_PASSWORD"]
    return args


def keystore_env(cfg: Cfg) -> dict:
    ident = cfg.identity
    pfile = ident.get("password_env_file")
    if not pfile or not Path(pfile).exists():
        raise FlopkitError(f"keystore password file missing: {pfile}")
    return {"FLOP_KEY_PASSWORD": Path(pfile).read_text().strip()}


def die(msg: str) -> None:
    print(f"FLOPKIT ERROR (fail-closed): {msg}", file=sys.stderr)
    sys.exit(1)
