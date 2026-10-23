# Repository manifest: omso-build.json (schema 1)

Each repository that omso Builder builds has an `omso-build.json` at the root of
its checkouts. It tells the builder which products exist and how to build, test and
install them. The builder itself knows no products.

A checkout with its own manifest is built with it, so every branch builds with its
own definitions. A checkout without one (for example a branch created before the
manifest existed) is built with the manifest of the repository's **tooling
checkout**: the `tooling` path set for that repository in the builder's repository
list while that checkout exists, else the checkout of `main` (else `master`, else
the primary checkout). The
selected checkout remains the source; only the build definitions come from the
tooling checkout. If neither has a manifest, the checkout is listed but cannot be
built.

## Common fields

| Field | Required | Meaning |
| --- | --- | --- |
| `schema` | yes | `1`. A higher number asks for a newer omso Builder. |
| `name` | yes | Repository name shown in the app, e.g. `"omso DAW"`. |
| `products` | yes | The products (form-specific, below). |
| `lock` | no | Lock file, relative to the checkout (default `build/.omso-builder.lock`). Builds and installations of one checkout hold it; share it with the repository's own tools to exclude them. |
| `notes` | no | Short lines shown under the product list. |

Unknown fields are rejected. Paths in a manifest are relative to the checkout and
may not contain `..`.

## Steps form

The builder runs each selected product's steps in order, in the source checkout,
and stops at the first failure. When all pass, it records a receipt with a digest of
each bundle to install (`build/omso-builder/ready/`); the build is then listed under
**Ready to install** until a bundle changes. Installing copies each bundle into its
destination.

```json
{
  "schema": 1,
  "name": "omso DAW",
  "products": [{
    "name": "omso DAW",
    "configuration": "Release",
    "steps": [
      {"name": "Configure", "run": ["cmake", "--preset", "release"]},
      {"name": "Build", "run": ["cmake", "--build", "--preset", "release", "--parallel", "{jobs}"]},
      {"name": "Test", "run": ["ctest", "--preset", "release"], "env": {"TMPDIR": "{tmp}"}},
      {"name": "Self-test", "run": ["{source}/build/release/App.app/Contents/MacOS/App", "--smoke", "{run}/smoke"]}
    ],
    "install": [{"copy": "build/release/App.app", "to": "/Applications/omso"}]
  }]
}
```

Product fields: `name`, `steps` (nonempty), `install` (nonempty) and optional
`configuration` (a label such as `Release`, shown with ready builds). Step fields:
`name`, `run` (argument array, no shell), optional `env` (added to the environment)
and `cwd` (relative to the checkout). A program name is found on `PATH`, which
includes `/opt/homebrew/bin` and `/usr/local/bin`.

Placeholders in `run` and `env` values (a lowercase word in braces; other braces, such as
a shell's `${VAR}`, are kept as written):

| Placeholder | Value |
| --- | --- |
| `{source}` | Absolute path of the selected checkout |
| `{manifest}` | Absolute path of the checkout whose manifest is used |
| `{jobs}` | Number of CPU cores |
| `{run}` | A new folder for this run, `build/omso-builder/runs/<time>-<id>` (created before the first step) |
| `{tmp}` | `{run}/tmp`, created before the first step |

## Provider form

For repositories whose build logic belongs in their own tools, the manifest names a
**provider** script. The builder still discovers checkouts, holds the lock, runs
every command, validates and performs the installation.

```json
{
  "schema": 1,
  "name": "omso audio plugins",
  "lock": "build/.finder-build.lock",
  "products": {"file": "plugins/catalog.json"},
  "options": [{"id": "auv3", "label": "DrumLab AUv2 + AUv3", "help": "Requires Xcode.",
               "default": true, "products": ["DrumLab"]}],
  "provider": {"run": ["python3", "{manifest}/scripts/build-provider.py"]}
}
```

`products` is a list of names, or `{"file": path}`: a JSON object (its keys) or array
in the **selected** checkout, so an older checkout offers its own products.
`options` are checkboxes passed to the provider; an option that names `products`
applies only when one of them is selected. `provider.run` may use `{manifest}` only.

### Provider protocol 1

The builder runs `provider.run` in the manifest's checkout, writes one JSON object to
stdin and reads one from stdout (stderr is for diagnostics). Every request has
`"protocol": 1` and an `operation`. Replies are `{"ok": true, ...}` or
`{"ok": false, "error": {"code": "...", "message": "..."}}`. Requests are quick: the
provider plans and inspects but never builds or installs.

| Operation | Request fields | Reply |
| --- | --- | --- |
| `plan` | `source_dir`, `products`, `options` (id → bool, defaults filled in), `preview` | `jobs`: `[{name, run, cwd, env?}]`; `cwd` is the source or manifest checkout. With `preview`, the jobs must only print what they would do. |
| `installation` | `source_dir`, `products`, `options` | `installation` (below), after all jobs succeeded |
| `ready` | `source_dirs` | `builds`: `[{source_dir, id, build_id, products, configuration, version, revision, dirty, created, installed, installer_running}]` |
| `ready_installation` | `source_dir`, `id`, `build_id` | `installation` for that ready build, or error code `build_not_ready` |

An `installation` object:

| Field | Meaning |
| --- | --- |
| `actions` | `{"copy": bundle, "to": folder}` or `{"cmake_install": build_dir, "config": "Debug"/"Release", "component": name, "prefix": "/Library/Audio/Plug-Ins" or "/Applications"}` |
| `files` | Build outputs to fingerprint, in addition to copied bundles and `cmake_install.cmake` |
| `post_install` | Commands run as the user after installing; each program must be inside the source or manifest checkout |
| `refuse_if_exists` | `[{path, message}]`: refuse installation while such a path exists |
| `notes` | Lines shown after installation |

## What the builder allows

Whatever a manifest or provider asks, installation is limited to:

- copies of `.app` bundles into `/Applications/omso`, and of `.vst3`, `.component`
  and `.clap` bundles into the matching `/Library/Audio/Plug-Ins` folders;
- `cmake --install` of a build folder with generated install instructions, with
  prefix `/Library/Audio/Plug-Ins` or `/Applications`;
- sources inside the selected checkout (symlinks may not escape it).

The files are fingerprinted right after the build and checked again, under the lock,
immediately before installation; any change requires a rebuild. A copy replaces the
installed bundle only after the new copy is complete.
