"""Checkouts of the registered repositories and what each one can build.

A checkout with its own omso-build.json is built by it. A checkout without one
(for example a branch older than the manifest) is built by the repository's
tooling checkout's manifest, with the selected checkout as its source.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess

from . import config, gitinfo, manifest as manifests

GIT_ERRORS = (OSError, ValueError, subprocess.SubprocessError)


def _tooling(entry, checkouts):
    """The configured tooling checkout while it exists, else main's checkout, else the primary one."""
    if entry.get('tooling'):
        configured = str(Path(entry['tooling']).resolve())
        if configured in [checkout['path'] for checkout in checkouts]:
            return configured
    for branch in ('main', 'master'):
        match = next((checkout for checkout in checkouts if checkout['branch'] == branch), None)
        if match:
            return match['path']
    primary = next((checkout for checkout in checkouts if checkout['primary']), None)
    return primary['path'] if primary else None


def _describe(checkout, tooling, fallback, fallback_problem):
    record = dict(checkout, dirty=False, updated=None, products=[], options=[], notes=[],
                  manifest=None, problem=None)
    try:
        record['dirty'], record['updated'] = gitinfo.state(checkout['path'])
    except GIT_ERRORS:
        record['problem'] = 'Git could not read this checkout.'
        return record
    try:
        own = manifests.load(checkout['path'])
    except manifests.ManifestError as error:
        record['problem'] = str(error)
        return record
    if own is not None:
        chosen, root, kind = own, checkout['path'], 'own'
    elif fallback is not None:
        chosen, root, kind = fallback, tooling, 'tooling'
    else:
        record['problem'] = fallback_problem or (
            f'No {manifests.FILENAME} in this checkout or in the repository\'s main checkout.')
        return record
    record['manifest'] = dict(path=root, kind=kind)
    record['options'] = chosen['options']
    record['notes'] = chosen['notes']
    try:
        record['products'] = manifests.products(chosen, checkout['path'])
    except (OSError, ValueError) as error:
        record['problem'] = f'No product list in this checkout ({error}).'
    return record


def _repository(entry, only=None):
    result = dict(path=entry['path'], name=Path(entry['path']).name, tooling=None, problem=None, sources=[])
    try:
        checkouts = gitinfo.worktrees(entry['path'])
    except GIT_ERRORS:
        result['problem'] = 'Not an available Git repository.'
        return result
    tooling = _tooling(entry, checkouts)
    fallback, problem = None, None
    if tooling:
        try:
            fallback = manifests.load(tooling)
        except manifests.ManifestError as error:
            problem = f'Main checkout: {error}'
    result['tooling'] = tooling
    if fallback:
        result['name'] = fallback['name']
    if only is not None:
        checkouts = [checkout for checkout in checkouts if checkout['path'] == only]
    with ThreadPoolExecutor(max_workers=8) as pool:
        result['sources'] = list(pool.map(lambda checkout: _describe(checkout, tooling, fallback, problem), checkouts))
    if not fallback:
        # Name the repository after any checkout's manifest, else its folder.
        for source in result['sources']:
            if source['manifest'] and source['manifest']['kind'] == 'own':
                result['name'] = manifests.load(source['path'])['name']
                break
    return result


def repositories():
    return [_repository(entry) for entry in config.load()]


def source(path):
    """(repository, source) for one checkout of a registered repository."""
    try:
        primary = str(gitinfo.primary_checkout(path))
    except GIT_ERRORS:
        raise LookupError('That folder is not a Git checkout.')
    entry = next((item for item in config.load() if item['path'] == primary), None)
    if entry is None:
        raise LookupError('That checkout belongs to a repository omso Builder does not list. Add the repository first.')
    repository = _repository(entry, only=str(Path(path).resolve()))
    if not repository['sources']:
        raise LookupError('That folder is not an available checkout of its repository.')
    return repository, repository.pop('sources')[0]
