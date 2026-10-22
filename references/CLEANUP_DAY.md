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

For .NET MAUI and Android, the profile also recognizes generated assembly,
resource, binding and intermediate files by their build context and generated
names. Original-looking source files, unknown extensions, distribution
deliverables and mixed directories remain omitted when their status cannot be
verified safely.

When a `bin` or `obj` tree is mixed, cleanup-day descends into it and plans
only verified files. It never promotes a mixed parent directory to a
candidate; file-level candidates keep the plan snapshot precise and avoid
overlapping file/directory candidates. Every selected path, size, metadata
snapshot and configured exclusion is included in the plan hash, so a same-size
file mutation also invalidates an old approval.

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
recovered. If an interruption occurs after partial progress, the result
reports the files and bytes actually removed before the error. The command
never performs global Windows cleanup and protects source,
Git, memory, sessions, evidence, backups, SDKs, shared caches, and other agents'
files. An open or active artifact is left as an error/omission if the operating
system refuses removal.
