import importlib.util
from pathlib import Path
from unittest.mock import patch
import unittest
import json
import os
import tempfile
import urllib.error

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('snapshot', ROOT / 'platform/wazuh/recovery/index-snapshot.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def fake_api(state='SUCCESS', failed=0):
    return [{helper.REPOSITORY: {'type': 'fs', 'settings': {'location': helper.LOCATION}}},
            {'snapshots': []}, {'snapshots': []}, {'snapshot': {'uuid': 'fixture-native-id', 'state': state, 'shards': {'failed': failed, 'total': 1, 'successful': 1}, 'indices': ['wazuh-alerts-4.x-test']}}]


class NativeSnapshot(unittest.TestCase):
    def test_cleanup_rejects_untrusted_or_mismatched_proof(self):
        for defect in ('parent-writable', 'file-writable', 'uuid', 'sha', 'unverified', 'symlink'):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); base = root / 'captures'; base.mkdir()
                proof = root / 'proof'; proof.mkdir(mode=0o755)
                name = 'wazuh-20261121t010000z-01234567'
                capture = base / name; capture.mkdir()
                (capture / 'indexer-capture.json').write_text(json.dumps({'sha256':'a'*64,'native_uuid':'fixture'}))
                data = {'indexer_readback_verified':True,'indexer_snapshot':name,
                    'indexer_sha256':'a'*64,'native_uuid':'fixture','snapshot_id':'b'*64}
                if defect == 'uuid': data['native_uuid'] = 'other'
                if defect == 'sha': data['indexer_sha256'] = 'c'*64
                if defect == 'unverified': data['indexer_readback_verified'] = False
                receipt = proof / (name + '.json'); receipt.write_text(json.dumps(data)); receipt.chmod(0o444)
                if defect == 'parent-writable': proof.chmod(0o775)
                if defect == 'file-writable': receipt.chmod(0o664)
                if defect == 'symlink':
                    other = root / 'other'; receipt.rename(other); receipt.symlink_to(other)
                original = os.fstat
                def root_owned(fd):
                    values = list(original(fd)); values[4] = 0
                    return os.stat_result(values)
                with patch.object(helper.os, 'fstat', side_effect=root_owned), patch.object(helper, 'api') as api:
                    with self.assertRaises((ValueError, OSError)): helper.cleanup_verified(base, proof)
                api.assert_not_called()
                self.assertTrue(capture.exists())

    def test_verified_cleanup_retries_after_native_delete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); base = root / 'captures'; base.mkdir()
            proof = root / 'proof'; proof.mkdir(mode=0o755)
            name = 'wazuh-20261121t010000z-01234567'
            capture = base / name; capture.mkdir()
            (capture / 'indexer-capture.json').write_text(json.dumps({'sha256': 'a'*64, 'native_uuid': 'fixture'}))
            receipt = proof / (name + '.json')
            receipt.write_text(json.dumps({'indexer_readback_verified': True,
                'indexer_snapshot': name, 'indexer_sha256': 'a'*64,
                'native_uuid': 'fixture', 'snapshot_id': 'b'*64}))
            receipt.chmod(0o444)
            original = os.fstat
            def root_owned(fd):
                values = list(original(fd)); values[4] = 0
                return os.stat_result(values)
            with patch.object(helper.os, 'fstat', side_effect=root_owned), patch.object(helper, 'api',
                    side_effect=urllib.error.HTTPError('fixture', 404, 'not found', {}, None)) as api:
                helper.cleanup_verified(base, proof)
            self.assertFalse(capture.exists())
            self.assertEqual(api.call_count, 1)

    def test_partial_never_publishes_receipt(self):
        for state, failed in [('PARTIAL', 0), ('SUCCESS', 1), ('FAILED', 1)]:
            with self.subTest(state=state), patch.object(helper, 'api', side_effect=fake_api(state, failed)):
                with self.assertRaises(ValueError): helper.snapshot()

    def test_no_repoint_or_delete(self):
        with patch.object(helper, 'api', return_value={helper.REPOSITORY: {'type': 'fs', 'settings': {'location': '/other'}}}) as api:
            with self.assertRaises(ValueError): helper.snapshot()
            self.assertEqual(api.call_count, 1)

    def test_success_uses_native_snapshot_without_security_state(self):
        with patch.object(helper, 'api', side_effect=fake_api()) as api:
            receipt = helper.snapshot()
        self.assertEqual(receipt['indexer_state'], 'SUCCESS')
        method, path, payload = api.call_args.args
        self.assertTrue(method == 'PUT' and path.endswith('?wait_for_completion=true'))
        self.assertFalse(payload['include_global_state'])
        self.assertFalse(payload['partial'])
        self.assertTrue(all(call.args[0] != 'DELETE' for call in api.call_args_list))


if __name__ == '__main__':
    unittest.main()
