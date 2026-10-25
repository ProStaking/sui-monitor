import io,json,tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock,patch
from contextlib import ExitStack,redirect_stdout,redirect_stderr
import staketasjson as p
from stake_discovery import DiscoveryError
class EmptyRunTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.dir=Path(self.t.name);self.session=p.payout_session
    def tearDown(self):self.t.cleanup()
    def call(self,ids=None,error=None):
        out=io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.object(p.socket,'gethostname',return_value='fixture-cypher'))
            stack.enter_context(patch.object(p,'payout_session',side_effect=lambda:self.session(self.dir)))
            discover=stack.enter_context(patch.object(p,'discover',side_effect=error,return_value=[] if ids is None else ids))
            payout=stack.enter_context(patch.object(p,'run_payout'))
            snapshot=stack.enter_context(patch.object(p,'snapshot',side_effect=AssertionError('balance query forbidden on empty normal run')))
            wallet=stack.enter_context(patch.object(p,'run_cli',side_effect=AssertionError('wallet forbidden')))
            stack.enter_context(patch('subprocess.Popen',side_effect=AssertionError('subprocess forbidden')))
            stack.enter_context(redirect_stdout(out));stack.enter_context(redirect_stderr(io.StringIO()))
            code=p.main([])
        return code,out.getvalue(),discover,payout,snapshot,wallet
    def complete(self):
        with self.session(self.dir) as j:j.begin(['historical']);j.complete()
        return (self.dir/'.cypher-payout-state.json').read_bytes()
    def test_empty_fresh_zero_no_journal_no_wallet(self):
        code,out,_,pay,snap,wallet=self.call();self.assertEqual(code,0);self.assertEqual(json.loads(out)['event'],'nothing_to_do')
        pay.assert_not_called();snap.assert_not_called();wallet.assert_not_called();self.assertIsNone(p.Journal(self.dir).load())
    def test_empty_after_complete_preserves_history_bytes(self):
        before=self.complete();self.assertEqual(self.call()[0],0)
        self.assertEqual((self.dir/'.cypher-payout-state.json').read_bytes(),before)
    def test_fragmented_excess_and_dust_never_even_queried(self):
        # No amount/fragmentation can trigger a sweep: the balance interfaces are tripwires.
        _,_,_,pay,snap,wallet=self.call();pay.assert_not_called();snap.assert_not_called();wallet.assert_not_called()
    def test_unresolved_hold_checked_before_discovery(self):
        with self.session(self.dir) as j:j.begin(['pending'])
        before=(self.dir/'.cypher-payout-state.json').read_bytes()
        code,_,query,pay,_,_=self.call();self.assertEqual(code,1);query.assert_not_called();pay.assert_not_called()
        self.assertEqual((self.dir/'.cypher-payout-state.json').read_bytes(),before)
    def test_next_day_nonempty_still_eligible(self):
        self.complete();self.assertEqual(self.call()[0],0)
        ids=['0x'+'a'*64];code,_,_,pay,_,_=self.call(ids);self.assertEqual(code,0);self.assertEqual(pay.call_args.args[0],ids)
    def test_discovery_failure_not_empty_success_and_no_history_damage(self):
        before=self.complete()
        for text in ['HTTP','API','schema','index','partial pagination']:
            code,out,_,pay,_,_=self.call(error=DiscoveryError(text));self.assertEqual(code,1);self.assertNotIn('nothing_to_do',out);pay.assert_not_called()
            self.assertEqual((self.dir/'.cypher-payout-state.json').read_bytes(),before)
