# Contributing to Machine Status · DDD

Thanks for your interest in improving the project! Bug reports, ideas and pull requests are all welcome.

## Before you start

- **Bugs**: open an issue with the steps to reproduce it. If possible, attach the domain as JSON (sidebar → *JSON* export) or a minimal one that shows the problem.
- **Features**: open an issue to discuss the idea before writing a lot of code, so we can agree on the approach.
- **Small fixes** (typos, obvious bugs): feel free to open a pull request directly.

## Development setup

```bash
docker compose up -d     # frontend http://localhost:5180 · API http://localhost:8010
```

Both backend and frontend reload automatically on every change. See the [README](README.md#getting-started) for running without Docker.

## Pull requests

- Keep each pull request focused on one change.
- Follow the style of the surrounding code.
- No hard-coded UI text: add every new label to both `frontend/src/locales/en.json` and `it.json`, and every new server-side message (analysis, conflicts, export) to both languages in `backend/app/i18n.py`.
- If you change the engine (`backend/app/engine.py`, `relations.py`), describe in the PR which domain you used to check the analysis results.
- Every commit must be signed off (see below).

## Translations

The UI is available in English and Italian. To add a language:

1. Copy `frontend/src/locales/en.json` to `<code>.json`, translate it and register it in `frontend/src/i18n.js` (`LOCALES` and `messages`).
2. Add a `<code>` block with the same keys to `MESSAGES` in `backend/app/i18n.py`.

Keep the `{placeholders}` unchanged, and the `|` separators of plural forms (`"{n} state | {n} states"`).

## Developer Certificate of Origin (DCO)

This project uses the [Developer Certificate of Origin](https://developercertificate.org/) instead of a Contributor License Agreement. You keep the copyright on your contributions, and by signing off you certify that you have the right to submit them under the project's license, the [GNU AGPL v3.0](LICENSE).

To sign off, add a `Signed-off-by` line to every commit with `-s`:

```bash
git commit -s -m "Fix cascade conflict on final states"
```

which produces:

```
Fix cascade conflict on final states

Signed-off-by: Jane Doe <jane@example.com>
```

Use your real name and an email you can be reached at. Forgot to sign off? Fix the last commit with `git commit --amend -s --no-edit`, or several with `git rebase --signoff main`, then force-push your branch.

The full text of the certificate:

```
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```
