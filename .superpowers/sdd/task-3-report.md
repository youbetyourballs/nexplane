# Task 3 Report: Go Agent Commands

**Status:** DONE

**Commit:** d5ffedf (bug fixes) — latest master

**Build:** `go build ./...` from `~/nexplane/agent` on EC2 — clean, no errors.

## Bug Fixes (2026-07-29)

**Commit:** d5ffedf — `fix: authorized_keys per-line dedup, rsync key path quoting`

### Bug 1: `add_authorized_key` dedup check (authorized_keys.go)
Replaced `strings.Contains(string(existing), keyLine)` with a per-line loop that trims and compares for exact equality. The old check could false-positive if the new key was a substring of an existing longer key.

### Bug 2: `rsync_push` key path quoting (rsync_push.go)
Wrapped `keyPath` in single quotes in the `-e` ssh format string: `ssh -i '%s' ...`. Prevents breakage when the temp dir path contains spaces.

## What was done

Created 3 new Go command packages and registered all 4 commands in the executor:

- `agent/commands/runcommand/run_command.go` — `RunCommand`: executes shell commands via `sh -c` with configurable timeout (default 60s); returns `{output, exit_code}`
- `agent/commands/authorizedkeys/authorized_keys.go` — `AddAuthorizedKey` / `RemoveAuthorizedKey`: reads homedir from `/etc/passwd`, creates `.ssh/` dir if needed, appends or removes a public key line; idempotent on add (returns `added: false` if already present)
- `agent/commands/rsyncpush/rsync_push.go` — `Execute`: decodes base64 SSH key to temp file, runs rsync with `-az --checksum --stats`, parses stats output for `bytes_transferred` and `files_transferred`
- `agent/executor/executor.go` — added imports for the 3 new packages and registered `run_command`, `rsync_push`, `add_authorized_key`, `remove_authorized_key` in the `commands` map

## Notes

- Module path is `nexplane-agent` (not `github.com/nexplane/nexplane`)
- `go build` must run from `~/nexplane/agent/` on EC2 where `go.mod` lives
- No rollback entries added — these are primitives used by higher-level CRs that own their own rollback logic
