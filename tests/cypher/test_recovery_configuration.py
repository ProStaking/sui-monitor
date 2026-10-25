import copy
import unittest
from unittest.mock import patch
import resume_guard as r
from stake_discovery import DiscoveryError
from test_resume import state
class RecoveryConfigurationTests(unittest.TestCase):
    def test_no_manifest_never_unlocks(self):
        with patch.object(r,'_RECOVERY',{}),self.assertRaises(DiscoveryError):r.validate_state(state(),r.RUN_ID,r.RECIPIENT)
    def test_unknown_manifest_field_rejected(self):
        bad=copy.deepcopy(r._RECOVERY);bad['force']=True
        with patch.object(r,'_RECOVERY',bad),self.assertRaises(DiscoveryError):r.validate_state(state(),r.RUN_ID,r.RECIPIENT)
    def test_query_interpolation_only_validated_digest(self):
        with patch.object(r,'TX','bad"query'),self.assertRaises(DiscoveryError):r.validate_state(state(),r.RUN_ID,r.RECIPIENT)
    def test_checkpoint_boolean_or_reversed_bounds_rejected(self):
        for value in [True,-1,r.HISTORY_AFTER]:
            with patch.object(r,'CHECKPOINT',value),self.assertRaises(DiscoveryError):r.validate_state(state(),r.RUN_ID,r.RECIPIENT)
