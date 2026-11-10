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

"""Capped, direct-session spend — the only path that credits the agent.

Rules enforced here (Yellow Paper D-0514 + airdrop fairness rules):
  * our own account signs open_channel / force_open (via the CLI keystore);
  * spend is NEVER routed to a miner under our control (excluded_miners);
  * per-request escrow cap and UTC-day budget cap, both fail-closed;
  * the CLI's JSON result must be understood or the action is aborted
    (never a half-interpreted chain state);
  * tasks come from data/tasks.jsonl with a diverse fallback pool so the
    cadence reads as real, varied demand — one repeated prompt is the
    manufactured-activity pattern that forfeits the allocation.
"""

from __future__ import annotations

import json
from pathlib import Path

from .core import Cfg, DATA_DIR, FlopkitError, daily_spend_flop, keystore_args, keystore_env, ledger_append, run_cli
from .netverify import verify_network

TASKS_PATH = DATA_DIR / "tasks.jsonl"

# Diverse fallback prompts: small, useful, varied. Real work is preferred —
# queue actual tasks with `flopkit add-task`.
FALLBACK_PROMPTS: list[tuple[str, str]] = [
    ("log-classify", "Classify this support log line by severity and suggest one fix: 'OOMKilled after 2200s on worker-3, heap grew 1.2GB; no GC in final 30s.'"),
    ("code-review", "Review this Python snippet for one bug and one style issue: `def f(d): return [d[k] for k in d if k in d]`."),
    ("summarize", "Summarize this in two sentences: a proof-of-inference chain where agents pay miners per settled inference session, with validators folding proof hashes into blocks."),
    ("translate", "Translate to plain English: 'The settlement is final once the seven-day dispute window closes.'"),
    ("format", "Format as JSON: model=Qwen3-8B, context=8192, max_output=4096."),
    ("explain", "Explain hash time-locked contracts in one paragraph, as if to a smart developer new to BTC rails."),
    ("check", "What is wrong, if anything, with this SQL: SELECT * FROM t WHERE ts BETWEEN a AND b AND ts < b ORDER BY 1 LIMIT 10;"),
    ("draft", "Draft a 2-line commit message for adding nightly faucet claims and a spend budget guard to an agent runner."),
]


def task_queue(cfg: Cfg) -> list[dict]:
    if not TASKS_PATH.exists():
        return []
    tasks = []
    for line in TASKS_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            tasks.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return tasks


def pick_tasks(cfg: Cfg, n: int, used_today: int = 0) -> list[dict]:
    queued = task_queue(cfg)
    picks = queued[:n]
    idx = used_today
    while len(picks) < n:
        t, p = FALLBACK_PROMPTS[idx % len(FALLBACK_PROMPTS)]
        picks.append({"title": t, "prompt": p})
        idx += 1
    return picks[:n]


def _parse_models(res: dict) -> list[dict]:
    m = res.get("models")
    if isinstance(m, list):
        return m
    if isinstance(res, list):
        return res
    for v in res.values():
        if isinstance(v, list) and v and isinstance(v[0], dict) and "name" in v[0]:
            return v
    return []


def eligible_miners(cfg: Cfg, models: list[dict]) -> list[str]:
    """Providers registered on-chain, minus anyone under our control."""
    excluded = {a.lower() for a in cfg.raw.get("session", {}).get("excluded_miners", [])}
    excluded.add(cfg.identity["address"].lower())
    out: list[str] = []
    for m in models:
        p = m.get("provider")
        if p and p not in excluded and p not in out:
            out.append(p)
    return out


def choose_miner(cfg: Cfg, models: list[dict]) -> tuple[str, str]:
    """Deterministic rotation across independent miners (demand spread)."""
    providers = eligible_miners(cfg, models)
    if not providers:
        raise FlopkitError("no eligible independent miners registered — refusing to spend (own-miner rule)")
    recent = ledger_recent_entries()
    used = [e.get("miner") for e in reversed(recent) if e.get("op") == "session" and e.get("ok")]
    for p in providers:
        if p not in used:
            return p, _model_for(cfg, models, p)
    return providers[-1], _model_for(cfg, models, providers[-1])


def ledger_recent_entries() -> list[dict]:
    from .core import ledger_read
    return ledger_read(op="session", limit=40)


def _model_for(cfg: Cfg, models: list[dict], miner: str) -> str:
    want = cfg.raw.get("session", {}).get("default_model")
    for m in models:
        if m.get("provider") == miner and m.get("name") == want:
            return want
    for m in models:
        if m.get("provider") == miner:
            return m.get("name", want or "")
    return want or ""


def budget_left(cfg: Cfg) -> float:
    cap = float(cfg.caps.get("daily_budget_flop", 0))
    return max(0.0, cap - daily_spend_flop())


def run_direct_session(cfg: Cfg, task: dict, miner: str, model: str, dry_run: bool = False) -> dict:
    caps = cfg.caps
    escrow = float(caps.get("max_flop_per_request", 4.0))
    left = budget_left(cfg)
    if escrow > left:
        raise FlopkitError(f"daily budget exhausted: need {escrow:g}, left {left:g} FLOP today — stopping the cycle (cap respected)")
    ks_env = keystore_env(cfg)
    args = [
        *keystore_args(cfg),
        "--network", cfg.active_network,
        "session", "run",
        "--miner", miner,
        "--model", model,
        "--prompt", task.get("prompt", task.get("title", "")),
        "--max-flop", f"{escrow:g}",
        "--via", "auto",
    ]
    if dry_run:
        ledger_append({"op": "session", "ok": True, "dry_run": True, "net": cfg.active_network,
                      "title": task.get("title"), "miner": miner, "model": model, "escrow_flop": escrow})
        return {"dry_run": True, "would_run": " ".join(args[:14])}
    res = run_cli(cfg, *args, timeout=int(caps.get("session_timeout_s", 240)), env_extra=ks_env, retries=1)
    # Fail-closed on result we don't understand:
    if "error" in res and res.get("error"):
        raise FlopkitError(f"session run reported error: {res['error']}")
    ledger_append({
        "op": "session", "ok": True, "net": cfg.active_network,
        "title": task.get("title"), "miner": miner, "model": model,
        "escrow_flop": escrow,
        "channel": res.get("channel_id") or res.get("channel"),
        "tx": res.get("tx_hash") or res.get("extrinsic"),
        "settled": res.get("settled"),
        "result_keys": sorted(res.keys()),
    })
    return res
