"""Read-only Git queries: a repository's checkouts and their state."""
from pathlib import Path
import subprocess


def git(root, *args, timeout=15):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True,
                                   stderr=subprocess.PIPE, timeout=timeout).strip()


def primary_checkout(path):
    """The main working tree of the repository that contains path."""
    common = Path(git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    if common.name != '.git':
        raise ValueError(f'{path} is not inside a Git repository with a working tree.')
    return common.parent.resolve()


def worktrees(repository):
    """Existing checkouts (primary first) with their branch and commit."""
    raw = subprocess.check_output(['git', '-C', str(repository), 'worktree', 'list', '--porcelain', '-z'],
                                  text=True, stderr=subprocess.PIPE, timeout=15)
    checkouts = []
    for index, record in enumerate(raw.split('\0\0')):
        fields = dict(field.partition(' ')[::2] for field in record.split('\0') if field)
        if 'worktree' not in fields or 'bare' in fields or 'prunable' in fields:
            continue
        path = Path(fields['worktree'])
        if not path.is_dir():
            continue
        branch = fields['branch'].removeprefix('refs/heads/') if 'branch' in fields else 'Detached HEAD'
        checkouts.append(dict(path=str(path.resolve()), branch=branch, head=fields.get('HEAD', ''),
                              primary=index == 0))
    return checkouts


def state(path):
    """(has local edits, Unix time of the checked-out commit)."""
    dirty = bool(git(path, 'status', '--porcelain', '--ignore-submodules=all'))
    return dirty, int(git(path, 'log', '-1', '--format=%ct'))
