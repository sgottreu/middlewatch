# Where these files go

| File | Destination | Notes |
|---|---|---|
| `storage-and-backup.md` | Repo root, alongside `arc.md` and `style.md` | Sibling document pattern — creative governance and infrastructure governance kept separate but adjacent. |
| `mw-sync.sh` | `scripts/mw-sync.sh` in the repo | Then on the instance: `chmod +x scripts/mw-sync.sh && sudo ln -s "$(pwd)/scripts/mw-sync.sh" /usr/local/bin/mw-sync` |

## Before first use

1. Work through sections 1 and 2 of `storage-and-backup.md` — disk created,
   mounted, `fstab` entry verified with `mount -a`, IAM user configured.
2. Override the defaults if your names differ:
   ```bash
   export MW_BUCKET=middlewatch-assets   # default
   export MW_WORK=/mnt/work              # default
   ```
   Put these in `~/.bashrc` on the instance if they are not the defaults.
3. Confirm with a no-op run once the tree exists:
   ```bash
   mw-sync push-all
   ```
   It skips missing directories rather than failing, so it is safe on an empty disk.

## Pipeline integration

`mw-sync` calls belong at the tail of each agent's step, not in a checklist you
run by hand. Section 4 of the runbook has the stage-to-command table.

## Verified

`mw-sync.sh` was syntax-checked and dry-run against a stub `aws` binary with a
fake arc tree. Confirmed: correct source and destination prefixes for shared,
episode and feature pushes; missing directories skipped rather than erroring;
`render/` and `tmp/` never included.
