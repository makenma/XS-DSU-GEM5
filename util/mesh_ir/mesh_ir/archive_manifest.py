import hashlib
import json
import re
from pathlib import Path

PLACEHOLDER = re.compile(r"<[^>]+>")


def digest(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None

def source_map_problems(matrix: Path, state: dict, *, worktree: Path):
    for identity in sorted(matrix.glob('*/identity.json')):
        case = identity.parent.name
        reference = json.loads(identity.read_text()).get('changed_sources')
        state['checked'] += 1
        if not (isinstance(reference, dict) and reference.get('path') and isinstance(reference.get('count'), int) and isinstance(reference.get('sha256'), str)):
            state['missing'].append(('case source map reference', case))
            continue
        path = worktree / reference['path']
        raw = digest(path)
        if raw is None:
            state['missing'].append(('case source map', reference['path']))
            continue
        if raw != reference['sha256']:
            state['mismatch'].append(('case source map', reference['path'], reference['sha256'], raw))
            continue
        mapping = json.loads(path.read_text())
        if len(mapping) != reference['count']:
            state['mismatch'].append(('case source map count', reference['path'], reference['count'], len(mapping)))
        document = json.loads(identity.read_text())
        for field in ('binary', 'config'):
            entry = document.get(field) or {}
            if entry.get('path') in mapping:
                if mapping[entry['path']] != entry.get('sha256'):
                    state['mismatch'].append(('case identity vs source map', '%s/%s' % (case, entry['path']), entry.get('sha256'), mapping[entry['path']]))

def environment_problems(matrix, state):
    for command in sorted(matrix.glob('*/command.json')):
        case = command.parent.name
        environment = json.loads(command.read_text()).get('environment')
        state['checked'] += 1
        if not isinstance(environment, dict) or not environment:
            state['missing'].append(('case environment', case))
            continue
        for key, value in sorted(environment.items()):
            if not isinstance(value, str) or PLACEHOLDER.search(value):
                state['missing'].append(('case environment placeholder', '%s/%s' % (case, key)))
