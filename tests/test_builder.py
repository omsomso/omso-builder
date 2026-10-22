"""Behavioral tests for omso Builder's core and JSON API, using temporary repositories."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from omso_builder import installation, manifest as manifests  # noqa: E402

API = ROOT / 'scripts/build-api.py'

# A steps product: "builds" a tiny app bundle with a version file, then "tests" it.
MAKE_APP = ('import pathlib, sys; app = pathlib.Path("build/out/Demo.app/Contents/MacOS"); '
            'app.mkdir(parents=True, exist_ok=True); (app / "Demo").write_text(open("version.txt").read()); '
            '(app.parent / "Info.plist").write_text("plist")')
STEPS_MANIFEST = dict(schema=1, name='Demo app', notes=['Installs Demo.app.'], products=[dict(
    name='Demo', configuration='Release',
    steps=[dict(name='Build', run=['python3', '-c', MAKE_APP]),
           dict(name='Test', run=['python3', '-c', 'import os, sys; sys.exit(0 if os.environ["TMPDIR"].endswith("/tmp") '
                                  'and os.path.isdir(os.environ["TMPDIR"]) else 3)'], env=dict(TMPDIR='{tmp}')),
           dict(name='Self-test', run=['python3', '-c', 'import os, sys; os.mkdir(sys.argv[1])', '{run}/smoke'])],
    install=[dict(copy='build/out/Demo.app', to='/Applications/omso')])])

PROVIDER = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Fake provider (protocol 1): products from catalog.json; installs build/<x>/Thing.vst3."""
    import json, pathlib, sys
    request = json.load(sys.stdin)
    assert request["protocol"] == 1
    operation = request["operation"]
    def reply(**fields):
        print(json.dumps(dict(ok=True, **fields))); sys.exit(0)
    if operation == "plan":
        source = request["source_dir"]
        flag = "--wide" if request["options"].get("wide") else "--narrow"
        command = ["python3", "-c", "import pathlib, sys; p = pathlib.Path('build/plug/Thing.vst3'); "
                   "p.mkdir(parents=True, exist_ok=True); (p / 'bin').write_text(sys.argv[1])", flag]
        if request["preview"]:
            command = ["echo", "preview", flag]
        reply(jobs=[dict(name="Plugins: " + ", ".join(request["products"]), run=command, cwd=source)])
    if operation in ("installation", "ready_installation"):
        source = pathlib.Path(request["source_dir"])
        if operation == "ready_installation" and request["build_id"] != "b1":
            print(json.dumps(dict(ok=False, error=dict(code="build_not_ready", message="Gone")))); sys.exit(1)
        reply(installation=dict(actions=[dict(copy=str(source / "build/plug/Thing.vst3"),
                                              to="/Library/Audio/Plug-Ins/VST3")],
                                files=[str(source / "build/plug/Thing.vst3/bin")], notes=["Rescan your DAW."]))
    if operation == "ready":
        builds = []
        for source in request["source_dirs"]:
            if (pathlib.Path(source) / "build/plug/Thing.vst3/bin").is_file():
                builds.append(dict(source_dir=source, id="release", build_id="b1", products=["Thing"],
                                   configuration="Release", version="1.0", revision="", dirty=False,
                                   created=1, installed=[], installer_running=False))
        reply(builds=builds)
    print(json.dumps(dict(ok=False, error=dict(code="unknown", message=operation)))); sys.exit(1)
    ''')
PROVIDER_MANIFEST = dict(schema=1, name='Demo plugins', lock='build/.finder-build.lock',
                         products=dict(file='catalog.json'),
                         options=[dict(id='wide', label='Wide', default=True, products=['Thing'])],
                         provider=dict(run=['python3', '{manifest}/provider.py']))


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True, stderr=subprocess.STDOUT).strip()


class BuilderTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.config = self.base / 'repositories.json'
        self.staging = self.base / 'staging'
        self.environment = dict(os.environ, OMSO_BUILDER_CONFIG=str(self.config),
                                GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        os.environ['OMSO_BUILDER_CONFIG'] = str(self.config)

    def tearDown(self):
        os.environ.pop('OMSO_BUILDER_CONFIG', None)
        self.temporary.cleanup()

    def repository(self, name, files):
        root = self.base / name
        root.mkdir()
        git(root, 'init', '-q', '-b', 'main')
        git(root, 'config', 'user.email', 'test@example.invalid')
        git(root, 'config', 'user.name', 'Test')
        (root / '.gitignore').write_text('build/\n')
        for path, content in files.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(content if isinstance(content, str) else json.dumps(content))
        git(root, 'add', '-A')
        git(root, 'commit', '-q', '-m', 'Initial')
        return root

    def api(self, request, staging=True, expect=0):
        command = [sys.executable, str(API)] + (['--staging-root', str(self.staging)] if staging else [])
        result = subprocess.run(command, input=json.dumps(dict(api_version=2, **request)), text=True,
                                capture_output=True, env=self.environment, timeout=120)
        response = json.loads(result.stdout)
        self.assertEqual(result.returncode, expect, result.stderr + result.stdout)
        response['_log'] = result.stderr
        return response

    def add(self, root):
        self.assertEqual(self.api(dict(operation='add_repository', path=str(root)))['status'], 'added')


class StepsFormTests(BuilderTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.repository('demo', {'omso-build.json': STEPS_MANIFEST, 'version.txt': 'v1'})
        self.add(self.root)

    def source(self, response, path=None):
        repository = next(item for item in response['repositories'] if item['path'] == str(self.root))
        return next(item for item in repository['sources'] if item['path'] == str(path or self.root))

    def test_sources_list_products_and_notes(self):
        response = self.api(dict(operation='sources'))
        repository = response['repositories'][0]
        self.assertEqual(repository['name'], 'Demo app')
        source = self.source(response)
        self.assertEqual(source['products'], ['Demo'])
        self.assertEqual(source['manifest'], dict(path=str(self.root), kind='own'))
        self.assertEqual(source['notes'], ['Installs Demo.app.'])
        self.assertTrue(source['primary'])
        self.assertFalse(source['dirty'])

    def test_plan_expands_placeholders_without_writing(self):
        response = self.api(dict(operation='plan', source_dir=str(self.root), products=['Demo']))
        self.assertEqual(response['status'], 'planned')
        self.assertEqual([job['name'] for job in response['jobs']], ['Demo: Build', 'Demo: Test', 'Demo: Self-test'])
        self.assertTrue(response['jobs'][1]['env']['TMPDIR'].startswith(str(self.root / 'build/omso-builder/runs/')))
        self.assertFalse((self.root / 'build').exists())

    def test_preview_runs_nothing(self):
        response = self.api(dict(operation='build', source_dir=str(self.root), products=['Demo'], dry_run=True))
        self.assertEqual(response['status'], 'previewed')
        self.assertIn('Preview only', response['_log'])
        self.assertFalse((self.root / 'build/out').exists())

    def test_build_install_ready_and_rebuild_invalidation(self):
        head = git(self.root, 'rev-parse', 'HEAD')
        response = self.api(dict(operation='build', source_dir=str(self.root), products=['Demo'],
                                 expected_head=head, expected_branch='main'))
        self.assertEqual(response['status'], 'awaiting_install', response['_log'])
        prepared = response['installation']
        self.assertEqual(prepared['summary'], ['Copy Demo.app to /Applications/omso'])
        self.assertIn('/usr/bin/ditto', prepared['script'])
        self.assertEqual(prepared['lock_path'], str(self.root / 'build/.omso-builder.lock'))
        self.assertIn(str(self.root / 'build/out/Demo.app/Contents/MacOS/Demo'), prepared['fingerprints'])
        self.assertTrue(list((self.root / 'build/omso-builder/runs').glob('*/smoke')))

        ready = self.api(dict(operation='ready'))['builds']
        self.assertEqual(len(ready), 1)
        build = ready[0]
        self.assertEqual((build['id'], build['products'], build['installed'], build['configuration']),
                         ('Demo', ['Demo'], [], 'Release'))
        self.assertEqual(build['repository']['name'], 'Demo app')

        installed = self.api(dict(operation='install', source_dir=str(self.root), build=build['id'],
                                  build_id=build['build_id'], install_mode='sudo'))
        self.assertEqual(installed['status'], 'completed', installed['_log'])
        app = self.staging / 'Applications/omso/Demo.app/Contents/MacOS/Demo'
        self.assertEqual(app.read_text(), 'v1')
        self.assertEqual(self.api(dict(operation='ready'))['builds'][0]['installed'], ['Demo'])

        # A later plain rebuild changes the bundle: the old receipt no longer counts as ready.
        (self.root / 'version.txt').write_text('v2')
        subprocess.run(['python3', '-c', MAKE_APP], cwd=self.root, check=True)
        self.assertEqual(self.api(dict(operation='ready'))['builds'], [])
        failed = self.api(dict(operation='install', source_dir=str(self.root), build=build['id'],
                               build_id=build['build_id']), expect=1)
        self.assertEqual(failed['error']['code'], 'build_not_ready')

    def test_build_then_sudo_mode_installs_in_staging(self):
        response = self.api(dict(operation='build', source_dir=str(self.root), products=['Demo'], install_mode='sudo'))
        self.assertEqual(response['status'], 'completed', response['_log'])
        self.assertTrue((self.staging / 'Applications/omso/Demo.app/Contents/Info.plist').is_file())
        self.assertIn('Installing: Copy Demo.app to /Applications/omso', response['_log'])

    def test_failed_step_stops_and_reports(self):
        (self.root / 'version.txt').unlink()
        response = self.api(dict(operation='build', source_dir=str(self.root), products=['Demo']), expect=1)
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(response['error']['code'], 'workflow_failed')
        self.assertNotIn('installation', response)
        self.assertEqual(self.api(dict(operation='ready'))['builds'], [])

    def test_changed_source_and_unknown_product_are_rejected(self):
        response = self.api(dict(operation='build', source_dir=str(self.root), products=['Demo'],
                                 expected_head='0' * 40), expect=1)
        self.assertEqual(response['error']['code'], 'source_changed')
        response = self.api(dict(operation='plan', source_dir=str(self.root), products=['Other']), expect=1)
        self.assertEqual(response['error']['code'], 'unknown_product')
        response = self.api(dict(operation='plan', source_dir=str(self.root), products=['Demo'],
                                 options=dict(wide=True)), expect=2)
        self.assertEqual(response['error']['code'], 'invalid_request')

    def test_busy_checkout_refuses_a_second_build(self):
        lock = self.root / 'build/.omso-builder.lock'
        lock.parent.mkdir(parents=True)
        with lock.open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            response = self.api(dict(operation='build', source_dir=str(self.root), products=['Demo']), expect=1)
        self.assertEqual(response['error']['code'], 'busy')

    def test_local_edits_and_worktrees_are_listed(self):
        worktree = self.base / 'demo-feature'
        git(self.root, 'worktree', 'add', '-q', '-b', 'feature', str(worktree))
        (worktree / 'version.txt').write_text('feature edit')
        source = self.source(self.api(dict(operation='sources')), worktree)
        self.assertEqual((source['branch'], source['dirty'], source['primary']), ('feature', True, False))
        response = self.api(dict(operation='build', source_dir=str(worktree), products=['Demo'], install_mode='sudo'))
        self.assertEqual(response['status'], 'completed', response['_log'])
        self.assertEqual((self.staging / 'Applications/omso/Demo.app/Contents/MacOS/Demo').read_text(), 'feature edit')

    def test_unregistered_and_relative_sources_are_rejected(self):
        other = self.repository('other', {'omso-build.json': STEPS_MANIFEST})
        response = self.api(dict(operation='plan', source_dir=str(other), products=['Demo']), expect=1)
        self.assertEqual(response['error']['code'], 'source_unavailable')
        response = self.api(dict(operation='plan', source_dir='demo', products=['Demo']), expect=2)
        self.assertEqual(response['error']['code'], 'invalid_request')
        response = self.api(dict(operation='ready', extra=True), expect=2)
        self.assertIn('Unknown fields', response['error']['message'])


class ProviderFormTests(BuilderTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.repository('plugins', {'omso-build.json': PROVIDER_MANIFEST, 'provider.py': PROVIDER,
                                                'catalog.json': {'Thing': 'Thing plugin'}})
        self.add(self.root)

    def test_options_reach_the_provider_and_installation_is_validated(self):
        source = self.api(dict(operation='sources'))['repositories'][0]['sources'][0]
        self.assertEqual(source['options'][0]['id'], 'wide')
        self.assertEqual(source['products'], ['Thing'])
        preview = self.api(dict(operation='build', source_dir=str(self.root), products=['Thing'], dry_run=True))
        self.assertEqual(preview['status'], 'previewed')
        self.assertIn('preview --wide', preview['_log'])
        response = self.api(dict(operation='build', source_dir=str(self.root), products=['Thing'],
                                 options=dict(wide=False), install_mode='sudo'))
        self.assertEqual(response['status'], 'completed', response['_log'])
        self.assertEqual((self.staging / 'Library/Audio/Plug-Ins/VST3/Thing.vst3/bin').read_text(), '--narrow')
        self.assertIn('Rescan your DAW.', response['_log'])

    def test_ready_builds_come_from_the_provider(self):
        self.api(dict(operation='build', source_dir=str(self.root), products=['Thing']))
        builds = self.api(dict(operation='ready'))['builds']
        self.assertEqual([(build['id'], build['build_id'], build['source']['path']) for build in builds],
                         [('release', 'b1', str(self.root))])
        response = self.api(dict(operation='install', source_dir=str(self.root), build='release', build_id='old'),
                            expect=1)
        self.assertEqual(response['error']['code'], 'build_not_ready')
        response = self.api(dict(operation='install', source_dir=str(self.root), build='release', build_id='b1'))
        self.assertEqual(response['status'], 'awaiting_install')

    def test_checkout_without_manifest_uses_the_main_checkout_manifest(self):
        older = self.base / 'plugins-older'
        git(self.root, 'worktree', 'add', '-q', '-b', 'older', str(older))
        (older / 'omso-build.json').unlink()
        (older / 'provider.py').unlink()
        (older / 'catalog.json').write_text(json.dumps({'Thing': 'Thing', 'OldThing': 'Old'}))
        repository = self.api(dict(operation='sources'))['repositories'][0]
        source = next(item for item in repository['sources'] if item['path'] == str(older))
        self.assertEqual(source['manifest'], dict(path=str(self.root), kind='tooling'))
        self.assertEqual(source['products'], ['Thing', 'OldThing'])
        response = self.api(dict(operation='build', source_dir=str(older), products=['Thing'], install_mode='sudo'))
        self.assertEqual(response['status'], 'completed', response['_log'])
        self.assertIn('this checkout has none', response['_log'])
        self.assertTrue((older / 'build/plug/Thing.vst3/bin').is_file())

    def test_shared_lock_excludes_the_repository_tools(self):
        lock = self.root / 'build/.finder-build.lock'
        lock.parent.mkdir(parents=True)
        with lock.open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            response = self.api(dict(operation='install', source_dir=str(self.root), build='release', build_id='b1'),
                                expect=1)
        self.assertEqual(response['error']['code'], 'busy')


class InstallationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve() / 'checkout'
        bundle = self.root / 'build/Demo.app/Contents'
        bundle.mkdir(parents=True)
        (bundle / 'Info.plist').write_text('plist')
        os.symlink('Info.plist', bundle / 'Link')
        self.source = dict(path=str(self.root), manifest=dict(path=str(self.root), kind='own'))

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self, **raw):
        return installation.prepare(raw, self.source, 'build/.lock')

    def rejects(self, **raw):
        with self.assertRaises(installation.InstallationError):
            self.prepare(**raw)

    def test_only_built_bundles_into_omso_locations(self):
        prepared = self.prepare(actions=[dict(copy='build/Demo.app', to='/Applications/omso')])
        self.assertEqual(list(prepared['fingerprints']), [str(self.root / 'build/Demo.app/Contents/Info.plist')])
        self.rejects(actions=[dict(copy='build/Demo.app', to='/Applications')])
        self.rejects(actions=[dict(copy='build/Demo.app', to='/Library/Audio/Plug-Ins/VST3')])
        self.rejects(actions=[dict(copy='/Applications/Safari.app', to='/Applications/omso')])
        self.rejects(actions=[dict(copy='build/../../outside.app', to='/Applications/omso')])
        self.rejects(actions=[])
        self.rejects(actions=[dict(run='rm -rf /')])
        self.rejects(actions=[dict(copy='build/Demo.app', to='/Applications/omso')], post_install=[['/bin/sh', '-c', 'x']])
        self.rejects(actions=[dict(cmake_install='build', config='Release', component='X', prefix='/Library/Audio/Plug-Ins')])
        (self.root / 'build/cmake_install.cmake').write_text('')
        self.prepare(actions=[dict(cmake_install='build', config='Release', component='OmsoPlugins',
                                   prefix='/Library/Audio/Plug-Ins')])
        self.rejects(actions=[dict(cmake_install='build', config='Release', component='X; rm', prefix='/tmp')])

    def test_symlinked_bundle_escape_is_rejected(self):
        outside = Path(self.temporary.name) / 'Outside.app'
        outside.mkdir()
        os.symlink(outside, self.root / 'build/Escape.app')
        self.rejects(actions=[dict(copy='build/Escape.app', to='/Applications/omso')])

    def test_changed_files_and_preconditions_block_installation(self):
        prepared = self.prepare(actions=[dict(copy='build/Demo.app', to='/Applications/omso')],
                                refuse_if_exists=[dict(path='/Library/X.component', message='Resolve X first.')])
        staging = Path(self.temporary.name) / 'staging'
        installation.verify(prepared, str(staging))
        (staging / 'Library/X.component').mkdir(parents=True)
        with self.assertRaisesRegex(installation.InstallationError, 'Resolve X first'):
            installation.verify(prepared, str(staging))
        (self.root / 'build/Demo.app/Contents/Info.plist').write_text('changed')
        with self.assertRaisesRegex(installation.InstallationError, 'changed'):
            installation.verify(prepared)

    def test_script_quotes_paths_and_replaces_atomically(self):
        bundle = self.root / "build/It's Demo.app"
        bundle.mkdir()
        (bundle / 'file').write_text('x')
        prepared = self.prepare(actions=[dict(copy="build/It's Demo.app", to='/Applications/omso')])
        self.assertIn("'/Applications/omso/.It'\"'\"'s Demo.app.omso-builder-new'", prepared['script'])
        staging = Path(self.temporary.name) / 'staging'
        with open(os.devnull, 'w') as log:
            self.assertTrue(installation.run(prepared, str(staging), log))
            (bundle / 'file').write_text('x')  # unchanged content: reinstall replaces cleanly
            self.assertTrue(installation.run(prepared, str(staging), log))
        installed = staging / "Applications/omso/It's Demo.app"
        self.assertEqual(installation.tree_digest(installed), installation.tree_digest(bundle))
        self.assertEqual([path.name for path in (staging / 'Applications/omso').iterdir()], ["It's Demo.app"])

    def test_tree_digest_covers_names_contents_and_links(self):
        bundle = self.root / 'build/Demo.app'
        first = installation.tree_digest(bundle)
        os.remove(bundle / 'Contents/Link')
        os.symlink('Other', bundle / 'Contents/Link')
        self.assertNotEqual(first, installation.tree_digest(bundle))


class ManifestTests(unittest.TestCase):
    def rejects(self, data, message):
        with self.assertRaisesRegex(manifests.ManifestError, message):
            manifests.validate(data)

    def test_valid_forms(self):
        self.assertEqual(manifests.validate(STEPS_MANIFEST)['lock'], 'build/.omso-builder.lock')
        provider = manifests.validate(PROVIDER_MANIFEST)
        self.assertEqual(provider['options'][0]['products'], ['Thing'])
        self.assertEqual(provider['lock'], 'build/.finder-build.lock')

    def test_invalid_manifests(self):
        self.rejects(dict(STEPS_MANIFEST, schema=2), 'newer omso Builder')
        self.rejects(dict(STEPS_MANIFEST, schema='1'), 'schema must be 1')
        self.rejects(dict(STEPS_MANIFEST, extra=1), 'unknown fields')
        self.rejects(dict(STEPS_MANIFEST, lock='/tmp/lock'), 'relative path')
        self.rejects(dict(STEPS_MANIFEST, options=[]), 'need a provider')
        self.rejects(dict(STEPS_MANIFEST, products=[]), 'nonempty')
        product = STEPS_MANIFEST['products'][0]
        self.rejects(dict(STEPS_MANIFEST, products=[product, product]), 'unique')
        self.rejects(dict(STEPS_MANIFEST, products=[dict(product, install=[dict(copy='../x.app', to='/Applications/omso')])]),
                     'inside the checkout')
        self.rejects(dict(STEPS_MANIFEST, products=[dict(product, steps=[dict(name='x', run=['{home}/x'])])]),
                     'unknown placeholder')
        self.rejects(dict(PROVIDER_MANIFEST, provider=dict(run=['{source}/x'])), 'unknown placeholder')
        self.rejects(dict(PROVIDER_MANIFEST, options=[dict(id='Wide', label='x')]), 'lowercase')


if __name__ == '__main__':
    unittest.main()
