"""Restore must decode native file URIs without escaping managed storage."""
import ast
from pathlib import Path
import tempfile
import unittest
import urllib.parse


class RestorePathTests(unittest.TestCase):
    def test_denial_requires_guard_status_and_no_context(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/cognee-restore-proof.py'
        module = ast.parse(script.read_text())
        probe = next(node.value.value for node in module.body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == 'probe' for target in node.targets))
        function = next((node for node in ast.parse(probe).body
                         if isinstance(node, ast.FunctionDef) and node.name == 'assert_denied'), None)
        self.assertIsNotNone(function, 'restore proof must inspect denial response context')
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(script), 'exec'), namespace)
        check = namespace['assert_denied']
        for status, detail in ((403, 'reader scope denied'), (409, 'reader context changed or exceeded limit')):
            check(status, {'detail': detail})
            with self.assertRaises(AssertionError):
                check(status, {'detail': detail, 'context': 'private source'})
        for status in (200, 401, 404, 500):
            with self.assertRaises(AssertionError):
                check(status, {'detail': 'reader scope denied'})
        with self.assertRaises(AssertionError):
            check(409, {'detail': 'private source'})
        with self.assertRaises(AssertionError):
            check(409, {'detail': 'reader context changed or exceeded limit'}, allowed=(403,))

    def test_native_uris_and_escape_rejection(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/cognee-restore-proof.py'
        module = ast.parse(script.read_text())
        probe = next(node.value.value for node in module.body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == 'probe' for target in node.targets))
        function = next((node for node in ast.parse(probe).body
                         if isinstance(node, ast.FunctionDef) and node.name == 'source_path'), None)
        self.assertIsNotNone(function, 'restore proof must decode native file URIs')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'managed'
            root.mkdir()
            document = root / 'reviewed source.txt'
            document.write_text('synthetic reviewed source')
            outside = Path(directory) / 'outside.txt'
            outside.write_text('outside managed storage')
            (root / 'escape').symlink_to(outside)
            namespace = {'root': root, 'pathlib': __import__('pathlib'), 'urllib': __import__('urllib')}
            exec(compile(ast.Module(body=[function], type_ignores=[]), str(script), 'exec'), namespace)
            decode = namespace['source_path']
            for location in (str(document), document.as_uri(), document.as_uri().replace('file:///', 'file://localhost/')):
                self.assertEqual(decode(location), document)
            for location in (outside.as_uri(), (root / 'escape').as_uri(),
                             root.as_uri() + '/%2e%2e/outside.txt',
                             'https://example.com/source', 'file://other-host' + str(document)):
                with self.assertRaises(ValueError, msg=location):
                    decode(location)


if __name__ == '__main__':
    unittest.main()
