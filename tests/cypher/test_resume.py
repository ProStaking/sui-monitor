import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import staketasjson as p
import resume_guard as r
from stake_discovery import OWNER,DiscoveryError
from test_wallet import coins,gas,receipt,encoded
FIXTURE=Path(__file__).with_name('recovery-original-journal.json')
def state():return json.loads(FIXTURE.read_text())
def responses():
    old=state();target=old['stake_ids'][0]
    proof={'data':{'chainIdentifier':r.CHAIN,'checkpoint':{'sequenceNumber':r.CHECKPOINT+50,'timestamp':'2000-01-01T00:01:00Z'},'transaction':{'digest':r.TX,'sender':{'address':OWNER},'effects':{'status':'SUCCESS','checkpoint':{'sequenceNumber':r.CHECKPOINT},'effectsJson':{'status':{'success':True},'transactionDigest':r.TX,'changedObjects':[{'objectId':target,'idOperation':'DELETED','outputState':'OUTPUT_OBJECT_STATE_DOES_NOT_EXIST','inputOwner':{'kind':'ADDRESS','address':OWNER}}],'gasUsed':{'computationCost':100000,'storageCost':909264000,'storageRebate':908185172}}}}}}
    history={'data':{'transactions':{'nodes':[{'digest':r.TX}],'pageInfo':{'hasNextPage':False}}}}
    stakes={'data':{'address':{'objects':{'nodes':[{'address':oid,'contents':{'type':{'repr':'0x3::staking_pool::StakedSui'}},'owner':{'__typename':'AddressOwner','address':{'address':OWNER}}} for oid in old['stake_ids'][1:]],'pageInfo':{'hasNextPage':False,'endCursor':None}}}}}
    return [proof,history,stakes]
def plan():return r.verify(state(),r.RUN_ID,r.RECIPIENT,Mock(side_effect=responses()),Mock(return_value={}))
def write_state(td):Path(td,'.cypher-payout-state.json').write_text(json.dumps(state()));Path(td,'.cypher-payout-state.json').chmod(0o600)
class ProofTests(unittest.TestCase):
    def test_exact_pinned_run(self):
        result=plan();self.assertEqual(result['remaining_stake_ids'],state()['stake_ids'][1:]);self.assertEqual(result['skip_confirmed_digests'],[r.TX])
    def test_journal_any_change_refused_before_reads(self):
        for key,value in [('status','running'),('started_utc','other'),('transactions',[]),('unexpected','intent')]:
            old=state();old[key]=value;fetch=Mock()
            with self.assertRaises(DiscoveryError):r.verify(old,r.RUN_ID,r.RECIPIENT,fetch,Mock())
            fetch.assert_not_called()
    def test_wrong_run_or_recipient_refused(self):
        for run,dest in [('other',r.RECIPIENT),(r.RUN_ID,OWNER)]:
            with self.assertRaises(DiscoveryError):r.verify(state(),run,dest,Mock(),Mock())
    def reject(self,res):
        with self.assertRaises((DiscoveryError,KeyError,TypeError)):r.verify(state(),r.RUN_ID,r.RECIPIENT,Mock(side_effect=res),Mock())
    def test_chain_sender_success_checkpoint_digest_checks(self):
        for field,value in [('chainIdentifier','wrong'),('checkpoint',{'sequenceNumber':1})]:
            res=responses();res[0]['data'][field]=value;self.reject(res)
        for field,value in [('digest','wrong'),('sender',{'address':r.RECIPIENT})]:
            res=responses();res[0]['data']['transaction'][field]=value;self.reject(res)
        for field,value in [('status','FAILURE'),('checkpoint',{'sequenceNumber':r.CHECKPOINT-1})]:
            res=responses();res[0]['data']['transaction']['effects'][field]=value;self.reject(res)
    def test_deleted_stake_proof_checks(self):
        for field,value in [('idOperation','NONE'),('inputOwner',{'kind':'ADDRESS','address':r.RECIPIENT}),('outputState','EXISTS')]:
            res=responses();res[0]['data']['transaction']['effects']['effectsJson']['changedObjects'][0][field]=value;self.reject(res)
    def test_extra_or_paginated_history_refused(self):
        res=responses();res[1]['data']['transactions']['nodes'].append({'digest':'unrecorded'});self.reject(res)
        res=responses();res[1]['data']['transactions']['pageInfo']['hasNextPage']=True;self.reject(res)
    def test_remaining_owner_mismatch(self):
        res=responses();res[2]['data']['address']['objects']['nodes'][0]['owner']['address']['address']=r.RECIPIENT;self.reject(res)
    def test_errors_partial_data_refused(self):
        res=responses();res[0]['errors']=[{'message':'partial'}];self.reject(res)
    def test_endpoint_validation(self):
        r.validate_endpoint('envs:\n  - alias: mystenmain\n    rpc: "https://fullnode.mainnet.sui.io:443"\n')
        for text in ['','envs:\n  - alias: mystenmain\n    rpc: "https://evil.example"\n','envs:\n - alias: mystenmain\n - alias: mystenmain\n']:
            with self.assertRaises(DiscoveryError):r.validate_endpoint(text)
    def test_wait_timeout_does_not_get_proof_or_submit(self):
        fetch=Mock()
        with self.assertRaises(DiscoveryError):r.verify(state(),r.RUN_ID,r.RECIPIENT,fetch,Mock(side_effect=DiscoveryError('timeout')))
        fetch.assert_not_called()
class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.td=self.tmp.name;write_state(self.td)
        self.guard=patch('subprocess.Popen',side_effect=AssertionError('REAL WALLET FORBIDDEN'));self.guard.start()
    def tearDown(self):self.guard.stop();self.tmp.cleanup()
    def test_default_stays_blocked(self):
        with self.assertRaises(p.PayoutError):
            with p.payout_session(self.td):pass
        self.assertEqual(p.Journal(self.td).load(),state())
    def test_archive_and_original_record_preserved(self):
        pl=plan()
        with p.payout_session(self.td,recovery=True) as j:j.resume(pl)
        data=p.Journal(self.td).load()
        self.assertEqual(data['transactions'],state()['transactions']);self.assertEqual(data['started_utc'],r.RUN_ID)
        archive=Path(self.td,data['recovery']['archive']);self.assertEqual(archive.stat().st_mode&0o777,0o600)
        self.assertEqual(json.loads(archive.read_text())['previous_state'],state())
    def test_existing_archive_blocks_without_overwrite(self):
        pl=plan();a=Path(self.td,'.cypher-recovery-'+r.STATE_HASH+'.json');a.write_text('preserve');a.chmod(0o600)
        with self.assertRaises(FileExistsError):
            with p.payout_session(self.td,recovery=True) as j:j.resume(pl)
        self.assertEqual(a.read_text(),'preserve');self.assertEqual(p.Journal(self.td).load(),state())
    def test_overlap_prevents_recovery(self):
        with p.payout_session(self.td,recovery=True):
            with self.assertRaises(p.PayoutError):
                with p.payout_session(self.td,recovery=True):pass
    def test_transition_failure_sends_nothing(self):
        with self.assertRaises(OSError):
            with p.payout_session(self.td,recovery=True) as j:
                with patch.object(j,'save',side_effect=OSError('disk')):j.resume(plan())
        self.assertEqual(p.Journal(self.td).load()['status'],'blocked')
    def test_resume_only_remaining_then_recomputed_payout(self):
        pl=plan();before=coins(8_000_000_000);after=coins(4_019_999_000)
        reads=[before]*8+[after]*2;results=[]
        for i,oid in enumerate(pl['remaining_stake_ids'],1):
            d=receipt(before,n=i,stake=oid);d['checkpoint']=str(r.CHECKPOINT+i)
            results.extend([gas(before),encoded(d)])
        d=receipt(before,n=4,amount=3_980_000_000);d['checkpoint']=str(r.CHECKPOINT+4)
        results.extend([gas(before),encoded(d),gas(after)])
        runner=Mock(side_effect=results)
        with p.payout_session(self.td,recovery=True) as j:
            j.resume(pl);p.run_payout(pl['remaining_stake_ids'],j,runner,Mock(side_effect=reads),lambda:None,resume_plan=pl)
        final=p.Journal(self.td).load();self.assertEqual(final['status'],'complete')
        self.assertEqual(final['transactions'][0],state()['transactions'][0]);self.assertEqual(len(final['transactions']),5)
        calls=[x.args[0] for x in runner.call_args_list]
        self.assertEqual([x[0] for x in calls].count('call'),3);self.assertEqual([x[0] for x in calls].count('ptb'),1)
        self.assertFalse(any(state()['stake_ids'][0] in x for x in calls))
        self.assertEqual(final['wallet_plan']['amount_mist'],3_980_000_000)
        completed=Path(self.td,final['recovery']['archive']+'.complete')
        self.assertEqual(json.loads(completed.read_text()),final)
        with p.payout_session(self.td) as j:j.begin([])
        self.assertTrue(completed.exists())
    def test_uncertain_new_withdrawal_blocks_any_second_resume(self):
        pl=plan();before=coins(8_000_000_000)
        with self.assertRaises(p.PayoutError):
            with p.payout_session(self.td,recovery=True) as j:
                j.resume(pl);p.run_payout(pl['remaining_stake_ids'],j,Mock(side_effect=[gas(before),p.PayoutError('unknown')]),Mock(return_value=before),lambda:None,resume_plan=pl)
        final=p.Journal(self.td).load();self.assertEqual(final['status'],'blocked');self.assertEqual(final['transactions'][-1]['status'],'unknown')
        with self.assertRaises(DiscoveryError):r.validate_state(final,r.RUN_ID,r.RECIPIENT)

class EntryTests(unittest.TestCase):
    def test_read_only_recovery_check_never_transitions_or_calls_wallet(self):
        from contextlib import contextmanager
        j=Mock();j.load.return_value=state()
        @contextmanager
        def session(**kw):
            self.assertTrue(kw['recovery']);yield j
        with patch.object(p.socket,'gethostname',return_value='fixture-cypher'),patch.object(p,'validate_identity'),patch.object(r,'validate_endpoint'),patch.object(p,'payout_session',session),patch.object(r,'verify',return_value=plan()),patch.object(p,'run_payout') as payout:
            self.assertEqual(p.main(['--check-recovery',r.RUN_ID,'--confirm-recipient',r.RECIPIENT]),0)
            payout.assert_not_called();j.resume.assert_not_called();j.save.assert_not_called()
    def test_explicit_resume_verifies_before_transition_and_workflow(self):
        from contextlib import contextmanager
        calls=[];j=Mock();j.load.return_value=state();pl=plan()
        j.resume.side_effect=lambda _:calls.append('transition')
        @contextmanager
        def session(**kw):yield j
        def verify(*args):calls.append('verify');return pl
        def payout(*args,**kw):calls.append('payout');self.assertEqual(kw['resume_plan'],pl)
        with patch.object(p.socket,'gethostname',return_value='fixture-cypher'),patch.object(p,'validate_identity'),patch.object(r,'validate_endpoint'),patch.object(p,'payout_session',session),patch.object(r,'verify',side_effect=verify),patch.object(p,'run_payout',side_effect=payout):
            self.assertEqual(p.main(['--resume-confirmed-run',r.RUN_ID,'--confirm-recipient',r.RECIPIENT]),0)
        self.assertEqual(calls,['verify','transition','payout'])
    def test_mutually_exclusive_discovery_and_resume(self):
        with self.assertRaises(SystemExit):p.main(['--discover-only','--resume-confirmed-run',r.RUN_ID])
