import copy
import unittest
from unittest.mock import Mock,patch
import wallet_snapshot as w
import staketasjson as p
from stake_discovery import DiscoveryError,OWNER
from recovery_review import continuation_plan
from test_snapshot import page,node,A,B
class Clock:
    def __init__(self):self.t=0
    def now(self):return self.t
    def sleep(self,n):self.t+=n
class WaitTests(unittest.TestCase):
    def test_catchup_requeries_reads_only(self):
        c=Clock();read=Mock(side_effect=[w.IndexLag(8,10),w.IndexLag(9,10),{}])
        self.assertEqual(w.wait_snapshot(min_checkpoint=10,reader=read,clock=c.now,sleep=c.sleep),{})
        self.assertEqual(read.call_count,3);self.assertEqual(c.t,4)
    def test_timeout_bounded(self):
        c=Clock();read=Mock(side_effect=w.IndexLag(8,10))
        with self.assertRaises(DiscoveryError):w.wait_snapshot(min_checkpoint=10,reader=read,clock=c.now,sleep=c.sleep,max_seconds=5)
        self.assertEqual(c.t,5);self.assertEqual(read.call_count,3)
    def test_regressing_backend_never_lowers_requirement(self):
        c=Clock();read=Mock(side_effect=[w.IndexLag(9,10),w.IndexLag(7,10),{}])
        w.wait_snapshot(min_checkpoint=10,reader=read,clock=c.now,sleep=c.sleep)
        self.assertTrue(all(x.kwargs['min_checkpoint']==10 for x in read.call_args_list))
    def test_transport_schema_error_not_retried(self):
        for exc in [DiscoveryError('schema'),TimeoutError('transport')]:
            read=Mock(side_effect=exc)
            with self.assertRaises(type(exc)):w.wait_snapshot(reader=read)
            self.assertEqual(read.call_count,1)
    def test_late_success_not_accepted(self):
        c=Clock()
        def read(**kw):c.t=6;return {}
        with self.assertRaises(DiscoveryError):w.wait_snapshot(reader=read,clock=c.now,max_seconds=5)
    def test_pagination_checkpoint_change_rejected(self):
        pages=iter([page([node(A)],20,True,'a',20),page([node(B)],20,False,None,21)])
        with self.assertRaises(DiscoveryError):w.snapshot(lambda c,t:next(pages))
    def test_pin_applies_to_first_and_subsequent_pages(self):
        f=Mock(side_effect=[page(),page([node(A)],20,True,'a'),page([node(B)],20)])
        self.assertEqual(len(w.snapshot(w.PinnedPages(f))),2)
        for call in f.call_args_list[1:]:
            q=call.args[2];self.assertIn('checkpoint(sequenceNumber:20)',q);self.assertIn('atCheckpoint:20',q)
    def test_no_transaction_command_in_wait(self):
        with patch('subprocess.Popen',side_effect=AssertionError('forbidden')):
            c=Clock();w.wait_snapshot(reader=Mock(side_effect=[w.IndexLag(0,1),{}]),min_checkpoint=1,clock=c.now,sleep=c.sleep)
    def test_main_error_nonzero_stderr(self):
        with patch.object(p.socket,'gethostname',return_value='fixture-cypher'),patch.object(p,'payout_session',side_effect=DiscoveryError('index timeout')):
            self.assertEqual(p.main([]),1)
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.r={'kind':'withdraw','status':'confirmed_success','object_id':A,'digest':'tx','checkpoint':20,'net_gas_mist':10}
        self.state={'version':1,'status':'blocked','stake_ids':[A,B],'transactions':[self.r]}
        self.proof={'tx':{'status':'SUCCESS','sender':OWNER,'digest':'tx','checkpoint':20,'deleted_stake':A,'net_gas_mist':10}}
    def test_partial_confirmed_plan_preserves_journal_and_skips_done(self):
        original=copy.deepcopy(self.state)
        plan=continuation_plan(self.state,self.proof,[B],['tx'])
        self.assertEqual(plan['remaining_stake_ids'],[B]);self.assertEqual(plan['min_checkpoint'],20)
        self.assertEqual(self.state,original);self.assertEqual(plan['preserve_transactions'],[self.r])
    def test_unknown_never_eligible(self):
        self.r['status']='unknown'
        with self.assertRaises(DiscoveryError):continuation_plan(self.state,self.proof,[B],['tx'])
    def test_missing_proof_blocks(self):
        with self.assertRaises(DiscoveryError):continuation_plan(self.state,{},[B],['tx'])
    def test_wrong_deleted_object_or_checkpoint_blocks(self):
        for key,value in [('deleted_stake',B),('checkpoint',19),('sender',A),('net_gas_mist',11)]:
            proof=copy.deepcopy(self.proof);proof['tx'][key]=value
            with self.assertRaises(DiscoveryError):continuation_plan(self.state,proof,[B],['tx'])
    def test_new_stake_or_missing_remaining_blocks(self):
        with self.assertRaises(DiscoveryError):continuation_plan(self.state,self.proof,[],['tx'])
    def test_additional_sender_transaction_blocks(self):
        with self.assertRaises(DiscoveryError):continuation_plan(self.state,self.proof,[B],['tx','other'])
    def test_transfer_not_auto_resumed(self):
        self.r['kind']='transfer'
        with self.assertRaises(DiscoveryError):continuation_plan(self.state,self.proof,[B],['tx'])

class PartialCatchupTests(unittest.TestCase):
    def test_after_confirmed_withdrawal_lag_does_not_resubmit(self):
        import tempfile
        from test_wallet import coins,gas,receipt,encoded,C
        before=coins(4_000_000_000);ready=coins(4_010_000_000)
        responses=iter([before,before,w.IndexLag(19,20),ready,ready])
        c=Clock()
        def read(**kw):
            result=next(responses)
            if isinstance(result,Exception):raise result
            return result
        def waiting(**kw):return w.wait_snapshot(reader=read,clock=c.now,sleep=c.sleep,**kw)
        runner=Mock(side_effect=[gas(before),encoded(receipt(before,stake=C)),gas(ready)])
        with tempfile.TemporaryDirectory() as td,patch('subprocess.Popen',side_effect=AssertionError('forbidden')):
            with p.payout_session(td) as journal:p.run_payout([C],journal,runner,waiting,lambda:None)
            self.assertEqual(p.Journal(td).load()['status'],'complete')
        self.assertEqual([x.args[0][0] for x in runner.call_args_list],['gas','call','gas'])
        self.assertEqual(c.t,2)
    def test_lag_timeout_after_confirmed_keeps_guard(self):
        import tempfile
        from test_wallet import coins,gas,receipt,encoded,C
        before=coins(4_000_000_000);c=Clock();n=0
        def read(**kw):
            nonlocal n
            n+=1
            if n<=2:return before
            raise w.IndexLag(19,20)
        def waiting(**kw):return w.wait_snapshot(reader=read,clock=c.now,sleep=c.sleep,max_seconds=5,**kw)
        runner=Mock(side_effect=[gas(before),encoded(receipt(before,stake=C))])
        with tempfile.TemporaryDirectory() as td,patch('subprocess.Popen',side_effect=AssertionError('forbidden')):
            with self.assertRaises(DiscoveryError):
                with p.payout_session(td) as journal:p.run_payout([C],journal,runner,waiting,lambda:None)
            state=p.Journal(td).load();self.assertEqual(state['status'],'blocked')
            self.assertEqual(state['transactions'][0]['status'],'confirmed_success')
            with self.assertRaises(p.PayoutError):
                with p.payout_session(td):pass
        self.assertEqual([x.args[0][0] for x in runner.call_args_list],['gas','call'])
