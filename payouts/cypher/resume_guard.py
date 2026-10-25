"""Exact-run, read-only recovery proof. No subprocess, signing or journal mutation."""
import hashlib
from deployment_config import CONFIG, read_config
import json
import re
from pathlib import Path
from stake_discovery import OWNER, QUERY, fetch_page, discover, DiscoveryError
from wallet_snapshot import wait_snapshot,number
from recovery_review import continuation_plan
# Recovery is disabled unless a separately reviewed local manifest is installed.
_RECOVERY=read_config('recovery.local.json',optional=True) or {}
RUN_ID=_RECOVERY.get('run_id')
TX=_RECOVERY.get('transaction_digest')
CHECKPOINT=_RECOVERY.get('checkpoint')
STATE_HASH=_RECOVERY.get('state_sha256')
CHAIN='4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S'
HISTORY_AFTER=_RECOVERY.get('history_after_checkpoint')
RECIPIENT=CONFIG['recipient']

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def state_hash(state):return hashlib.sha256(canonical(state)).hexdigest()
def validate_state(state,run_id,recipient):
    if set(_RECOVERY)!={'run_id','transaction_digest','checkpoint','state_sha256','history_after_checkpoint'}:
        raise DiscoveryError('No reviewed recovery manifest installed')
    if not isinstance(RUN_ID,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',RUN_ID):
        raise DiscoveryError('Invalid recovery run ID')
    if not isinstance(TX,str) or not re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{32,44}',TX):
        raise DiscoveryError('Invalid recovery digest')
    if type(CHECKPOINT) is not int or type(HISTORY_AFTER) is not int or not 0<=HISTORY_AFTER<CHECKPOINT<2**53:
        raise DiscoveryError('Invalid recovery checkpoint bounds')
    if not isinstance(STATE_HASH,str) or not re.fullmatch(r'[0-9a-f]{64}',STATE_HASH):
        raise DiscoveryError('Invalid recovery state hash')
    if run_id!=RUN_ID or recipient!=RECIPIENT or state_hash(state)!=STATE_HASH:
        raise DiscoveryError('Recovery requires the exact reviewed run, recipient and unchanged blocked journal')
    return state

def validate_endpoint(text=None):
    text=Path(CONFIG['client_config_path']).read_text() if text is None else text
    blocks=re.split(r'^\s*- alias:\s*',text,flags=re.M)[1:]
    matches=[b for b in blocks if b.splitlines()[0].strip().strip('\"\'')==CONFIG['active_env']]
    if len(matches)!=1:raise DiscoveryError('Ambiguous mainnet environment')
    rpc=re.findall(r'^\s+rpc:\s*(.+)$',matches[0],flags=re.M)
    if len(rpc)!=1 or rpc[0].strip().strip('\"\'')!=CONFIG['rpc_url']:
        raise DiscoveryError('Reviewed mainnet CLI endpoint changed')

def query_data(fetch,q):
    r=fetch(None,20,q)
    if not isinstance(r,dict) or r.get('errors') or not isinstance(r.get('data'),dict):
        raise DiscoveryError('Recovery GraphQL response failed')
    return r['data']

def verify(state,run_id,recipient,fetch=fetch_page,waiter=wait_snapshot):
    validate_state(state,run_id,recipient)
    waiter(min_checkpoint=CHECKPOINT) # read-only catch-up only, no CLI
    q='{chainIdentifier checkpoint{sequenceNumber timestamp} transaction(digest:"'+TX+'") {digest sender{address} effects{status checkpoint{sequenceNumber} effectsJson}}}'
    data=query_data(fetch,q)
    if data['chainIdentifier']!=CHAIN:raise DiscoveryError('Wrong recovery chain')
    watermark=number(data['checkpoint']['sequenceNumber'])
    if watermark<CHECKPOINT:raise DiscoveryError('Recovery watermark regressed')
    tx=data['transaction'];e=tx['effects'];raw=e['effectsJson']
    if tx['digest']!=TX or tx['sender']['address']!=OWNER or e['status']!='SUCCESS' or raw['status']!={'success':True} or raw['transactionDigest']!=TX:
        raise DiscoveryError('Withdrawal success/sender/digest proof mismatch')
    if number(e['checkpoint']['sequenceNumber'])!=CHECKPOINT:raise DiscoveryError('Wrong recovery transaction checkpoint')
    target=state['stake_ids'][0]
    deleted=[o for o in raw['changedObjects'] if o.get('objectId')==target]
    if len(deleted)!=1 or deleted[0].get('idOperation')!='DELETED' or deleted[0].get('outputState')!='OUTPUT_OBJECT_STATE_DOES_NOT_EXIST' or deleted[0].get('inputOwner')!={'kind':'ADDRESS','address':OWNER}:
        raise DiscoveryError('Stake consumption/owner proof mismatch')
    g=raw['gasUsed'];fee=number(g['computationCost'])+number(g['storageCost'])-number(g['storageRebate'])
    history=query_data(fetch,'{transactions(first:50,filter:{sentAddress:"'+OWNER+'",afterCheckpoint:'+str(HISTORY_AFTER)+',beforeCheckpoint:'+str(watermark+1)+'}){nodes{digest} pageInfo{hasNextPage}}}')['transactions']
    if history['pageInfo']['hasNextPage'] is not False:raise DiscoveryError('Unbounded recovery history; manual reconciliation required')
    senders=[n['digest'] for n in history['nodes']]
    pinned=QUERY.replace('address(address:$owner)','address(address:$owner,atCheckpoint:'+str(watermark)+')')
    remaining=discover(fetch=lambda c,t:fetch(c,t,pinned))
    proof={TX:{'status':'SUCCESS','sender':OWNER,'digest':TX,'checkpoint':CHECKPOINT,'deleted_stake':target,'net_gas_mist':fee}}
    plan=continuation_plan(state,proof,remaining,senders)
    plan.update(chain=CHAIN,owner=OWNER,recipient=RECIPIENT,verified_checkpoint=watermark,
                verified_timestamp=data['checkpoint']['timestamp'],prior_state_sha256=STATE_HASH,run_id=RUN_ID)
    return plan
