# Controlled deployment and rollback

Repository publication and server deployment are separate actions. This change
installs nothing, edits no cron and performs no wallet transaction. Existing root
scripts and monitoring workflows remain untouched for compatibility.

## Current deployment relationship

The verified servers presently run repaired scripts from dated trial directories
under the service account's home, not from this repository checkout. Their journal,
lock, recovery archives and logs reside in those directories. Renaming them does
not make them disposable. Do not start a new checkout against the same wallet:
a fresh directory would bypass the existing unresolved-run guard and lock.

This public repository intentionally omits actual owner/recipient addresses,
private network routes, dated runtime paths and operational evidence. The existing
repository already contains historical wallet snapshots and hardcoded settings;
this change neither republishes new evidence nor rewrites unrelated Git history.
Use the owner's private verified deployment inventory to resolve the actual paths.

## Before installing a reviewed release

1. Review the diff, run both offline test suites, and record source hashes/commit.
   This branch adds configurable versions of verified implementations. It is not
   byte-identical to current deployment: public config replaces hardcoded identity
   and path settings, and Cypher's one-time recovery values become a reviewed local
   manifest. The financial policies in the comparison table remain separate.
2. Read the current host/user/timezone, crontab, CLI version and public selections.
   Compare actual full owner and recipient independently. Current compatibility
   evidence covers Sui CLI `1.80.1-782577ca40c9`, Python 3.10.12 and mainnet.
   Newer CLI JSON/flags require review; do not assume compatibility from version alone.
3. Coordinate an exclusive window with the owner: no cron/manual wallet writer.
   The candidate lock cannot stop a different script or an external signer.
4. Acquire the **existing runtime directory's original lock**, inspect all journal
   records and archives, and record pre-install SHA256 of state/logs/code/config.
   If unresolved, stop financial work and follow recovery; code repair must not
   clear state. Back up code/config into an exclusive new code-only archive.
5. Copy only the selected variant's `.py` runtime files and reviewed
   `deployment.local.json` into that same directory, with ordinary private file
   permissions. No automatic installer is supplied. Do not copy tests, synthetic
   fixture files, example recovery settings or the other variant. Verify source
   hashes and that all journal/archive/log bytes and lock inode remain unchanged.
6. Check imports/config using target Python with `-B`, then run only
   `--discover-only`. Do not invoke normal payouts as an installation test.
   JSM and Cypher scripts retain their own `.jsm-*` / `.cypher-*` state names.
7. Owner performs any financial trial separately, records exit status immediately,
   and has effects/digests/recipient/fees/reserve reconciled independently. A quiet
   terminal with redirected output is not success. Only a complete journal plus
   confirmed chain results justify later scheduling.

No code-only backup or incoming directory may be used as an alternate payout
runtime. A future move to a permanent path needs a separately reviewed, quiescent
migration that transfers the authoritative state/archives/logs, retains one lock
coordination domain, updates exactly one cron and retires the old execution path.
That migration is not automated here.

## Owner-applied schedules

Back up the entire crontab, replace exactly one matching old payout entry, preserve
unrelated monitoring jobs, and read back for duplicates. Use the privately verified
absolute runtime path in place of each placeholder below. Do not paste placeholders.

The host must remain `America/Chicago` with no conflicting `TZ`/`CRON_TZ` override.
This preserves local daylight-saving transitions. Do not assume an unsupported
`CRON_TZ` feature or change host timezone as part of code deployment.

```cron
# JSM: Friday 12:33 Central; preserve its existing verified log destination/policy.
33 12 * * 5 cd /ABSOLUTE/EXISTING/JSM_RUNTIME && /usr/bin/python3 -B -u /ABSOLUTE/EXISTING/JSM_RUNTIME/staketasjson.py > /ABSOLUTE/EXISTING/JSM_LOG 2>&1
# Cypher: daily 12:58 Central, append scheduled output.
58 12 * * * cd /ABSOLUTE/EXISTING/CYPHER_RUNTIME && /usr/bin/python3 -B -u /ABSOLUTE/EXISTING/CYPHER_RUNTIME/staketasjson.py >> /ABSOLUTE/EXISTING/CYPHER_RUNTIME/scheduled-run.log 2>&1
```

Never put resume flags in cron. Both completed journals allow later legitimate
runs. Cypher's successful empty discovery exits 0 before wallet access; errors and
unresolved records do not masquerade as empty success. JSM's existing empty-day
and per-coin behavior is deliberately preserved and must not be assumed identical.
Configure log retention separately with the owner; append logs can grow.

## Rollback

If installation verification fails, keep financial execution stopped. Under the
same lock, restore only the reviewed code/config backup and verify hashes. Never
roll back/delete the journal or archives: chain transactions cannot be undone by
restoring files. If the new version submitted anything, reconcile it first and
check old-code state compatibility; an unresolved run remains held. Owner alone
reverts any activated cron, preserving unrelated entries. Do not re-enable the
unsafe historical root payout script as an automatic fallback.
