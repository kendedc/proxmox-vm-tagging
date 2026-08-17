"""Filter plugin that diffs desired tags (from Excel) against live Proxmox tags."""

from __future__ import annotations

import re

from ansible.errors import AnsibleFilterError

# Proxmox accepts alphanumerics and _ - + . with an alphanumeric/underscore first char
_INVALID_CHARS = re.compile(r"[^A-Za-z0-9_\-+.]")
_LEADING_JUNK = re.compile(r"^[^A-Za-z0-9_]+")
_SPLIT_CELL = re.compile(r"[,;|\n]")

_DEFAULTS = {
    "vmid_column": "vmid",
    "name_column": "name",
    "tags_column": "tags",
    "tag_columns": {},
    "separator": "-",
    "mode": "replace",
    "lowercase": True,
    "protected_prefixes": [],
    "protected_tags": [],
    "skip_blank_vmid": True,
}


def _config(overrides):
    """Merge caller overrides onto the defaults without mutating either"""
    merged = dict(_DEFAULTS)
    merged.update(overrides or {})
    if merged["mode"] not in ("replace", "merge"):
        raise AnsibleFilterError(
            "mode must be 'replace' or 'merge', got {0!r}".format(merged["mode"])
        )
    return merged


def normalize_tag(raw, separator="-", lowercase=True):
    """Coerce one raw value into a Proxmox-legal tag, or None if unusable"""
    text = str(raw or "").strip()
    if not text:
        return None
    text = text.replace(" ", separator)
    text = _INVALID_CHARS.sub(separator, text)
    text = _LEADING_JUNK.sub("", text)
    text = re.sub(re.escape(separator) + r"{2,}", separator, text)
    text = text.strip(separator)
    if not text:
        return None
    return text.lower() if lowercase else text


def split_tags(value, separator="-", lowercase=True):
    """Split a free-form cell into a sorted, de-duplicated tag list"""
    parts = _SPLIT_CELL.split(str(value or ""))
    tags = (normalize_tag(part, separator, lowercase) for part in parts)
    return sorted({tag for tag in tags if tag})


def desired_tags_for_row(row, cfg):
    """Build the full desired tag set for one spreadsheet row"""
    tags = set(split_tags(row.get(cfg["tags_column"]), cfg["separator"], cfg["lowercase"]))

    for column, prefix in (cfg["tag_columns"] or {}).items():
        for value in split_tags(row.get(column), cfg["separator"], cfg["lowercase"]):
            combined = "{0}{1}{2}".format(prefix, cfg["separator"], value) if prefix else value
            tag = normalize_tag(combined, cfg["separator"], cfg["lowercase"])
            if tag:
                tags.add(tag)

    return sorted(tags)


def _is_protected(tag, cfg):
    """True when a tag must survive even in replace mode"""
    if tag in set(cfg["protected_tags"] or []):
        return True
    return any(tag.startswith(prefix) for prefix in (cfg["protected_prefixes"] or []))


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
            "tags": sorted({t for t in str(guest.get("tags") or "").split(";") if t}),
        }
        by_vmid[vmid] = record
        by_name.setdefault(record["name"].lower(), []).append(record)
    return by_vmid, by_name


def _merge_desired(current_tags, desired, cfg):
    """Apply the merge mode and protected-tag rules to produce the final set"""
    if cfg["mode"] == "merge":
        return sorted(set(current_tags) | set(desired))
    keep = {tag for tag in current_tags if _is_protected(tag, cfg)}
    return sorted(set(desired) | keep)


def tag_plan(rows, current, **kwargs):
    """Return the full change plan for a spreadsheet against live Proxmox state"""
    cfg = _config(kwargs)
    by_vmid, by_name = _index_current(current)

    changes, unchanged, missing, invalid = [], [], [], []
    seen_vmids = set()

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
        if guest["vmid"] in seen_vmids:
            invalid.append(
                {"row": row.get("_row"), "reason": "vmid {0} listed twice".format(guest["vmid"])}
            )
            continue
        seen_vmids.add(guest["vmid"])

        desired = _merge_desired(guest["tags"], desired_tags_for_row(row, cfg), cfg)
        entry = {
            "vmid": guest["vmid"],
            "node": guest["node"],
            "name": guest["name"],
            "type": guest["type"],
            "current_tags": guest["tags"],
            "desired_tags": desired,
            "added": sorted(set(desired) - set(guest["tags"])),
            "removed": sorted(set(guest["tags"]) - set(desired)),
        }
        (changes if desired != guest["tags"] else unchanged).append(entry)

    unmanaged = [
        {"vmid": guest["vmid"], "name": guest["name"], "tags": guest["tags"]}
        for vmid, guest in sorted(by_vmid.items())
        if vmid not in seen_vmids
    ]

    total = len(by_vmid) or 1
    return {
        "changes": sorted(changes, key=lambda item: item["vmid"]),
        "unchanged": sorted(unchanged, key=lambda item: item["vmid"]),
        "missing_in_proxmox": missing,
        "unmanaged": unmanaged,
        "invalid_rows": invalid,
        "stats": {
            "rows": len(rows or []),
            "guests": len(by_vmid),
            "changed": len(changes),
            "unchanged": len(unchanged),
            "missing_in_proxmox": len(missing),
            "unmanaged": len(unmanaged),
            "invalid_rows": len(invalid),
            "changed_pct": round(len(changes) * 100.0 / total, 1),
        },
    }


class FilterModule(object):
    def filters(self):
        return {
            "tag_plan": tag_plan,
            "normalize_tag": normalize_tag,
            "split_tags": split_tags,
        }
