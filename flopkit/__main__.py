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

"""flopkit command-line entry point.

Subcommands:
  status                Show config, identity, last credit snapshot, today's spend.
  credit                Key-free on-chain credit snapshot (writes a ledger row).
  watch                 Poll official testnet status surfaces (read-only, cron-safe).
  cycle [--dry-run]     One participation cycle on the active network.
  arm-testnet <genesis> <explorer> <faucet> <rpc> <gateway>
                        Pin a published testnet identity so cycles may sign there.
  report [day]          Print a day's human report (or regenerate it).
  add-task <title> <prompt>
                        Queue a real task for the next cycle's spend.
  settle                Finalize eligible channels now (subset of cycle).
"""

from __future__ import annotations

import argparse
import json
import sys

from . import explorer, loop, spend, watch
from .core import (
    DATA_DIR,
    Cfg,
    FlopkitError,
    daily_spend_flop,
    last_credit_snapshot,
    load_cfg,
    save_cfg,
    utc_day,
)


def cmd_status(cfg: Cfg) -> None:
    last = last_credit_snapshot()
    print(f"active network : {cfg.active_network}  (airdrop weight: {cfg.net_profile(cfg.active_network).get('airdrop_weight')})")
    print(f"identity       : {cfg.identity['did']}")
    print(f"account        : {cfg.identity['address']}")
    print(f"caps           : per-req {cfg.caps.get('max_flop_per_request')} FLOP, day {cfg.caps.get('daily_budget_flop')} FLOP, sessions/cycle {cfg.caps.get('max_sessions_per_cycle')}")
    print(f"tasks queued   : {len(spend.task_queue(cfg))}")
    print(f"spend today    : {daily_spend_flop():g} FLOP (UTC {utc_day()})")
    if last:
        print(f"last credit    : {last.get('total_spent_flop', 0):g} FLOP settled over {last.get('settled_channels')} channels ({last.get('net')})")
    else:
        print("last credit    : none yet")


def cmd_credit(cfg: Cfg) -> None:
    snap = explorer.credit_snapshot(cfg)
    print(json.dumps(snap, indent=2))


def cmd_watch(cfg: Cfg) -> None:
    print(watch.render_watch(watch.watch(cfg)))


def cmd_cycle(cfg: Cfg, args: argparse.Namespace) -> None:
    s = loop.cycle(cfg, dry_run=bool(args.dry_run), tasks=args.tasks)
    if s.get("dry_run"):
        print("dry-run cycle complete: no signing occurred.")
        return
    print(json.dumps(
        {
            "network": s["net"],
            "faucet_tx": (s.get("faucet") or {}).get("tx"),
            "sessions": len(s.get("sessions", [])),
            "settled": len(s.get("settled", [])),
            "credit": s.get("credit_after"),
            "credited_delta": s.get("credited_spend_delta"),
        },
        indent=2,
    ))
    print(f"report written to {DATA_DIR / 'reports' / (utc_day() + '.md')}")


def cmd_arm_testnet(cfg: Cfg, args: argparse.Namespace) -> None:
    g = args.genesis
    if not (isinstance(g, str) and g.startswith("0x") and len(g) == 66 and all(c in "0123456789abcdef" for c in g[2:])):
        raise FlopkitError("expected_genesis must be a 0x + 64 lowercase hex chars (32-byte chain hash)")
    prof = cfg.raw["networks"]["testnet"]
    prof["expected_genesis"] = g
    prof["explorer"] = args.explorer
    prof["faucet"] = args.faucet
    prof["rpc"] = args.rpc
    prof["gateway"] = args.gateway
    prof["pinned"] = True
    cfg.raw["active_network"] = "testnet"
    save_cfg(cfg)
    print(f"testnet pinned: genesis {g}")
    print(f"  explorer={args.explorer} faucet={args.faucet} rpc={args.rpc} gateway={args.gateway}")
    print("active_network is now 'testnet'. Run `flopkit cycle --dry-run` to verify reads, then `flopkit cycle` for the first real cycle.")
    print("Only arm with values published by FLOP Labs on the official surfaces. Wrong identity = spend on the wrong chain.")


def cmd_report(cfg: Cfg, args: argparse.Namespace) -> None:
    day = args.day or utc_day()
    p = DATA_DIR / "reports" / f"{day}.md"
    if p.exists():
        print(p.read_text())
    else:
        # regenerate from ledger for the day if present
        from .core import ledger_read
        rows = ledger_read(day=day, op="cycle")
        if rows:
            loop.write_report(cfg, rows[-1].get("summary") or {})
            print((DATA_DIR / "reports" / f"{day}.md").read_text())
        else:
            print(f"no report or ledger rows for {day}")


def cmd_add_task(cfg: Cfg, args: argparse.Namespace) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with (DATA_DIR / "tasks.jsonl").open("a") as f:
        f.write(json.dumps({"title": args.title, "prompt": args.prompt}) + "\n")
    print(f"queued task '{args.title}' (now {len(spend.task_queue(cfg))} in queue)")


def cmd_settle(cfg: Cfg) -> None:
    for row in loop.settle_channels(cfg, dry_run=False):
        print(row)


def cmd_use(cfg: Cfg, net: str) -> None:
    nets = cfg.raw.get("networks", {})
    if net not in nets:
        raise FlopkitError(f"unknown network '{net}' (known: {', '.join(nets)})")
    prof = nets[net]
    if net == "testnet" and not prof.get("pinned"):
        raise FlopkitError(
            "testnet is not pinned — run `flopkit arm-testnet <genesis> <explorer> <faucet> <rpc> <gateway>` "
            "with FLOP-published values first. Switching active network to an unverified testnet is refused."
        )
    cfg.raw["active_network"] = net
    save_cfg(cfg)
    note = " (rehearsal — no airdrop weight)" if net == "devnet" else " (airdrop-weight; pinned identity required)"
    print(f"active_network → {net}{note}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flopkit", description="Credit-safe FLOP agent participation toolkit")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show config, identity, last credit, today's spend")
    sub.add_parser("credit", help="key-free on-chain credit snapshot")
    sub.add_parser("watch", help="poll official testnet status (read-only)")
    c = sub.add_parser("cycle", help="one participation cycle on the active network")
    c.add_argument("--dry-run", action="store_true", help="verify + plan, no signing")
    c.add_argument("--tasks", type=int, default=2, help="sessions this cycle (capped by config)")
    a = sub.add_parser("arm-testnet", help="pin a published testnet identity (then cycles may sign there)")
    a.add_argument("genesis"); a.add_argument("explorer"); a.add_argument("faucet"); a.add_argument("rpc"); a.add_argument("gateway")
    r = sub.add_parser("report", help="print a day's report")
    r.add_argument("day", nargs="?")
    t = sub.add_parser("add-task", help="queue a real task for the next spend")
    t.add_argument("title"); t.add_argument("prompt")
    sub.add_parser("settle", help="finalize eligible channels now")
    u = sub.add_parser("use", help="switch the active network (devnet rehearsal / armed testnet)")
    u.add_argument("network")

    args = ap.parse_args(argv)
    cfg = load_cfg()
    try:
        if args.cmd == "status":
            cmd_status(cfg)
        elif args.cmd == "credit":
            cmd_credit(cfg)
        elif args.cmd == "watch":
            cmd_watch(cfg)
        elif args.cmd == "cycle":
            cmd_cycle(cfg, args)
        elif args.cmd == "arm-testnet":
            cmd_arm_testnet(cfg, args)
        elif args.cmd == "report":
            cmd_report(cfg, args)
        elif args.cmd == "add-task":
            cmd_add_task(cfg, args)
        elif args.cmd == "settle":
            cmd_settle(cfg)
        elif args.cmd == "use":
            cmd_use(cfg, args.network)
    except FlopkitError as e:
        print(f"flopkit: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
