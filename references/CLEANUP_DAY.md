# cleanup-day

`cleanup-day` is a conservative, provider-neutral cleanup command.

The default command is preview-only:

```text
cleanup-day --json
```

The default profile preserves the conservative legacy detection. The optional
build-cache profile detects only `bin` and `obj` directories when a nearby
project descriptor proves they belong to a real project and all contents are
recognized regenerable artifacts:

```text
cleanup-day --profile build-cache --exclude path/to/keep --json
```

The profile is opt-in, rejects source files, locks, symbolic links and APK/AAB
distribution outputs, and reports insufficient evidence or configured
exclusions as omissions. `--exclude` may be repeated; its values and the
profile are included in the preview hash.

It reports candidate paths, sizes, recoverable space, omitted paths, and errors.
Candidates are limited to verified build/test artifacts inside the project and
external temporary directories containing `.neoprars-cleanup.json` with:

```json
{"schema_version": 1, "project_root": "...", "discardable": true}
```

Execution requires explicit confirmation and an approver. Reusing the preview
hash prevents deleting a changed plan:

```text
cleanup-day --execute --confirm-cleanup --approved-by operator --plan-sha256 HASH --json
```

The result records deleted paths, omitted paths, errors, and bytes actually
recovered. The command never performs global Windows cleanup and protects source,
Git, memory, sessions, evidence, backups, SDKs, shared caches, and other agents'
files. An open or active artifact is left as an error/omission if the operating
system refuses removal.
