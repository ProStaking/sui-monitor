"""Public deployment settings only. Missing/placeholder config fails before wallet use."""
import json
import re
from pathlib import Path

def unique(pairs):
    out={}
    for key,value in pairs:
        if key in out:raise ValueError('Duplicate configuration field')
        out[key]=value
    return out

def read_config(name,optional=False):
    path=Path(__file__).with_name(name)
    if optional and not path.exists():return None
    if path.is_symlink():raise ValueError('Configuration symlink refused')
    raw=path.read_bytes()
    if len(raw)>16384:raise ValueError('Configuration exceeds limit')
    data=json.loads(raw,object_pairs_hook=unique)
    if not isinstance(data,dict):raise ValueError('Configuration must be an object')
    return data

def validate(data):
    required={'owner','recipient','hostname','cli_path','client_config_path','active_env','rpc_url'}
    if set(data)!=required:raise ValueError('Unexpected or missing deployment settings')
    for key in ('owner','recipient'):
        if not isinstance(data[key],str) or not re.fullmatch(r'0x[0-9a-f]{64}',data[key]):
            raise ValueError('Provide a full lowercase public '+key+' address')
    if data['owner']==data['recipient']:raise ValueError('Self-payment requires a separate review')
    for key in ('hostname','active_env'):
        if not isinstance(data[key],str) or not re.fullmatch(r'[A-Za-z0-9._-]+',data[key]) or data[key].startswith('REPLACE'):
            raise ValueError('Invalid '+key)
    for key in ('cli_path','client_config_path'):
        if not isinstance(data[key],str) or not Path(data[key]).is_absolute() or '\x00' in data[key]:
            raise ValueError('Expected an absolute '+key)
    if data['rpc_url']!='https://fullnode.mainnet.sui.io:443':
        raise ValueError('Only the reviewed mainnet CLI endpoint is supported')
    return data

CONFIG=validate(read_config('deployment.local.json'))
