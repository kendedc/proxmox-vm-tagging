# proxmox-vm-tagging

Reconciles Proxmox VE guest tags against an Excel source of truth. Built to run
as an AWX job template over a ~400 guest cluster.

The playbook reads the spreadsheet, fetches every guest's current tags in one
API call, computes a diff, enforces safety limits, and writes only the guests
that actually drifted. Nothing is SSH'd into — it is entirely API-driven and
runs against `localhost`.

## Layout

```
tag_vms.yml                     reconcile and write
validate_source.yml             dry run, report only
ansible.cfg                     local convenience only; AWX does not need it
execution-environment.yml       custom EE definition (xlsx path only)
collections/requirements.yml    optional collections, auto-installed by AWX
requirements.txt                optional Python deps (xlsx path only)
inventory/localhost.yml         single local host
inventory/group_vars/all.yml    site configuration (no secrets)
files/vm_tags.csv               your source of truth (not in git yet)
roles/proxmox_tagging/
  lookup_plugins/read_table.py  csv/tsv/xlsx -> list of row dicts
  filter_plugins/tag_plan.py    diff engine (source vs live tags)
  tasks/                        load_source, fetch_current, plan, apply, report
scripts/make_example_source.py  regenerates files/vm_tags.example.{csv,xlsx}
tests/                          34 unit tests
```

The playbooks live at the repo root and the plugins live inside the role on
purpose. Ansible auto-loads `roles/<role>/{lookup,filter}_plugins/` whenever the
role runs, and finds `roles/` next to a root-level playbook, so nothing here
depends on `ansible.cfg` being honoured. That is what makes it portable to AWX.

## Source file format

`files/vm_tags.example.csv` (or `.xlsx`, sheet `VMs`):

| VMID | Name | Environment | Owner | Application | Backup | Tags |
|---|---|---|---|---|---|---|
| 100 | web-prod-01 | Production | platform | nginx | daily | public;tier-web |

- **VMID** is the match key. If blank, **Name** is used instead; an ambiguous
  name fails the run rather than guessing.
- **Environment / Owner / Application / Backup** become prefixed tags —
  `Production` under `Environment` becomes `env-production`. The mapping lives
  in `pxt_tag_columns`.
- **Tags** is a free-form column, split on `,` `;` `|` or newline, for tags that
  do not belong to a category.
- Values are normalized to Proxmox-legal tags: spaces and illegal characters
  become `-`, and everything is lowercased by default. Proxmox de-duplicates
  tags case-insensitively unless `tag-style: case-sensitive=1` is set in
  `datacenter.cfg`, so leave `pxt_lowercase: true` unless you have set that.

### CSV or xlsx?

**Use CSV.** Both work — the format is detected from the file extension — but
CSV is better here on every axis that matters:

| | CSV | XLSX |
|---|---|---|
| Dependency | none, Python stdlib | `openpyxl` |
| AWX execution environment | stock `awx-ee` works | must build a custom EE |
| `git diff` on a metadata change | readable line diff | binary blob, no diff |
| Merge conflicts | resolvable | not resolvable |
| Formulas, colours, multiple sheets | lost | kept |

Keep authoring in Excel — just *Save As → CSV UTF-8* and commit that. The
export step is not extra friction, because committing the file is already a
manual step, and the reviewable diff is worth it: `git log -p files/vm_tags.csv`
becomes the audit trail for who changed which VM's metadata and when.

Two Excel-export gotchas, both handled:

- Excel's "CSV UTF-8" prepends a BOM, which silently turns the first column
  header into `﻿VMID` and breaks matching. The reader defaults to
  `utf-8-sig`, which strips it. Plain "CSV" export on Windows writes cp1252
  instead — set `pxt_source_encoding: cp1252` if you get a decode error.
- Excel eats leading zeros and reformats anything that looks like a date. VMIDs
  are integers so they are safe, but format the `Tags` column as **Text** if a
  value could ever look like a date or a number.

## Running locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install ansible-core pytest      # add openpyxl only for the xlsx path

# dry run — prints the plan, writes nothing
ansible-playbook validate_source.yml \
  -e pxt_api_token_id='ansible@pve!tagging' \
  -e pxt_api_token_secret="$PVE_TOKEN"

# apply
ansible-playbook tag_vms.yml \
  -e pxt_api_token_id='ansible@pve!tagging' \
  -e pxt_api_token_secret="$PVE_TOKEN"
```

`--check` also works and behaves like `validate_source.yml`. No collections
need installing for the default path.

## Key variables

| Variable | Default | Purpose |
|---|---|---|
| `pxt_api_url` | — | `https://pve01.example.com:8006` |
| `pxt_api_token_id` | — | `user@realm!tokenname` |
| `pxt_api_token_secret` | — | token UUID, inject from AWX |
| `pxt_source_path` | `files/vm_tags.csv` | source table; `.csv`, `.tsv` or `.xlsx` |
| `pxt_source_url` | `""` | download the table per run instead of reading the checkout |
| `pxt_source_encoding` | `utf-8-sig` | set `cp1252` for a non-UTF-8 Excel export |
| `pxt_source_sheet` | `VMs` | xlsx only, ignored for csv |
| `pxt_mode` | `replace` | `replace` = sheet is authoritative; `merge` = only ever adds |
| `pxt_protected_prefixes` | `[]` | tag prefixes never removed in replace mode |
| `pxt_max_changes` | `100` | abort before writing if more guests than this would change |
| `pxt_max_changed_pct` | `40` | same guard, as a percentage of the cluster |
| `pxt_apply` | `true` | set false to plan only |
| `pxt_throttle` | `5` | concurrent API writes |
| `pxt_guest_types` | `[qemu, lxc]` | which guest types to manage |

The two `pxt_max_*` guards exist because a broken spreadsheet — a shifted
column, a bad export — would otherwise retag the whole cluster in one run. They
are deliberately low; raise them consciously for a large planned migration.

## Proxmox API token

Create a dedicated token rather than using `root@pam`:

```bash
pveum role add TagManager -privs "VM.Audit,VM.Config.Options"
pveum user add ansible@pve
pveum aclmod / -user ansible@pve -role TagManager
pveum user token add ansible@pve tagging --privsep 0
```

`VM.Audit` covers the read, `VM.Config.Options` covers the tag write.

## Getting this repo onto AWX

AWX does not run playbooks from a directory on the host. It runs them from a
**Project**, and a Project has two possible sources.

### Option A — Git (recommended)

Push this repo anywhere AWX can reach over HTTPS or SSH: GitHub, GitLab, or a
Gitea/bare repo on your own network. Then create the Project with SCM Type
`Git`, set the URL, and enable *Update Revision on Launch*.

This is the option to pick. The spreadsheet is committed alongside the
playbook, so the Git history becomes the audit trail for tag changes, and AWX
re-syncs on every launch — no copying files onto the VM ever again.

If the AWX VM has no route to a Git host, a bare repo on the VM itself works:

```bash
# on the AWX VM
git init --bare /srv/git/proxmox-vm-tagging.git
# from your workstation
git remote add origin ssh://user@awx-vm/srv/git/proxmox-vm-tagging.git
git push -u origin main
```

Point the Project at that SSH URL with a Machine credential.

### Option B — Manual project path

AWX can read from `/var/lib/awx/projects/<subdir>`, exposed as SCM Type
`Manual`. The catch is *where* that path has to exist:

- **AWX on Kubernetes (awx-operator, the current install method)** — the path
  must be inside the task pod, not on the VM filesystem. You need a
  PersistentVolumeClaim (`projects_persistence: true` and a
  `projects_storage_class` in the AWX spec), then copy files in with
  `kubectl cp` on every change. Workable, but you are hand-syncing forever.
- **Older docker-compose AWX** — bind-mount a host directory to
  `/var/lib/awx/projects` and drop the repo there.

Only reach for this if Git is genuinely unavailable.

### Will it run once it is there?

Yes, on the CSV path, with no custom Execution Environment. The two things that
usually break a project moved into AWX are both designed around here:

- **Custom plugins.** They live in `roles/proxmox_tagging/lookup_plugins/` and
  `filter_plugins/`, which Ansible loads automatically with the role. Verified
  by running from an unrelated working directory with an empty `ansible.cfg` —
  the project's `ansible.cfg` is *not* required.
- **Python dependencies.** The CSV reader is standard library, and every module
  used is in `ansible.builtin`. The stock `awx-ee` image is enough. Only the
  xlsx path needs `openpyxl`, and therefore a custom EE.

## AWX setup

1. **Project** — see above; Git with *Update Revision on Launch*.
2. **Execution Environment** — leave it on the default `AWX EE`. Only if you
   keep the source as .xlsx do you need
   `ansible-builder build -t proxmox-tagging-ee:1.0.0 -f execution-environment.yml`,
   pushed to a registry and registered in AWX.
3. **Credential** — create a custom credential type so the token never appears
   in job output:

   *Input configuration*
   ```yaml
   fields:
     - id: pve_token_id
       type: string
       label: Token ID
     - id: pve_token_secret
       type: string
       label: Token Secret
       secret: true
   required: [pve_token_id, pve_token_secret]
   ```

   *Injector configuration*
   ```yaml
   extra_vars:
     pxt_api_token_id: "{{ pve_token_id }}"
     pxt_api_token_secret: "{{ pve_token_secret }}"
   ```

4. **Inventory** — a static inventory containing only `localhost`, with
   `ansible_connection=local`. Everything is API-driven; AWX never touches a
   guest over SSH.
5. **Job templates** — one on `validate_source.yml` (dry run) and one on
   `tag_vms.yml` (apply), both with the credential attached.
6. **Workflow** — chain dry run → approval node → apply. The approval node is
   what makes the 400 VM blast radius reviewable before it lands.
7. **Schedule** — run the dry run nightly to surface drift; keep the apply
   manual or weekly.

Both playbooks call `set_stats`, so `pxt_changed`, `pxt_unmanaged` and friends
are available to later workflow nodes and to notification templates.

## Where the source file lives

The default reads `files/vm_tags.csv` from the project checkout, so Git is the
audit trail — the team commits the export, AWX syncs it on launch. If it must
stay on SharePoint or a file share, set `pxt_source_url` instead and the role
downloads it to a temp dir per run.

Committing it is the better default: history, blame and PR review on metadata
changes for free.

## Tests

```bash
pytest tests/ -q      # 34 tests
```

Two areas, both chosen because they are where the risk is:

- **`tag_plan`** — normalization, replace vs merge, protected tags, unmatched
  rows, duplicate and malformed VMIDs, LXC vs QEMU routing.
- **`read_table`** — BOM stripping, cp1252 exports, quoted fields containing
  commas, blank rows, header offsets, and the openpyxl float-to-`100.0` trap.

## Why the raw API instead of `community.general.proxmox_kvm`

`proxmox_kvm` with `update: true` works, but it re-sends the whole guest config
per VM and its change reporting is unreliable for tags alone. Talking to
`/nodes/{node}/{type}/{vmid}/config` directly means one read for the entire
cluster, writes only for genuinely drifted guests, exact control over
merge semantics, and no `proxmoxer` dependency. At 400 guests that is one GET
plus N PUTs where N is usually single digits.

Swap in `community.general.proxmox_kvm` in `roles/proxmox_tagging/tasks/apply.yml`
if you would rather stay on the maintained module — the plan structure feeds it
unchanged.
