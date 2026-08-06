#!/usr/bin/env python3
"""
LSC Profile Bootstrap — clone profile + Admin Console + DbSchema + layouts/apps
from a golden profile in a source org into a deployable package for a target org.

Usage:
  python3 lsc_profile_bootstrap.py SOURCE_ORG DEST_ORG "Source_Profile_Api_Name" "Target Profile Label"
  python3 lsc_profile_bootstrap.py SOURCE_ORG DEST_ORG "Custom: Golden Profile" "New Profile Label" --package-only

  Parameter 3 = source profile API name (metadata fullName), e.g. "Field Sales Representative"
                or "Custom: My Golden Profile"
  Parameter 4 = target profile display Name (Setup label), NOT the API name

Requires: Salesforce CLI (sf), Python 3.9+, network access to both orgs.

If source or destination org is not authenticated, the script opens browser login
automatically (sf org login web) before continuing.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, unquote

SCRIPT_DIR = Path(__file__).resolve().parent
NS = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", NS)
TOTAL_STEPS = 9

# Standard Salesforce profiles (partial list); anything else is treated as custom for metadata API names.
STANDARD_PROFILES = {
    "Chatter External User",
    "Chatter Free User",
    "Chatter Moderator User",
    "Contract Manager",
    "Custom: Marketing Profile",
    "Custom: Sales Profile",
    "Custom: Support Profile",
    "Guest License User",
    "Identity User",
    "Marketing User",
    "Minimum Access - Salesforce",
    "Read Only",
    "Salesforce API Only System Integrations",
    "Solution Manager",
    "Standard Platform User",
    "Standard User",
    "System Administrator",
}


@dataclass
class RunLog:
    """Collects verifiable actions for the execution summary."""
    steps: list[str] = field(default_factory=list)
    cloned_admin_settings: list[str] = field(default_factory=list)
    dbschema_assigned: list[str] = field(default_factory=list)
    source_admin_setting_names: list[str] = field(default_factory=list)

    def record(self, message: str) -> None:
        self.steps.append(message)


RUN = RunLog()


def log(msg: str) -> None:
    print(msg, flush=True)


def step_header(num: int, title: str) -> None:
    log(f"\n{'=' * 60}")
    log(f"STEP {num}/{TOTAL_STEPS}: {title}")
    log(f"{'=' * 60}")


def step_detail(msg: str) -> None:
    log(f"  → {msg}")
    RUN.record(msg)


def step_ok(msg: str) -> None:
    log(f"  ✓ {msg}")
    RUN.record(msg)


def run_sf(args: list[str], *, check: bool = True, interactive: bool = False) -> subprocess.CompletedProcess:
    cmd = ["sf", *args]
    log(f"  $ {' '.join(cmd)}")
    if interactive:
        return subprocess.run(cmd, text=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Command failed ({result.returncode}): {err}")
    return result


def sf_json(args: list[str], *, check: bool = True) -> dict:
    result = run_sf([*args, "--json"], check=check)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from sf: {result.stdout[:500]}") from exc


def is_org_connected(alias: str) -> bool:
    data = sf_json(["org", "display", "--target-org", alias], check=False)
    if data.get("status") != 0:
        return False
    return bool(data.get("result", {}).get("username"))


def ensure_org_login(alias: str, role: str, instance_url: Optional[str] = None) -> str:
    """Ensure org is authenticated; open browser login if needed. Returns username."""
    step_detail(f"Checking {role} org alias '{alias}'")
    if is_org_connected(alias):
        username = sf_json(["org", "display", "--target-org", alias])["result"]["username"]
        step_ok(f"{role} org already connected: {alias} ({username})")
        return username

    step_detail(f"{role} org not connected — starting browser login for '{alias}'")
    log("  ⚠ Complete Salesforce login in your browser, then return to this terminal.")
    login_args = ["org", "login", "web", "--alias", alias]
    if instance_url:
        login_args.extend(["--instance-url", instance_url])
    result = run_sf(login_args, check=False, interactive=True)
    if result.returncode != 0:
        raise RuntimeError(f"Login failed for {role} org alias '{alias}'")

    if not is_org_connected(alias):
        raise RuntimeError(
            f"Login finished but {role} org '{alias}' is still not connected. "
            f"Retry: sf org login web --alias {alias}"
        )
    username = sf_json(["org", "display", "--target-org", alias])["result"]["username"]
    step_ok(f"{role} org logged in: {alias} ({username})")
    return username


def org_ok(alias: str) -> None:
    """Legacy helper — use ensure_org_login in main flow."""
    if not is_org_connected(alias):
        raise RuntimeError(f"Org alias '{alias}' is not authenticated.")
    username = sf_json(["org", "display", "--target-org", alias])["result"]["username"]
    log(f"  Connected: {alias} ({username})")


@dataclass(frozen=True)
class ResolvedProfile:
    api_name: str  # metadata fullName, e.g. "Custom: FSR POC" or "System Administrator"
    name: str  # display Name (Setup label)
    custom: bool
    id: Optional[str] = None


def normalize_profile_api_name(api_name: str) -> str:
    """Normalize for comparison (decode URL encoding, unify Custom: prefix spacing)."""
    s = unquote(api_name.strip())
    s = re.sub(r"^Custom\s*:\s*", "Custom: ", s, flags=re.IGNORECASE)
    if s.lower().startswith("custom:"):
        rest = s.split(":", 1)[1].strip()
        return f"Custom: {rest}"
    return s


def parse_profile_api_name(api_name: str) -> tuple[str, bool]:
    """Return (display Name, is_custom) from a profile metadata API name."""
    normalized = normalize_profile_api_name(api_name)
    if normalized.startswith("Custom: "):
        return normalized[len("Custom: ") :], True
    return normalized, False


def list_profiles_by_api_name(org: str) -> dict[str, ResolvedProfile]:
    """Index all profiles in org by metadata API fullName."""
    data = sf_json(["org", "list", "metadata", "--target-org", org, "--metadata-type", "Profile"])
    index: dict[str, ResolvedProfile] = {}
    for row in data.get("result", []):
        full_name = row.get("fullName")
        if not full_name:
            continue
        display, custom = parse_profile_api_name(full_name)
        profile_id = get_profile_id_by_name(org, display)
        resolved = ResolvedProfile(
            api_name=full_name,
            name=display,
            custom=custom,
            id=profile_id,
        )
        index[normalize_profile_api_name(full_name)] = resolved
    return index


def get_profile_id_by_name(org: str, profile_name: str) -> Optional[str]:
    safe = profile_name.replace("'", "\\'")
    data = sf_json(
        [
            "data",
            "query",
            "--target-org",
            org,
            "--query",
            f"SELECT Id, Name FROM Profile WHERE Name = '{safe}' LIMIT 1",
        ],
        check=False,
    )
    if data.get("status") != 0:
        return None
    records = data.get("result", {}).get("records", [])
    return records[0]["Id"] if records else None


def resolve_source_profile(org: str, source_profile_api_name: str) -> ResolvedProfile:
    """Resolve golden profile strictly by metadata API name."""
    normalized = normalize_profile_api_name(source_profile_api_name)
    index = list_profiles_by_api_name(org)
    if normalized not in index:
        log(
            f"  Hint: list API names with "
            f"`sf org list metadata --metadata-type Profile --target-org {org}`"
        )
        raise RuntimeError("source profile not found in sourceorg")
    resolved = index[normalized]
    if not resolved.id:
        raise RuntimeError(
            f"source profile not found in sourceorg (API name '{source_profile_api_name}' "
            f"listed in metadata but no Profile record with Name '{resolved.name}')"
        )
    return resolved


def resolve_target_profile(org: str, target_profile_name: str) -> Optional[ResolvedProfile]:
    """Resolve target by display Name only. Returns None if profile does not exist."""
    profile_id = get_profile_id_by_name(org, target_profile_name)
    if not profile_id:
        return None
    index = list_profiles_by_api_name(org)
    for resolved in index.values():
        if resolved.name == target_profile_name:
            return ResolvedProfile(
                api_name=resolved.api_name,
                name=target_profile_name,
                custom=resolved.custom,
                id=profile_id,
            )
    # Profile exists in SOQL but not yet in metadata index — infer API name
    custom = target_profile_name not in STANDARD_PROFILES
    api = profile_metadata_api_name(target_profile_name, custom)
    return ResolvedProfile(api_name=api, name=target_profile_name, custom=custom, id=profile_id)


def profile_metadata_api_name(profile_name: str, custom: bool) -> str:
    if custom:
        return f"Custom: {profile_name}"
    return profile_name


def profile_metadata_selector_from_api(api_name: str) -> str:
    return f"Profile:{quote(normalize_profile_api_name(api_name), safe='')}"


def is_dbschema_member(name: str) -> bool:
    return name.startswith("DbSchema_") or name.startswith("DBSchema_")


def is_member_for_source_profile(member_name: str, source: ResolvedProfile) -> bool:
    if is_dbschema_member(member_name):
        return False
    if source.id and source.id in member_name:
        return True
    if f"-Custom-{source.name}" in member_name or member_name.endswith(f"_{source.name}"):
        return True
    if f"-{source.name}" in member_name:
        return True
    return False


def analyze_source_lifesci_members(members: list[str], source: ResolvedProfile) -> dict[str, Any]:
    dbschema = [m for m in members if is_dbschema_member(m)]
    admin = [m for m in members if not is_dbschema_member(m)]
    source_admin = [m for m in admin if is_member_for_source_profile(m, source)]
    return {
        "total_retrieved": len(members),
        "dbschema_total": len(dbschema),
        "admin_console_total": len(admin),
        "source_profile_admin_count": len(source_admin),
        "source_profile_admin_names": source_admin,
    }


def count_profile_xml_sections(profile_file: Path) -> dict[str, int]:
    merge_tags = {
        "applicationVisibilities",
        "layoutAssignments",
        "tabVisibilities",
        "recordTypeVisibilities",
        "pageAccesses",
        "flowAccesses",
    }
    tree = ET.parse(profile_file)
    root = tree.getroot()
    counts: dict[str, int] = {}
    for tag in merge_tags:
        counts[tag] = sum(1 for elem in root.iter() if strip_ns(elem.tag) == tag)
    return counts


def retrieve_metadata(org: str, metadata_items: list[str], output_dir: Path) -> None:
    project_file = SCRIPT_DIR / "sfdx-project.json"
    shutil.copy(project_file, output_dir / "sfdx-project.json")
    manifest = output_dir / "manifest.xml"
    manifest.write_text(build_package_xml(metadata_items), encoding="utf-8")
    run_sf(
        [
            "project",
            "retrieve",
            "start",
            "--target-org",
            org,
            "--manifest",
            str(manifest),
            "--output-dir",
            str(output_dir),
            "--wait",
            "30",
        ]
    )


def build_package_xml(metadata_items: list[str]) -> str:
    types: dict[str, list[str]] = {}
    for item in metadata_items:
        if ":" not in item:
            continue
        mtype, name = item.split(":", 1)
        types.setdefault(mtype, []).append(name)
    members_xml = ""
    for mtype in sorted(types):
        members_xml += f"    <types>\n"
        for name in sorted(set(types[mtype])):
            members_xml += f"        <members>{name}</members>\n"
        members_xml += f"        <name>{mtype}</name>\n    </types>\n"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
{members_xml}    <version>67.0</version>
</Package>
"""


def find_profile_file(root: Path, profile: ResolvedProfile) -> Optional[Path]:
    profiles_dir = root / "force-app" / "main" / "default" / "profiles"
    if not profiles_dir.exists():
        profiles_dir = root / "profiles"
    if not profiles_dir.exists():
        return None
    api = normalize_profile_api_name(profile.api_name)
    candidates = [
        profiles_dir / f"{profile.name}.profile-meta.xml",
        profiles_dir / f"Custom%3A {profile.name}.profile-meta.xml",
        profiles_dir / f"Custom: {profile.name}.profile-meta.xml",
        profiles_dir / f"{api.replace(':', '%3A ')}.profile-meta.xml",
        profiles_dir / f"{api}.profile-meta.xml",
    ]
    for c in candidates:
        if c.exists():
            return c
    files = list(profiles_dir.glob("*.profile-meta.xml"))
    return files[0] if len(files) == 1 else None


def rename_profile_file(src: Path, dest_dir: Path, target_name: str, target_custom: bool) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_api = profile_metadata_api_name(target_name, target_custom)
    safe_filename = dest_api.replace(":", "%3A ") + ".profile-meta.xml"
    dest = dest_dir / safe_filename
    tree = ET.parse(src)
    root = tree.getroot()
    # Profile metadata root element text content is not fullName; filename drives identity on deploy.
    tree.write(dest, encoding="UTF-8", xml_declaration=True)
    return dest


def merge_profile_sections(source_file: Path, target_file: Path, out_file: Path) -> None:
    """Merge layout/app/tab/record-type assignments from source into target profile."""
    merge_tags = {
        "applicationVisibilities",
        "layoutAssignments",
        "tabVisibilities",
        "recordTypeVisibilities",
        "pageAccesses",
        "customMetadataTypeAccesses",
        "flowAccesses",
    }

    src_tree = ET.parse(source_file)
    tgt_tree = ET.parse(target_file)
    src_root = src_tree.getroot()
    tgt_root = tgt_tree.getroot()

    for tag in merge_tags:
        for child in list(tgt_root):
            if strip_ns(child.tag) == tag:
                tgt_root.remove(child)
        for child in src_root:
            if strip_ns(child.tag) == tag:
                tgt_root.append(child)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    tgt_tree.write(out_file, encoding="UTF-8", xml_declaration=True)


def strip_ns(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag



def profile_assignment_token(profile_name: str, custom: bool) -> str:
    return f"Custom-{profile_name}" if custom else profile_name


def is_profile_specific_config(filename: str, source_profile: str, source_profile_id: Optional[str]) -> bool:
    if f"-Custom-{source_profile}" in filename or f"_{source_profile}" in filename:
        return True
    if f"-{source_profile}." in filename or filename.endswith(f"-{source_profile}.lifeSciConfigRecord"):
        return True
    if source_profile_id and source_profile_id in filename:
        return True
    return False


def target_config_filename(
    filename: str,
    source_profile: str,
    target_profile: str,
    source_profile_id: Optional[str],
    target_profile_id: Optional[str],
    target_custom: bool,
) -> Optional[str]:
    """Return new filename for a profile-specific config, or None if not profile-specific."""
    new_name = filename
    replaced = False

    if f"-Custom-{source_profile}" in filename:
        repl = f"-Custom-{target_profile}" if target_custom else f"-{target_profile}"
        new_name = filename.replace(f"-Custom-{source_profile}", repl, 1)
        replaced = True
    elif f"-{source_profile}." in filename:
        repl = f"-Custom-{target_profile}." if target_custom else f"-{target_profile}."
        new_name = filename.replace(f"-{source_profile}.", repl, 1)
        replaced = True
    elif source_profile_id and source_profile_id in filename:
        # Prefer profile-name based metadata naming for deploy (works before target profile id exists)
        base = filename.split(".")[0]
        category = base.split("_")[0] if "_" in base else base.split("-")[0]
        suffix = "-Custom-" if target_custom else "-"
        new_name = f"{category}{suffix}{target_profile}.lifeSciConfigRecord"
        replaced = True

    if not replaced:
        return None
    return new_name


def clone_lifesci_config_records(
    config_dir: Path,
    source_profile: str,
    target_profile: str,
    source_custom: bool,
    target_custom: bool,
    source_profile_id: Optional[str],
    target_profile_id: Optional[str],
) -> dict:
    stats: dict[str, Any] = {
        "cloned": 0,
        "dbschema_updated": 0,
        "skipped": 0,
        "cloned_admin_files": [],
        "dbschema_files": [],
    }
    if not config_dir.exists():
        return stats

    target_token = profile_assignment_token(target_profile, target_custom)

    for path in list(config_dir.glob("*.lifeSciConfigRecord")):
        name = path.name
        if is_dbschema_member(name):
            continue
        if not is_profile_specific_config(name, source_profile, source_profile_id):
            stats["skipped"] += 1
            continue

        new_name = target_config_filename(
            name, source_profile, target_profile, source_profile_id, target_profile_id, target_custom
        )
        if not new_name or new_name == name:
            stats["skipped"] += 1
            continue

        dest = config_dir / new_name
        shutil.copy2(path, dest)
        update_assigned_to_in_file(dest, target_profile, target_custom)
        stats["cloned"] += 1
        stats["cloned_admin_files"].append(new_name)
        RUN.cloned_admin_settings.append(new_name)
        step_detail(f"Admin Console setting cloned: {name} → {new_name}")

    for path in config_dir.glob("*.lifeSciConfigRecord"):
        if not is_dbschema_member(path.name):
            continue
        if add_dbschema_assignment(path, target_token):
            stats["dbschema_updated"] += 1
            stats["dbschema_files"].append(path.name)
            RUN.dbschema_assigned.append(path.name)
            step_detail(f"DbSchema assignment added: {path.name} → profile '{target_token}'")

    return stats


def update_assigned_to_in_file(path: Path, profile_name: str, custom: bool) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    assigned = profile_assignment_token(profile_name, custom)
    text = re.sub(
        r"(<assignedTo>)(.*?)(</assignedTo>)",
        rf"\1{assigned}\3",
        text,
        count=1,
    )
    path.write_text(text, encoding="utf-8")


def add_dbschema_assignment(path: Path, target_token: str) -> bool:
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return add_dbschema_assignment_regex(path, target_token)

    root = tree.getroot()
    assigned_values = set()
    for elem in root.iter():
        if strip_ns(elem.tag) == "assignedTo" and elem.text:
            assigned_values.add(elem.text.strip())

    if target_token in assigned_values:
        return False

    block = ET.SubElement(root, f"{{{NS}}}assignments")
    assigned_el = ET.SubElement(block, f"{{{NS}}}assignedTo")
    assigned_el.text = target_token
    level_el = ET.SubElement(block, f"{{{NS}}}assignmentLevel")
    level_el.text = "Profile"

    tree.write(path, encoding="UTF-8", xml_declaration=True)
    return True


def add_dbschema_assignment_regex(path: Path, target_token: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if re.search(rf"<assignedTo>\s*{re.escape(target_token)}\s*</assignedTo>", text):
        return False
    insert = (
        f"\n    <assignments>\n"
        f"        <assignedTo>{target_token}</assignedTo>\n"
        f"        <assignmentLevel>Profile</assignmentLevel>\n"
        f"    </assignments>"
    )
    if "</LifeSciConfigRecord>" in text:
        text = text.replace("</LifeSciConfigRecord>", insert + "\n</LifeSciConfigRecord>")
        path.write_text(text, encoding="utf-8")
        return True
    return False


def clone_lifesci_metadata_records(
    org: str, source_profile_name: str, target_profile_name: str, target_org: str
) -> dict:
    """
    Optional fallback: clone LifeSciMetadataRecord + field values for legacy Admin Console storage.
    Uses Data API when LifeSciConfigRecord retrieve returns nothing useful.
    """
    stats = {"records": 0, "field_values": 0}
    if get_profile_id_by_name(target_org, target_profile_name):
        pass

    safe = source_profile_name.replace("'", "\\'")
    q = (
        "SELECT Id, Name, RecordApiName, IsActive FROM LifeSciMetadataRecord "
        f"WHERE Name LIKE '%{safe}%' OR Name LIKE '%' "
    )
    # Narrow: profile-specific records often embed profile id not name — best-effort only
    try:
        data = sf_json(["data", "query", "--target-org", org, "--query", q + " LIMIT 200"])
    except RuntimeError:
        log("  (Skipping LifeSciMetadataRecord fallback — not queryable in this org)")
        return stats

    records = data.get("result", {}).get("records", [])
    profile_specific = [
        r for r in records if source_profile_name.replace(" ", "") in r.get("Name", "").replace(" ", "")
    ]
    if not profile_specific:
        return stats

    log(f"  Found {len(profile_specific)} legacy LifeSciMetadataRecord rows (manual review recommended)")
    stats["records"] = len(profile_specific)
    return stats


def deploy_package(package_dir: Path, dest_org: str) -> None:
    run_sf(
        [
            "project",
            "deploy",
            "start",
            "--source-dir",
            str(package_dir / "force-app"),
            "--target-org",
            dest_org,
            "--wait",
            "30",
        ]
    )


def print_execution_summary(
    *,
    source_org: str,
    dest_org: str,
    source: ResolvedProfile,
    target_profile_name: str,
    profile_created: bool,
    source_analysis: dict[str, Any],
    lifesci_stats: dict[str, Any],
    profile_counts: dict[str, int],
    package_root: Path,
    deployed: bool,
) -> None:
    admin_generated = lifesci_stats.get("cloned", 0)
    dbschema_generated = lifesci_stats.get("dbschema_updated", 0)

    log(f"\n{'=' * 60}")
    log("EXECUTION SUMMARY")
    log(f"{'=' * 60}")
    log(f"  Source org:              {source_org}")
    log(f"  Destination org:         {dest_org}")
    log(f"  Source profile API:      {source.api_name}")
    log(f"  Source profile Name:     {source.name}")
    log(f"  Target profile Name:     {target_profile_name}")
    log(f"  Profile action:          {'CREATED (cloned)' if profile_created else 'UPDATED (merged assignments)'}")
    log(f"  Package path:            {package_root}")
    log(f"  Deployed to dest org:    {'Yes' if deployed else 'No (package-only)'}")
    log("")
    log("  --- Retrieved from source org ---")
    log(f"  LifeSciConfigRecord total:              {source_analysis.get('total_retrieved', 0)}")
    log(f"  Admin Console settings (non-DbSchema):  {source_analysis.get('admin_console_total', 0)}")
    log(f"  DbSchema object configs:                {source_analysis.get('dbschema_total', 0)}")
    log(
        f"  Source-profile Admin Console settings:  "
        f"{source_analysis.get('source_profile_admin_count', 0)}"
    )
    log("")
    log("  --- Generated for target profile ---")
    log(f"  Admin Console settings generated:       {admin_generated}")
    log(f"  DbSchema profiles assignments added:    {dbschema_generated}")
    log("")
    log("  --- Profile assignments copied ---")
    log(f"  Page layout assignments:                {profile_counts.get('layoutAssignments', 0)}")
    log(f"  Lightning / record-type assignments:    {profile_counts.get('recordTypeVisibilities', 0)}")
    log(f"  Salesforce app assignments:             {profile_counts.get('applicationVisibilities', 0)}")
    log(f"  Tab visibilities:                     {profile_counts.get('tabVisibilities', 0)}")
    log(f"{'=' * 60}")

    if source_analysis.get("source_profile_admin_names"):
        log("\n  Source-profile Admin Console settings found in golden org:")
        for name in source_analysis["source_profile_admin_names"]:
            log(f"    • {name}")

    if lifesci_stats.get("cloned_admin_files"):
        log("\n  Admin Console settings generated in package:")
        for name in lifesci_stats["cloned_admin_files"]:
            log(f"    • {name}")

    log("")


def write_readme(
    out_dir: Path,
    source_org: str,
    dest_org: str,
    source_profile: str,
    target_profile: str,
    profile_created: bool,
    stats: dict,
) -> None:
    readme = f"""# LSC Profile Bootstrap Package

Generated: {datetime.now(timezone.utc).isoformat()}

| Parameter | Value |
|-----------|-------|
| Source org | `{source_org}` |
| Destination org | `{dest_org}` |
| Source profile | `{source_profile}` |
| Target profile | `{target_profile}` |
| Profile created | `{profile_created}` |

## Contents
- `force-app/main/default/profiles/` — profile definition (if cloned) or merged assignments
- `force-app/main/default/lifeSciConfigRecords/` — Admin Console + DbSchema settings

## Stats
```json
{json.dumps(stats, indent=2)}
```

## Deploy manually
```bash
sf project deploy start --source-dir force-app --target-org {dest_org} --wait 30
```

## Post-deploy (required)
1. Admin Console → Mobile → Object Metadata Cache → **Validate**
2. **Generate Metadata Cache** for `{target_profile}`
3. Assign LSC permission sets to test users
4. iPad sync smoke test
"""
    (out_dir / "README-PACKAGE.md").write_text(readme, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap LSC profile config from a golden profile into a deployable package."
    )
    parser.add_argument("source_org", help="Source org alias (golden profile)")
    parser.add_argument("dest_org", help="Destination org alias")
    parser.add_argument("source_profile_api_name", help="Golden profile metadata API name (e.g. Custom: FSR POC)")
    parser.add_argument("target_profile_name", help="Target profile display Name from Setup (not API name)")
    parser.add_argument(
        "--package-only",
        action="store_true",
        help="Build package only; do not deploy to destination org",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory for deploy package (default: ./packages/lsc-bootstrap-TIMESTAMP)",
    )
    parser.add_argument(
        "--source-instance-url",
        help="Optional My Domain URL for source org browser login (e.g. https://mycompany.my.salesforce.com)",
    )
    parser.add_argument(
        "--dest-instance-url",
        help="Optional My Domain URL for destination org browser login",
    )
    args = parser.parse_args()

    source_org = args.source_org
    dest_org = args.dest_org
    source_profile_api_name = args.source_profile_api_name.strip()
    target_profile_name = args.target_profile_name.strip()

    log("=== LSC Profile Bootstrap ===")
    log(f"Source: {source_org} / API name '{source_profile_api_name}'")
    log(f"Dest:   {dest_org} / Name '{target_profile_name}'")

    step_header(1, "Authenticate source org")
    ensure_org_login(source_org, "Source", args.source_instance_url)

    step_header(2, "Authenticate destination org")
    ensure_org_login(dest_org, "Destination", args.dest_instance_url)

    step_header(3, "Resolve source profile by API name")
    source = resolve_source_profile(source_org, source_profile_api_name)
    step_ok(f"Source profile: Name='{source.name}', API='{source.api_name}', Id={source.id}")

    step_header(4, "Check target profile in destination org")
    target = resolve_target_profile(dest_org, target_profile_name)
    target_exists = target is not None
    if target_exists:
        step_ok(f"Target profile exists — will merge assignments (API: {target.api_name})")
    else:
        step_ok(f"Target profile '{target_profile_name}' not found — will create new custom profile")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.output_dir:
        package_root = Path(args.output_dir).resolve()
    else:
        package_root = (SCRIPT_DIR / "packages" / f"lsc-bootstrap-{timestamp}").resolve()
    package_root.mkdir(parents=True, exist_ok=True)
    retrieve_root = Path(tempfile.mkdtemp(prefix="lsc-retrieve-"))
    force_app = package_root / "force-app" / "main" / "default"
    profiles_out = force_app / "profiles"
    configs_out = force_app / "lifeSciConfigRecords"
    profiles_out.mkdir(parents=True, exist_ok=True)
    configs_out.mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT_DIR / "sfdx-project.json", package_root / "sfdx-project.json")

    stats: dict = {}
    profile_created = False
    source_analysis: dict[str, Any] = {}
    profile_counts: dict[str, int] = {}
    dest_profile: Optional[Path] = None
    target_custom = True
    target_profile_id: Optional[str] = None
    lifesci_stats: dict[str, Any] = {}
    deployed = False

    try:
        step_header(5, "Retrieve source profile (layouts, apps, tabs, record pages)")
        profile_selector = profile_metadata_selector_from_api(source.api_name)
        step_detail(f"Metadata selector: {profile_selector}")
        retrieve_metadata(source_org, [profile_selector], retrieve_root)
        source_profile_file = find_profile_file(retrieve_root, source)
        if not source_profile_file:
            raise RuntimeError(
                f"Could not retrieve Profile API '{source.api_name}' from {source_org}. "
                f"Check permissions."
            )
        step_ok(f"Retrieved profile file: {source_profile_file.name}")

        step_header(6, "Build target profile package")
        if not target_exists:
            target_custom = True
            dest_profile = rename_profile_file(
                source_profile_file, profiles_out, target_profile_name, target_custom
            )
            step_ok(f"Cloned profile → {dest_profile.name} (display Name: {target_profile_name})")
            profile_created = True
            target_profile_id = None
        else:
            target_custom = target.custom
            target_profile_id = target.id
            tmp = Path(tempfile.mkdtemp(prefix="lsc-tgt-profile-"))
            try:
                tgt_selector = profile_metadata_selector_from_api(target.api_name)
                step_detail(f"Retrieving existing target profile: {tgt_selector}")
                retrieve_metadata(dest_org, [tgt_selector], tmp)
                target_profile_file = find_profile_file(tmp, target)
                if target_profile_file:
                    dest_profile = profiles_out / target_profile_file.name
                    merge_profile_sections(source_profile_file, target_profile_file, dest_profile)
                    step_ok(f"Merged layout/app/tab assignments into: {dest_profile.name}")
                else:
                    step_detail("Could not retrieve target profile for merge — copying source profile file")
                    dest_profile = rename_profile_file(
                        source_profile_file, profiles_out, target_profile_name, target_custom
                    )
                    step_ok(f"Wrote profile file: {dest_profile.name}")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        if dest_profile and dest_profile.exists():
            profile_counts = count_profile_xml_sections(dest_profile)
            step_detail(
                f"Profile sections: {profile_counts.get('layoutAssignments', 0)} layouts, "
                f"{profile_counts.get('applicationVisibilities', 0)} apps, "
                f"{profile_counts.get('recordTypeVisibilities', 0)} record types"
            )

        step_header(7, "Retrieve LifeSciConfigRecord from source org (Admin Console + DbSchema)")
        lifesci_tmp = Path(tempfile.mkdtemp(prefix="lsc-lifesci-"))
        members: list[str] = []
        try:
            list_result = sf_json(
                ["org", "list", "metadata", "--target-org", source_org, "--metadata-type", "LifeSciConfigRecord"]
            )
            members = [r.get("fullName") for r in list_result.get("result", []) if r.get("fullName")]
            source_analysis = analyze_source_lifesci_members(members, source)
            RUN.source_admin_setting_names = source_analysis.get("source_profile_admin_names", [])
            step_ok(f"Listed {len(members)} LifeSciConfigRecord entries in source org")
            step_detail(f"Admin Console settings (non-DbSchema): {source_analysis.get('admin_console_total', 0)}")
            step_detail(f"DbSchema object configs: {source_analysis.get('dbschema_total', 0)}")
            step_detail(
                f"Source-profile Admin Console settings: "
                f"{source_analysis.get('source_profile_admin_count', 0)}"
            )
            if members:
                chunk_size = 200
                for i in range(0, len(members), chunk_size):
                    chunk = members[i : i + chunk_size]
                    items = [f"LifeSciConfigRecord:{m}" for m in chunk]
                    step_detail(f"Retrieving LifeSciConfigRecord batch {i // chunk_size + 1} ({len(chunk)} items)")
                    retrieve_metadata(source_org, items, lifesci_tmp)
                src_configs = lifesci_tmp / "force-app" / "main" / "default" / "lifeSciConfigRecords"
                if not src_configs.exists():
                    src_configs = lifesci_tmp / "lifeSciConfigRecords"
                if src_configs.exists():
                    copied = 0
                    for f in src_configs.glob("*.lifeSciConfigRecord"):
                        shutil.copy2(f, configs_out / f.name)
                        copied += 1
                    step_ok(f"Copied {copied} LifeSciConfigRecord files into package")
        finally:
            shutil.rmtree(lifesci_tmp, ignore_errors=True)

        step_header(8, "Generate Admin Console settings + DbSchema assignments for target profile")
        lifesci_stats = clone_lifesci_config_records(
            configs_out,
            source.name,
            target_profile_name,
            source.custom,
            target_custom,
            source.id,
            target_profile_id,
        )
        stats["lifesci_config"] = lifesci_stats
        stats["source_analysis"] = source_analysis
        legacy_stats = clone_lifesci_metadata_records(
            source_org, source.name, target_profile_name, dest_org
        )
        stats["lifesci_metadata_legacy"] = legacy_stats
        step_ok(
            f"Generated {lifesci_stats.get('cloned', 0)} Admin Console settings, "
            f"{lifesci_stats.get('dbschema_updated', 0)} DbSchema profile assignments"
        )

        manifest_items = ["Profile:" + profile_metadata_api_name(target_profile_name, target_custom)]
        manifest_items.extend(
            f"LifeSciConfigRecord:{p.stem}" for p in configs_out.glob("*.lifeSciConfigRecord")
        )
        (package_root / "manifest.xml").write_text(build_package_xml(manifest_items), encoding="utf-8")
        step_detail(f"Wrote deploy manifest with {len(manifest_items)} metadata members")

        write_readme(
            package_root,
            source_org,
            dest_org,
            source.api_name,
            target_profile_name,
            profile_created,
            stats,
        )
        step_ok(f"Package ready at: {package_root}")

        if args.package_only:
            step_header(9, "Deploy to destination org (skipped — package-only)")
            step_ok("Package built; deploy skipped (--package-only)")
            step_detail(
                f"Deploy manually: sf project deploy start --source-dir "
                f"{package_root / 'force-app'} --target-org {dest_org}"
            )
        else:
            step_header(9, "Deploy package to destination org")
            deploy_package(package_root, dest_org)
            deployed = True
            step_ok(f"Deploy complete to {dest_org}")
            step_detail("Post-deploy: Validate DbSchema → Generate Metadata Cache → assign permission sets → iPad sync")

        print_execution_summary(
            source_org=source_org,
            dest_org=dest_org,
            source=source,
            target_profile_name=target_profile_name,
            profile_created=profile_created,
            source_analysis=source_analysis,
            lifesci_stats=lifesci_stats,
            profile_counts=profile_counts,
            package_root=package_root,
            deployed=deployed,
        )

    finally:
        shutil.rmtree(retrieve_root, ignore_errors=True)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        log(f"\nERROR: {exc}")
        sys.exit(1)
