"""Cypher-only staged payout candidate. Imports inert; --discover-only never opens wallet."""
import argparse
from deployment_config import CONFIG
import hashlib
import copy
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
from stake_discovery import DiscoveryError, discover, OWNER
from wallet_snapshot import snapshot, wait_snapshot, address_balance

CLI = CONFIG['cli_path']
RECIPIENT = CONFIG['recipient']
RESERVE = 4_000_000_000
MAX_GAS_COINS = 64  # Conservative local cap; no batching/partial sweep.
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
        self.path = self.directory / '.cypher-payout-state.json'
        self.data = None
    def load(self):
        try: fd = safe_open(self.path, os.O_RDONLY)
        except FileNotFoundError: return None
        with os.fdopen(fd, 'rb') as f: return parse_json(f.read(MAX_OUTPUT + 1))
    def save(self):
        fd, name = tempfile.mkstemp(prefix='.cypher-state-', dir=self.directory)
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
    def resume(self, plan):
        # Called only while holding the original directory lock, after proof verification.
        from resume_guard import validate_state, canonical, STATE_HASH
        previous=self.load()
        validate_state(previous,plan['run_id'],plan['recipient'])
        if plan['prior_state_sha256']!=STATE_HASH:
            raise PayoutError('Recovery proof does not match journal')
        archive=self.directory / ('.cypher-recovery-'+STATE_HASH+'.json')
        # Exclusive creation: a prior/interrupted resume attempt requires review.
        fd=safe_open(archive,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        with os.fdopen(fd,'wb') as f:
            f.write(canonical({'previous_state':previous,'recovery_evidence':plan}))
            f.flush();os.fsync(f.fileno())
        dfd=os.open(self.directory,os.O_RDONLY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
        self.data=copy.deepcopy(previous)
        self.data['status']='running'
        self.data['recovery']={'archive':archive.name,'prior_state_sha256':STATE_HASH,
                               'evidence':plan,'resumed_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
        self.save() # Previous confirmed transaction stays in the same journal.
    def begin(self, ids):
        self.data = {'version':1, 'status':'running', 'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'stake_ids':ids, 'transactions':[]}
        self.save()
    def pending(self, kind, oid, amount):
        record = {'kind':kind, 'object_id':oid, 'amount_mist':amount, 'status':'pending'}
        self.data['transactions'].append(record); self.save(); return record
    def complete(self):
        self.data['status'] = 'complete'
        if 'recovery' in self.data:
            # Retain the complete recovery lineage even when a later normal run begins.
            from resume_guard import canonical
            archive=self.directory / (self.data['recovery']['archive']+'.complete')
            fd=safe_open(archive,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
            with os.fdopen(fd,'wb') as f:
                f.write(canonical(self.data));f.flush();os.fsync(f.fileno())
            dfd=os.open(self.directory,os.O_RDONLY)
            try:os.fsync(dfd)
            finally:os.close(dfd)
        self.save()
    def block(self):
        if self.data is not None:
            self.data['status'] = 'blocked'; self.save()

@contextmanager
def payout_session(directory=STATE_DIR, recovery=False):
    directory = Path(directory)
    s = directory.stat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o022:
        raise PayoutError('State directory must be owned by this user and not group/world writable')
    fd = safe_open(directory / '.cypher-payout.lock', os.O_CREAT | os.O_RDWR)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc: raise PayoutError('Another Cypher payout run holds the lock') from exc
        journal = Journal(directory)
        if not recovery: journal.ensure_clear()
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
        if checkpoint < max((r.get('checkpoint',0) for r in journal.data['transactions'][:-1]),default=0):
            raise PayoutError('Transaction checkpoint regressed; reconcile outcome')
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
        validate_payment_response(data, kind, journal)
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

def validate_identity():
    # client.yaml contains public account/environment selections, not private-key bytes.
    text = Path(CONFIG['client_config_path']).read_text()
    for field, expected in [('active_address', OWNER), ('active_env', CONFIG['active_env'])]:
        found = re.findall(r'^'+field+r':\s*([^\n]+)$', text, re.M)
        if len(found)!=1 or found[0].strip().strip("\"'")!=expected:
            raise PayoutError('Cypher public wallet configuration changed; stop')

def validate_payment_response(data, kind, journal):
    txdata = data['transaction']['data']
    if txdata['sender']!=OWNER or txdata['gasData']['owner']!=OWNER:
        raise PayoutError('Unexpected sender or gas sponsor')
    if uint(txdata['gasData']['budget'])!=GAS_BUDGET:
        raise PayoutError('Unexpected gas budget')
    expected = journal.data['current_payment']
    actual = {}
    for ref in txdata['gasData']['payment']:
        oid=object_id(ref['objectId'])
        if oid in actual: raise PayoutError('Duplicate gas payment reference')
        actual[oid]={'version':uint(ref['version']), 'digest':digest(ref['digest'])}
    if actual!={k:{'version':v['version'],'digest':v['digest']} for k,v in expected.items()}:
        raise PayoutError('Gas object versions changed; reconcile outcome')
    g=data['effects']['gasUsed']
    fee=uint(g['computationCost'])+uint(g['storageCost'])-uint(g['storageRebate'])
    if fee>GAS_BUDGET:raise PayoutError('Gas fee exceeded declared bound')
    journal.data['transactions'][-1]['net_gas_mist']=fee
    if kind=='transfer':
        plan=journal.data['wallet_plan']
        if plan['total_mist']-plan['amount_mist']-fee<RESERVE:
            raise PayoutError('Reserve confirmation failed')

def wallet_plan(coins):
    if len(coins)>MAX_GAS_COINS:raise PayoutError('Too many gas coins; no partial sweep')
    total=sum(v['balance'] for v in coins.values())
    if total>2**64-1:raise PayoutError('Wallet sum out of range')
    amount=max(0,total-RESERVE-GAS_BUDGET)
    return {'total_mist':total,'amount_mist':amount,'reserve_mist':RESERVE,'gas_allowance_mist':GAS_BUDGET}

def checked_wallet(reader, runner, min_checkpoint):
    first=reader(min_checkpoint=min_checkpoint)
    actual=balances(runner)  # Checked pinned CLI gRPC result, no cached gas file.
    second=reader(min_checkpoint=min_checkpoint)
    if first!=second:raise PayoutError('Coin IDs/versions/digests/balances changed during preflight')
    if getattr(second,'checkpoint',0)<getattr(first,'checkpoint',0):
        raise PayoutError('Wallet checkpoint regressed during preflight')
    if address_balance(second)<address_balance(first):
        raise PayoutError('Address balance decreased during preflight; reconcile external spending')
    if actual!={oid:v['balance'] for oid,v in second.items()}:
        raise PayoutError('GraphQL and current gRPC coin inventory disagree')
    wallet_plan(second)  # Enforce coin-count and u64 bounds even for withdrawal preflight.
    return second

def run_payout(staked_sui_ids, journal, runner=run_cli, reader=wait_snapshot, identity=validate_identity, resume_plan=None):
    identity()
    ids=[object_id(x) for x in staked_sui_ids]
    if len(ids)!=len(set(ids)):raise PayoutError('Duplicate stake ID')
    if resume_plan is None:
        journal.begin(ids);checkpoint=0
    else:
        if ids!=resume_plan['remaining_stake_ids'] or journal.data is None or 'recovery' not in journal.data:
            raise PayoutError('Resume workflow has no verified durable transition')
        checkpoint=resume_plan['min_checkpoint']
    def read_wallet():
        coins=checked_wallet(reader,runner,checkpoint)
        credit=address_balance(coins)
        if credit<journal.data.get('last_address_balance_mist',0):
            raise PayoutError('Address balance decreased during run; reconcile external spending')
        journal.data['last_address_balance_mist']=credit
        journal.save()
        return coins
    for oid in ids:
        coins=read_wallet()
        payment={};funds=0
        for cid in sorted(coins,key=lambda k:(-coins[k]['balance'],k)):
            payment[cid]=coins[cid];funds+=coins[cid]['balance']
            if funds>=GAS_BUDGET:break
        if funds<GAS_BUDGET:raise PayoutError('Insufficient gas for withdrawal; no submission')
        journal.data['current_payment']=payment;journal.save()
        args=['call','--package','0x3','--module','sui_system','--function','request_withdraw_stake','--args','0x5',oid,'--sender',OWNER,'--gas',*payment,'--gas-budget',str(GAS_BUDGET)]
        transact('withdraw',oid,None,args,journal,runner)
        checkpoint=journal.data['transactions'][-1]['checkpoint']
    coins=read_wallet()
    plan=wallet_plan(coins);journal.data['wallet_plan']=plan
    if plan['amount_mist']:
        # Explicit gas payment coins are smashed by protocol, then one GasCoin split is transferred.
        # No standalone merge transaction and no per-coin reserve is kept.
        coins=dict(sorted(coins.items()))
        journal.data['current_payment']=coins;journal.save()
        args=['ptb','--sender','@'+OWNER,'--gas-coin',*('@'+oid for oid in coins),'--gas-budget',str(GAS_BUDGET),'--split-coins','gas','['+str(plan['amount_mist'])+']','--assign','payout','--transfer-objects','[payout.0]','@'+RECIPIENT]
        transact('transfer',next(iter(coins)),plan['amount_mist'],args,journal,runner)
        checkpoint=journal.data['transactions'][-1]['checkpoint']
        after=read_wallet()
        expected=plan['total_mist']-plan['amount_mist']-journal.data['transactions'][-1]['net_gas_mist']
        if len(after)!=1 or sum(v['balance'] for v in after.values())!=expected or expected<RESERVE:
            raise PayoutError('Post-payout single-coin/reserve reconciliation failed')
    else:
        after=coins
    journal.data['final_coin_balance_mist']=sum(v['balance'] for v in after.values())
    journal.data['final_address_balance_mist']=address_balance(after)
    journal.data['final_wallet_balance_mist']=journal.data['final_coin_balance_mist']+address_balance(after)
    journal.complete()
    print(json.dumps({'event':'complete','payout_mist':plan['amount_mist'],
                      'coin_balance_mist':journal.data['final_coin_balance_mist'],
                      'excluded_address_balance_mist':address_balance(after),
                      'wallet_balance_mist':journal.data['final_wallet_balance_mist']}),flush=True)

def main(argv=None):
    parser = argparse.ArgumentParser()
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--discover-only',action='store_true')
    modes.add_argument('--check-recovery',metavar='RUN_ID')
    modes.add_argument('--resume-confirmed-run',metavar='RUN_ID')
    parser.add_argument('--confirm-recipient')
    args = parser.parse_args(argv)
    try:
        if args.discover_only:
            ids = discover(); coins=snapshot(); print(json.dumps({'owner':OWNER,'recipient':RECIPIENT,'object_ids':ids,'count':len(ids),'coins':coins,'excluded_address_balance_mist':address_balance(coins),'wallet_balance_mist':sum(v['balance'] for v in coins.values())+address_balance(coins),'action':'stakes_available' if ids else 'nothing_to_do','coin_only_plan_before_withdrawals':wallet_plan(coins) if ids else None})); return 0
        if socket.gethostname() != CONFIG['hostname']:
            print('Refusing payout on a non-Cypher host', file=sys.stderr); return 2
        if args.check_recovery or args.resume_confirmed_run:
            from resume_guard import verify, validate_endpoint
            validate_identity();validate_endpoint()
            with payout_session(recovery=True) as journal:
                plan=verify(journal.load(),args.check_recovery or args.resume_confirmed_run,
                            args.confirm_recipient)
                if args.check_recovery:
                    print(json.dumps({'event':'recovery_verified_read_only','plan':plan}),flush=True)
                    return 0
                journal.resume(plan)
                run_payout(plan['remaining_stake_ids'],journal,resume_plan=plan)
        else:
            if args.confirm_recipient:raise PayoutError('Recipient confirmation requires explicit recovery mode')
            with payout_session() as journal:
                ids = discover()
                if not ids:
                    # ensure_clear already ran under the lock. Preserve any completed
                    # history; do not start a new journal or touch a wallet on empty days.
                    print(json.dumps({'event':'nothing_to_do','message':'No stake objects; no payout attempted. A later scheduled run may check again.'}),flush=True)
                    return 0
                run_payout(ids, journal)
        return 0
    except (DiscoveryError, PayoutError, OSError, ValueError, KeyError, TypeError) as exc:
        print('Payout stopped; no automatic retry. '+type(exc).__name__+': '+str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Interrupted; reconcile journal before another payout', file=sys.stderr); return 1

if __name__ == '__main__': sys.exit(main())
