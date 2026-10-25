"""JSM-only staged payout candidate. Imports inert; --discover-only never opens wallet."""
import argparse
from deployment_config import CONFIG
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from stake_discovery import DiscoveryError, discover

CLI = CONFIG['cli_path']
RECIPIENT = CONFIG['recipient']
RESERVE = 4_000_000_000
MIN_TRANSFER = 5_000_000_000
GAS_BUDGET = 20_000_000
COMMAND_SECONDS = 120
MAX_OUTPUT = 4 * 1024 * 1024
STATE_DIR = Path(__file__).resolve().parent

class PayoutError(Exception):
    pass

def unique_keys(pairs):
    out = {}
    for k, v in pairs:
        if k in out: raise PayoutError('Duplicate JSON key')
        out[k] = v
    return out

def parse_json(raw):
    if len(raw) > MAX_OUTPUT: raise PayoutError('CLI output exceeds limit')
    try:
        return json.loads(raw, object_pairs_hook=unique_keys,
                          parse_constant=lambda _: (_ for _ in ()).throw(PayoutError('Invalid JSON number')))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PayoutError('Malformed CLI JSON; outcome may be unknown') from exc

def object_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'0x[0-9a-f]{64}', value):
        raise PayoutError('Invalid object ID')
    return value

def uint(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r'[0-9]+', str(value)):
        raise PayoutError('Invalid unsigned integer')
    number = int(value)
    if number > 2**64 - 1: raise PayoutError('Integer out of range')
    return number

def digest(value):
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    if not isinstance(value, str) or not 32 <= len(value) <= 44 or any(c not in alphabet for c in value):
        raise PayoutError('Missing/invalid transaction digest')
    n = 0
    for c in value: n = n * 58 + alphabet.index(c)
    if (n.bit_length() + 7)//8 + len(value) - len(value.lstrip('1')) != 32:
        raise PayoutError('Invalid transaction digest length')
    return value

def run_cli(args):
    """No shell, retries or inherited stdin; bound duration and on-disk output."""
    argv = [CLI, 'client', *args, '--json']
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        proc = None
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stdout,
                                    stderr=stderr, start_new_session=True, close_fds=True)
            deadline = time.monotonic() + COMMAND_SECONDS
            while True:
                if os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > MAX_OUTPUT:
                    raise PayoutError('CLI output limit; transaction outcome may be unknown')
                if time.monotonic() >= deadline:
                    raise PayoutError('CLI timeout; transaction outcome unknown; do not retry')
                try:
                    code = proc.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > MAX_OUTPUT:
                raise PayoutError('CLI output limit; transaction outcome may be unknown')
            stdout.seek(0)
            return code, stdout.read(MAX_OUTPUT + 1)
        except OSError as exc:
            raise PayoutError('CLI process failure; do not retry without review') from exc
        finally:
            if proc is not None and proc.poll() is None:
                # Killing a local process does not undo any transaction already submitted.
                try: os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                proc.wait(timeout=5)

def safe_open(path, flags):
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    s = os.fstat(fd)
    if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_nlink != 1 or s.st_mode & 0o077:
        os.close(fd)
        raise PayoutError('Unsafe lock/state file; operator review required')
    return fd

class Journal:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / '.jsm-payout-state.json'
        self.data = None
    def load(self):
        try: fd = safe_open(self.path, os.O_RDONLY)
        except FileNotFoundError: return None
        with os.fdopen(fd, 'rb') as f: return parse_json(f.read(MAX_OUTPUT + 1))
    def save(self):
        fd, name = tempfile.mkstemp(prefix='.jsm-state-', dir=self.directory)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(self.data, f); f.flush(); os.fsync(f.fileno())
            os.replace(name, self.path)
            parent = os.open(self.directory, os.O_RDONLY)
            try: os.fsync(parent)
            finally: os.close(parent)
        finally:
            if os.path.exists(name): os.unlink(name)
    def ensure_clear(self):
        previous = self.load()
        if previous is not None and (not isinstance(previous, dict) or previous.get('version') != 1 or previous.get('status') != 'complete'):
            raise PayoutError('Previous incomplete/blocked run: owner must reconcile before any new payout')
    def begin(self, ids):
        self.data = {'version':1, 'status':'running', 'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'stake_ids':ids, 'transactions':[]}
        self.save()
    def pending(self, kind, oid, amount):
        record = {'kind':kind, 'object_id':oid, 'amount_mist':amount, 'status':'pending'}
        self.data['transactions'].append(record); self.save(); return record
    def complete(self):
        self.data['status'] = 'complete'; self.save()
    def block(self):
        if self.data is not None:
            self.data['status'] = 'blocked'; self.save()

@contextmanager
def payout_session(directory=STATE_DIR):
    directory = Path(directory)
    s = directory.stat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o022:
        raise PayoutError('State directory must be owned by this user and not group/world writable')
    fd = safe_open(directory / '.jsm-payout.lock', os.O_CREAT | os.O_RDWR)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc: raise PayoutError('Another JSM payout run holds the lock') from exc
        journal = Journal(directory); journal.ensure_clear()
        try: yield journal
        except BaseException:
            journal.block()
            raise
    finally:
        os.close(fd)  # Do not unlink the lock inode; that would permit a lock race.

def confirmation(data):
    if not isinstance(data, dict) or data.get('errors') not in (None, []):
        raise PayoutError('CLI response error; outcome unknown')
    tx = digest(data['digest'])
    checkpoint = uint(data['checkpoint'])
    effects = data['effects']
    if digest(effects['transactionDigest']) != tx:
        raise PayoutError('Digest mismatch; outcome unknown')
    status = effects['status']['status']
    if status not in ('success', 'failure'): raise PayoutError('Unrecognized transaction status')
    return tx, checkpoint, status

def transact(kind, oid, amount, args, journal, runner):
    record = journal.pending(kind, oid, amount)  # Durable BEFORE any possible submission.
    try:
        code, raw = runner(args)
        data = parse_json(raw)
        tx, checkpoint, status = confirmation(data)
        if any(r.get('digest') == tx for r in journal.data['transactions'][:-1]):
            raise PayoutError('Reused transaction digest; outcome uncertain')
        record.update(digest=tx, checkpoint=checkpoint)
        if status == 'failure':
            record['status'] = 'confirmed_failure'; journal.save()
            raise PayoutError('Transaction confirmed failed; owner review required')
        if code != 0: raise PayoutError('CLI nonzero exit despite success JSON; owner reconciliation required')
        if kind == 'withdraw':
            deleted = [x.get('objectId') for x in data.get('objectChanges', []) if isinstance(x, dict) and x.get('type') == 'deleted']
            if oid not in deleted: raise PayoutError('Withdrawal did not confirm target stake deletion')
        else:
            credited = 0
            for change in data['balanceChanges']:
                if change.get('owner') == {'AddressOwner':RECIPIENT} and change.get('coinType') in ('0x2::sui::SUI', '0x'+'0'*63+'2::sui::SUI'):
                    credited += uint(change['amount'])
            if credited != amount: raise PayoutError('Transfer recipient/amount confirmation mismatch')
        record['status'] = 'confirmed_success'; journal.save()
        print(json.dumps({'event':'confirmed', **record}), flush=True)
    except BaseException:
        if record['status'] == 'pending': record['status'] = 'unknown'
        journal.save()
        raise

def balances(runner):
    code, raw = runner(['gas'])
    if code != 0: raise PayoutError('Gas refresh failed; no transfers permitted')
    data = parse_json(raw)
    rows = data.get('gasCoins') if isinstance(data, dict) else data
    if not isinstance(rows, list): raise PayoutError('Malformed gas balance list')
    result = {};
    for row in rows:
        oid = object_id(row['gasCoinId']); amount = uint(row['mistBalance'])
        if oid in result: raise PayoutError('Duplicate gas coin')
        result[oid] = amount
    # Retain original scope: address balances are not added to the coin budget.
    return result

def run_payout(staked_sui_ids, journal, runner=run_cli):
    ids = [object_id(x) for x in staked_sui_ids]
    if len(ids) != len(set(ids)): raise PayoutError('Duplicate stake ID')
    journal.begin(ids)
    for oid in ids:
        transact('withdraw', oid, None, ['call','--package','0x3','--module','sui_system','--function','request_withdraw_stake','--args','0x5',oid,'--gas-budget',str(GAS_BUDGET)], journal, runner)
    coins = balances(runner)  # Only reachable after every withdrawal is confirmed.
    ordered = sorted(coins, key=coins.get)
    transferred = 0
    for oid in ordered:
        if oid not in coins: raise PayoutError('Expected coin disappeared; operator review required')
        balance, remaining = coins[oid], sum(coins.values())
        if balance > MIN_TRANSFER and remaining - balance >= RESERVE:
            args = ['transfer','--to',RECIPIENT,'--object-id',oid]
            amount = balance
        elif remaining > RESERVE and balance > RESERVE:
            amount = min(remaining - RESERVE, balance - RESERVE)
            args = ['transfer-sui','--to',RECIPIENT,'--sui-coin-object-id',oid,'--amount',str(amount),'--gas-budget',str(GAS_BUDGET)]
        else:
            continue
        transact('transfer', oid, amount, args, journal, runner)
        transferred += 1
        # Refresh after confirmed transfer so paid fees and changed coins cannot inflate the next budget.
        coins = balances(runner)
    journal.complete()
    print(json.dumps({'event':'complete','transfers':transferred,'coin_balance_mist':sum(coins.values())}), flush=True)

def main(argv=None):
    parser = argparse.ArgumentParser(); parser.add_argument('--discover-only', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.discover_only:
            ids = discover(); print(json.dumps({'object_ids':ids,'count':len(ids)})); return 0
        if socket.gethostname() != CONFIG['hostname']:
            print('Refusing payout on a non-JSM host', file=sys.stderr); return 2
        with payout_session() as journal:
            ids = discover()
            run_payout(ids, journal)
        return 0
    except (DiscoveryError, PayoutError, OSError, ValueError, KeyError, TypeError) as exc:
        print('Payout stopped; no automatic retry. '+type(exc).__name__+': '+str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Interrupted; reconcile journal before another payout', file=sys.stderr); return 1

if __name__ == '__main__': sys.exit(main())
