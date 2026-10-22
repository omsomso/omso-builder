"""Planning, running and finishing builds for both manifest forms.

Steps form: the builder runs each selected product's steps in the source
checkout, then records a receipt (build/omso-builder/ready/) holding a digest of
every bundle to install. The receipt marks a ready build while those bundles
are unchanged.

Provider form: the repository's script returns jobs, the installation and its
ready builds (protocol 1, docs/MANIFEST.md). The builder runs the jobs.
"""
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import time

from . import installation, manifest as manifests

RECEIPTS = 'build/omso-builder/ready'
RUNS = 'build/omso-builder/runs'
PROTOCOL = 1


class ProviderError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def provider_call(manifest, manifest_root, request, timeout=300):
    """One JSON request to the repository's provider script."""
    command = [manifests.expand(item, dict(manifest=manifest_root)) for item in manifest['provider']['run']]
    try:
        result = subprocess.run(command, cwd=manifest_root, input=json.dumps(dict(protocol=PROTOCOL, **request)),
                                text=True, capture_output=True, env=installation.environment(), timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        raise ProviderError('provider_failed', f'The repository build script could not run ({error}).')
    try:
        response = json.loads(result.stdout)
    except ValueError:
        response = None
    if not isinstance(response, dict):
        detail = result.stderr.strip()[-600:]
        raise ProviderError('provider_failed', 'The repository build script returned no result.' +
                            (' ' + detail if detail else ''))
    if result.returncode or response.get('ok') is not True:
        error = response.get('error') if isinstance(response.get('error'), dict) else {}
        raise ProviderError(str(error.get('code') or 'provider_failed'),
                            str(error.get('message') or 'The repository build script reported a failure.'))
    return response


def _jobs(jobs, roots):
    if not isinstance(jobs, list) or not jobs:
        raise ProviderError('provider_failed', 'The repository build script planned no jobs.')
    checked = []
    for job in jobs:
        if (not isinstance(job, dict) or set(job) - {'name', 'run', 'cwd', 'env'} or
                not isinstance(job.get('name'), str) or not isinstance(job.get('run'), list) or not job['run'] or
                any(not isinstance(item, str) for item in job['run']) or not isinstance(job.get('cwd'), str) or
                not any(Path(job['cwd']).resolve() == root for root in roots) or
                not isinstance(job.get('env', {}), dict) or
                any(not isinstance(key, str) or not isinstance(value, str) for key, value in job.get('env', {}).items())):
            raise ProviderError('provider_failed', 'The repository build script planned an invalid job.')
        checked.append(dict(name=job['name'], run=job['run'], cwd=job['cwd'], env=job.get('env', {})))
    return checked


def plan(source, manifest, products, options, preview):
    """{form, jobs, run_dir}. Products run in manifest order."""
    root = Path(source['path'])
    manifest_root = source['manifest']['path']
    if 'provider' in manifest:
        response = provider_call(manifest, manifest_root, dict(
            operation='plan', source_dir=str(root), products=products, options=options, preview=preview))
        roots = {root.resolve(), Path(manifest_root).resolve()}
        return dict(form='provider', jobs=_jobs(response.get('jobs'), roots), run_dir=None)
    run_dir = root / RUNS / (time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3))
    values = dict(source=str(root), manifest=manifest_root, jobs=str(os.cpu_count() or 4),
                  run=str(run_dir), tmp=str(run_dir / 'tmp'))
    jobs = []
    for product in manifest['products']:
        if product['name'] not in products:
            continue
        for step in product['steps']:
            jobs.append(dict(name=product['name'] + ': ' + step['name'],
                             run=[manifests.expand(item, values) for item in step['run']],
                             cwd=str(root / step['cwd']) if 'cwd' in step else str(root),
                             env={key: manifests.expand(value, values) for key, value in step.get('env', {}).items()}))
    return dict(form='steps', jobs=jobs, run_dir=str(run_dir))


def execute(planned, preview, log):
    """Run the jobs in order and stop at the first failure. Returns the exit code."""
    if preview and planned['form'] == 'steps':
        for job in planned['jobs']:
            print(f'== {job["name"]}\n+ {shlex.join(job["run"])}', file=log)
        print('Preview only: nothing was built or installed.', file=log, flush=True)
        return 0
    if planned['run_dir']:
        Path(planned['run_dir'], 'tmp').mkdir(parents=True)
    for job in planned['jobs']:
        print(f'\n== {job["name"]}\n+ {shlex.join(job["run"])}', file=log, flush=True)
        started = time.monotonic()
        try:
            code = subprocess.run(job['run'], cwd=job['cwd'], env=installation.environment(job['env']),
                                  stdin=subprocess.DEVNULL, stdout=log, stderr=log).returncode
        except OSError as error:
            print(f'Could not start {job["run"][0]}: {error}', file=log, flush=True)
            code = 127
        print(f'Step elapsed: {round(time.monotonic() - started)} seconds (exit {code}).', file=log, flush=True)
        if code:
            return code if code > 0 else 128 - code
    return 0


def _copies(manifest, root, products):
    return [dict(product=product['name'], copy=str(root / item['copy']), to=item['to'])
            for product in manifest['products'] if product['name'] in products for item in product['install']]


def _receipt_id(products):
    return '+'.join(sorted(products)).replace('/', '_')


def finish(source, manifest, planned, products, options):
    """After a successful run: the prepared installation (and, for steps, its receipt)."""
    root = Path(source['path'])
    if planned['form'] == 'provider':
        response = provider_call(manifest, source['manifest']['path'], dict(
            operation='installation', source_dir=str(root), products=products, options=options))
        return installation.prepare(response.get('installation'), source, manifest['lock'])
    copies = _copies(manifest, root, products)
    prepared = installation.prepare(dict(actions=[dict(copy=item['copy'], to=item['to']) for item in copies]),
                                    source, manifest['lock'])
    receipt = dict(schema=1, id=_receipt_id(products), build_id=secrets.token_hex(8), products=products,
                   configuration=next((product.get('configuration', '') for product in manifest['products']
                                       if product['name'] in products), ''),
                   built=int(time.time()), head=source['head'], branch=source['branch'], dirty=source['dirty'],
                   copies=[dict(item, digest=installation.tree_digest(item['copy'])) for item in copies])
    folder = root / RECEIPTS
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / ('.' + receipt['id'] + '.json')
    temporary.write_text(json.dumps(receipt, indent=2) + '\n')
    os.replace(temporary, folder / (receipt['id'] + '.json'))
    return prepared


def _installed_copy(item, staging_root):
    folder = Path(staging_root) / item['to'].lstrip('/') if staging_root else Path(item['to'])
    return folder / Path(item['copy']).name


def _receipt(root, build_id_key):
    """A steps receipt whose bundles are unchanged, or None."""
    if not build_id_key or '/' in build_id_key or build_id_key.startswith('.'):
        return None
    file = Path(root) / RECEIPTS / (build_id_key + '.json')
    try:
        receipt = json.loads(file.read_text())
        if receipt.get('schema') != 1 or receipt['id'] != build_id_key or not receipt['copies']:
            return None
        for item in receipt['copies']:
            if not str(Path(item['copy']).resolve()).startswith(str(Path(root).resolve()) + '/'):
                return None
            if installation.tree_digest(item['copy']) != item['digest']:
                return None
        return receipt
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _steps_ready(source, staging_root):
    builds = []
    for file in sorted((Path(source['path']) / RECEIPTS).glob('*.json')):
        receipt = _receipt(source['path'], file.stem)
        if receipt is None:
            continue
        installed = []
        for product in receipt['products']:
            copies = [item for item in receipt['copies'] if item['product'] == product]
            try:
                if all(installation.tree_digest(_installed_copy(item, staging_root)) == item['digest'] for item in copies):
                    installed.append(product)
            except (OSError, ValueError):
                pass
        builds.append(dict(id=receipt['id'], build_id=receipt['build_id'], products=receipt['products'],
                           configuration=receipt['configuration'], version='', revision=receipt['head'],
                           dirty=receipt['dirty'], created=receipt['built'], installed=installed,
                           installer_running=False))
    return builds


READY_FIELDS = {'source_dir', 'id', 'build_id', 'products', 'configuration', 'version', 'revision', 'dirty',
                'created', 'installed', 'installer_running'}


def ready(manifest, manifest_root, members, staging_root=None):
    """[(source, build)] for the given sources that share one manifest."""
    if 'provider' not in manifest:
        return [(source, build) for source in members for build in _steps_ready(source, staging_root)]
    response = provider_call(manifest, manifest_root, dict(operation='ready',
                                                           source_dirs=[source['path'] for source in members]))
    by_path = {source['path']: source for source in members}
    result = []
    for build in response.get('builds') or []:
        if (not isinstance(build, dict) or set(build) != READY_FIELDS or build['source_dir'] not in by_path or
                not isinstance(build['id'], str) or not isinstance(build['build_id'], str) or
                not isinstance(build['products'], list) or not isinstance(build['installed'], list)):
            raise ProviderError('provider_failed', 'The repository build script listed an invalid ready build.')
        result.append((by_path[build['source_dir']], {key: value for key, value in build.items() if key != 'source_dir'}))
    return result


def ready_installation(source, manifest, build, build_id):
    """The prepared installation of one ready build, without rebuilding it."""
    if 'provider' in manifest:
        response = provider_call(manifest, source['manifest']['path'], dict(
            operation='ready_installation', source_dir=source['path'], id=build, build_id=build_id))
        return installation.prepare(response.get('installation'), source, manifest['lock'])
    receipt = _receipt(source['path'], build)
    if receipt is None or receipt['build_id'] != build_id:
        raise ProviderError('build_not_ready', 'That build is no longer ready. Refresh the list.')
    return installation.prepare(dict(actions=[dict(copy=item['copy'], to=item['to']) for item in receipt['copies']]),
                                source, manifest['lock'])
