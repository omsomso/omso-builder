"""The privileged step: validated install actions, fingerprints and the install script.

A repository says what to install; this policy says where. Only bundles and
install scripts built inside the selected checkout are installed, and only into
the standard omso locations. Fingerprints bind approval to the files that passed
the build: any change between building and installing requires a rebuild.
"""
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys

# Prefixes for `cmake --install`, and the folders a bundle copy may replace.
CMAKE_PREFIXES = ('/Library/Audio/Plug-Ins', '/Applications')
COPY_DESTINATIONS = {
    '/Applications/omso': '.app',
    '/Library/Audio/Plug-Ins/VST3': '.vst3',
    '/Library/Audio/Plug-Ins/Components': '.component',
    '/Library/Audio/Plug-Ins/CLAP': '.clap',
}
CONFIGURATIONS = ('Debug', 'Release')
COMPONENT = re.compile(r'^[A-Za-z0-9_.-]+$')
PATH_PREFIX = ['/opt/homebrew/bin', '/usr/local/bin']


class InstallationError(ValueError):
    pass


def environment(extra=None):
    """The process environment with Homebrew tools on PATH (apps start with a minimal PATH).

    /usr/bin/python3, which runs the builder, is an xcrun stub that exports the
    Command Line Tools SDK as SDKROOT. A Terminal build has none, and Xcode-generator
    builds fail with it, so jobs do not inherit it.
    """
    env = dict(os.environ)
    if env.get('SDKROOT', '').startswith('/Library/Developer/CommandLineTools/'):
        del env['SDKROOT']
    parts = [part for part in env.get('PATH', '/usr/bin:/bin:/usr/sbin:/sbin').split(':') if part]
    env['PATH'] = ':'.join(PATH_PREFIX + [part for part in parts if part not in PATH_PREFIX])
    env['PYTHONUNBUFFERED'] = '1'
    env.update(extra or {})
    return env


def _inside(path, root):
    resolved = Path(path).resolve()
    return resolved == root or root in resolved.parents


def _absolute(value, root, what):
    if not isinstance(value, str) or not value:
        raise InstallationError(f'{what} must be a path.')
    path = Path(value) if os.path.isabs(value) else root / value
    if not _inside(path, root):
        raise InstallationError(f'{what} must be inside the selected checkout.')
    return path


def tree_files(path):
    """Regular files of a bundle (symlinks are copied as links and not hashed)."""
    return sorted(item for item in Path(path).rglob('*')
                  if stat.S_ISREG(item.lstat().st_mode))


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(path):
    """One digest for a bundle's names, file contents and symlink targets."""
    root = Path(path)
    if not root.is_dir() or root.is_symlink():
        raise FileNotFoundError(str(root))
    digest = hashlib.sha256()
    for item in sorted(root.rglob('*')):
        mode = item.lstat().st_mode
        relative = str(item.relative_to(root)).encode()
        if stat.S_ISLNK(mode):
            digest.update(b'L\0' + relative + b'\0' + os.readlink(item).encode() + b'\0')
        elif stat.S_ISREG(mode):
            digest.update(b'F\0' + relative + b'\0' + file_digest(item).encode() + b'\0')
    return digest.hexdigest()


def prepare(raw, source, lock):
    """Validate a repository's installation request and bind it to the current files."""
    if not isinstance(raw, dict) or set(raw) - {'actions', 'post_install', 'files', 'refuse_if_exists', 'notes'}:
        raise InstallationError('The repository returned an invalid installation.')
    root = Path(source['path']).resolve()
    tooling = Path(source['manifest']['path']).resolve()
    actions, files, summary = [], [], []
    if not isinstance(raw.get('actions'), list) or not raw['actions']:
        raise InstallationError('The installation has nothing to install.')
    for action in raw['actions']:
        if isinstance(action, dict) and set(action) == {'copy', 'to'}:
            bundle = _absolute(action['copy'], root, 'An installed bundle')
            suffix = COPY_DESTINATIONS.get(action['to'])
            if suffix is None:
                raise InstallationError(f'Bundles may be installed only into {", ".join(COPY_DESTINATIONS)}.')
            if bundle.suffix != suffix or not bundle.is_dir() or bundle.is_symlink():
                raise InstallationError(f'{bundle.name} is not a built {suffix} bundle.')
            actions.append(dict(copy=str(bundle), to=action['to']))
            files += tree_files(bundle)
            summary.append(f'Copy {bundle.name} to {action["to"]}')
        elif isinstance(action, dict) and set(action) == {'cmake_install', 'config', 'component', 'prefix'}:
            build = _absolute(action['cmake_install'], root, 'A CMake build folder')
            if not (build / 'cmake_install.cmake').is_file():
                raise InstallationError(f'{build} has no generated install instructions.')
            if (action['config'] not in CONFIGURATIONS or not isinstance(action['component'], str) or
                    not COMPONENT.match(action['component']) or action['prefix'] not in CMAKE_PREFIXES):
                raise InstallationError('Unexpected CMake installation request.')
            actions.append(dict(cmake_install=str(build), config=action['config'],
                                component=action['component'], prefix=action['prefix']))
            files.append(build / 'cmake_install.cmake')
            summary.append(f'Install {action["component"]} ({action["config"]}) to {action["prefix"]}')
        else:
            raise InstallationError('Unknown installation action.')
    listed = raw.get('files', [])
    if not isinstance(listed, list):
        raise InstallationError('files must be a list of paths.')
    for value in listed:
        file = _absolute(value, root, 'A fingerprinted file')
        if not file.is_file():
            raise InstallationError(f'Missing build output: {file}')
        files.append(file)
    post_install = raw.get('post_install', [])
    if (not isinstance(post_install, list) or
            any(not isinstance(command, list) or not command or any(not isinstance(item, str) for item in command)
                or not os.path.isabs(command[0])
                or not (_inside(command[0], root) or _inside(command[0], tooling)) for command in post_install)):
        raise InstallationError('Follow-up commands must be scripts inside the checkout.')
    refusals = raw.get('refuse_if_exists', [])
    if (not isinstance(refusals, list) or
            any(not isinstance(item, dict) or set(item) != {'path', 'message'} or
                not isinstance(item['path'], str) or not os.path.isabs(item['path']) or
                not isinstance(item['message'], str) for item in refusals)):
        raise InstallationError('Invalid installation precondition.')
    notes = raw.get('notes', [])
    if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
        raise InstallationError('notes must be a list of strings.')
    fingerprints = {str(file): file_digest(file) for file in dict.fromkeys(files)}
    return dict(source=str(root), lock_path=str(root / lock), actions=actions, summary=summary,
                fingerprints=fingerprints, post_install=post_install, refuse_if_exists=refusals,
                notes=notes, script=script(actions))


def _staged(path, staging_root):
    return str(Path(staging_root) / path.lstrip('/')) if staging_root else path


def script(actions, staging_root=None):
    """Shell commands for the approved installation (run as root unless staging)."""
    quote = shlex.quote
    lines = ['set -e']
    for action in actions:
        if 'copy' in action:
            name = Path(action['copy']).name
            folder = _staged(action['to'], staging_root)
            target, temporary = folder + '/' + name, folder + '/.' + name + '.omso-builder-new'
            lines += [f'/bin/mkdir -p {quote(folder)}', f'/bin/rm -rf {quote(temporary)}',
                      f'/usr/bin/ditto {quote(action["copy"])} {quote(temporary)}',
                      f'/bin/rm -rf {quote(target)}', f'/bin/mv {quote(temporary)} {quote(target)}']
        else:
            cmake = shutil.which('cmake', path=environment()['PATH'])
            if not cmake:
                raise InstallationError('CMake is unavailable for installation.')
            lines.append(' '.join(quote(item) for item in [
                cmake, '--install', action['cmake_install'], '--config', action['config'],
                '--component', action['component'], '--prefix', _staged(action['prefix'], staging_root)]))
    return '\n'.join(lines) + '\n'


def verify(prepared, staging_root=None):
    """Refuse if a precondition fails or any fingerprinted file changed."""
    for refusal in prepared['refuse_if_exists']:
        if os.path.lexists(_staged(refusal['path'], staging_root)):
            raise InstallationError(refusal['message'])
    for path, expected in prepared['fingerprints'].items():
        try:
            actual = file_digest(path)
        except OSError:
            actual = None
        if actual != expected:
            raise InstallationError('The built files changed. Build again before installing.')


def run(prepared, staging_root=None, log=sys.stderr):
    """Install without a dialog: into a staging folder, or with `sudo -n`.

    Returns False when sudo needs a password (nothing was installed); raises when
    installation started and failed.
    """
    verify(prepared, staging_root)
    log.flush()
    if staging_root:
        command = ['/bin/sh', '-c', script(prepared['actions'], staging_root)]
    else:
        command = ['/usr/bin/sudo', '-n', '/bin/sh', '-c', prepared['script']]
    print('Installing: ' + '; '.join(prepared['summary']), file=log, flush=True)
    if not staging_root and subprocess.run(['/usr/bin/sudo', '-n', '/usr/bin/true'], stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        return False
    result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    if result.returncode:
        raise InstallationError(f'Installation failed (exit {result.returncode}). See the log.')
    for follow_up in prepared['post_install']:
        if staging_root:
            print('Skipped in staging: ' + shlex.join(follow_up), file=log, flush=True)
            continue
        if subprocess.run(follow_up, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=environment()).returncode:
            raise InstallationError('Files installed, but a follow-up step failed: ' + shlex.join(follow_up))
    for note in prepared['notes']:
        print(note, file=log, flush=True)
    return True
