# omso Builder

One macOS builder for the omso projects. It lists every checkout and worktree of the
registered repositories (the VSTS audio plugins and the omso DAW today), builds and
tests the chosen products from that checkout as it is, including uncommitted edits,
and installs the result through one macOS approval dialog. No merge is needed to
build a feature branch.

omso Builder is open source under the GNU General Public License, version 3 or later
(`LICENSE`). Copyright (C) 2026 omso. The repositories it builds today are private, so
it is shared mainly as an example of the build tooling.

The builder knows no products. Each repository describes its own products and
build, test and install steps in an `omso-build.json` ([MANIFEST](docs/MANIFEST.md)):

- **omso DAW** lists its steps directly: configure and build the `release` preset,
  `ctest`, the silent workspace and arrangement self-tests, then copy
  `omso DAW.app` into `/Applications/omso`. It installs no plugins.
- **VSTS** points to its own provider script, which uses the repository's plugin
  catalog and canonical `test-install.sh`: Release build, tests and archive per
  product, then AU/VST3/CLAP into `/Library/Audio/Plug-Ins` and standalone apps into
  `/Applications/omso`. Ensemble registration and the DrumLab AUv3 option are
  handled there too.

## Start here

1. Build the app (once, and after updating this project):

   ```bash
   ./scripts/build-app.sh
   ```

   It compiles **omso Builder.app** into `build/`, links it as
   `omso Builder.app` in this folder (drag it to the Dock if you like) and runs
   its self-test. It builds no products and installs nothing.
2. Open **omso Builder.app**. Click **Add…** and choose any checkout of each
   repository to register it (or use the API's `add_repository`).
3. Pick the repository, then the branch under **Build from**, tick the products and
   click **Build & Install**. **Preview** shows the commands without building.
   The live log shows each step. When the build and tests pass, approve the macOS
   dialog to install. If you cancel it, **Retry installation** reuses the tested
   build. Close the DAW (or the app being replaced) first.

**Ready to install** lists finished, tested builds from every repository, including
builds left by agents or other sessions whose installation was not completed. Each
row shows the branch, its products, the repository and whether it is a Debug build;
**Install** installs it without rebuilding. A build drops off the list once its files
change.

A checkout without its own `omso-build.json` (for example a VSTS branch older than
the manifest) is built with the repository's main checkout's manifest. Set a different one with `tooling` in the repository
list. Builds of one checkout never run at the same time; different checkouts can
build side by side.

## Agents and scripts

`python3 scripts/build-api.py` is a JSON API over the same workflow: list sources,
plan, preview, build, list ready builds and install ([API](docs/API.md)). With
`"install_mode": "sudo"` it installs right after a successful build when `sudo` is
already authorized; otherwise the build waits under **Ready to install** and the
result says so. It never asks for a password.

```bash
python3 scripts/build-api.py <<< '{"api_version":2,"operation":"sources"}'
```

## Safety

Only bundles built inside the selected checkout are installed, and only into
`/Applications/omso` (apps) or the `/Library/Audio/Plug-Ins` folders (plugins,
directly or through the repository's `cmake --install` components). Every installed
file is fingerprinted after the tests; installation stops if anything changed
since. Approval is requested by macOS for each installation; no password is stored.

## Development

```bash
python3 -m unittest discover -s tests
./scripts/build-app.sh
```

The tests drive the API against temporary Git repositories and install only into
staging folders (`--staging-root`). Python 3.9 (macOS's own `/usr/bin/python3`) and
the Swift compiler from Xcode or its Command Line Tools are enough; there are no
other dependencies. See [PROJECT_RULES](PROJECT_RULES.md) for the working rules.

| Path | Contents |
| --- | --- |
| `omso_builder/` | Core: repository list, checkout discovery, manifests, builds, installation policy, API |
| `scripts/build-api.py` | JSON API entry point |
| `app/` | The SwiftUI app (a client of the API) and its icon |
| `scripts/build-app.sh` | Builds, links and self-tests the app |
| `docs/` | Manifest and API contracts |
| `tests/` | Core and API tests |

## Status

In use since 23 October 2026. It replaced VSTS's Build Plugins.app and build API v1,
which were removed from VSTS (audio-plugins PR #139). Both repositories' manifests
are on their `main` branches (audio-plugins #137, omso-daw #27).
