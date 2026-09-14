"""Checks for resuming bounded comparisons from an already-fitted artifact."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run_precision


class ResumeTests(unittest.TestCase):
    def test_frozen_shard_keeps_every_selected_triple_without_refitting(self):
        docs = [dict(id=str(i), split=('calibration', 'selection', 'evaluation')[i // 6]) for i in range(18)]
        configs = [dict(id=str(i)) for i in range(28)]
        envelope = dict(captures_sha256='capture-pin', captures=['record'], configurations=configs)
        with TemporaryDirectory() as directory:
            args = SimpleNamespace(out=Path(directory), captures=Path('/unused'),
                captures_sha256='capture-pin', fitted_sha256='fit-pin', config_start=16, config_stop=18)
            with patch.object(run_precision, 'load_fitted', return_value=envelope), \
                 patch.object(run_precision, 'load_captures', return_value=({str(i): i for i in range(18)}, ['record'])), \
                 patch.object(run_precision.precision_campaign, 'fit_configurations', side_effect=AssertionError('must not refit')), \
                 patch.object(run_precision.precision_campaign, 'compare_frozen', side_effect=lambda capture, config, mode: dict(config_id=config['id'], mode=mode, observed=capture)), \
                 patch.object(run_precision, 'emit'):
                run_precision.frozen(args, dict(documents=docs))
                rows = [json.loads(line) for line in (args.out / 'results.jsonl').read_text().splitlines()]
                self.assertEqual(len(rows), 108)
                self.assertEqual({(r['config_id'], r['document_id'], r['mode']) for r in rows},
                    {(str(c), str(d), mode) for c in (16, 17) for d in range(18) for mode in ('k', 'v', 'kv')})
                self.assertTrue(all(r['fitted_sha256'] == 'fit-pin' and r['observed'] == int(r['document_id']) for r in rows))
                with self.assertRaises(FileExistsError):
                    run_precision.frozen(args, dict(documents=docs))
                args.captures_sha256 = 'different-pin'
                with self.assertRaisesRegex(ValueError, 'differ from fitting'):
                    run_precision.frozen(args, dict(documents=docs))

    def test_invalid_shard_is_rejected_before_output(self):
        with TemporaryDirectory() as directory:
            args = SimpleNamespace(out=Path(directory), config_start=2, config_stop=1)
            with self.assertRaises(ValueError):
                run_precision.compare_captures(args, dict(documents=[]), {}, [{}, {}], 'pin')
            self.assertFalse((args.out / 'results.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
