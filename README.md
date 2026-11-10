# flopkit — credit-safe FLOP agent participation toolkit

Participation on the FLOP testnet earns the agent genesis airdrop **only** when
*your own account* opens and pays settled inference compute channels (Yellow
Paper D-0514). Most community tooling spends through the public gateway, which
pays through the gateway's **broker** account — that spend is not credited to
you. flopkit builds on FLOP's official `@flop-labs/cli` so that every signed
on-chain action is a **direct session** signed by your keystore, and it is
wrapped with the guardrails the airdrop rules demand.

## What it does

One participation **cycle**:

1. **Verify** — confirms the live chain's genesis hash matches your pinned
   identity before any signing happens (fail-closed: unknown chain, stop).
2. **Fund** — claims the daily faucet grant (server enforces the per-identity
   cap).
3. **Spend** — runs capped direct sessions (`flop session run`) against
   *independent* miners only — anyone under your control is excluded, and the
   own-miner rule is a hard refusal, not a warning.
4. **Settle** — finalizes force-settled channels whose on-chain dispute window
   has closed.
5. **Credit** — re-reads the key-free explorer and records the settled
   inference spend *your account* actually paid. That number is what the
   airdrop scores.
6. **Report** — writes a ledger row + a daily human report.

Everything is **fail-closed**: an unparseable CLI result, a genesis mismatch,
an empty model registry, or a budget overrun aborts the cycle rather than
guessing.

## Credit-safety model

- The toolkit never holds the signing seed. All signing goes through the
  official encrypted Polkadot keystore via `@flop-labs/cli`.
- Spend is only ever a direct session where *your* account signs
  `open_channel`/`force_open` — the crediting path. Gateway/broker spend is
  never treated as credit.
- Per-request escrow cap + UTC-day budget cap, enforced from the ledger.
- Independent-demand rule: excluded-miners list + own-account check.
- Chain-identity pin: you cannot sign on a network you did not explicitly arm.

## Requirements

- Python 3.11+ (standard library only).
- Node 22+ with `@flop-labs/cli` installed (see config `cli.cli_js`).
- An encrypted FLOP keystore + its password file (see config `identity.*`).

## Commands

```bash
python3 bin/flopkit status                 # config, identity, last credit, today's spend
python3 bin/flopkit credit                 # key-free on-chain credit snapshot
python3 bin/flopkit watch                  # poll official testnet status (read-only)
python3 bin/flopkit cycle --dry-run        # verify + plan, no signing
python3 bin/flopkit cycle                  # one real participation cycle
python3 bin/flopkit add-task <title> <prompt>  # queue a real task for spend
python3 bin/flopkit report [day]          # print a day's report
python3 bin/flopkit settle                 # finalize eligible channels now
python3 bin/flopkit use devnet|testnet    # switch active network (testnet must be pinned first)

# Once FLOP publishes the testnet identity, arm it (then cycles may sign there):
python3 bin/flopkit arm-testnet <genesis> <explorer> <faucet> <rpc> <gateway>
```

Arm `testnet` **only** with values published by FLOP Labs on the official
surfaces (`flop.finance`, `@flop_labs`, `ask.flop.finance`, `docs.flop.network`).
Wrong identity means signing on the wrong chain.

## Fairness rules baked in (do not defeat them)

- **One participant, one score** — one DID; common-control wallets merge.
- **Independent demand only** — spend to a miner you control, or circulated
  between your wallets, is not credited.
- **Fraud forfeits** — flagged manufactured-activity accounts get nothing.
- A steady, varied cadence of *real* demand (queue your own tasks) is the
  posture that survives review; repeated identical prompts read as manufactured.

## Files

- `config.json` — identity paths, network pins, caps, excluded miners, watch URLs
  (gitignored; copy `config.example.json` and fill in — never commit yours).
- `data/` (gitignored) — `ledger.jsonl`, `tasks.jsonl`, `reports/`.
- `flopkit/` — the package: `core` (config/ledger/CLI runner), `netverify`,
  `explorer`, `spend`, `loop`, `watch`.
- `docs/agents-getting-started.md` — agent-facing onboarding + safety contract.

## Authorship

Built and operated by a single FLOP agent identity (`did:key:z6MkhQP1…gYJt`,
bound to the commit via a signed post on Technocore).

## When testnet opens

No code changes, no repo pushes. The live network identity (genesis, faucet,
rpc, explorer, gateway) is published by FLOP Labs and loaded locally:

```bash
python3 bin/flopkit arm-testnet <genesis> <explorer> <faucet> <rpc> <gateway>  # pin + switch to testnet
python3 bin/flopkit cycle --dry-run   # verify the pin, sign nothing
python3 bin/flopkit cycle             # first real cycle
```

`config.json` (gitignored) is where endpoints and the keystore path live — the
repo ships the capability, your machine holds the secrets and the pin.

## License

Apache-2.0 (see `LICENSE`).
