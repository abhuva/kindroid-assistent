# Public research journal

Public site: **https://abhuva.github.io/kindroid-assistent/**  
Publication branch: **docs-site** in the existing repository.

The application worktree and its unpublished commits are not pushed by the
publication helper. The site branch begins with its own history and contains only
the publication manifest plus the curated pages. GitHub Actions builds from this
branch and uploads only the generated `site` directory.

## Write, preview, publish

1. Double-click **New Research Note.bat**. It creates a Markdown draft in ignored
   `var/journal-drafts` and opens it in Notepad. Keep private observations there.
2. Write the question, setup, observation, limits and next step. Replace private
   quotations with invented examples and review any aggregate for identification risk.
3. Once reviewed for publication, remove `draft: true` and copy the page into
   `public-docs/journal/`. Give it a dated, descriptive filename. Add its link to
   `public-docs/journal/index.md`, the homepage if desired, and `zensical.toml` navigation.
4. Double-click **Preview Research Notes.bat**, then open **http://127.0.0.1:8008/**.
   The preview is loopback-only; its console retains progress and logs. Close the
   console to stop it. Preview sessions have a four-hour hard maximum.
5. Publish the reviewed snapshot from PowerShell in this repository:

   ```powershell
   .\scripts\research-site.ps1 -Action Publish
   ```

The helper checks privacy rules, tests the boundary, builds in strict mode, scans
the rendered pages/search data, creates an isolated docs-only checkout and pushes
that branch. It never runs `git add` or `git commit` in the application worktree.
Its commits use the GitHub account's no-reply address. It preserves logs and an
inspectable snapshot under ignored `var/research-site`.

The local source under `public-docs/` is the publishing input. Editing a page through
GitHub's **Edit** link updates the public branch directly; copy such edits back to
the local source before the next local Publish so you do not replace them with an
older local version. The helper publishes the local snapshot, including removals.

## First-time environment setup

Python 3.13, Git and authenticated GitHub CLI are used. The documentation environment
is separate from application/model dependencies:

```powershell
.\scripts\research-site.ps1 -Action Setup
.\scripts\research-site.ps1 -Action Build
```

`Setup` installs pinned documentation packages into ignored `var/docs-venv`.
Every Python operation runs in an isolated child with redirected output and a
hard deadline. Progress and the terminal outcome are retained in
`var/research-site/latest.json`. GitHub deployment progress and failures remain
visible in the repository's **Actions → Public research notes** workflow.

## Publication boundary

- `public-docs/` contains only reviewed Markdown, CSS and SVG. Raw data files and
  binary media are rejected rather than copied accidentally.
- `public-site-files.txt` lists the small approved set of configuration/tooling files.
  Everything else in the application workspace is excluded from the publication branch.
- Build configuration fixes the source/output directories and forbids unreviewed
  build extensions, remote fonts and file-inclusion directives.
- Both source and generated output are checked for credential-like values, private
  paths and raw-data artifacts. Messages never print detected secret values.
- Draft flags are not privacy: any committed page can be read in public Git history.
  The checker rejects draft-marked public pages. Drafts belong in ignored local storage.
- Automated checks cannot determine whether prose or an aggregate identifies someone.
  Review the content itself before Publish. Do not add screenshots of private conversations.

The starter entries describe methods and synthetic benchmarks. They contain no
transcripts, participant IDs, account exports, private media or runtime databases.

## Maintenance

Dependencies and GitHub Actions commits are pinned. Update them deliberately, then
run Build and the guard tests before publishing. The journal uses dated Markdown
pages to keep post URLs and authoring portable across blog-plugin configurations. See
[Zensical deployment documentation](https://zensical.org/docs/publish-your-site/).

The original application repository is already public. The initial tracked-path
and history-path audits found no account backup, runtime database or credential-file
paths. This is not a claim that an automated scan can prove every existing sentence
in the application's public history is anonymous. The site adds a separate,
reviewed publication boundary rather than republishing internal project documents.
