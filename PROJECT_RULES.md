# omso Builder project rules

## Scope and boundaries

- omso Builder builds and installs products from several repositories (today VSTS
  and omso-daw). It must not hard-code any repository's products, presets, build
  commands or install components. Each repository describes itself in its own
  `omso-build.json` (and, for the provider form, its own script); keep
  repository-specific logic there, not here.
- `docs/MANIFEST.md` (manifest schema 1, provider protocol 1) and `docs/API.md`
  (JSON API v2) are contracts with other repositories and with agents. They are
  unreleased and used locally only, so deliberate changes are allowed, but change
  them together with every affected manifest, provider and document, and bump the
  version when an old reader would misread the new data. Do not add legacy readers
  solely for earlier local test versions.
- Changes to another repository (its manifest, provider, rules or docs) are made
  in that repository's own worktree and branch, under that repository's rules.

## Installation safety

- The install policy in `omso_builder/installation.py` (allowed destinations,
  bundle types, fingerprints, locks) protects the system install. Widening it
  needs the user's explicit approval. Never weaken fingerprint, lock or source
  checks to make an installation pass.
- Never request or store passwords. Installation uses only the app's macOS
  approval dialog or noninteractive `sudo -n`; when that is unavailable, leave
  the build under Ready to install and report it as pending, not installed.
- Tests and validation install only into a staging folder (`--staging-root`).
  Ask the user before installing into `/Applications` or `/Library` from this
  project's own work, unless the session's instructions already authorize it.

## Validation and delivery

- Run `python3 -m unittest discover -s tests` and `./scripts/build-app.sh`
  (which ends with the app's self-test) for every change. For changes that affect
  real builds, plan and build at least one product of each registered repository
  from a feature worktree and install it into a staging folder. State what was not
  verified, such as the approval dialog or a real system installation.
- Keep build outputs, run logs and the user's repository list out of Git.
- Name branches after the work; `main` is the integration branch. Commit validated
  checkpoints. Ask before pushing, merging or releasing unless the session's
  instructions authorize it.
