"""Filter plugins that diff desired VM metadata against live Proxmox notes."""

from __future__ import annotations

from ansible.errors import AnsibleFilterError

_DEFAULTS = {
    "vmid_column": "VMID",
    "name_column": "Name",
    "metadata_columns": {},
    "preserve_unmanaged": True,
    "write_empty": True,
    "skip_blank_vmid": True,
}


def _config(overrides):
    """Merge caller overrides onto the defaults without mutating either"""
    merged = dict(_DEFAULTS)
    merged.update(overrides or {})
    if not merged["metadata_columns"]:
        raise AnsibleFilterError("metadata_columns must map sheet headers to note keys")
    return merged


def normalize_notes(text):
    """Canonicalize a notes blob so cosmetic differences are not changes"""
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    trimmed = [line.rstrip() for line in lines]
    while trimmed and not trimmed[0]:
        trimmed.pop(0)
    while trimmed and not trimmed[-1]:
        trimmed.pop()
    return "\n".join(trimmed)


def parse_notes(text, managed_keys):
    """Split a notes blob into managed Key=Value pairs and everything else"""
    managed = {}
    unmanaged = []
    for line in normalize_notes(text).split("\n"):
        key, separator, value = line.partition("=")
        if separator and key.strip() in managed_keys:
            managed[key.strip()] = value.strip()
        elif line:
            unmanaged.append(line)
    return managed, unmanaged


def desired_pairs(row, cfg):
    """Build the ordered Key=Value pairs for one spreadsheet row"""
    pairs = []
    for column, key in cfg["metadata_columns"].items():
        value = str(row.get(column, "") or "").strip()
        if value:
            pairs.append([key, value])
    return pairs


def render_notes(pairs, preserved):
    """Render the managed block followed by any preserved free-text lines"""
    lines = ["{0}={1}".format(key, value) for key, value in pairs]
    return "\n".join(lines + list(preserved)).strip()


def _resolve_target(row, cfg, by_vmid, by_name):
    """Match a spreadsheet row to a live guest by vmid, falling back to name"""
    raw_vmid = str(row.get(cfg["vmid_column"], "")).strip()
    if raw_vmid:
        try:
            return by_vmid.get(int(float(raw_vmid))), None
        except (TypeError, ValueError):
            return None, "vmid {0!r} is not a number".format(raw_vmid)

    raw_name = str(row.get(cfg["name_column"], "")).strip()
    if raw_name:
        matches = by_name.get(raw_name.lower(), [])
        if len(matches) > 1:
            return None, "name {0!r} matches {1} guests".format(raw_name, len(matches))
        return (matches[0] if matches else None), None

    if cfg["skip_blank_vmid"]:
        return None, None
    return None, "row has neither a vmid nor a name"


def _index_current(current):
    """Index live guests by vmid and by lowercased name"""
    by_vmid = {}
    by_name = {}
    for guest in current or []:
        vmid = int(guest.get("vmid"))
        record = {
            "vmid": vmid,
            "node": guest.get("node"),
            "name": guest.get("name") or "",
            "type": "lxc" if guest.get("type") in ("lxc", "ct") else "qemu",
        }
        by_vmid[vmid] = record
        by_name.setdefault(record["name"].lower(), []).append(record)
    return by_vmid, by_name


def resolve_targets(rows, current, **kwargs):
    """Match spreadsheet rows to live guests before their configs are fetched"""
    cfg = _config(kwargs)
    by_vmid, by_name = _index_current(current)

    targets, missing, invalid = [], [], []
    seen = set()

    for row in rows or []:
        guest, error = _resolve_target(row, cfg, by_vmid, by_name)
        if error:
            invalid.append({"row": row.get("_row"), "reason": error})
            continue
        if guest is None:
            identifier = row.get(cfg["vmid_column"]) or row.get(cfg["name_column"])
            if identifier:
                missing.append({"row": row.get("_row"), "identifier": str(identifier)})
            continue
        if guest["vmid"] in seen:
            invalid.append(
                {"row": row.get("_row"), "reason": "vmid {0} listed twice".format(guest["vmid"])}
            )
            continue
        seen.add(guest["vmid"])

        pairs = desired_pairs(row, cfg)
        if not pairs and not cfg["write_empty"]:
            continue

        targets.append(dict(guest, row=row.get("_row"), desired_pairs=pairs))

    unmanaged = [
        {"vmid": vmid, "name": guest["name"]}
        for vmid, guest in sorted(by_vmid.items())
        if vmid not in seen
    ]

    return {
        "targets": sorted(targets, key=lambda item: item["vmid"]),
        "missing_in_proxmox": missing,
        "invalid_rows": invalid,
        "unmanaged": unmanaged,
        "guests": len(by_vmid),
    }


def _current_notes_by_vmid(config_results):
    """Pull the description out of each registered config GET"""
    notes = {}
    for result in config_results or []:
        target = result.get("item") or {}
        vmid = target.get("vmid")
        if vmid is None:
            continue
        data = (result.get("json") or {}).get("data") or {}
        notes[int(vmid)] = data.get("description") or ""
    return notes


def notes_plan(resolved, config_results, **kwargs):
    """Diff desired metadata against the notes currently set on each guest"""
    cfg = _config(kwargs)
    managed_keys = set(cfg["metadata_columns"].values())
    current_notes = _current_notes_by_vmid(config_results)

    changes, unchanged = [], []
    for target in resolved.get("targets", []):
        current_raw = current_notes.get(target["vmid"], "")
        _, unmanaged_lines = parse_notes(current_raw, managed_keys)
        preserved = unmanaged_lines if cfg["preserve_unmanaged"] else []

        desired = render_notes(target["desired_pairs"], preserved)
        current = normalize_notes(current_raw)

        entry = {
            "vmid": target["vmid"],
            "node": target["node"],
            "name": target["name"],
            "type": target["type"],
            "row": target["row"],
            "current_notes": current,
            "desired_notes": desired,
            "preserved_lines": preserved,
            "managed_keys": [pair[0] for pair in target["desired_pairs"]],
        }
        (changes if desired != current else unchanged).append(entry)

    total = resolved.get("guests") or 1
    return {
        "changes": changes,
        "unchanged": unchanged,
        "missing_in_proxmox": resolved.get("missing_in_proxmox", []),
        "unmanaged": resolved.get("unmanaged", []),
        "invalid_rows": resolved.get("invalid_rows", []),
        "stats": {
            "guests": resolved.get("guests", 0),
            "targets": len(resolved.get("targets", [])),
            "changed": len(changes),
            "unchanged": len(unchanged),
            "missing_in_proxmox": len(resolved.get("missing_in_proxmox", [])),
            "unmanaged": len(resolved.get("unmanaged", [])),
            "invalid_rows": len(resolved.get("invalid_rows", [])),
            "changed_pct": round(len(changes) * 100.0 / total, 1),
        },
    }


class FilterModule(object):
    def filters(self):
        return {
            "resolve_targets": resolve_targets,
            "notes_plan": notes_plan,
            "normalize_notes": normalize_notes,
            "render_notes": render_notes,
        }
