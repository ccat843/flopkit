"""Network identity verification — the fail-closed gate before any mutation.

A cycle may only touch a network whose chain identity was pinned in
config.json (expected_genesis). The official CLI's `network status`
verifies the live chain against its profile; we re-pin the genesis hash
ourselves so a swapped endpoint can never receive signed spend.
"""

from __future__ import annotations

import time

from .core import Cfg, FlopkitError, run_cli


def verify_network(cfg: Cfg, net_name: str | None = None) -> dict:
    name = net_name or cfg.active_network
    profile = cfg.net_profile(name)
    expected = profile.get("expected_genesis")
    if not expected:
        raise FlopkitError(
            f"network '{name}' has no pinned expected_genesis — "
            "run `flopkit arm-testnet <genesis> ...` first. Refusing to operate."
        )
    res: dict = {}
    for _attempt in range(3):  # devnet WSS is flaky: retry transient unreachability before failing
        res = run_cli(cfg, "network", "status", "--network", name, retries=3)
        rpc = res.get("rpc") if isinstance(res.get("rpc"), dict) else {}
        if rpc.get("reachable") is not False and rpc.get("genesis_hash"):
            break
        time.sleep(5)
    rpc = res.get("rpc") if isinstance(res.get("rpc"), dict) else {}
    genesis = rpc.get("genesis_hash")
    if genesis != expected:
        raise FlopkitError(
            f"GENESIS MISMATCH on '{name}': chain reports {genesis}, pinned {expected}. "
            "Aborting: signed spend must not go to an unverified chain."
        )
    reachable = rpc.get("reachable")
    faucet_ok = res.get("faucet")
    if isinstance(faucet_ok, dict) and not faucet_ok.get("matches_rpc", True):
        raise FlopkitError(f"faucet chain identity does not match RPC on '{name}'")
    return {
        "network": name,
        "genesis": genesis,
        "spec_version": rpc.get("spec_version"),
        "rpc_reachable": bool(reachable),
        "faucet_reachable": bool((faucet_ok or {}).get("reachable")) if isinstance(faucet_ok, dict) else None,
        "verified_at": res.get("verified_at"),
    }


def cli_network_version_ok(cfg: Cfg, net_name: str | None = None) -> None:
    """Surface spec-version incompatibilities as clear errors.

    CLI versions pin supported runtime specs; when FLOP force-upgrades the
    runtime, an old CLI fails with 'Unsupported runtime spec version'. That
    is a definite, non-retryable error — stop and say so instead of retrying.
    """
    # run_cli already raises on hard errors; nothing extra needed here.
    # Kept as a documented no-op hook for callers that want to check explicitly.
    verify_network(cfg, net_name)
