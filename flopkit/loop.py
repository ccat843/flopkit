"""One participation cycle: verify → fund → spend → settle → credit → report.

The cycle is the unit of 'active participation'. Each run:
  1. verifies the chain identity is the pinned one (fail-closed);
  2. claims the faucet (server-side daily per-identity cap is the ceiling);
  3. runs capped direct sessions to independent miners;
  4. finalizes any force-settled channels whose dispute window has closed;
  5. re-reads the key-free explorer and records credited, settled spend —
     the number the airdrop actually scores;
  6. writes a ledger entry + daily report.
A dry-run does every step except signing: no faucet, no session, no finalize.
"""

from __future__ import annotations

from pathlib import Path

from . import explorer, spend
from .core import Cfg, DATA_DIR, FlopkitError, keystore_args, keystore_env, ledger_append, now_iso, run_cli, utc_day, die
from .netverify import verify_network


def claim_faucet(cfg: Cfg, dry_run: bool = False) -> dict:
    """Claim the faucet; a cooldown is NOT fatal — the cycle proceeds from the
    existing balance (spend still credits even without a fresh grant)."""
    if dry_run:
        ledger_append({"op": "faucet", "ok": True, "dry_run": True, "net": cfg.active_network})
        return {"dry_run": True}
    try:
        res = run_cli(cfg, "--network", cfg.active_network, "faucet", "request", cfg.identity["address"], retries=1)
    except FlopkitError as e:
        if "cooldown" in str(e) or "retryable" in str(e):
            ledger_append({"op": "faucet", "ok": False, "net": cfg.active_network, "cooldown": True, "note": str(e)[:300]})
            return {"cooldown": True, "note": str(e)[:300]}
        raise
    if res.get("error") or res.get("retryable"):
        retry = str(res.get("next") or res.get("error"))
        ledger_append({"op": "faucet", "ok": False, "net": cfg.active_network, "cooldown": True, "note": retry[:300]})
        return {"cooldown": True, "note": retry[:300]}
    ledger_append({"op": "faucet", "ok": True, "net": cfg.active_network,
                   "tx": res.get("tx_hash"), "amount": res.get("amount")})
    return res


def run_sessions(cfg: Cfg, n: int, dry_run: bool = False) -> list[dict]:
    caps = cfg.caps
    n = min(n, int(caps.get("max_sessions_per_cycle", 4)))
    if dry_run:
        planned = []
        for t in spend.pick_tasks(cfg, n):
            spend.run_direct_session(cfg, t, miner="(dry-run)", model="(dry-run)", dry_run=True)
            planned.append({"planned": True, "title": t.get("title")})
        return planned
    res = run_cli(cfg, "model", "list", "--network", cfg.active_network, retries=2)
    models = spend._parse_models(res)
    if not models:
        raise FlopkitError("model registry empty or unparseable — failing closed (no spend on unknown pricing)")
    miner, model = spend.choose_miner(cfg, models)
    out = []
    tasks = spend.pick_tasks(cfg, n)
    for i, task in enumerate(tasks):
        m, mdl = (miner, model) if i == 0 else spend.choose_miner(cfg, models)
        out.append(spend.run_direct_session(cfg, task, m, mdl))
    return out


def settle_channels(cfg: Cfg, dry_run: bool = False) -> list[dict]:
    """Finalize force-settled channels whose challenge window has closed.

    The CLI enforces the window on-chain; anything not yet eligible is
    left pending and reported, not forced.
    """
    if dry_run:
        return []
    # channel list is READ-ONLY: no keystore. Filter to our paying agent.
    # Short per-call timeout: this runs inside a daily cycle, not a debug shell.
    res = run_cli(cfg, "--network", cfg.active_network, "channel", "list",
                  "--agent", cfg.identity["address"], retries=2, timeout=60)
    items = res.get("channels") or res.get("items") or res.get("list") or (res if isinstance(res, list) else [])
    done = []
    for ch in items:
        cid = ch.get("id") or ch.get("channelId") or ch.get("channel_id")
        state = str(ch.get("status") or ch.get("state") or ch.get("phase") or "").lower()
        if not cid:
            continue
        if state in ("settled", "closed", "refunded"):
            continue
        if state in ("force-settled", "force_settled", "dispute-ended") or ch.get("finalizable"):
            try:
                fin = run_cli(cfg, *keystore_args(cfg), "--network", cfg.active_network,
                              "channel", "finalize", str(cid), env_extra=keystore_env(cfg),
                              retries=1, timeout=180)
                done.append({"channel": cid, "finalized": True, "res_keys": sorted(fin.keys())})
                ledger_append({"op": "settle", "ok": True, "net": cfg.active_network, "channel": cid, "finalized": True})
            except FlopkitError as e:
                done.append({"channel": cid, "finalized": False, "pending": str(e)[:200]})
                ledger_append({"op": "settle", "ok": False, "net": cfg.active_network, "channel": cid, "error": str(e)[:300]})
        else:
            # Unknown/non-terminal state (incl. miner-answered-pending, challenge window):
            # leave it. Never force-finalize a state we don't understand.
            done.append({"channel": cid, "state": state or "unknown", "note": "pending — dispute window open or state unrecognized"})
            ledger_append({"op": "settle", "ok": True, "net": cfg.active_network, "channel": cid,
                           "finalized": False, "state": state or "unknown"})
    return done


def cycle(cfg: Cfg, dry_run: bool = False, tasks: int = 2) -> dict:
    prof = cfg.net_profile(cfg.active_network)
    if not prof.get("pinned") and cfg.active_network == "testnet":
        die("testnet profile is not pinned — run `flopkit arm-testnet` first; refusing to sign on an unverified chain")

    verify = verify_network(cfg)
    before = explorer.credit_snapshot(cfg)
    faucet = claim_faucet(cfg, dry_run=dry_run)
    sessions = run_sessions(cfg, tasks, dry_run=dry_run)
    settled = settle_channels(cfg, dry_run=dry_run) if not dry_run else []
    after = explorer.credit_snapshot(cfg) if not dry_run else before

    summary = {
        "ts": now_iso(), "net": cfg.active_network, "dry_run": dry_run,
        "verify": verify, "faucet": faucet,
        "sessions": sessions, "settled": settled,
        "credit_before": before, "credit_after": after,
        "credited_spend_delta": (after.get("total_spent_flop", 0) - before.get("total_spent_flop", 0)) if after is not before else None,
    }
    ledger_append({"op": "cycle", "ok": True, "net": cfg.active_network, "dry_run": dry_run,
                   "sessions": len(sessions), "settled": len(settled), "summary": summary})
    write_report(cfg, summary)
    return summary


def write_report(cfg: Cfg, s: dict) -> Path:
    d = DATA_DIR / "reports"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{utc_day()}.md"
    cb, ca = s.get("credit_before") or {}, s.get("credit_after") or s.get("credit_before") or {}
    faucet = s.get("faucet") or {}
    faucet_txt = faucet.get("tx") or ("cooldown — " + str(faucet.get("note")) if faucet.get("cooldown") else "dry-run")
    lines = [
        f"# FLOP participation report — {utc_day()} ({cfg.active_network}{', dry-run' if s.get('dry_run') else ''})",
        "",
        f"- chain identity: genesis {s.get('verify', {}).get('genesis')} spec {s.get('verify', {}).get('spec_version')} (pinned+verified)",
        f"- faucet: {faucet_txt}",
        f"- sessions this cycle: {len(s.get('sessions', []))} (caps: per-req {cfg.caps.get('max_flop_per_request')}, day {cfg.caps.get('daily_budget_flop')})",
        f"- settled/finalized channels: {len(s.get('settled', []))}",
        f"- credited settled inference spend: {cb.get('total_spent_flop', 0):g} → {ca.get('total_spent_flop', 0):g} FLOP "
        f"({cb.get('settled_channels', 0)} → {ca.get('settled_channels', 0)} channels)",
        f"- pending (dispute window open): {ca.get('pending_channels', 0)}",
        "",
        "The credited, settled spend above is the number the agent airdrop scores. "
        "If delta == 0, the sessions have not settled yet — settle_channels handles eligible ones next cycle.",
    ]
    p.write_text("\n".join(lines) + "\n")
    return p
