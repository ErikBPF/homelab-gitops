#!/usr/bin/env python3
"""Hash complete graph records and schema without allocator-dependent RDB bytes."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

QUERIES = {
    'nodes': 'MATCH (n) RETURN id(n), labels(n), properties(n)',
    'edges': 'MATCH (a)-[r]->(b) RETURN id(r), id(a), id(b), type(r), properties(r)',
    'labels': 'CALL db.labels()',
    'relationships': 'CALL db.relationshipTypes()',
    'properties': 'CALL db.propertyKeys()',
    'constraints': 'CALL db.constraints()',
    # Include index definitions and readiness, excluding runtime GC/timing counters.
    'indexes': 'CALL db.indexes() YIELD label, properties, types, options, language, stopwords, entitytype, status RETURN label, properties, types, options, language, stopwords, entitytype, status',
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def graph_state(database):
    result = []
    for name in sorted(database.list_graphs()):
        graph = database.select_graph(name)
        state = {}
        for key, query in QUERIES.items():
            rows = graph.ro_query(query).result_set
            if key == 'nodes':
                rows = [[identity, sorted(labels), properties] for identity, labels, properties in rows]
            state[key] = sorted(rows, key=canonical)
        result.append((name, hashlib.sha256(canonical(state).encode()).hexdigest()))
    return result


def main():
    if sys.argv[1:] == ['--inside-pod']:
        # -S skips Cognee's startup hooks; use the image's existing native client.
        sys.path.append(str(Path(sys.executable).parent.parent / 'lib' /
                            ('python' + '.'.join(map(str, sys.version_info[:2]))) / 'site-packages'))
        from falkordb import FalkorDB
        for name, digest in graph_state(FalkorDB(host='falkordb', port=6379)):
            print('graph', name, digest)
    else:
        context, namespace = sys.argv[1:]
        subprocess.run(['kubectl', '--context', context, '-n', namespace, 'exec', '-i',
                        'deployment/cognee', '--', '/app/.venv/bin/python', '-S', '-', '--inside-pod'],
                       input=Path(__file__).read_text(), text=True, check=True)


if __name__ == '__main__':
    main()
