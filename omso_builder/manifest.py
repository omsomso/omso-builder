"""omso-build.json (schema 1): how a repository's checkouts are built and installed.

Two forms (docs/MANIFEST.md):
- steps: each product lists its build/test commands and the bundles to copy;
  the builder runs the steps and performs the copies.
- provider: a script in the repository answers the builder's plan, installation
  and ready-build questions (protocol 1); the builder runs the commands it returns.
"""
import json
from pathlib import Path, PurePosixPath
import re

FILENAME = 'omso-build.json'
SCHEMA = 1
PLACEHOLDERS = ('source', 'manifest', 'jobs', 'run', 'tmp')
DEFAULT_LOCK = 'build/.omso-builder.lock'
OPTION_ID = re.compile(r'^[a-z][a-z0-9_]*$')
# Lowercase words in braces are placeholders; other braces (such as a shell's ${VAR}) stay literal.
_PLACEHOLDER = re.compile(r'\{([a-z_]+)\}')


class ManifestError(ValueError):
    pass


def load(checkout):
    """The checkout's validated manifest, or None when it has none."""
    file = Path(checkout) / FILENAME
    if not file.is_file():
        return None
    try:
        data = json.loads(file.read_text())
    except ValueError as error:
        raise ManifestError(f'{FILENAME} is not valid JSON ({error}).')
    return validate(data)


def expand(value, values):
    return _PLACEHOLDER.sub(lambda match: values[match.group(1)], value)


def products(manifest, source):
    """Product names a source checkout offers. A provider's catalog file is read
    from the source itself, so an older checkout lists its own products."""
    entries = manifest['products']
    if isinstance(entries, list):
        return [entry if isinstance(entry, str) else entry['name'] for entry in entries]
    data = json.loads((Path(source) / entries['file']).read_text())
    names = list(data) if isinstance(data, (dict, list)) else []
    if not names or any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise ValueError(entries['file'] + ' must list unique product names.')
    return names


def _fields(data, required, optional, what):
    if not isinstance(data, dict):
        raise ManifestError(f'{what} must be an object.')
    missing = [key for key in required if key not in data]
    if missing:
        raise ManifestError(f'{what} is missing {", ".join(missing)}.')
    unknown = sorted(set(data) - set(required) - set(optional))
    if unknown:
        raise ManifestError(f'{what} has unknown fields: {", ".join(unknown)}.')


def _text(value, what):
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f'{what} must be a nonempty string.')
    return value


def _strings(value, what, nonempty=True):
    if (not isinstance(value, list) or (nonempty and not value) or
            any(not isinstance(item, str) or not item for item in value)):
        raise ManifestError(f'{what} must be a {"nonempty " if nonempty else ""}list of strings.')
    return value


def _relative(value, what):
    _text(value, what)
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts:
        raise ManifestError(f'{what} must be a relative path inside the checkout.')
    return value


def _placeholders(value, what, allowed=PLACEHOLDERS):
    for name in _PLACEHOLDER.findall(value):
        if name not in allowed:
            raise ManifestError(f'{what} uses unknown placeholder {{{name}}}.')
    return value


def _command(value, what, allowed=PLACEHOLDERS):
    for item in _strings(value, what):
        _placeholders(item, what, allowed)
    return value


def _unique(names, what):
    if len(set(names)) != len(names):
        raise ManifestError(f'{what} must be unique.')


def _option(data, index):
    what = f'options[{index}]'
    _fields(data, ('id', 'label'), ('help', 'default', 'products'), what)
    if not isinstance(data['id'], str) or not OPTION_ID.match(data['id']):
        raise ManifestError(f'{what}.id must be lowercase letters, digits or _.')
    _text(data['label'], what + '.label')
    if 'help' in data:
        _text(data['help'], what + '.help')
    if type(data.get('default', False)) is not bool:
        raise ManifestError(f'{what}.default must be true or false.')
    return dict(id=data['id'], label=data['label'], help=data.get('help', ''), default=data.get('default', False),
                products=_strings(data.get('products', []), what + '.products', nonempty=False))


def _step(data, what):
    _fields(data, ('name', 'run'), ('env', 'cwd'), what)
    _text(data['name'], what + '.name')
    _command(data['run'], what + '.run')
    env = data.get('env', {})
    if not isinstance(env, dict) or any(not isinstance(key, str) or not key or not isinstance(value, str)
                                        for key, value in env.items()):
        raise ManifestError(f'{what}.env must map names to strings.')
    for value in env.values():
        _placeholders(value, what + '.env')
    if 'cwd' in data:
        _relative(data['cwd'], what + '.cwd')
    return data


def _product(data, index):
    what = f'products[{index}]'
    _fields(data, ('name', 'steps', 'install'), ('configuration',), what)
    _text(data['name'], what + '.name')
    if 'configuration' in data:
        _text(data['configuration'], what + '.configuration')
    if not isinstance(data['steps'], list) or not data['steps']:
        raise ManifestError(f'{what}.steps must be a nonempty list.')
    for number, step in enumerate(data['steps']):
        _step(step, f'{what}.steps[{number}]')
    if not isinstance(data['install'], list) or not data['install']:
        raise ManifestError(f'{what}.install must be a nonempty list.')
    for number, item in enumerate(data['install']):
        where = f'{what}.install[{number}]'
        _fields(item, ('copy', 'to'), (), where)
        _relative(item['copy'], where + '.copy')
        _text(item['to'], where + '.to')
    return data


def validate(data):
    if isinstance(data, dict) and type(data.get('schema')) is int and data['schema'] > SCHEMA:
        raise ManifestError(f'{FILENAME} schema {data["schema"]} needs a newer omso Builder.')
    _fields(data, ('schema', 'name', 'products'), ('lock', 'notes', 'options', 'provider'), FILENAME)
    if type(data['schema']) is not int or data['schema'] != SCHEMA:
        raise ManifestError(f'{FILENAME} schema must be {SCHEMA}.')
    result = dict(schema=SCHEMA, name=_text(data['name'], 'name'),
                  lock=_relative(data.get('lock', DEFAULT_LOCK), 'lock'),
                  notes=_strings(data.get('notes', []), 'notes', nonempty=False))
    products = data['products']
    if 'provider' in data:
        _fields(data['provider'], ('run',), (), 'provider')
        result['provider'] = dict(run=_command(data['provider']['run'], 'provider.run', ('manifest',)))
        if isinstance(products, dict):
            _fields(products, ('file',), (), 'products')
            _relative(products['file'], 'products.file')
        else:
            _strings(products, 'products')
            _unique(products, 'products')
        options = data.get('options', [])
        if not isinstance(options, list):
            raise ManifestError('options must be a list.')
        result['options'] = [_option(option, index) for index, option in enumerate(options)]
        _unique([option['id'] for option in result['options']], 'Option ids')
    else:
        if 'options' in data:
            raise ManifestError('options need a provider; the steps form has none.')
        if not isinstance(products, list) or not products:
            raise ManifestError('products must be a nonempty list of products.')
        for index, product in enumerate(products):
            _product(product, index)
        _unique([product['name'] for product in products], 'Product names')
        result['options'] = []
    result['products'] = products
    return result
