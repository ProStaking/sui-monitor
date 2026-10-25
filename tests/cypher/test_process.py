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
A = '0x' + 'a' * 64
B = '0x' + 'b' * 64
C = '0x' + 'c' * 64

def receipt(n=1, oid=A, amount=None, status='success'):
    tx = '1' * 31 + '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'[n]
    return {'digest': tx, 'checkpoint': '12345', 'effects': {'transactionDigest': tx, 'status': {'status': status}}, 'objectChanges': [{'type': 'deleted', 'objectId': oid}], 'balanceChanges': [] if amount is None else [{'owner': {'AddressOwner': p.RECIPIENT}, 'coinType': '0x2::sui::SUI', 'amount': str(amount)}]}

def gas(rows):
    return {'gasCoins': [{'gasCoinId': oid, 'mistBalance': amount, 'suiBalance': 'display ignored'} for oid, amount in rows], 'addressMistBalance': 0, 'addressSuiBalance': '0'}

def result(data, code=0):
    return (code, json.dumps(data).encode())

class PayoutTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.guard = patch('subprocess.Popen', side_effect=AssertionError('REAL SUBPROCESS FORBIDDEN'))
        self.guard.start()
        self.out = redirect_stdout(io.StringIO())
        self.out.__enter__()

    def tearDown(self):
        self.out.__exit__(None, None, None)
        self.guard.stop()
        self.temp.cleanup()

    def test_balance_schema_adversarial(self):
        bad = [{}, None, {'gasCoins': {}}, gas([(A, -1)]), gas([(A, True)]), gas([(A, 1), (A, 1)]), gas([('x;evil', 1)]), gas([(A, 2 ** 64)]), {'gasCoins': [{'gasCoinId': A, 'suiBalance': '10'}]}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises((p.PayoutError, KeyError, TypeError)):
                p.balances(Mock(return_value=result(value)))

    def test_overlap_rejected_then_lock_reusable(self):
        with p.payout_session(self.directory):
            with self.assertRaises(p.PayoutError):
                with p.payout_session(self.directory):
                    pass
        with p.payout_session(self.directory):
            pass

    def test_incomplete_journal_blocks_new_execution(self):
        j = p.Journal(self.directory)
        j.begin([A])
        j.pending('withdraw', A, None)
        with self.assertRaises(p.PayoutError):
            with p.payout_session(self.directory):
                self.fail('must not enter')

    def test_symlink_lock_refused(self):
        (self.directory / '.cypher-payout.lock').symlink_to('/dev/null')
        with self.assertRaises(OSError):
            with p.payout_session(self.directory):
                pass

    def test_symlink_state_refused(self):
        (self.directory / '.cypher-payout-state.json').symlink_to('/dev/null')
        with self.assertRaises(OSError):
            with p.payout_session(self.directory):
                pass

    def test_world_writable_state_refused(self):
        path = self.directory / '.cypher-payout-state.json'
        path.write_text('{}')
        path.chmod(438)
        with self.assertRaises(p.PayoutError):
            with p.payout_session(self.directory):
                pass

    def test_failure_propagates_nonzero_main(self):
        with patch.object(p.socket, 'gethostname', return_value='fixture-cypher'), patch.object(p, 'payout_session', return_value=p.payout_session(self.directory)), patch.object(p, 'discover', return_value=['0x'+'a'*64]), patch.object(p, 'run_payout', side_effect=p.PayoutError('bad')), redirect_stderr(io.StringIO()):
            self.assertEqual(p.main([]), 1)

    def test_duplicate_json_and_nonnumeric_values(self):
        for raw in (b'{"x":1,"x":2}', b'NaN', b'\xff'):
            with self.assertRaises(p.PayoutError):
                p.parse_json(raw)

    def test_runner_success_uses_fixed_argv_no_shell(self):
        proc = Mock()
        proc.wait.return_value = 0
        proc.poll.return_value = 0

        def launch(argv, **kw):
            kw['stdout'].write(b'{}')
            return proc
        with patch('subprocess.Popen', side_effect=launch) as popen:
            self.assertEqual(p.run_cli(['gas']), (0, b'{}'))
            self.assertEqual(popen.call_args.args[0], [p.CLI, 'client', 'gas', '--json'])
            self.assertNotIn('shell', popen.call_args.kwargs)

    def test_runner_timeout_kills_local_process_only(self):
        proc = Mock(pid=123456)
        proc.poll.return_value = None
        proc.wait.return_value = 0
        with patch('subprocess.Popen', return_value=proc), patch.object(p.time, 'monotonic', side_effect=[0, 121]), patch.object(p.os, 'killpg') as kill:
            with self.assertRaises(p.PayoutError):
                p.run_cli(['gas'])
            kill.assert_called_once_with(123456, p.signal.SIGKILL)

    def test_output_cap_kills_process(self):
        proc = Mock(pid=123456)
        proc.poll.return_value = None

        def launch(argv, **kw):
            kw['stdout'].write(b'x' * 100)
            kw['stdout'].flush()
            return proc
        with patch('subprocess.Popen', side_effect=launch), patch.object(p, 'MAX_OUTPUT', 50), patch.object(p.os, 'killpg') as kill:
            with self.assertRaises(p.PayoutError):
                p.run_cli(['gas'])
            kill.assert_called_once()
