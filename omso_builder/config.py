"""Registered repositories, kept in the user's Application Support folder.

{"schema": 1, "repositories": [{"path": "/abs/primary/checkout", "tooling": "/abs/checkout"}]}

`tooling` is optional. It names the checkout whose omso-build.json builds the
repository's checkouts that have none; by default the checkout of `main`, else
the primary checkout. OMSO_BUILDER_CONFIG overrides the file (tests).
"""
import json
import os
from pathlib import Path
import tempfile

from . import gitinfo

DEFAULT_PATH = Path.home() / 'Library/Application Support/omso Builder/repositories.json'


def path():
    return Path(os.environ.get('OMSO_BUILDER_CONFIG') or DEFAULT_PATH)


def load():
    file = path()
    try:
        data = json.loads(file.read_text())
    except FileNotFoundError:
        return []
    entries = data.get('repositories') if isinstance(data, dict) and data.get('schema') == 1 else None
    if not isinstance(entries, list):
        raise ValueError(f'{file} is not an omso Builder repository list (schema 1).')
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) - {'path', 'tooling'} or
                not isinstance(entry.get('path'), str) or not os.path.isabs(entry['path']) or
                not isinstance(entry.get('tooling', '/'), str) or not os.path.isabs(entry.get('tooling', '/'))):
            raise ValueError(f'{file} has an invalid repository entry.')
    return [dict(entry) for entry in entries]


def save(repositories):
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=file.parent, prefix='.repositories-', suffix='.json')
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(dict(schema=1, repositories=repositories), stream, indent=2)
        stream.write('\n')
    os.replace(temporary, file)


def add(folder, tooling=None):
    """Register the repository containing folder (any of its checkouts)."""
    entry = dict(path=str(gitinfo.primary_checkout(folder)))
    if tooling:
        entry['tooling'] = str(Path(tooling).resolve())
    repositories = load()
    index = next((i for i, item in enumerate(repositories) if item['path'] == entry['path']), None)
    if index is None:
        repositories.append(entry)
    else:
        repositories[index] = entry
    save(repositories)
    return entry


def remove(folder):
    root = str(Path(folder).resolve())
    repositories = load()
    kept = [entry for entry in repositories if entry['path'] != root]
    if len(kept) == len(repositories):
        raise LookupError('That repository is not registered.')
    save(kept)
