"""Offline test runner: isolated copies, synthetic config/journals, no real wallet."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
def fixture(variant):
    owner='0x'+'1'*64 if variant=='cypher' else '0x'+'4'*64
    recipient='0x'+'2'*64 if variant=='cypher' else '0x'+'5'*64
    return {'owner':owner,'recipient':recipient,'hostname':'fixture-'+variant,
            'cli_path':'/nonexistent/test-only/sui','client_config_path':'/nonexistent/test-only/client.yaml',
            'active_env':'mystenmain','rpc_url':'https://fullnode.mainnet.sui.io:443'}
def recovery_fixture():
    ids=['0x'+format(n,'064x') for n in (101,102,103,104)]
    tx='1'*31+'z';run='2000-01-01T00:00:00Z'
    state={'version':1,'status':'blocked','started_utc':run,'stake_ids':ids,
           'transactions':[{'kind':'withdraw','object_id':ids[0],'amount_mist':None,
                            'status':'confirmed_success','digest':tx,'checkpoint':1000,'net_gas_mist':1178828}],
           'current_payment':{'0x'+'6'*64:{'version':9,'digest':'1'*31+'2','balance':4_000_000_000}}}
    sha=hashlib.sha256(json.dumps(state,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    recovery={'run_id':run,'transaction_digest':tx,'checkpoint':1000,'state_sha256':sha,'history_after_checkpoint':990}
    return state,recovery

def main():
    selected=sys.argv[1:] or ['jsm','cypher']
    if any(v not in ('jsm','cypher') for v in selected):raise SystemExit('Choose jsm or cypher')
    for variant in selected:
        with tempfile.TemporaryDirectory(prefix='sui-payout-tests-') as tmp:
            target=Path(tmp)
            for folder in (ROOT/'payouts'/variant,ROOT/'tests'/variant,ROOT/'tests'/'common'):
                for f in folder.glob('*.py'):shutil.copyfile(f,target/f.name)
            (target/'deployment.local.json').write_text(json.dumps(fixture(variant)))
            if variant=='cypher':
                state,recovery=recovery_fixture()
                (target/'recovery-original-journal.json').write_text(json.dumps(state))
                (target/'recovery.local.json').write_text(json.dumps(recovery))
            print('Testing '+variant+' in isolated synthetic environment',flush=True)
            result=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',tmp,'-p','test_*.py','-v'],cwd=tmp)
            if result.returncode:return result.returncode
    return 0
if __name__=='__main__':sys.exit(main())
