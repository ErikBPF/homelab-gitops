"""Verify archive data and modes while allowing Kubernetes-owned mount-root metadata."""
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


class ArchiveTests(unittest.TestCase):
    def test_root_exception_keeps_content_and_child_mode_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, restored = root / 'source', root / 'restored'
            source.mkdir(mode=0o755)
            restored.mkdir(mode=0o700)
            child = source / 'data'
            child.mkdir(mode=0o750)
            document = child / 'source.txt'
            document.write_text('reviewed text')
            document.chmod(0o640)
            archive = root / 'data.tar'
            subprocess.run(['tar', '-C', str(source), '-cf', str(archive), '.'], check=True)
            members = root / 'members'
            with tarfile.open(archive) as tar:
                members.write_bytes(b''.join(entry.name.encode() + b'\0' for entry in tar if Path(entry.name).parts))
            subprocess.run(['tar', '--same-permissions', '-C', str(restored), '-xf', str(archive), '--no-recursion', '--null', '-T', str(members)], check=True)
            self.assertEqual(restored.stat().st_mode & 0o777, 0o700)
            command = ['tar', '-C', str(restored), '-df', str(archive), '--no-recursion', '--null', '-T', str(members)]
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
            (restored / 'data/source.txt').chmod(0o600)
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            (restored / 'data/source.txt').chmod(0o640)
            (restored / 'data').chmod(0o700)
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            (restored / 'data').chmod(0o750)
            (restored / 'data/source.txt').write_text('modified text')
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
