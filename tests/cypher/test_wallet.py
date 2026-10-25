import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout
import staketasjson as p
import wallet_snapshot as w
from stake_discovery import DiscoveryError,OWNER
A='0x'+'a'*64;B='0x'+'b'*64;C='0x'+'c'*64
D='1'*31+'2'
def coins(*amounts):return {oid:{'balance':n,'version':9,'digest':D} for oid,n in zip([A,B,C],amounts)}
def gas(c):return (0,json.dumps({'gasCoins':[{'gasCoinId':k,'mistBalance':v['balance']} for k,v in c.items()]}).encode())
def receipt(c,n=1,amount=None,stake=C,fee=1000,status='success'):
    dig='1'*31+'123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'[n]
    data={'digest':dig,'checkpoint':'20','effects':{'transactionDigest':dig,'status':{'status':status},'gasUsed':{'computationCost':str(max(fee,0)),'storageCost':'0','storageRebate':str(max(-fee,0))}},'objectChanges':[{'type':'deleted','objectId':stake}],'balanceChanges':[] if amount is None else [{'owner':{'AddressOwner':p.RECIPIENT},'coinType':'0x2::sui::SUI','amount':str(amount)}], 'transaction':{'data':{'sender':OWNER,'gasData':{'owner':OWNER,'budget':str(p.GAS_BUDGET),'payment':[{'objectId':k,'version':str(v['version']),'digest':v['digest']} for k,v in c.items()]}}}}
    return data

def encoded(d,rc=0):return rc,json.dumps(d).encode()
class WalletTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.directory=Path(self.tmp.name)
        self.guard=patch('subprocess.Popen',side_effect=AssertionError('REAL WALLET EXECUTION FORBIDDEN'));self.guard.start()
        self.output=redirect_stdout(io.StringIO());self.output.__enter__()
    def tearDown(self):self.output.__exit__(None,None,None);self.guard.stop();self.tmp.cleanup()
    def plan(self,ids,states,results):
        reader=Mock(side_effect=states);runner=Mock(side_effect=results)
        with p.payout_session(self.directory) as j:p.run_payout(ids,j,runner,reader,lambda:None)
        return runner,reader,p.Journal(self.directory).load()
    def rejected(self,ids,states,results):
        reader=Mock(side_effect=states);runner=Mock(side_effect=results)
        with self.assertRaises((p.PayoutError,DiscoveryError,KeyError,TypeError)):
            with p.payout_session(self.directory) as j:p.run_payout(ids,j,runner,reader,lambda:None)
        state=p.Journal(self.directory).load();self.assertEqual(state['status'],'blocked')
        with self.assertRaises(p.PayoutError):
            with p.payout_session(self.directory):pass
        return runner,state
    def test_fragmented_wallet_one_ptb_and_reserve(self):
        before=coins(3998811481,3997441871);amount=3976253352;after=coins(4019999000)
        runner,reader,state=self.plan([], [before,before,after,after],[gas(before),encoded(receipt(before,amount=amount)),gas(after)])
        args=runner.call_args_list[1].args[0]
        self.assertEqual([x.args[0][0] for x in runner.call_args_list],['gas','ptb','gas'])
        self.assertIn('--gas-coin',args);self.assertNotIn('--gas-coins',args)
        self.assertIn('@'+A,args);self.assertIn('@'+B,args);self.assertIn('[3976253352]',args)
        self.assertIn('[payout.0]',args);self.assertEqual(args[-1],'@'+p.RECIPIENT)
        self.assertEqual(state['status'],'complete');self.assertEqual(state['final_coin_balance_mist'],4019999000)
    def test_worst_budget_preserves_exact_four(self):
        before=coins(8*10**9);after=coins(p.RESERVE);amount=3980000000
        _,_,s=self.plan([],[before,before,after,after],[gas(before),encoded(receipt(before,amount=amount,fee=p.GAS_BUDGET)),gas(after)])
        self.assertEqual(s['final_coin_balance_mist'],p.RESERVE)
    def test_storage_rebate_can_raise_remainder(self):
        before=coins(8*10**9);after=coins(4020001000)
        self.plan([],[before,before,after,after],[gas(before),encoded(receipt(before,amount=3980000000,fee=-1000)),gas(after)])
    def test_below_reserve_allowance_no_transfer(self):
        for balance in (0,3999999999,4000000000,4019999999,4020000000):
            with self.subTest(balance=balance):
                before=coins(balance) if balance else {}
                r,_,s=self.plan([],[before,before],[gas(before)])
                self.assertEqual(r.call_count,1);self.assertEqual(s['wallet_plan']['amount_mist'],0)
    def test_integer_boundary_one_mist(self):self.assertEqual(p.wallet_plan(coins(4020000001))['amount_mist'],1)
    def test_one_withdrawal_confirmed_before_transfer(self):
        before=coins(4*10**9);ready=coins(12*10**9);after=coins(4019999000)
        r,reader,s=self.plan([C],[before,before,ready,ready,after,after],[gas(before),encoded(receipt(before,stake=C)),gas(ready),encoded(receipt(ready,n=2,amount=7980000000)),gas(after)])
        self.assertEqual([x.args[0][0] for x in r.call_args_list],['gas','call','gas','ptb','gas'])
        self.assertEqual(reader.call_args_list[2].kwargs['min_checkpoint'],20)
    def test_insufficient_gas_never_submits(self):
        before=coins(10_000_000);r,s=self.rejected([C],[before,before],[gas(before)])
        self.assertEqual(r.call_count,1);self.assertEqual(s['transactions'],[])
    def test_withdrawal_failed_stops(self):
        before=coins(4*10**9);r,s=self.rejected([C],[before,before],[gas(before),encoded(receipt(before,status='failure'),1)])
        self.assertEqual(r.call_count,2);self.assertEqual(s['transactions'][0]['status'],'confirmed_failure')
    def test_partial_withdrawal_unknown_stops(self):
        before=coins(4*10**9);ready=coins(5*10**9)
        r,s=self.rejected([B,C],[before,before,ready,ready],[gas(before),encoded(receipt(before,stake=B)),gas(ready),p.PayoutError('timeout')])
        self.assertEqual([x['status'] for x in s['transactions']],['confirmed_success','unknown'])
        self.assertEqual(r.call_count,4)
    def test_transfer_timeout_blocks_future_runs(self):
        before=coins(8*10**9);r,s=self.rejected([],[before,before],[gas(before),p.PayoutError('timeout')]);self.assertEqual(r.call_count,2)
    def test_changed_version_stops_before_submission(self):
        before=coins(8*10**9);after=copy.deepcopy(before);after[A]['version']+=1
        r,s=self.rejected([],[before,after],[gas(before)]);self.assertEqual(r.call_count,1)
    def test_changed_balance_stops_before_submission(self):
        before=coins(8*10**9);r,s=self.rejected([],[before,before],[gas(coins(9*10**9))]);self.assertEqual(r.call_count,1)
    def test_changed_payment_ref_in_receipt_is_unknown(self):
        before=coins(8*10**9);bad=receipt(before,amount=3980000000);bad['transaction']['data']['gasData']['payment'][0]['version']='10'
        r,s=self.rejected([],[before,before],[gas(before),encoded(bad)]);self.assertEqual(s['transactions'][0]['status'],'unknown')
    def test_wrong_sender_or_recipient(self):
        for field in ('sender','recipient'):
            with tempfile.TemporaryDirectory() as td:
                self.directory=Path(td);before=coins(8*10**9);bad=receipt(before,amount=3980000000)
                if field=='sender':bad['transaction']['data']['sender']=A
                else:bad['balanceChanges'][0]['owner']['AddressOwner']=A
                self.rejected([],[before,before],[gas(before),encoded(bad)])
    def test_fee_over_budget_is_unknown(self):
        before=coins(8*10**9);self.rejected([],[before,before],[gas(before),encoded(receipt(before,amount=3980000000,fee=p.GAS_BUDGET+1))])
    def test_post_transfer_fragmentation_is_blocked(self):
        before=coins(8*10**9);after=coins(2*10**9,2019999000)
        self.rejected([],[before,before,after,after],[gas(before),encoded(receipt(before,amount=3980000000)),gas(after)])
    def test_post_transfer_wrong_reserve_is_blocked(self):
        before=coins(8*10**9);after=coins(3*10**9)
        self.rejected([],[before,before,after,after],[gas(before),encoded(receipt(before,amount=3980000000)),gas(after)])
    def test_coin_cap_no_partial_sweep(self):
        before={'0x'+format(i,'064x'):{'version':1,'digest':D,'balance':10**9} for i in range(65)}
        with self.assertRaises(p.PayoutError):p.wallet_plan(before)
    def test_identity_mismatch_no_command(self):
        runner=Mock()
        with p.payout_session(self.directory) as j:
            with self.assertRaises(p.PayoutError):p.run_payout([],j,runner,Mock(),Mock(side_effect=p.PayoutError('wrong wallet')))
        runner.assert_not_called()
    def test_complete_run_can_run_again(self):
        for _ in range(2):self.plan([],[{},{}],[gas({})])
