#!/usr/bin/env python3
"""Exercise the restore identity gate without contacting a cluster."""
from pathlib import Path
import os
import re
import subprocess
import tempfile

backup_source = Path('apps/cognee/resources.yaml').read_text()
restore_source = Path('scripts/cognee-restore-drill.sh').read_text()
assert 'identity-digest.sha256' in backup_source, 'C6 missing: backup has no identity digest'
backup_sql = re.search(r'-c "([^"\n]+identity_rows)"', backup_source).group(1)
restore_sql = re.search(r'-c "([^"\n]+identity_rows)"', restore_source).group(1)
assert backup_sql == restore_sql, 'backup and restore identity definitions differ'
assert 'sha256sum cognee-data.tar identity-digest.sha256 >>SHA256SUMS.pending' in backup_source
assert 'cognee-data.tar identity-digest.sha256 SHA256SUMS;' in restore_source

start = restore_source.index('# Compare identity rows before restored application mutations.')
end = restore_source.index('# Identity comparison complete.', start)
gate = restore_source[start:end]
assert start < restore_source.index('run cognee-restore-falkor'), 'identity validation happens too late'
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'identity-digest.sha256'
    for expected, restored, success in [('a' * 64, 'a' * 64, True), ('a' * 64, 'b' * 64, False), ('invalid', 'invalid', False)]:
        path.write_text(expected + '\n')
        script = 'set -euo pipefail\ncontext=fixture\nnamespace=fixture\nbackup=$1\nkubectl() { printf "%s\\n" "$RESTORED_DIGEST"; }\n' + gate
        result = subprocess.run(['bash', '-c', script, 'test', directory], env={**os.environ, 'RESTORED_DIGEST': restored}, capture_output=True, text=True)
        assert (result.returncode == 0) == success, 'C6 identity mismatch gate accepted invalid restoration'
print('C6 identity equality gate passed')
