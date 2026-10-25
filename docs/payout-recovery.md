# Recovery without replay

A timeout or CLI error does not prove a transaction failed on-chain. Neither
killing a process nor restoring a file reverses a submitted transaction. Do not
retry blindly, delete a journal, unlink its lock or use another runtime directory.

1. Read the authoritative log/journal under the original lock. Preserve evidence
   privately; do not commit wallet logs, journals, digests linked to an operator,
   owner settings or archive files to this public repository.
2. Resolve every recorded digest independently through a trusted read-only mainnet
   source: sender, success/failure, checkpoint, consumed objects, recipient credit
   and fee. A pending/unknown record or missing digest needs investigation before
   any continuation. Check other wallet writers and indexed sender history; index
   lag or absent indexed data is not proof of absence of pending transactions.
3. Compare current stake objects, ownership, coin references and balances. Verify
   destination against the owner's independently confirmed configuration, never
   an incoming transfer or lookalike address. JSM recovery remains manual review;
   there is no generic unblock command.

## Cypher reviewed partial-withdrawal resume

`resume_guard.py` preserves the verified recovery mechanism but ships **disabled**:
no `recovery.local.json` is included. `recovery.example.json` is invalid until a
reviewer supplies an exact run ID, confirmed digest/checkpoint, canonical journal
SHA256 and an earlier history checkpoint bound. Generate the hash using UTF-8
`json.dumps(state, sort_keys=True, separators=(',', ':'))`, not the raw file hash.
Keep the manifest private and beside the existing runtime code, never in Git.

The supported case is a reviewed successful withdrawal prefix with no unresolved
intent. The current verifier independently proves the first confirmed withdrawal;
the continuation planner rejects unsupported additional proofs or a transfer record.
It is not a general recovery tool for arbitrary runs. An unknown transaction,
multiple unverified prefix records or an already executed payout requires a fresh
review and potentially a separately tested recovery adaptation. Do not relax checks
or manufacture a manifest to force acceptance.

After independent review, the owner/preparer can invoke this **read-only** mode
with exact reviewed values (placeholders must be replaced):

```sh
python3 -B -u staketasjson.py --check-recovery 'REVIEWED_RUN_ID' --confirm-recipient FULL_VERIFIED_RECIPIENT
```

It holds the existing lock, verifies mainnet/public config, exact journal hash,
withdrawal effects, remaining stake set and bounded sender history. It neither
unblocks state nor calls a wallet. Schema, pagination, lag or proof mismatches stop.

Only after explicit owner approval, the owner can perform the financial step:

```sh
python3 -B -u staketasjson.py --resume-confirmed-run 'REVIEWED_RUN_ID' --confirm-recipient FULL_VERIFIED_RECIPIENT
rc=$?; printf 'Resume exit status: %s\n' "$rc"
```

This rechecks proof, writes an exclusive private archive and fsyncs it and its
parent before saving the recovery transition. It retains the original start time
and confirmed records; only remaining stakes are processed. New intents are saved
before commands. Complete recovery is archived separately before final state is
persisted. Existing archive paths or changed journals refuse subsequent attempts.
There is no automatic unblock or force option. A new interruption requires review.

Do not publish a live recovery manifest or reuse a synthetic test fixture. After
success, remove/retire the private recovery authorization through a separately
reviewed owner action if appropriate; normal scheduling never needs resume flags.
Historical archives and the current journal must remain intact.
