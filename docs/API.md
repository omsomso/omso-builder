# omso Builder JSON API (version 2)

Agents and scripts use the same workflow as omso Builder.app without opening it.
Run `python3 /path/to/omso-builder/scripts/build-api.py`, send one
JSON object on stdin and close stdin. The process prints one JSON object on stdout
and exits. Build progress, compiler output and errors stream to stderr. There is no
server, background job or network access.

Every request has `"api_version": 2` and an `operation`. Unknown fields and wrong
types are rejected. Responses have `ok`, `operation` and `status`, or
`"ok": false` with `error: {code, message}`.

## Repositories

| Operation | Fields | Result |
| --- | --- | --- |
| `repositories` | none | `repositories` (the registered list) and `config` (its file) |
| `add_repository` | `path` (any checkout), optional `tooling` | `status: "added"`; registers the repository's primary checkout |
| `remove_repository` | `path` (the registered path) | `status: "removed"` |

The list lives in `~/Library/Application Support/omso Builder/repositories.json`
(`OMSO_BUILDER_CONFIG` overrides it). `tooling` names the checkout whose
`omso-build.json` builds checkouts that have none (see [MANIFEST](MANIFEST.md)).

## Sources

```sh
python3 scripts/build-api.py <<'JSON'
{"api_version":2,"operation":"sources"}
JSON
```

`repositories` is an array of `{path, name, tooling, problem, sources}`. Each source:

| Field | Meaning |
| --- | --- |
| `path` | Absolute checkout path to send as `source_dir` |
| `branch`, `head` | Checked-out branch (or `Detached HEAD`) and full commit SHA |
| `dirty` | Whether the checkout has local edits (included when building) |
| `primary` | Whether this is the repository's primary checkout |
| `updated` | Unix time of the checked-out commit |
| `products`, `options`, `notes` | What this checkout can build, from its manifest |
| `manifest` | `{path, kind}`: `kind` is `own` or `tooling`; `null` when it cannot be built |
| `problem` | Why the checkout cannot be built, or `null` |

The API never fetches, switches branches or merges.

## Plan, preview and build

```sh
python3 scripts/build-api.py <<'JSON'
{"api_version":2,"operation":"plan","source_dir":"/path/to/omso-daw","products":["omso DAW"]}
JSON
```

`plan` and `build` take `source_dir` (absolute), `products` (nonempty, unique, from
that source) and optional `options` (id → bool; unset options use the manifest's
default), `expected_head` and `expected_branch` (reject if the checkout moved).
`plan` returns `jobs` (`name`, `run` argument array, `cwd`, `env`) and creates nothing.

`build` also takes `dry_run` (preview the jobs without building) and `install_mode`:

| `install_mode` | After all steps pass |
| --- | --- |
| `deferred` (default) | `status: "awaiting_install"` with `installation`. Nothing is installed. |
| `sudo` | Installs with `sudo -n`: `completed` when it worked. Without cached sudo authorization, nothing is installed and the result is `awaiting_install` with a `message`: install it from the app's **Ready to install**. |

The build holds the checkout's lock throughout and rechecks its branch and commit
after taking it. Other statuses: `previewed`, `failed` (with `exit_code` and
`error.code: "workflow_failed"`). `ok: true` with `awaiting_install` means built and
tested, **not installed**. A successful build does not prove that a product works in
a DAW or on a device.

The `installation` object has `summary` (lines such as `Copy omso DAW.app to
/Applications/omso`), `script` (the shell commands to run as root), `fingerprints`
(path → SHA-256), `lock_path`, `post_install`, `refuse_if_exists`, `notes`,
`actions` and `source`. A client that installs it must take the lock, check every
fingerprint and refusal, run `script` with administrator rights, then run each
`post_install` command as the user. omso Builder.app does exactly this.

## Ready builds and installation

`{"api_version":2,"operation":"ready"}` lists finished, tested builds in all
sources: `builds` of `{repository: {path, name}, source, id, build_id, products,
configuration, version, revision, dirty, created, installed, installer_running}`,
plus `problems` (repositories whose ready builds could not be listed).

`{"api_version":2,"operation":"install","source_dir":"…","build":"<id>","build_id":"<build_id>"}`
prepares that build's installation without rebuilding (`install_mode` as above).
A build that changed or disappeared fails with `build_not_ready`; a running build in
that checkout fails with `busy`.

## Errors and exit status

Codes: `invalid_request`, `unsupported_version`, `unsupported_platform`,
`source_unavailable`, `source_changed`, `unknown_product`, `busy`,
`build_not_ready`, `workflow_failed`, `install_failed`, `provider_failed`,
`request_failed`, `interrupted`. Exit status is 0 for success, 2 for invalid
requests, the build's own nonzero code when a step fails, 130 when interrupted and 1
otherwise. Check both the exit status and `ok`, and keep stderr for the log.

## Testing without installing

`python3 scripts/build-api.py --staging-root /tmp/stage` runs `sudo` mode without
privileges, installing into `/tmp/stage/Applications/omso`,
`/tmp/stage/Library/Audio/Plug-Ins` and so on, and skips `post_install`. Use it in
tests and validation; it is not offered by the app.

## From Python

```python
import json, subprocess

request = {"api_version": 2, "operation": "build", "source_dir": "/abs/checkout",
           "products": ["omso DAW"], "install_mode": "sudo"}
result = subprocess.run(["python3", "/path/to/omso-builder/scripts/build-api.py"],
                        input=json.dumps(request), text=True, stdout=subprocess.PIPE)
response = json.loads(result.stdout)  # stderr stays attached to the console for progress
if result.returncode or not response["ok"]:
    raise RuntimeError(response["error"])
```
