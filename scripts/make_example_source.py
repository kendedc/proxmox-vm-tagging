#!/usr/bin/env python3
"""Generate the example metadata source in both csv and xlsx form.

Mirrors the exported sheet: a spacer row, headers on row 2, data from row 3.
"""

from __future__ import annotations

import csv
import pathlib

# Excel keeps a spacer row above the headers once the title line is dropped,
# so the export has headers on line 2 and data from line 3
PREAMBLE = ((),)

HEADERS = (
    "VMID", "Name", "Host", "Type", "IP Address", "Status", "OS", "CPU",
    "Memory", "Disk", "Owner", "Environment", "Application", "Site",
    "Internet Facing", "Criticality", "Business Service", "Contact Group",
    "BackupRequired", "SupportVendor",
)

ROWS = (
    (105, "BenWin", "server1", "VM", "10.1.2.200", "running", "Win10", 4,
     "8.0 GB", "200G", "ben.leung@digitaledgedc.com", "PRO",
     "Jumphost for recovery", "HKGA1", "No", "Low", "IT DR", "ITInfra",
     "No", "NA"),
    (134, "lhkgautoinv1", "server1", "VM", "10.1.2.54", "running", "Linux", 2,
     "4.0 GB", "100G", "wai.kwong@digitaledgedc.com", "PRO",
     "Proxmox Auto Inventory", "HKGA1", "No", "Low",
     "Infrastructure Automation", "ITOps", "No", "NA"),
    (117, "wireguard", "server3", "VM", "", "stopped", "Linux", 4,
     "4.0 GB", "50G", "Kelvin.chia@digitaledgedc.com", "PRO", "WireGuard",
     "HKGA1", "Yes", "Low", "Backdoor VPN for Kelvin", "Kelvin", "No", "NA"),
    (106, "gideon", "server2", "VM", "", "stopped", "Linux", 4,
     "8.0 GB", "100G", "Can remove", "", "", "", "", "", "", "", "", ""),
    (136, "dns1", "server2", "Container", "10.1.2.41", "running", "ubuntu", 8,
     "2.0 GB", "10G", "", "", "", "", "", "", "", "", "", ""),
)

FILES_DIR = pathlib.Path(__file__).resolve().parent.parent / "files"


def write_csv(path):
    """Write the example as UTF-8 csv, matching Excel's 'CSV UTF-8' export"""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for row in PREAMBLE:
            writer.writerow(row)
        writer.writerow(HEADERS)
        writer.writerows(ROWS)
    print("wrote {0}".format(path))


def write_xlsx(path):
    """Write the example workbook, skipped when openpyxl is unavailable"""
    try:
        from openpyxl import Workbook
    except ImportError:
        print("skipping {0}: openpyxl not installed".format(path))
        return

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "VMs"
    for row in PREAMBLE:
        sheet.append(row)
    sheet.append(HEADERS)
    for row in ROWS:
        sheet.append(row)
    sheet.freeze_panes = "A3"

    workbook.save(path)
    print("wrote {0}".format(path))


def main():
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(FILES_DIR / "vm_tags.example.csv")
    write_xlsx(FILES_DIR / "vm_tags.example.xlsx")


if __name__ == "__main__":
    main()
