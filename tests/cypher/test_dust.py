import copy
import io
import json
import tempfile
import unittest
from unittest.mock import Mock,patch
from contextlib import redirect_stdout
import staketasjson as p
import wallet_snapshot as w
from stake_discovery import DiscoveryError
from test_wallet import coins,gas,receipt,encoded
from test_snapshot import page,node,A,B

def wallet(amount=8_000_000_000,credit=0,cp=20):return w.CoinSnapshot(coins(amount),credit,cp)
class DustTests(unittest.TestCase):
    def test_zero_dust_large_credit_never_spendable(self):
        for credit in (0,2452,10**15):
            c=wallet(8_000_000_000,credit)
            self.assertEqual(p.wallet_plan(c)['amount_mist'],3_980_000_000)
    def test_credit_cannot_fund_insufficient_coin_reserve(self):
        self.assertEqual(p.wallet_plan(wallet(1_000_000,10**15))['amount_mist'],0)
    def test_pinned_total_reconciliation_with_large_credit(self):
        x=page([node()],10);x['data']['address']['balance'].update(addressBalance=str(10**15),totalBalance=str(10**15+10))
        c=w.snapshot(lambda c,t:x);self.assertEqual(w.address_balance(c),10**15);self.assertEqual(sum(v['balance'] for v in c.values()),10)
        x['data']['address']['balance']['totalBalance']='10'
        with self.assertRaises(DiscoveryError):w.snapshot(lambda c,t:x)
    def test_changed_credit_within_pinned_pages_fails(self):
        a=page([node(A)],20,True,'a');b=page([node(B)],20);b['data']['address']['balance'].update(addressBalance='1',totalBalance='21');it=iter([a,b])
        with self.assertRaises(DiscoveryError):w.snapshot(lambda c,t:next(it))
    def test_incoming_between_preflight_reads_allowed(self):
        before=wallet(credit=2452);after=wallet(credit=10**15,cp=21)
        self.assertEqual(w.address_balance(p.checked_wallet(Mock(side_effect=[before,after]),Mock(return_value=gas(before)),20)),10**15)
    def test_outgoing_credit_decrease_fails(self):
        with self.assertRaises(p.PayoutError):p.checked_wallet(Mock(side_effect=[wallet(credit=2452),wallet(credit=1,cp=21)]),Mock(return_value=gas(wallet())),20)
    def test_coin_spend_or_stale_ref_not_hidden_by_credit(self):
        for field,value in [('version',10),('balance',7_999_999_999),('digest','1'*31+'3')]:
            before=wallet();after=wallet(credit=10**15);after[A][field]=value
            with self.assertRaises(p.PayoutError):p.checked_wallet(Mock(side_effect=[before,after]),Mock(return_value=gas(before)),20)
    def test_checkpoint_regression_rejected_despite_incoming_credit(self):
        with self.assertRaises(p.PayoutError):p.checked_wallet(Mock(side_effect=[wallet(cp=22),wallet(credit=2452,cp=21)]),Mock(return_value=gas(wallet())),20)
    def run_trial(self,pre,after,record=None):
        runner=Mock(side_effect=[gas(pre),encoded(record or receipt(pre,amount=3_980_000_000)),gas(after)])
        with tempfile.TemporaryDirectory() as td,patch('subprocess.Popen',side_effect=AssertionError('wallet forbidden')),redirect_stdout(io.StringIO()):
            with p.payout_session(td) as j:p.run_payout([],j,runner,Mock(side_effect=[pre,pre,after,after]),lambda:None)
            return p.Journal(td).load(),runner
    def test_incoming_after_payout_not_false_failure(self):
        state,runner=self.run_trial(wallet(credit=2452),wallet(4_019_999_000,10**15,21))
        self.assertEqual(state['status'],'complete');self.assertEqual(state['final_address_balance_mist'],10**15)
        self.assertEqual(state['final_wallet_balance_mist'],10**15+4_019_999_000)
        self.assertEqual(state['wallet_plan']['amount_mist'],3_980_000_000)
        self.assertEqual(runner.call_args_list[1].args[0][-1],'@'+p.RECIPIENT)
    def test_address_decrease_after_payout_requires_reconciliation(self):
        with self.assertRaises(p.PayoutError):self.run_trial(wallet(credit=2452),wallet(4_019_999_000,0,21))
    def test_lookalike_recipient_rejected(self):
        before=wallet(credit=2452);bad=receipt(before,amount=3_980_000_000)
        bad['balanceChanges'][0]['owner']['AddressOwner']='0x3333333333333333333333333333333333333333333333333333333333333333'
        with self.assertRaises(p.PayoutError):self.run_trial(before,wallet(4_019_999_000,2452),bad)
    def test_future_normal_run_after_complete_with_dust(self):
        with tempfile.TemporaryDirectory() as td,patch('subprocess.Popen',side_effect=AssertionError('wallet forbidden')),redirect_stdout(io.StringIO()):
            for credit in (2452,5000):
                c=wallet(4_000_000_000,credit);runner=Mock(return_value=gas(c))
                with p.payout_session(td) as j:p.run_payout([],j,runner,Mock(return_value=c),lambda:None)
                self.assertEqual(p.Journal(td).load()['status'],'complete');self.assertEqual(runner.call_args.args[0],['gas'])
