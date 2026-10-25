import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import deployment_config as c
class ConfigurationTests(unittest.TestCase):
    def test_valid_config_roundtrip(self):self.assertEqual(c.validate(copy.deepcopy(c.CONFIG)),c.CONFIG)
    def test_reject_placeholder_or_malformed_addresses(self):
        for value in ['REPLACE_ME','0x123','0x'+'A'*64,'0x'+'1'*64+';sh',None]:
            x=copy.deepcopy(c.CONFIG);x['owner']=value
            with self.assertRaises(ValueError):c.validate(x)
    def test_owner_and_recipient_must_differ(self):
        x=copy.deepcopy(c.CONFIG);x['recipient']=x['owner']
        with self.assertRaises(ValueError):c.validate(x)
    def test_unknown_or_missing_fields(self):
        x=copy.deepcopy(c.CONFIG);x['private_key']='never-supported'
        with self.assertRaises(ValueError):c.validate(x)
        del x['private_key'];del x['owner']
        with self.assertRaises(ValueError):c.validate(x)
    def test_relative_cli_or_client_config_refused(self):
        for field in ['cli_path','client_config_path']:
            x=copy.deepcopy(c.CONFIG);x[field]='relative/path'
            with self.assertRaises(ValueError):c.validate(x)
    def test_endpoint_and_host_validation(self):
        for key,value in [('hostname','host;cmd'),('active_env','REPLACE_ENV'),('rpc_url','https://unreviewed.example')]:
            x=copy.deepcopy(c.CONFIG);x[key]=value
            with self.assertRaises(ValueError):c.validate(x)
    def test_duplicate_fields_rejected(self):
        with self.assertRaises(ValueError):c.unique([('owner','a'),('owner','b')])
    def test_missing_optional_recovery_stays_disabled(self):
        self.assertIsNone(c.read_config('nonexistent-test-recovery.local.json',optional=True))
