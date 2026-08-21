# proxmox-vm-tagging

Reconciles Proxmox VE guest **Notes / Description** fields against a CSV export
of the Excel source of truth. Built to run as an AWX job template over a ~400
guest cluster.

Metadata is written as one `Key=Value` per line:

```
Owner=wai.kwong@digitaledgedc.com
Environment=PRO
Application=Proxmox Auto Inventory
Site=HKGA1
InternetFacing=No
Criticality=Low
BusinessService=Infrastructure Automation
ContactGroup=ITOps
BackupRequired=No
SupportVendor=NA
```

The playbook reads the spreadsheet, matches rows to live guests, reads their
current notes, computes a diff, enforces safety limits, and writes only the
guests that actually drifted. Nothing is SSH'd into — it is entirely API-driven
and runs against `localhost`.

## Why notes and not tags

Proxmox tags are restricted to `[a-zA-Z0-9_][a-zA-Z0-9_\-+.]*` — no `=`, no
`@`, no spaces — and render as chips on one line, not as separate lines. So
`Owner=wai.kwong@digitaledgedc.com`, `Application=Proxmox Auto Inventory` and
`BusinessService=Infrastructure Automation` cannot be expressed as tags without
mangling them beyond recognition.

The Notes field has no such limits, which is why it is the conventional home for
CMDB metadata in Proxmox. **Guest tags are never touched by this playbook.**

If you later want tag-based filtering in the UI as well, the right move is to
derive a small set of sanitized tags (`env-pro`, `site-hkga1`,
`criticality-low`) from the safe columns — additive, and it leaves the notes
alone.

## Layout

```
sync_vm_metadata.yml            the only playbook; dry run unless told otherwise
ansible.cfg                     local convenience only; AWX does not need it
inventory/localhost.yml         single local host, for local runs
roles/proxmox_tagging/
  defaults/main.yml             every setting, and the only place they live
  lookup_plugins/read_csv.py    csv -> list of row dicts
  filter_plugins/metadata_plan.py  diff engine (source vs live notes)
  tasks/                        load_source, fetch_current, plan, apply, report
files/vm_tags.example.csv       the expected column layout
scripts/make_example_source.py  regenerates files/vm_tags.example.csv
tests/                          49 unit tests
```

There is no `collections/requirements.yml` and no `requirements.txt` on purpose.
Everything the role uses ships with `ansible-core`, so the stock `awx-ee` image
runs it unchanged and every project sync stays fast.

The playbooks live at the repo root and the plugins live inside the role on
purpose. Ansible auto-loads `roles/<role>/{lookup,filter}_plugins/` whenever the
role runs, and finds `roles/` next to a root-level playbook, so nothing here
depends on `ansible.cfg` being honoured. That is what makes it portable to AWX.

## Source file format

**CSV only.** See `files/vm_tags.example.csv`. Anything that is not a `.csv`
is rejected up front with a message telling you to re-export, rather than being
parsed as text and producing garbage rows.

**The header row is found automatically.** `pxt_source_header_row: "auto"`
scans for the first row containing the `VMID` column, so it does not matter
whether the export keeps the "Metadata Standard:" title, keeps only the spacer
row, or starts straight at the headers — all three read identically. Pin a
number instead of `"auto"` if you ever need to force it.

Reported row numbers stay relative to the source file, so a row logged as
`row 3` is line 3 of the CSV.

Two columns are match keys:

- **VMID** — the primary key. If blank, **Name** is used instead; an ambiguous
  name fails the run rather than guessing.

Ten columns become notes, in this order, via `pxt_metadata_columns`:

| Sheet header | Note key |
|---|---|
| `Owner` | `Owner` |
| `Environment` | `Environment` |
| `Application` | `Application` |
| `Site` | `Site` |
| `Internet Facing` | `InternetFacing` |
| `Criticality` | `Criticality` |
| `Business Service` | `BusinessService` |
| `Contact Group` | `ContactGroup` |
| `BackupRequired` | `BackupRequired` |
| `SupportVendor` | `SupportVendor` |

The left column must match your headers **exactly**. If any is missing the run
fails and prints the headers it actually found, so a rename is a one-line fix in
`roles/proxmox_tagging/defaults/main.yml`.

Everything else in the sheet — `Host`, `Type`, `IP Address`, `Status`, `OS`,
`CPU`, `Memory`, `Disk` — is a fact read *from* Proxmox, not metadata, and is
never written back.

### Behaviour rules

- **Blank cells are omitted**, not written as `Owner=`. A row where every
  metadata column is blank clears the managed keys entirely — set
  `pxt_write_empty: false` to skip such rows instead.
- **Free text is written literally.** `Owner=Can remove` and `Owner=No idea`
  go in as-is, because the sheet is the source of truth.
- **Existing non-managed note lines are preserved** and re-appended below the
  managed block. If a VM's notes say `migrated from esxi 2024-03`, that line
  survives. Set `pxt_preserve_unmanaged: false` to make the spreadsheet
  authoritative over the whole field instead.
- **Key order is the config order**, not alphabetical, so notes are stable and
  re-runs are genuinely idempotent.

### Why CSV only

The reader used to accept `.xlsx` and `.tsv` too. That is gone, because xlsx
was worse on every axis that matters here:

| | CSV | XLSX |
|---|---|---|
| Dependency | none, Python stdlib | `openpyxl` |
| AWX execution environment | stock `awx-ee` works | must build a custom EE |
| `git diff` on a metadata change | readable line diff | binary blob, no diff |
| Merge conflicts | resolvable | not resolvable |
| Formulas, colours, multiple sheets | lost | kept |

Dropping it removes the only reason this project would ever need a custom
execution environment, which is why `execution-environment.yml` and
`requirements.txt` are no longer in the repo.

Keep authoring in Excel — just *Save As → CSV UTF-8*. The export step is not
extra friction, because getting the file to AWX is already a manual step, and
the reviewable diff is worth it.

Two Excel-export gotchas, both handled:

- Excel's "CSV UTF-8" prepends a BOM, which silently turns the first column
  header into `﻿VMID` and breaks matching. The reader defaults to
  `utf-8-sig`, which strips it. Plain "CSV" export on Windows writes cp1252
  instead — set `pxt_source_encoding: cp1252` if you get a decode error.
- Excel eats leading zeros and reformats anything that looks like a date. VMIDs
  are integers so they are safe, but format any column as **Text** if a value
  could ever look like a date or a number.

## Running locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install ansible-core pytest      # that is the whole dependency list

# dry run — prints the plan, writes nothing. This is the default
ansible-playbook sync_vm_metadata.yml \
  -e pxt_api_token_id='ansible@pve!tagging' \
  -e pxt_api_token_secret="$PVE_TOKEN"

# apply — writing takes an explicit opt-in
ansible-playbook sync_vm_metadata.yml \
  -e pxt_dry_run=false \
  -e pxt_api_token_id='ansible@pve!tagging' \
  -e pxt_api_token_secret="$PVE_TOKEN"
```

There is one playbook. `pxt_dry_run` defaults to `true`, so a run that forgets
to set it reports instead of writing. `--check` also forces a dry run. There are
no collections or Python packages to install.

## Key variables

| Variable | Default | Purpose |
|---|---|---|
| `pxt_api_url` | — | `https://pve01.example.com:8006` |
| `pxt_api_token_id` | — | `user@realm!tokenname` |
| `pxt_api_token_secret` | — | token UUID, inject from AWX |
| `pxt_source_path` | `files/vm_tags.csv` | source csv; a non-`.csv` file is rejected |
| `pxt_source_url` | `""` | download the csv per run instead of reading the checkout |
| `pxt_source_encoding` | `utf-8-sig` | set `cp1252` for a non-UTF-8 Excel export |
| `pxt_source_delimiter` | `,` | set `;` for an Excel export from a non-English locale |
| `pxt_source_header_row` | `"auto"` | header row; `auto` finds it via the VMID column |
| `pxt_metadata_columns` | 10 columns | sheet header -> note key, in write order |
| `pxt_preserve_unmanaged` | `true` | keep existing non-managed note lines |
| `pxt_write_empty` | `true` | write rows whose metadata is entirely blank |
| `pxt_max_changes` | `100` | abort before writing if more guests than this would change |
| `pxt_max_changed_pct` | `40` | same guard, as a percentage of the cluster |
| `pxt_dry_run` | `true` | `true` plans only; `false` writes. Writing is an explicit opt-in |
| `pxt_throttle` | `5` | concurrent API writes |
| `pxt_guest_types` | `[qemu, lxc]` | which guest types to manage |

The two `pxt_max_*` guards exist because a broken spreadsheet — a shifted
column, a bad export — would otherwise rewrite notes across the whole cluster in
one run. They
are deliberately low; raise them consciously for a large planned migration.

## Proxmox API token

Create a dedicated token rather than using `root@pam`:

```bash
pveum role add TagManager -privs "VM.Audit,VM.Config.Options"
pveum user add ansible@pve
pveum aclmod / -user ansible@pve -role TagManager
pveum user token add ansible@pve tagging --privsep 0
```

`VM.Audit` covers the read, `VM.Config.Options` covers the notes write.

## AWX

The AWX objects are **not** created by hand — they are managed as code in the
`terraform-awx` repo, which runs on the AWX VM. It creates the project (this
repo, synced from GitHub on every launch), a localhost inventory, the Proxmox
credential, and two job templates:

| Template | `pxt_dry_run` | What it does |
|---|---|---|
| `proxmox-vm-tagging-validate` | `true` | Prints the plan, writes nothing |
| `proxmox-vm-tagging-apply` | `false` | Writes notes into Proxmox |

Both point at `sync_vm_metadata.yml`. Writing is an explicit opt-in, so a
misconfigured template reports rather than writes. Restrict *Execute* on the
apply template with AWX RBAC.

Two things usually break a project moved into AWX, and both are designed around
here:

- **Custom plugins.** They live in `roles/proxmox_tagging/lookup_plugins/` and
  `filter_plugins/`, which Ansible loads automatically with the role. Verified
  by running from an unrelated working directory with an empty `ansible.cfg` —
  the project's `ansible.cfg` is *not* required.
- **Python dependencies.** There are none. The CSV reader is standard library
  and every module used is in `ansible.builtin`, so the stock `awx-ee` image is
  enough and no custom Execution Environment is needed.

The playbook calls `set_stats`, so `pxt_changed`, `pxt_unmanaged` and friends
are available to later workflow nodes and to notification templates.

## Where the source file lives

Two options:

- **`pxt_source_path`** — reads `files/vm_tags.csv` from the project checkout,
  so Git becomes the audit trail: history, blame and PR review on metadata
  changes for free.
- **`pxt_source_url`** — downloads the csv to a temp dir per run and deletes it
  afterwards. Use this when the export must not be committed. This is what the
  dev environment does: the csv is served over HTTP from the AWX VM itself.

Keep the `.csv` suffix on the URL — the reader checks the extension.

## Tests

```bash
pytest tests/ -q      # 49 tests
```

Two areas, both chosen because they are where the risk is:

- **`metadata_plan`** — note rendering and parsing, idempotency, blank-cell
  omission, free-text preservation, unmatched rows, duplicate and malformed
  VMIDs, LXC vs QEMU routing.
- **`read_csv`** — header auto-detection across all three preamble layouts,
  BOM stripping, cp1252 exports, quoted fields containing commas, blank rows,
  and rejection of non-csv files.

## API call volume

`/cluster/resources` does not return descriptions, so current notes have to be
read per guest:

- 1 GET for the cluster index
- 1 GET per guest **listed in the spreadsheet** (not per guest in the cluster)
- 1 PUT per guest whose notes actually drifted

At 400 rows that is ~401 reads and usually a handful of writes, throttled to
`pxt_throttle` (5) concurrent requests. A steady-state run where nothing changed
still costs the 401 reads; that is the price of accurate drift detection, and it
is why the nightly job should be the dry run rather than the apply.

`community.general.proxmox_kvm` is not used because it re-sends the whole guest
config per VM and its change reporting is unreliable for a single field. Talking
to `/nodes/{node}/{type}/{vmid}/config` directly gives exact control over merge
semantics and drops the `proxmoxer` dependency.
