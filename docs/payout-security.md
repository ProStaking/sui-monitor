# Review scope and limits

Reviewed 2026-10-25. New payout code and offline tests use Python's standard library
only; no dependency installation, credentials or remote execution framework added.
The repository's historical root monitoring/financial scripts are out of scope
and unchanged. They may depend on requests/dotenv and execute at import time.

## Checks

- Fixed official HTTPS GraphQL endpoint with TLS verification; no ambient proxy,
  password manager or redirects. Strict JSON, owner/type/ID/cursor checks and
  bounded bodies/pages. Cypher pins balance and coin pages to one checkpoint.
- Shell-free wallet argv, explicit CLI path, bounded subprocess output/time,
  durable pending records before submission, confirmed effects before later work,
  exact recipient checks and no automatic transaction retries.
- Private owner-checked state and lock files, symlink rejection, nonblocking flock,
  atomic journal replacement and fsync. Recovery archives use exclusive creation.
- Public deployment settings are external, strictly validated and ignored by Git.
  Duplicate/unknown fields, placeholders, self-payment and unreviewed RPC URLs
  fail closed. Synthetic fixtures are generated only in temporary test directories.
- Independent JSM/Cypher test processes preserve policy differences. Tests mock
  all financial paths; no end-to-end signing/submission is claimed.

## Current CVE/GHSA assessment

Reviewed authoritative advisories, not merely package-version strings:

- [Requests CVE-2024-47081 / GHSA-9hjg-9r4m-mvj7](https://github.com/advisories/GHSA-9hjg-9r4m-mvj7):
  new payout modules do not use Requests or netrc authentication. Ubuntu backport
  versions must be compared using [Ubuntu's status](https://ubuntu.com/security/CVE-2024-47081).
- [urllib3 CVE-2025-66471 / GHSA-2xpw-w6gg-jr37](https://github.com/advisories/GHSA-2xpw-w6gg-jr37):
  new payout modules do not use urllib3 or transparent decompression. The advisory
  remains relevant to other host software; do not claim the whole host is safe.
- [urllib.request CVE-2026-15806](https://ubuntu.com/security/CVE-2026-15806):
  credential-scheme confusion is outside this code's used surface: no password
  manager/credentials, fixed HTTPS and redirect refusal.

This is not an exhaustive audit of Python/OpenSSL/OS packages or the Sui binary's
Rust dependencies. Keep supported distro security updates and review the exact
installed CLI source/JSON behavior before future releases.

## Known limits and preserved differences

The lock coordinates this runtime directory only, not old cron scripts or outside
wallet signers. CLI object versions are resolved after preflight, leaving a race;
receipt verification detects mismatches but cannot undo already executed actions.
GraphQL can lag; Cypher retries only read-only lagged snapshots, not transactions.
Network elapsed limits are checked around operations, not an OS hard deadline.

Cypher ignores address accumulator credits for spending/reserve and reports them
separately. Incoming address credits are tolerated across independent snapshots;
observed decreases or inconsistent pinned totals fail. Net balance checks cannot
prove absence of offsetting external activity or a pending external transaction.
Incoming actual coin objects can still invalidate exact coin snapshots. No
address-balance sweeping/conversion is introduced. Coin count is capped at64.

JSM intentionally retains its existing per-coin reserve/threshold behavior and
can fall slightly below4SUI after transfer fees; its empty-day behavior and identity
checks are not silently upgraded to Cypher's. Such policy changes need a separate
review. Never deploy one variant over the other to get a newer feature.

## Publication review

Review only intended changes with Git. This public checkout's pre-existing JSON
snapshots and hardcoded legacy settings are not a reason to add current wallet
information. Do not stage generated fixtures, credentials, operational records,
private network routes or local deployment/recovery manifests. `.gitignore` is
not a substitute for reviewing the actual diff. Signed-commit settings must be
honored; if the signing agent requires owner interaction, stop rather than bypass.
