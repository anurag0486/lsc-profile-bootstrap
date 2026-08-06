# LSC Profile Bootstrap

Automate LSC profile setup: clone a **golden profile**'s Admin Console settings, DbSchema assignments, page layouts, Lightning record pages, and app assignments into a **new target profile** in a dev org.

Public repo: https://github.com/anurag0486/lsc-profile-bootstrap

---

## What it does

| Copies from golden profile | Does **not** touch |
|----------------------------|-------------------|
| Profile (layouts, apps, tabs, record types) | Org-level Admin Console settings |
| Profile-specific Admin Console settings | Other profiles' admin settings |
| DbSchema object assignments (adds target profile) | Permission sets (assign manually post-deploy) |

**Deploy safety:** Only metadata in `target/` is deployed. Omitting org-level settings from the package does **not** delete existing settings in the destination org.

---

## Prerequisites

| Tool | Link |
|------|------|
| VS Code | https://code.visualstudio.com/ |
| Salesforce CLI (`sf`) | https://developer.salesforce.com/tools/salesforcecli |
| Python 3.9+ | https://www.python.org/downloads/ |

Authenticate orgs once with `sf org login web --alias YOUR_ALIAS`, or let the script open browser login automatically.

---

## Quick start

### Clone and open

```bash
git clone https://github.com/anurag0486/lsc-profile-bootstrap.git
cd lsc-profile-bootstrap
```

Open `lsc-profile-bootstrap.code-workspace` in VS Code. Install recommended extensions when prompted (**Salesforce Extension Pack**, **Python**).

### Run from terminal

```bash
python3 lsc_profile_bootstrap.py SOURCE_ORG DEST_ORG "Source Profile API Name" "Target Profile Name"
```

**Example:**

```bash
python3 lsc_profile_bootstrap.py GOLDEN_ORG MY_DEV_ORG "Field Sales Representative" "Medical Science Liaison"
```

**Package only (no deploy):**

```bash
python3 lsc_profile_bootstrap.py GOLDEN_ORG MY_DEV_ORG "Custom: FSR POC" "MSL Profile" --package-only
```

**Optional flags:**

| Flag | Purpose |
|------|---------|
| `--package-only` | Build `source/` + `target/` folders without deploying |
| `--output-dir PATH` | Custom output folder (default: `packages/lsc-bootstrap-TIMESTAMP`) |
| `--source-instance-url URL` | My Domain URL for source org browser login |
| `--dest-instance-url URL` | My Domain URL for destination org browser login |

Find source profile API names:

```bash
sf org list metadata --metadata-type Profile --target-org GOLDEN_ORG
```

---

## CLI parameters

| # | Parameter | Example | Notes |
|---|-----------|---------|-------|
| 1 | Source org alias | `GOLDEN_ORG` | Golden profile org |
| 2 | Destination org alias | `MY_DEV_ORG` | Dev org |
| 3 | Source profile **API name** | `Custom: FSR POC` | Metadata fullName, not display label |
| 4 | Target profile **display Name** | `Medical Science Liaison` | Setup label for new/existing profile |

---

## Run from VS Code

1. **Cmd/Ctrl + Shift + P** → **Tasks: Run Task**
2. Choose a task:

| Task | What it does |
|------|----------------|
| **LSC: Run Profile Bootstrap (deploy)** | Auto-login, build package, deploy to dev org |
| **LSC: Run Profile Bootstrap (package only)** | Auto-login, build package only |
| **LSC: List Profile API Names (Source Org)** | Find golden profile API name |
| **LSC: Login to Source Org** / **Login to Dev Org** | Manual browser login |
| **LSC: List Connected Orgs** | Show `sf org list` |

**Default build:** **Cmd/Ctrl + Shift + B** runs deploy task.

Each run prints **9 numbered steps** with `→` detail lines and `✓` confirmations, plus an **EXECUTION SUMMARY** at the end.

---

## Output folder structure

Every run creates a timestamped folder under `packages/`:

```
packages/lsc-bootstrap-<timestamp>/
├── README.md                          ← run summary
├── source/                            ← retrieved from golden org (retained, NOT deployed)
│   ├── README.md
│   ├── manifest-retrieved.xml         ← what was retrieved from source
│   └── force-app/main/default/
│       ├── profiles/                  ← golden profile as retrieved
│       └── lifeSciConfigRecords/      ← source-profile admin + DbSchema as retrieved
└── target/                            ← deploy THIS folder only
    ├── README-DEPLOY.md
    ├── manifest.xml                   ← deploy manifest (target profile only)
    ├── sfdx-project.json
    └── force-app/main/default/
        ├── profiles/                  ← target profile
        └── lifeSciConfigRecords/      ← target-profile admin + DbSchema only
```

### Deploy manually

```bash
sf project deploy start \
  --source-dir packages/lsc-bootstrap-<timestamp>/target/force-app \
  --target-org MY_DEV_ORG \
  --wait 30
```

---

## Execution summary (example)

At the end of each run the script prints:

- Source-profile Admin Console settings found in golden org
- Admin Console settings **generated** for target profile
- DbSchema profile assignments added
- Profile layout / app / tab / record-type counts
- Count of unrelated configs **excluded** from deploy

---

## Post-deploy checklist

In the **destination org**:

1. Admin Console → Mobile → Object Metadata Cache → **Validate**
2. **Generate Metadata Cache** for the new profile
3. Assign **LSC permission sets** to test users
4. iPad sync smoke test

---

## Project layout

```
lsc-profile-bootstrap/
├── lsc-profile-bootstrap.code-workspace
├── lsc_profile_bootstrap.py           ← main script
├── sfdx-project.json                  ← Salesforce API 67
├── config/
│   └── bootstrap.example.env          ← example values only (no secrets)
├── packages/                          ← generated runs (gitignored)
└── .vscode/                           ← tasks, launch, extensions
```

---

## Security / credentials

**Never commit org credentials.** The following are gitignored:

- `packages/` — generated run output (may contain org-specific metadata)
- `.lsc-work/` — temporary retrieve folders
- `.sf/` — Salesforce CLI org auth cache
- `config/bootstrap.env` — local env overrides
- `.env`

Use `sf org login web` for authentication; credentials stay in your local Salesforce CLI keychain.

---

## Common errors

| Error | Fix |
|-------|-----|
| `source profile not found in sourceorg` | Use exact API name from `sf org list metadata --metadata-type Profile` |
| Org not authenticated | Script auto-opens browser login, or run **Login to Source/Dev Org** task |
| `sf` not found | Install Salesforce CLI, restart terminal/VS Code |
| `OutputDirOutsideProjectError` | Run script from project root (fixed in current version) |

---

## How it works (9 steps)

1. Authenticate source org
2. Authenticate destination org
3. Resolve source profile by API name
4. Check if target profile exists in destination
5. Retrieve source profile → `source/profiles/`
6. Build target profile → `target/profiles/`
7. Retrieve source-profile admin + DbSchema → `source/lifeSciConfigRecords/`
8. Clone admin settings + add DbSchema assignments → `target/lifeSciConfigRecords/`
9. Deploy `target/force-app` (skipped with `--package-only`)
