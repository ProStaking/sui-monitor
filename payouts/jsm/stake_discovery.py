"""Read-only JSM discovery. No wallet libraries, credentials or transaction calls."""
import json
from deployment_config import CONFIG
import re
import time
import urllib.request

ENDPOINT = 'https://graphql.mainnet.sui.io/graphql'
OWNER = CONFIG['owner']
STAKE_TYPE = '0x' + '0' * 63 + '3::staking_pool::StakedSui'
QUERY = '''query($owner:SuiAddress!,$after:String){address(address:$owner){objects(first:50,after:$after,filter:{type:"0x3::staking_pool::StakedSui"}){nodes{address contents{type{repr}} owner{__typename ... on AddressOwner{address{address}}}} pageInfo{hasNextPage endCursor}}}}'''
MAX_BODY = 1048576
MAX_PAGES = 100
MAX_SECONDS = 120

class DiscoveryError(Exception):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DiscoveryError('GraphQL redirect refused')

def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DiscoveryError('Duplicate JSON key')
        result[key] = value
    return result

def decode_response(status, content_type, body):
    if status != 200:
        raise DiscoveryError('GraphQL HTTP status ' + str(status))
    if content_type.split(';')[0].strip().lower() not in ('application/json', 'application/graphql-response+json'):
        raise DiscoveryError('GraphQL response is not JSON content type')
    if len(body) > MAX_BODY:
        raise DiscoveryError('GraphQL body exceeds limit')
    try:
        return json.loads(body.decode('utf-8'), object_pairs_hook=_unique_keys,
                          parse_constant=lambda _: (_ for _ in ()).throw(DiscoveryError('Invalid JSON number')))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DiscoveryError('Invalid JSON body') from exc

def fetch_page(cursor, timeout):
    payload = json.dumps({'query': QUERY, 'variables': {'owner': OWNER, 'after': cursor}}).encode()
    request = urllib.request.Request(ENDPOINT, data=payload, headers={
        'Content-Type': 'application/json', 'Accept': 'application/graphql-response+json, application/json'})
    # Disable ambient proxies and redirects; standard HTTPS certificate/hostname validation remains enabled.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            return decode_response(response.status, response.headers.get('Content-Type', ''), response.read(MAX_BODY + 1))
    except DiscoveryError:
        raise
    except Exception as exc:
        # Do not emit untrusted response bodies, endpoint credentials or environment details.
        raise DiscoveryError('GraphQL transport/status failure: ' + type(exc).__name__) from exc

def discover(fetch=fetch_page, clock=time.monotonic):
    """Return IDs only after every page validates. Never return partial discovery."""
    started = clock()
    cursor = None
    cursors = set()
    ids = []
    seen = set()
    for _ in range(MAX_PAGES):
        remaining = MAX_SECONDS - (clock() - started)
        if remaining <= 0:
            raise DiscoveryError('Discovery deadline exceeded')
        try:
            page = fetch(cursor, min(20, remaining))
            if clock() - started > MAX_SECONDS:
                raise DiscoveryError('Discovery deadline exceeded')
            if not isinstance(page, dict) or ('errors' in page and page['errors'] != []):
                raise DiscoveryError('GraphQL API error or malformed envelope')
            conn = page['data']['address']['objects']
            nodes, info = conn['nodes'], conn['pageInfo']
            if not isinstance(nodes, list) or len(nodes) > 50 or not isinstance(info, dict):
                raise DiscoveryError('Malformed connection')
            more, next_cursor = info['hasNextPage'], info['endCursor']
            if type(more) is not bool or (next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 8192)):
                raise DiscoveryError('Malformed pageInfo')
            for node in nodes:
                oid = node['address']
                if not isinstance(oid, str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', oid):
                    raise DiscoveryError('Invalid object ID')
                oid = oid.lower()
                if node['contents']['type']['repr'] not in (STAKE_TYPE, '0x3::staking_pool::StakedSui'):
                    raise DiscoveryError('Unexpected object type')
                owner = node['owner']
                if owner['__typename'] != 'AddressOwner' or owner['address']['address'] != OWNER:
                    raise DiscoveryError('Unexpected object owner')
                if oid in seen:
                    raise DiscoveryError('Duplicate object ID')
                seen.add(oid)
                ids.append(oid)
            if more:
                if not nodes or not next_cursor or next_cursor in cursors:
                    raise DiscoveryError('Pagination did not advance')
                cursors.add(next_cursor)
                cursor = next_cursor
            else:
                return ids
        except DiscoveryError:
            raise
        except Exception as exc:
            raise DiscoveryError('Malformed page or failed page fetch: ' + type(exc).__name__) from exc
    raise DiscoveryError('Discovery page limit exceeded')

if __name__ == '__main__':
    import sys
    try:
        print(json.dumps({'owner': OWNER, 'endpoint': ENDPOINT, 'object_ids': discover()}))
    except DiscoveryError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
