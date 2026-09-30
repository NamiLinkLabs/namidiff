# Releasing `namidiff`

This repository (`NamiLinkLabs/namidiff`) is the NamiLink Kft. fork of [reladiff](https://github.com/erezsh/reladiff),
which is itself a fork of [data-diff](https://github.com/datafold/data-diff).
The upstream name is taken on PyPI, so the fork is published as **`namidiff`**.

| Thing              | Name        |
|--------------------|-------------|
| PyPI distribution  | `namidiff`  |
| Python import      | `namidiff`  |
| CLI command        | `namidiff`  |
| Database layer     | `namidiff.sqeleton` (vendored fork of erezsh/sqeleton) |
| Git tag format     | `vX.Y.Z`    |

`namidiff` owns only the `namidiff` top-level package, so it can coexist with the
upstream `reladiff` and `sqeleton` distributions in the same environment.

The version lives in one place: `pyproject.toml`. `namidiff.__version__` reads it
from the installed package metadata at import time.

---

## Workflows

| File                                   | Trigger                          | Purpose                                                              |
|----------------------------------------|----------------------------------|----------------------------------------------------------------------|
| `.github/workflows/package.yml`        | push to `main`, PRs            | Builds sdist + wheel, validates metadata, installs the wheel and runs `namidiff --version`. |
| `.github/workflows/release.yml`        | push of tag `v*`, or called by bump-version | Runs the `ci_full.yml` tests, verifies tag == `pyproject.toml` version, builds, publishes to PyPI, creates a GitHub Release with the artifacts and auto-generated notes. |
| `.github/workflows/bump-version.yml`   | manual (Actions tab)             | Bumps the version with uv, commits to `main`, creates the tag, then runs the release workflow. |

Every release runs `ci_full.yml` (against the docker-compose database stack)
first: the `test` job in `release.yml` calls `ci_full.yml`, and `build` has
`needs: test`. A red test run blocks publishing; nothing is built, uploaded to
PyPI or released on GitHub.

Python versions tested before a release:

| Release                                  | Python        |
|------------------------------------------|---------------|
| bump `patch` / `minor`                   | 3.12 only     |
| bump `major` / `explicit`, or a hand-pushed tag | 3.11–3.14 |

PRs run `ci_full.yml` on 3.12 and `ci.yml` (reduced cross-database tests) on
3.11–3.14. To run the full matrix by hand: **Actions → CI-COVER-DATABASES →
Run workflow**, tick `full_matrix`.

---

## One-time setup

Do these once before the first release.

### 1. PyPI Trusted Publisher

No API token is stored anywhere. PyPI authenticates the GitHub Actions job via OIDC.

1. Log in to <https://pypi.org> with the account that will own `namidiff`.
2. Go to **Your account → Publishing** (<https://pypi.org/manage/account/publishing/>).
3. Under **Add a new pending publisher**, fill in:

   | Field            | Value          |
   |------------------|----------------|
   | PyPI project name| `namidiff`     |
   | Owner            | `NamiLinkLabs` |
   | Repository name  | `namidiff`     |
   | Workflow name    | `release.yml`  |
   | Environment name | `pypi`         |

4. Click **Add**.

"Pending" means the project does not exist yet. The first successful publish
creates it and converts the pending publisher into a normal one.

If the repository is ever moved or renamed again, update **Owner** / **Repository name** here. PyPI matches these literally; GitHub redirects do not help.

### 2. GitHub environment `pypi`

1. Repo → **Settings → Environments → New environment** → name it `pypi`.
2. Optional but recommended: add **Required reviewers** (yourself). Every publish
   will then pause and wait for approval in the Actions UI before uploading.
3. Optional: under **Deployment branches and tags**, restrict to tags matching `v*`
   and the `main` branch. A release started by `bump-version.yml` runs on `main`
   (it is a `workflow_call` from a manual run), so a `v*`-only rule blocks it.

The environment name must match the one entered on PyPI exactly.

### 3. Actions permissions

Repo → **Settings → Actions → General**:

- **Actions permissions**: the workflows use third-party actions
  (`astral-sh/setup-uv`, `softprops/action-gh-release`). If the
  organisation restricts actions to "GitHub-owned only", either allow these two
  explicitly or choose "Allow all actions and reusable workflows".
- **Workflow permissions**: select **Read and write permissions**. Needed so
  `bump-version.yml` can push the version commit and tag, and so `release.yml`
  can create the GitHub Release.

Organisation-level settings (**NamiLinkLabs → Settings → Actions → General**)
override repository settings. If a toggle is greyed out at the repo level, change
it at the org level first.

### 4. Deploy key for the bump workflow

`main` has a ruleset that requires pull requests. `bump-version.yml` pushes a
version commit directly to `main`, and the built-in `GITHUB_TOKEN` cannot
bypass that rule (you get `GH013: Changes must be made through a pull request`).
The workflow therefore checks out with a deploy key that is listed as a bypass
actor. Set it up once:

```bash
ssh-keygen -t ed25519 -C namidiff-release-bot -f ~/namidiff-release -N ""
```

1. Repo → **Settings → Deploy keys → Add deploy key**: paste
   `~/namidiff-release.pub`, tick **Allow write access**.
2. Repo → **Settings → Secrets and variables → Actions → New repository secret**:
   name `RELEASE_DEPLOY_KEY`, value = full contents of `~/namidiff-release`
   (the private key, including the BEGIN/END lines).
3. Repo → **Settings → Rules → Rulesets** → edit the `main` ruleset →
   **Bypass list → Add bypass → Deploy keys**.
4. Delete the local key files: `rm ~/namidiff-release ~/namidiff-release.pub`.

If the secret is missing the checkout step fails with an SSH error. To rotate,
repeat the steps with a new key and remove the old deploy key.

---

## Cutting a release

### Option A: from the GitHub UI (recommended)

1. Make sure everything you want in the release is merged to `main`.
2. Go to **Actions → Bump version and release → Run workflow**.
3. Pick a bump: `patch`, `minor`, `major`, or `explicit` (then fill
   in the **version** field, e.g. `0.7.0`).
4. Click **Run workflow**.

The workflow will:

1. Run `uv version --bump <bump>` on `main`.
2. Commit `pyproject.toml` and `uv.lock` as `Release vX.Y.Z`.
3. Create annotated tag `vX.Y.Z` and push both.
4. Invoke the release workflow for that tag (tests → build → publish → GitHub Release).

If you configured required reviewers on the `pypi` environment, approve the
**Publish to PyPI** job when it pauses.

### Option B: from your machine

Use this if you prefer to write the version commit yourself. Note that the
`main` ruleset requires pull requests, so the version commit has to go through
a PR; only the tag push below is direct.

```bash
git checkout main && git pull

# bump the version in pyproject.toml (pick one)
uv version --bump patch       # 0.6.8 -> 0.6.9
uv version --bump minor       # 0.6.8 -> 0.7.0
uv version 0.7.0              # explicit

VERSION=$(uv version --short)
git commit -am "Release v${VERSION}"
git tag -a "v${VERSION}" -m "namidiff ${VERSION}"
git push origin main
git push origin "v${VERSION}"
```

Pushing the tag triggers `release.yml`. Nothing else is needed.

If you do not have uv installed, edit the `version = "..."` line in
`pyproject.toml` by hand. The tag must equal that value with a `v` prefix.

### Pre-releases

From the GitHub UI pick `explicit` and enter the full version. `uv version --bump`
cannot go straight from a stable version to a pre-release.

Use a PEP 440 pre-release version, e.g. `0.7.0rc1` or `0.7.0b2`, and tag it as
`v0.7.0rc1`. The release workflow detects the letters in the version and marks
the GitHub Release as a pre-release. On PyPI, `pip install namidiff` skips
pre-releases unless the user passes `--pre` or pins the exact version.

---

## What a release run does

```
tag vX.Y.Z pushed (or called by bump-version)
   │
   ▼
test ──────────── ci_full.yml, Python 3.12 (patch/minor) or 3.11–3.14   (any failure stops the release)
   │
   ▼
build ─────────── checkout tag
                  assert tag == pyproject version   (fails fast on mismatch)
                  uv build                          (sdist + wheel)
                  uvx twine check --strict
                  install wheel in fresh venv, assert `namidiff --version` == vX.Y.Z
                  upload dist/ as artifact
   │
   ▼
publish-pypi ──── environment: pypi   (pauses here if reviewers are configured)
                  uv publish --trusted-publishing always   (OIDC, no token)
   │
   ▼
github-release ── softprops/action-gh-release
                  attaches .whl + .tar.gz
                  auto-generated notes from merged PRs / commits since last tag
                  marked pre-release if version contains letters
```

The GitHub Release is only created **after** the PyPI upload succeeds, so a
Release on GitHub always means the version is installable.

---

## Verifying a release

```bash
pip install --upgrade "namidiff==X.Y.Z"
namidiff --version           # -> vX.Y.Z
python -c "import namidiff; print(namidiff.__version__)"
```

Check <https://pypi.org/project/namidiff/> and the repo's **Releases** page.

---

## Building locally (no publish)

```bash
uve activate namidiff        # or any venv
uv build
uvx twine check --strict dist/*
uv pip install dist/*.whl    # in a scratch venv
```

`dist/` is git-ignored.

---

## Troubleshooting

**`Tag vX.Y.Z does not match pyproject.toml version`**
The tag and `pyproject.toml` disagree. Delete the tag (`git push --delete origin vX.Y.Z && git tag -d vX.Y.Z`), fix the version, commit, re-tag.

**`Tag vX.Y.Z already exists`** (bump workflow)
That version was already tagged. Choose a different bump or an explicit higher version.

**PyPI: `invalid-publisher` / `The given token is not valid for this project`**
The trusted-publisher record on PyPI does not match. Every field must be exact: owner `NamiLinkLabs`, repo `namidiff`, workflow `release.yml`, environment `pypi`. The workflow filename is compared literally, so renaming `release.yml` requires updating PyPI.

**PyPI: `File already exists`**
PyPI never accepts the same version twice, even after deletion. Bump to a new version and release again. Delete the stale git tag and GitHub Release if they were created.

**Bump workflow fails on `git push origin main`**
Branch protection is blocking the bot. See *4. Deploy key for the bump workflow* above, or use Option B.

**Release job never appears after pushing a tag from the bump workflow**
This should not happen because `bump-version.yml` calls `release.yml` directly instead of relying on the tag push event. Tags pushed with `GITHUB_TOKEN` do not fire `on: push` workflows by design. If you ever restructure this, keep the `workflow_call` link.

**Need to re-run only the publish step**
Actions → the failed run → **Re-run failed jobs**. The `dist` artifact from the build job is reused.

---

## Transferring the repository to `NamiLinkLabs/namidiff`

Everything in the repo already points at `NamiLinkLabs/namidiff`. After the
GitHub transfer + rename, do the following.

**On GitHub**

1. Transfer: old repo → **Settings → General → Danger Zone → Transfer ownership**
   → new owner `NamiLinkLabs`. GitHub offers to rename during transfer; set the
   name to `namidiff`. If not offered, rename afterwards under **Settings → General**.
2. Org **Settings → Actions → General**: allow third-party actions (or at least
   `astral-sh/setup-uv@*` and `softprops/action-gh-release@*`) and set
   workflow permissions to read and write. Repo-level toggles inherit from here.
3. Recreate anything that does not survive a transfer:
   - **Environments** are transferred, but check that `pypi` still exists and
     its reviewers are org members (personal-account reviewers may be dropped).
   - **Branch protection / rulesets** may need re-applying if they referenced
     users who are not in the org.
4. Enable **Discussions** if you want the README links to work (Settings → General → Features).

**On PyPI**

5. Trusted publisher must use the new owner and repo name. If you already added
   a pending publisher for `vmatt/namidiff`, delete it and add one for
   `NamiLinkLabs/namidiff` (workflow `release.yml`, environment `pypi`).
   PyPI compares these strings literally, so the old entry stops working the
   moment the transfer completes.

**Locally**

6. Point your clone at the new remote (GitHub redirects the old URL, but
   redirects break silently if a new repo is ever created under the old name):

   ```bash
   git remote set-url origin git@github.com:NamiLinkLabs/namidiff.git
   git fetch origin
   ```

7. Optionally rename the working directory to match:

   ```bash
   mv ~/Documents/GitHub/reladiff ~/Documents/GitHub/namidiff
   ```

   If you have git worktrees under the repo, run `git worktree repair <worktree-path>` for each afterwards.

   The `uve` environment name is independent of the folder name; `uve activate namidiff` keeps working.

**Sanity check**

8. Push any commit that touches `pyproject.toml` or `namidiff/**` and confirm the
   **Package build check** workflow goes green under the new org.

---

## Checklist for a first release

- [ ] Trusted publisher added on PyPI (pending publisher for `namidiff`)
- [ ] GitHub environment `pypi` exists
- [ ] Workflow permissions set to read and write
- [ ] Deploy key added, `RELEASE_DEPLOY_KEY` secret set, "Deploy keys" in ruleset bypass list
- [ ] `main` contains everything you want shipped
- [ ] Run **Bump version and release** (or push a `vX.Y.Z` tag manually)
- [ ] Approve the `pypi` environment if reviewers are configured
- [ ] `pip install namidiff==X.Y.Z && namidiff --version`
