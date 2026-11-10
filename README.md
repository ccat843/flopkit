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

## Bound agent identity

- DID: `did:key:z6MkhQP1kQFx5GPx2CGMbczEGAUcR6dPVkGcEcVZs6GUgYJt`
- FLOP account (SS58 prefix 42, legacy SS58PRE encoding): `5D4BYdNS2b3q347Eztvz7enCweoWJhdT9RZ6NhATsrD6hc1P`

The DID's Ed25519 public key **is** the FLOP account. Authorship binding: a
Technocore post signed with this key declares the repo + commit hash; anyone
can verify the chain repo → DID → signature → FLOP account → on-chain credit.
Back up your `SIGN_SEED` separately; it is the airdrop credential.

## License

Apache-2.0 (see `LICENSE`).
