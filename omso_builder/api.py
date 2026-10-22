"""omso Builder JSON API v2: one request on stdin, one response on stdout.

Build logs and progress go to stderr. See docs/API.md for the contract.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

from . import builds, config, installation, manifest as manifests, sources

API_VERSION = 2
BUILD_FIELDS = ('source_dir', 'products', 'options', 'expected_head', 'expected_branch')
OPERATIONS = {
    'repositories': (),
    'add_repository': ('path', 'tooling'),
    'remove_repository': ('path',),
    'sources': (),
    'plan': BUILD_FIELDS,
    'build': BUILD_FIELDS + ('dry_run', 'install_mode'),
    'ready': (),
    'install': ('source_dir', 'build', 'build_id', 'install_mode'),
}
INSTALL_MODES = ('deferred', 'sudo')


class RequestError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@contextmanager
def locked(path):
    """The checkout's builder lock, shared with the repository's own tools."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RequestError('busy', 'A build or installation is running in this checkout. Try again when it finishes.')
        yield


def _string(request, key, required=True):
    value = request.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value:
        raise RequestError('invalid_request', f'{key} must be a nonempty string.')
    return value


def _absolute(request, key):
    value = _string(request, key)
    if not os.path.isabs(value):
        raise RequestError('invalid_request', f'{key} must be an absolute path.')
    return value


def _source(request):
    """(repository, source, manifest) for request['source_dir'], checked against expectations."""
    try:
        repository, source = sources.source(_absolute(request, 'source_dir'))
    except LookupError as error:
        raise RequestError('source_unavailable', str(error))
    if source['problem'] or source['manifest'] is None:
        raise RequestError('source_unavailable', source['problem'] or 'This checkout cannot be built.')
    for key, field in (('expected_head', 'head'), ('expected_branch', 'branch')):
        if _string(request, key, required=False) not in (None, source[field]):
            raise RequestError('source_changed', 'The source version changed; list sources and plan again.')
    try:
        manifest = manifests.load(source['manifest']['path'])
    except manifests.ManifestError as error:
        raise RequestError('source_unavailable', str(error))
    if manifest is None:
        raise RequestError('source_unavailable', 'The build manifest disappeared; refresh the sources.')
    return repository, source, manifest


def _selection(request, source):
    products = request.get('products')
    if (not isinstance(products, list) or not products or
            any(not isinstance(name, str) or not name for name in products) or len(set(products)) != len(products)):
        raise RequestError('invalid_request', 'products must be a nonempty array of unique product names.')
    if not set(products).issubset(source['products']):
        raise RequestError('unknown_product', 'A requested product is absent from the selected checkout.')
    requested = request.get('options', {})
    if not isinstance(requested, dict) or any(type(value) is not bool for value in requested.values()):
        raise RequestError('invalid_request', 'options must map option ids to true or false.')
    known = {option['id']: option['default'] for option in source['options']}
    if set(requested) - set(known):
        raise RequestError('invalid_request', 'Unknown options: ' + ', '.join(sorted(set(requested) - set(known))))
    return [name for name in source['products'] if name in products], dict(known, **requested)


def _summary(repository):
    return dict(path=repository['path'], name=repository['name'])


def _install(prepared, mode, staging_root, log):
    """Response fields after a build or ready lookup produced an installation."""
    if mode == 'deferred':
        return dict(status='awaiting_install', installation=prepared)
    try:
        installed = installation.run(prepared, staging_root, log)
    except installation.InstallationError as error:
        raise RequestError('install_failed', str(error))
    if installed:
        return dict(status='completed', installation=prepared)
    return dict(status='awaiting_install', installation=prepared,
                message='Built and tested; installation needs administrator approval. '
                        'Install it from omso Builder under Ready to install.')


def build(request, log, staging_root):
    repository, source, manifest = _source(request)
    products, options = _selection(request, source)
    preview = request.get('dry_run', False)
    mode = request.get('install_mode', 'deferred')
    with locked(Path(source['path']) / manifest['lock']):
        # Build the folder as it is now; refuse if it moved since it was listed.
        _, current = sources.source(source['path'])
        if (current['head'], current['branch']) != (source['head'], source['branch']):
            raise RequestError('source_changed', 'The source version changed; list sources and plan again.')
        print(f'Repository: {repository["name"]}', file=log)
        print(f'Source: {source["branch"]} @ {source["head"][:8]}' +
              (' (includes local edits)' if source['dirty'] else ''), file=log)
        print(f'Folder: {source["path"]}', file=log)
        if source['manifest']['kind'] == 'tooling':
            print(f'Manifest: {source["manifest"]["path"]} (this checkout has none)', file=log)
        print('Building: ' + ', '.join(products), file=log, flush=True)
        try:
            planned = builds.plan(source, manifest, products, options, preview)
            code = builds.execute(planned, preview, log)
            response = dict(repository=_summary(repository), source=source, jobs=planned['jobs'], exit_code=code)
            if code:
                response.update(status='failed', error=dict(
                    code='workflow_failed', message='Build/test did not complete. See the log; outputs are kept in the checkout.'))
                return response, code
            if preview:
                response['status'] = 'previewed'
                return response, 0
            prepared = builds.finish(source, manifest, planned, products, options)
        except builds.ProviderError as error:
            raise RequestError(error.code, str(error))
        except installation.InstallationError as error:
            raise RequestError('install_failed', str(error))
        response.update(_install(prepared, mode, staging_root, log))
        if response['status'] == 'awaiting_install' and mode == 'deferred':
            print('Build and tests completed; installation is pending.', file=log, flush=True)
        return response, 0


def plan(request):
    repository, source, manifest = _source(request)
    products, options = _selection(request, source)
    try:
        planned = builds.plan(source, manifest, products, options, preview=False)
    except builds.ProviderError as error:
        raise RequestError(error.code, str(error))
    return dict(status='planned', repository=_summary(repository), source=source, jobs=planned['jobs'])


def ready(staging_root):
    listed, problems = [], []
    for repository in sources.repositories():
        groups = {}
        for source in repository['sources']:
            if source['manifest'] and not source['problem']:
                groups.setdefault(source['manifest']['path'], []).append(source)
        for root, members in groups.items():
            try:
                manifest = manifests.load(root)
                for source, item in builds.ready(manifest, root, members, staging_root):
                    listed.append(dict(item, repository=_summary(repository), source=source))
            except (builds.ProviderError, manifests.ManifestError, OSError) as error:
                problems.append(f'{repository["name"]}: {error}')
    return dict(status='listed', builds=listed, problems=problems)


def install(request, log, staging_root):
    repository, source, manifest = _source(request)
    build_key, build_id = _string(request, 'build'), _string(request, 'build_id')
    mode = request.get('install_mode', 'deferred')
    with locked(Path(source['path']) / manifest['lock']):
        try:
            prepared = builds.ready_installation(source, manifest, build_key, build_id)
        except builds.ProviderError as error:
            raise RequestError(error.code, str(error))
        except installation.InstallationError as error:
            raise RequestError('install_failed', str(error))
        return dict(repository=_summary(repository), source=source, build=build_key, **_install(
            prepared, mode, staging_root, log)), 0


def handle(request, log=sys.stderr, staging_root=None):
    if not isinstance(request, dict):
        raise RequestError('invalid_request', 'Request must be a JSON object.')
    if type(request.get('api_version')) is not int or request['api_version'] != API_VERSION:
        raise RequestError('unsupported_version', f'api_version must be {API_VERSION}.')
    operation = request.get('operation')
    if operation not in OPERATIONS:
        raise RequestError('invalid_request', 'operation must be one of ' + ', '.join(OPERATIONS) + '.')
    unknown = set(request) - {'api_version', 'operation', *OPERATIONS[operation]}
    if unknown:
        raise RequestError('invalid_request', 'Unknown fields: ' + ', '.join(sorted(unknown)))
    if sys.platform != 'darwin':
        raise RequestError('unsupported_platform', 'omso Builder requires macOS.')
    for key in ('dry_run',):
        if key in request and type(request[key]) is not bool:
            raise RequestError('invalid_request', key + ' must be a boolean.')
    if request.get('install_mode', 'deferred') not in INSTALL_MODES:
        raise RequestError('invalid_request', 'install_mode must be deferred or sudo.')
    if operation == 'repositories':
        result = dict(status='listed', repositories=config.load(), config=str(config.path()))
    elif operation == 'add_repository':
        try:
            entry = config.add(_absolute(request, 'path'), _string(request, 'tooling', required=False))
        except (ValueError, subprocess.SubprocessError, OSError):
            raise RequestError('invalid_request', 'path must be a checkout of a Git repository.')
        result = dict(status='added', repository=entry)
    elif operation == 'remove_repository':
        try:
            config.remove(_absolute(request, 'path'))
        except LookupError as error:
            raise RequestError('invalid_request', str(error))
        result = dict(status='removed')
    elif operation == 'sources':
        result = dict(status='listed', repositories=sources.repositories())
    elif operation == 'plan':
        result = plan(request)
    elif operation == 'ready':
        result = ready(staging_root)
    elif operation == 'build':
        result, code = build(request, log, staging_root)
        return dict(api_version=API_VERSION, ok=code == 0, operation=operation, **result), code
    else:
        result, code = install(request, log, staging_root)
        return dict(api_version=API_VERSION, ok=True, operation=operation, **result), code
    return dict(api_version=API_VERSION, ok=True, operation=operation, **result), 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staging-root', type=Path,
                        help='Testing only: install into this folder without privileges instead of the system')
    args = parser.parse_args(argv)
    staging_root = str(args.staging_root.resolve()) if args.staging_root else None
    try:
        response, code = handle(json.load(sys.stdin), staging_root=staging_root)
    except RequestError as error:
        code = 2 if error.code in ('invalid_request', 'unsupported_version') else 1
        response = dict(api_version=API_VERSION, ok=False, error=dict(code=error.code, message=str(error)))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        code = 1
        response = dict(api_version=API_VERSION, ok=False, error=dict(code='request_failed', message=str(error)))
    except KeyboardInterrupt:
        code = 130
        response = dict(api_version=API_VERSION, ok=False, error=dict(code='interrupted', message='Interrupted.'))
    print(json.dumps(response), flush=True)
    return code
