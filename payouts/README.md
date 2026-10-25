# Validator payout variants

These are separately tested JSM and Cypher payout implementations. They are not
a drop-in replacement for the repository's historical root scripts. Importing
root `staketasjson.py` or related legacy scripts can execute financial actions;
never import them for discovery or tests.

| Policy | JSM | Cypher |
| --- | --- | --- |
| Schedule | Friday 12:33 America/Chicago | Daily 12:58 America/Chicago |
| Spend scope | Existing per-coin policy, >5 SUI full-transfer threshold | Real coin objects wallet-wide; one payout PTB |
| Reserve | 4 SUI target before transfer fees; actual remainder may be below 4 | 4 SUI coin reserve plus 0.02 SUI payout fee allowance before fees |
| Empty stake discovery | Preserved existing behavior: can still evaluate liquid coins for payout | Clean exit 0, no wallet commands, completed history preserved |
| Address accumulator | Not included in coin accounting | Reported separately, never spent or counted toward coin reserve |
| Index catch-up | Existing discovery behavior preserved | Bounded read-only lag requery; explicitly pinned coin snapshots |
| Recovery | Stop and reconcile; no generic unblock | Optional exact-run reviewed recovery manifest; explicit owner invocation |

Both withdraw entire discovered StakedSui objects, including principal and any
applicable rewards. The recipient and source wallet are distinct deployment
settings. Successful on-chain receipt does not establish exchange credit.

## Configuration

Each variant is a standalone directory. Copy `deployment.example.json` to
`deployment.local.json` **only in the intended deployment directory**, and fill
it with independently verified public settings. Placeholder examples fail closed.
Never infer the recipient from transaction history, a short address prefix/suffix,
or the active owner. Do not put private keys, recovery phrases, CLI credentials,
raw logs or journals in this repository. There is no environment-variable recipient
override. Local config must be a regular JSON file with the exact documented keys.

- `owner`: full lowercase 32-byte public address used for stake discovery.
- `recipient`: separately verified full address; self-payment is rejected.
- `hostname`: exact expected server hostname.
- `cli_path`: absolute path to the reviewed installed Sui CLI binary.
- `client_config_path`: path to its existing public selection/config file; do not
  copy that file into Git. Cypher verifies active owner/environment before payouts.
- `active_env`: existing reviewed CLI environment alias.
- `rpc_url`: the reviewed mainnet fullnode URL from the example. Alternate chains
  or providers require separate compatibility review rather than loosening this check.

Configuration errors can terminate import/startup before normal logging. They
must never be interpreted as successful empty discovery. Imports read public
configuration only; they do not open a wallet or submit transactions.

Each directory includes `deployment_config.py` intentionally: variants can be
installed independently without adding parent-directory imports or coupling a JSM
release to Cypher changes. Keep the identical configuration validators synchronized;
shared validation tests run against both copies.

## Offline validation

From the repository root, with Python 3.10 or newer:

```sh
python3 -B tests/run_tests.py
```

The runner creates isolated temporary copies with synthetic public identities and
synthetic recovery state. JSM and Cypher run in separate processes to avoid module
name collisions. Transaction subprocesses are mocked; no live wallets, networks,
configuration or journal files are required. Do not run `unittest discover` against
the legacy repository root or import historical financial scripts.

## Read-only discovery

Only after configuring the selected variant and reviewing installation:

```sh
python3 -B staketasjson.py --discover-only
```

This calls the official public mainnet GraphQL endpoint. It does not issue wallet
commands or create payout state. Cypher reports coin balance and excluded address
balance separately, and reports `nothing_to_do` with no payout plan when there are
no stakes. Diagnostic discovery can still fail on a balance/API error; normal
scheduled empty-stake runs exit before querying balances.

**Running without `--discover-only` is a financial action.** The owner must perform
manual financial trials and approve/apply any cron activation. Do not use a guessed
empty wallet as permission to execute the normal command.

See [deployment and rollback](../docs/payout-deployment.md),
[recovery](../docs/payout-recovery.md), and [security review](../docs/payout-security.md).
