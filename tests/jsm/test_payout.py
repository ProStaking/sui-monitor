"""Transaction fixtures and process mocks ONLY. No real wallet subprocess is allowed."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import Mock, patch
import staketasjson as p

A='0x'+'a'*64
B='0x'+'b'*64
C='0x'+'c'*64

def receipt(n=1,oid=A,amount=None,status='success'):
    tx='1'*31+'123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'[n]
    return {'digest':tx,'checkpoint':'12345','effects':{'transactionDigest':tx,'status':{'status':status}},'objectChanges':[{'type':'deleted','objectId':oid}], 'balanceChanges':[] if amount is None else [{'owner':{'AddressOwner':p.RECIPIENT},'coinType':'0x2::sui::SUI','amount':str(amount)}]}
def gas(rows):return {'gasCoins':[{'gasCoinId':oid,'mistBalance':amount,'suiBalance':'display ignored'} for oid,amount in rows], 'addressMistBalance':0,'addressSuiBalance':'0'}
def result(data,code=0):return code,json.dumps(data).encode()

class PayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.guard=patch('subprocess.Popen',side_effect=AssertionError('REAL SUBPROCESS FORBIDDEN'));self.guard.start()
        self.out=redirect_stdout(io.StringIO());self.out.__enter__()
    def tearDown(self):
        self.out.__exit__(None,None,None);self.guard.stop();self.temp.cleanup()
    def run_plan(self,ids,responses):
        runner=Mock(side_effect=responses)
        with p.payout_session(self.directory) as journal:p.run_payout(ids,journal,runner)
        return runner,p.Journal(self.directory).load()
    def fail_plan(self,ids,responses):
        runner=Mock(side_effect=responses)
        with self.assertRaises((p.PayoutError,ValueError,KeyError,TypeError)):
            with p.payout_session(self.directory) as journal:p.run_payout(ids,journal,runner)
        state=p.Journal(self.directory).load()
        self.assertEqual(state['status'],'blocked')
        with self.assertRaises(p.PayoutError):
            with p.payout_session(self.directory):pass
        return runner,state
    def test_success_all_withdrawals_then_gas_then_transfer(self):
        runner,state=self.run_plan([A,B],[result(receipt(1,A)),result(receipt(2,B)),result(gas([(C,10_000_000_000)])),result(receipt(3,C,6_000_000_000)),result(gas([(C,3_999_000_000)]))])
        self.assertEqual([c.args[0][0] for c in runner.call_args_list],['call','call','gas','transfer-sui','gas'])
        self.assertEqual(state['status'],'complete');self.assertEqual(len(state['transactions']),3)
        self.assertTrue(all(r['status']=='confirmed_success' for r in state['transactions']))
        self.assertIn(str(p.GAS_BUDGET),runner.call_args_list[0].args[0]);self.assertIn(p.RECIPIENT,runner.call_args_list[3].args[0])
    def test_partial_withdrawal_failure_stops_before_gas(self):
        runner,state=self.fail_plan([A,B],[result(receipt(1,A)),result(receipt(2,B,status='failure'),1)])
        self.assertEqual(runner.call_count,2)
        self.assertEqual([r['status'] for r in state['transactions']],['confirmed_success','confirmed_failure'])
    def test_timeout_unknown_never_retried(self):
        runner,state=self.fail_plan([A],[p.PayoutError('timeout')])
        self.assertEqual(runner.call_count,1);self.assertEqual(state['transactions'][0]['status'],'unknown')
    def test_nonzero_with_success_json_is_uncertain(self):
        runner,state=self.fail_plan([A],[result(receipt(),1)])
        self.assertEqual(state['transactions'][0]['status'],'unknown');self.assertIn('digest',state['transactions'][0])
    def test_unknown_result_and_malformed_json(self):
        runner,state=self.fail_plan([A],[(0,b'not json')]);self.assertEqual(runner.call_count,1)
    def test_success_without_checkpoint_is_not_confirmation(self):
        data=receipt();data.pop('checkpoint');self.fail_plan([A],[result(data)])
    def test_wrong_withdrawal_object_stops(self):self.fail_plan([A],[result(receipt(1,B))])
    def test_wrong_effects_digest_stops(self):
        data=receipt();data['effects']['transactionDigest']='2'*44;self.fail_plan([A],[result(data)])
    def test_reused_digest_stops(self):self.fail_plan([A,B],[result(receipt(1,A)),result(receipt(1,B))])
    def test_api_errors_with_success_stops(self):
        data=receipt();data['errors']=['bad'];self.fail_plan([A],[result(data)])
    def test_malformed_gas_stops_after_confirmed_withdrawal(self):
        runner,state=self.fail_plan([A],[result(receipt()),result({'gasCoins':None})]);self.assertEqual(runner.call_count,2)
    def test_nonzero_gas_prevents_transfer(self):self.fail_plan([],[result(gas([(A,10**10)]),1)])
    def test_transfer_failure_stops_no_next_coin(self):
        runner,state=self.fail_plan([],[result(gas([(A,6*10**9),(B,8*10**9)])),result(receipt(1,A,status='failure'),1)])
        self.assertEqual(runner.call_count,2);self.assertEqual(runner.call_args_list[1].args[0][0],'transfer')
    def test_transfer_recipient_mismatch_stops(self):
        data=receipt(1,A,6*10**9);data['balanceChanges'][0]['owner']={'AddressOwner':B}
        self.fail_plan([],[result(gas([(A,10*10**9)])),result(data)])
    def test_transfer_amount_mismatch_stops(self):self.fail_plan([],[result(gas([(A,10*10**9)])),result(receipt(1,A,7*10**9))])
    def test_balance_refresh_after_transfer_failure_blocks_next_run(self):
        runner,state=self.fail_plan([],[result(gas([(A,10*10**9)])),result(receipt(1,A,6*10**9)),(1,b'')])
        self.assertEqual(state['transactions'][0]['status'],'confirmed_success');self.assertEqual(runner.call_count,3)
    def test_fragmented_coins_remain_skipped(self):
        runner,state=self.run_plan([],[result(gas([(A,3998811481),(B,3997441871)]))]);self.assertEqual(runner.call_count,1)
    def test_exact_reserve_remains_skipped(self):
        runner,state=self.run_plan([],[result(gas([(A,p.RESERVE)]))]);self.assertEqual(runner.call_count,1)
    def test_full_object_transfer_keeps_original_threshold(self):
        runner,state=self.run_plan([],[result(gas([(A,6*10**9),(B,6*10**9)])),result(receipt(1,A,6*10**9)),result(gas([(B,5999000000)])),result(receipt(2,B,1999000000)),result(gas([(B,3999000000)]))])
        self.assertEqual(runner.call_args_list[1].args[0],['transfer','--to',p.RECIPIENT,'--object-id',A])
        self.assertIn('1999000000',runner.call_args_list[3].args[0])
    def test_balance_schema_adversarial(self):
        bad=[{},None,{'gasCoins':{}},gas([(A,-1)]),gas([(A,True)]),gas([(A,1),(A,1)]),gas([('x;evil',1)]),gas([(A,2**64)]),{'gasCoins':[{'gasCoinId':A,'suiBalance':'10'}]}]
        for value in bad:
            with self.subTest(value=value),self.assertRaises((p.PayoutError,KeyError,TypeError)):p.balances(Mock(return_value=result(value)))
    def test_overlap_rejected_then_lock_reusable(self):
        with p.payout_session(self.directory):
            with self.assertRaises(p.PayoutError):
                with p.payout_session(self.directory):pass
        with p.payout_session(self.directory):pass
    def test_pending_persisted_before_runner(self):
        def runner(args):
            state=p.Journal(self.directory).load()
            self.assertEqual(state['status'],'running');self.assertEqual(state['transactions'][-1]['status'],'pending')
            raise p.PayoutError('unknown')
        with self.assertRaises(p.PayoutError):
            with p.payout_session(self.directory) as journal:p.run_payout([A],journal,runner)
    def test_incomplete_journal_blocks_new_execution(self):
        j=p.Journal(self.directory);j.begin([A]);j.pending('withdraw',A,None)
        with self.assertRaises(p.PayoutError):
            with p.payout_session(self.directory):self.fail('must not enter')
    def test_symlink_lock_refused(self):
        (self.directory/'.jsm-payout.lock').symlink_to('/dev/null')
        with self.assertRaises(OSError):
            with p.payout_session(self.directory):pass
    def test_symlink_state_refused(self):
        (self.directory/'.jsm-payout-state.json').symlink_to('/dev/null')
        with self.assertRaises(OSError):
            with p.payout_session(self.directory):pass
    def test_world_writable_state_refused(self):
        path=self.directory/'.jsm-payout-state.json';path.write_text('{}');path.chmod(0o666)
        with self.assertRaises(p.PayoutError):
            with p.payout_session(self.directory):pass
    def test_failure_propagates_nonzero_main(self):
        with patch.object(p.socket,'gethostname',return_value='fixture-jsm'),patch.object(p,'payout_session',return_value=p.payout_session(self.directory)),patch.object(p,'discover',return_value=[]),patch.object(p,'run_payout',side_effect=p.PayoutError('bad')),redirect_stderr(io.StringIO()):
            self.assertEqual(p.main([]),1)
    def test_duplicate_json_and_nonnumeric_values(self):
        for raw in (b'{"x":1,"x":2}',b'NaN',b'\xff'):
            with self.assertRaises(p.PayoutError):p.parse_json(raw)
    def test_journal_write_failure_before_submission(self):
        runner=Mock()
        with p.payout_session(self.directory) as journal:
            with patch.object(journal,'save',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):p.run_payout([A],journal,runner)
        runner.assert_not_called()
    def test_interrupt_preserves_unknown_and_blocks_restart(self):
        with self.assertRaises(KeyboardInterrupt):
            with p.payout_session(self.directory) as journal:p.run_payout([A],journal,Mock(side_effect=KeyboardInterrupt))
        state=p.Journal(self.directory).load()
        self.assertEqual(state['status'],'blocked');self.assertEqual(state['transactions'][0]['status'],'unknown')
        with self.assertRaises(p.PayoutError):
            with p.payout_session(self.directory):pass
    def test_completed_run_allows_next_run(self):
        self.run_plan([],[result(gas([]))])
        runner,state=self.run_plan([],[result(gas([]))])
        self.assertEqual(state['status'],'complete')
    def test_runner_success_uses_fixed_argv_no_shell(self):
        proc=Mock();proc.wait.return_value=0;proc.poll.return_value=0
        def launch(argv,**kw):kw['stdout'].write(b'{}');return proc
        with patch('subprocess.Popen',side_effect=launch) as popen:
            self.assertEqual(p.run_cli(['gas']),(0,b'{}'))
            self.assertEqual(popen.call_args.args[0],[p.CLI,'client','gas','--json'])
            self.assertNotIn('shell',popen.call_args.kwargs)
    def test_runner_timeout_kills_local_process_only(self):
        proc=Mock(pid=123456);proc.poll.return_value=None;proc.wait.return_value=0
        with patch('subprocess.Popen',return_value=proc),patch.object(p.time,'monotonic',side_effect=[0,121]),patch.object(p.os,'killpg') as kill:
            with self.assertRaises(p.PayoutError):p.run_cli(['gas'])
            kill.assert_called_once_with(123456,p.signal.SIGKILL)
    def test_output_cap_kills_process(self):
        proc=Mock(pid=123456);proc.poll.return_value=None
        def launch(argv,**kw):kw['stdout'].write(b'x'*100);kw['stdout'].flush();return proc
        with patch('subprocess.Popen',side_effect=launch),patch.object(p,'MAX_OUTPUT',50),patch.object(p.os,'killpg') as kill:
            with self.assertRaises(p.PayoutError):p.run_cli(['gas'])
            kill.assert_called_once()
if __name__=='__main__':unittest.main(verbosity=2)
