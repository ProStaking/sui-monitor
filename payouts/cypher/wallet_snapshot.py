"""Read-only paginated Cypher SUI-coin snapshot; no wallet/transaction commands."""
import re
import time
import sys
import json
from stake_discovery import fetch_page, DiscoveryError, OWNER

MAX_PAGES = 100
MAX_SECONDS = 120
COIN_TYPE = '0x2::coin::Coin<0x2::sui::SUI>'
CANONICAL_TYPE = '0x'+'0'*63+'2::coin::Coin<0x'+'0'*63+'2::sui::SUI>'
QUERY = '''query($owner:SuiAddress!,$after:String){checkpoint{sequenceNumber} address(address:$owner){balance(coinType:"0x2::sui::SUI"){coinBalance addressBalance totalBalance} objects(first:50,after:$after,filter:{type:"0x2::coin::Coin<0x2::sui::SUI>"}){nodes{address version digest owner{__typename ... on AddressOwner{address{address}}} contents{type{repr} json}} pageInfo{hasNextPage endCursor}}}}'''

def number(value):
    if isinstance(value,bool) or not isinstance(value,(str,int)) or not re.fullmatch(r'[0-9]+',str(value)) or int(value)>2**64-1:
        raise DiscoveryError('Invalid wallet integer')
    return int(value)

class CoinSnapshot(dict):
    """Only actual coin objects are mapping entries; accumulator is reporting metadata."""
    def __init__(self, coins, address_balance_mist=0, checkpoint=0):
        super().__init__(coins)
        self.address_balance_mist=number(address_balance_mist)
        self.checkpoint=number(checkpoint)

def address_balance(coins):
    return number(getattr(coins,'address_balance_mist',0))

class IndexLag(DiscoveryError):
    def __init__(self, observed, required):
        self.observed, self.required = observed, required
        super().__init__(f'Wallet index checkpoint {observed} is behind required {required}')

class PinnedPages:
    """Pin aggregate balances and all object pages to one explicit checkpoint."""
    def __init__(self, fetch=fetch_page):
        self.fetch = fetch
        self.checkpoint = None
    def __call__(self, cursor, timeout):
        if self.checkpoint is None:
            # First page selects its own query watermark; rebuild subsequent pages
            # explicitly at that same checkpoint, rather than combining new balances
            # with an older cursor-pinned object page.
            result = self.fetch(cursor, timeout, QUERY)
            if not isinstance(result, dict) or result.get('errors'):
                raise DiscoveryError('Initial wallet page failed')
            self.checkpoint = number(result['data']['checkpoint']['sequenceNumber'])
            # Re-query even page one at the explicit checkpoint. Its unpinned
            # contents are never used as evidence for submission.
        query = QUERY.replace('checkpoint{', 'checkpoint(sequenceNumber:'+str(self.checkpoint)+'){').replace(
            'address(address:$owner)', 'address(address:$owner,atCheckpoint:'+str(self.checkpoint)+')')
        return self.fetch(cursor, timeout, query)

def snapshot(fetch=None, clock=time.monotonic, min_checkpoint=0, max_seconds=MAX_SECONDS):
    fetch = fetch or PinnedPages()
    start=clock();cursor=None;cursors=set();coins={};totals=None;page_checkpoint=None
    try:
        for _ in range(MAX_PAGES):
            left=max_seconds-(clock()-start)
            if left<=0:raise DiscoveryError('Wallet snapshot deadline')
            page=fetch(cursor,min(20,left))
            if clock()-start>max_seconds:raise DiscoveryError('Wallet snapshot deadline')
            if not isinstance(page,dict) or ('errors' in page and page['errors']!=[]):raise DiscoveryError('Wallet GraphQL error')
            observed=number(page['data']['checkpoint']['sequenceNumber'])
            if observed<min_checkpoint:raise IndexLag(observed,min_checkpoint)
            if page_checkpoint is not None and page_checkpoint!=observed:raise DiscoveryError('Wallet pagination checkpoint changed')
            page_checkpoint=observed
            address=page['data']['address'];b=address['balance']
            current=tuple(number(b[k]) for k in ('coinBalance','addressBalance','totalBalance'))
            if current[0]+current[1]!=current[2]:raise DiscoveryError('Inconsistent wallet totals')
            if totals is not None and totals!=current:raise DiscoveryError('Wallet changed between pages')
            totals=current;conn=address['objects'];nodes=conn['nodes'];info=conn['pageInfo']
            if not isinstance(nodes,list) or len(nodes)>50:raise DiscoveryError('Invalid wallet page')
            more,end=info['hasNextPage'],info['endCursor']
            if type(more) is not bool or (end is not None and (not isinstance(end,str) or not end or len(end)>8192)):raise DiscoveryError('Invalid wallet cursor')
            for n in nodes:
                oid=n['address'];dig=n['digest']
                if not isinstance(oid,str) or not re.fullmatch('0x[0-9a-f]{64}',oid) or oid in coins:raise DiscoveryError('Invalid/duplicate coin ID')
                if not isinstance(dig,str) or not re.fullmatch('[1-9A-HJ-NP-Za-km-z]{32,44}',dig):raise DiscoveryError('Invalid coin digest')
                alphabet='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
                decoded=0
                for char in dig:decoded=decoded*58+alphabet.index(char)
                if (decoded.bit_length()+7)//8+len(dig)-len(dig.lstrip('1'))!=32:raise DiscoveryError('Invalid coin digest length')
                if n['owner']!={'__typename':'AddressOwner','address':{'address':OWNER}}:raise DiscoveryError('Wrong coin owner')
                if n['contents']['type']['repr'] not in (COIN_TYPE,CANONICAL_TYPE):raise DiscoveryError('Wrong coin type')
                content=n['contents']['json']
                if content['id']!=oid:raise DiscoveryError('Coin content ID mismatch')
                coins[oid]={'version':number(n['version']),'digest':dig,'balance':number(content['balance'])}
            if not more:
                if sum(v['balance'] for v in coins.values())!=totals[0]:raise DiscoveryError('Coin pagination does not reconcile with wallet total')
                return CoinSnapshot(coins,totals[1],page_checkpoint)
            if not nodes or not end or end in cursors:raise DiscoveryError('Wallet pagination did not advance')
            cursors.add(end);cursor=end
    except DiscoveryError:raise
    except Exception as exc:raise DiscoveryError('Malformed/failed wallet snapshot: '+type(exc).__name__) from exc
    raise DiscoveryError('Wallet page limit')


def wait_snapshot(*, min_checkpoint=0, reader=snapshot, clock=time.monotonic,
                  sleep=time.sleep, max_seconds=120, interval=2):
    """Retry ONLY read-only lagged snapshots. Never wraps CLI or transaction logic."""
    required=number(min_checkpoint);highest=0;start=clock();attempts=0
    while True:
        remaining=max_seconds-(clock()-start)
        if remaining<=0:
            raise DiscoveryError(f'Index wait expired; required={required}, highest_observed={highest}; no transaction retry')
        attempts+=1
        try:
            result=reader(min_checkpoint=max(required,highest),max_seconds=remaining)
            if clock()-start>=max_seconds:raise DiscoveryError('Index wait deadline exceeded; no submission')
            return result
        except IndexLag as exc:
            highest=max(highest,exc.observed)
            print(json.dumps({'event':'waiting_for_index','required_checkpoint':required,
                              'observed_checkpoint':exc.observed,'highest_observed':highest,
                              'attempt':attempts}),file=sys.stderr,flush=True)
            remaining=max_seconds-(clock()-start)
            if remaining<=0:continue
            sleep(min(interval,remaining))
