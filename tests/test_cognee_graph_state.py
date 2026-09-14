import copy
import importlib.util
import pathlib
import unittest

PATH = pathlib.Path(__file__).resolve().parents[1] / 'scripts/cognee-graph-state.py'


class GraphStateTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PATH.exists(), 'complete graph snapshot helper missing')
        spec = importlib.util.spec_from_file_location('graph_state', PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.rows = {
            'nodes': [[1, ['Entity'], {'text': 'source', 'vector': [0.25, 0.75]}]],
            'edges': [[2, 1, 1, 'depends_on', {'weight': 1}]],
            'labels': [['Entity']], 'relationships': [['depends_on']],
            'properties': [['text'], ['vector'], ['weight']],
            'indexes': [['Entity', ['vector'], {'vector': ['VECTOR']},
                         {'vector': {'dimension': 1024}}, 'english', [], 'NODE', 'OPERATIONAL']],
        }

    def digest(self, rows):
        class Graph:
            def ro_query(inner, query):
                key = next(key for key, text in self.module.QUERIES.items() if text == query)
                return type('Result', (), {'result_set': rows[key]})()
        class Database:
            def list_graphs(inner): return ['dataset-id']
            def select_graph(inner, name): return Graph()
        return self.module.graph_state(Database())

    def test_complete_content_and_schema_mutations_change_digest(self):
        baseline = self.digest(self.rows)
        for field in self.rows:
            changed = copy.deepcopy(self.rows)
            changed[field].append(copy.deepcopy(changed[field][0]))
            with self.subTest(field=field):
                self.assertNotEqual(baseline, self.digest(changed))
        for value in ('text', 'vector'):
            changed = copy.deepcopy(self.rows)
            changed['nodes'][0][2][value] = 'changed'
            self.assertNotEqual(baseline, self.digest(changed))
        changed = copy.deepcopy(self.rows)
        changed['indexes'][0][3]['vector']['dimension'] = 768
        self.assertNotEqual(baseline, self.digest(changed))

    def test_result_and_map_order_do_not_change_digest(self):
        changed = copy.deepcopy(self.rows)
        changed['properties'].reverse()
        changed['nodes'][0][2] = {'vector': [0.25, 0.75], 'text': 'source'}
        self.assertEqual(self.digest(self.rows), self.digest(changed))

    def test_drill_uses_complete_snapshot_instead_of_dump(self):
        script = (PATH.parent / 'cognee-restore-drill.sh').read_text()
        self.assertIn('scripts/cognee-graph-state.py', script)
        self.assertNotIn('redis-cli --raw DUMP', script)


if __name__ == '__main__':
    unittest.main()
