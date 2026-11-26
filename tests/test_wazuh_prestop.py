import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('prestop', ROOT / 'platform/wazuh/recovery/manager-prestop.py')
hook = importlib.util.module_from_spec(spec); spec.loader.exec_module(hook)
UID = '24bb0968-7b98-463a-af73-249a186fddc8'


class GracefulStop(unittest.TestCase):
    def test_old_success_removed_on_stop_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'marker'; marker.write_text('old')
            with patch.object(hook, 'stop', side_effect=ValueError('stop failed')):
                with self.assertRaises(ValueError): hook.shutdown(UID, marker)
            self.assertFalse(marker.exists())

    def test_running_daemon_prevents_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); process = root / '42'; process.mkdir()
            (process / 'comm').write_text('wazuh-db'); (process / 'cmdline').write_bytes(b'/var/ossec/bin/wazuh-db\0')
            with patch.object(hook, 'stop'):
                with self.assertRaises(ValueError): hook.shutdown(UID, root / 'marker', root)
            self.assertFalse((root / 'marker').exists())

    def test_success_identity_private_and_filebeat_not_claimed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); process = root / '42'; process.mkdir()
            (process / 'comm').write_text('filebeat'); (process / 'cmdline').write_bytes(b'filebeat\0')
            with patch.object(hook, 'stop'): hook.shutdown(UID, root / 'marker', root)
            data = json.loads((root / 'marker').read_text())
            self.assertEqual(data['pod_uid'], UID)
            self.assertTrue(data['wazuh_databases_gracefully_stopped'])
            self.assertFalse(data['filebeat_consistency_proven'])
            self.assertEqual((root / 'marker').stat().st_mode & 0o077, 0)


if __name__ == '__main__': unittest.main()
