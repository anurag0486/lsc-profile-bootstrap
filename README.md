# LSC Profile Bootstrap — VS Code Project

Copy LSC Admin Console settings, DbSchema, page layouts, apps, and profile config from a **golden profile** to a **new profile** in your dev org.

---

## Import this project in VS Code (first time)

### 1. Install prerequisites

| Tool | Link |
|------|------|
| VS Code | https://code.visualstudio.com/ |
| Salesforce CLI | https://developer.salesforce.com/tools/salesforcecli |
| Python 3 | https://www.python.org/downloads/ |

### 2. Get the project

```bash
git clone https://github.com/anurag0486/lsc-profile-bootstrap.git
```

### 3. Open as a VS Code workspace

**Recommended** — double-click or open this file in VS Code:

```
lsc-profile-bootstrap.code-workspace
```

Or in VS Code:

1. **File → Open Workspace from File…**
2. Select `lsc-profile-bootstrap.code-workspace`

VS Code will prompt you to install recommended extensions (**Salesforce Extension Pack**, **Python**). Click **Install**.

---

## Project layout

```
lsc-profile-bootstrap/
├── lsc-profile-bootstrap.code-workspace   ← open this file in VS Code
├── lsc_profile_bootstrap.py               ← main script
├── sfdx-project.json                      ← Salesforce API 67
├── config/
│   └── bootstrap.example.env              ← optional reference values
├── force-app/main/default/                ← deploy output structure
├── packages/                              ← generated packages land here
└── .vscode/
    ├── tasks.json                         ← run script from Command Palette
    ├── launch.json                        ← debug configuration
    └── extensions.json                    ← recommended extensions
```

---

## Run from VS Code (no typing commands)

1. Press **Cmd + Shift + P** (Mac) or **Ctrl + Shift + P** (Windows)
2. Type **Tasks: Run Task**
3. Pick a task:

| Task | What it does |
|------|----------------|
| **LSC: Login to Source Org** | Browser login to golden org |
| **LSC: Login to Dev Org** | Browser login to dev org |
| **LSC: List Connected Orgs** | Show `sf org list` |
| **LSC: List Profile API Names (Source Org)** | Find source profile API name |
| **LSC: Run Profile Bootstrap (deploy)** | Auto-login both orgs if needed, run script + deploy |
| **LSC: Run Profile Bootstrap (package only)** | Auto-login, build package only, no deploy |

Tasks will **prompt** for:

- Source org alias (e.g. `GOLDEN_ORG`)
- Dev org alias (e.g. `MY_DEV_ORG`)
- Source profile **API name** (e.g. `Custom: FSR POC`)
- Target profile **Name** (e.g. `Medical Science Liaison`)

**Automatic login:** If either org is not connected, the script opens a browser login window for that org before continuing. You can still use the separate **Login to Source/Dev Org** tasks if you prefer to authenticate ahead of time.

**Step-by-step logs:** Each run prints 9 numbered steps with `→` detail lines and `✓` confirmations so you can verify every action.

**Default build task:** **Cmd + Shift + B** (Mac) or **Ctrl + Shift + B** (Windows) runs **LSC: Run Profile Bootstrap (deploy)**.

---

## Run from terminal (alternative)

```bash
python3 lsc_profile_bootstrap.py GOLDEN_ORG MY_DEV_ORG "Custom: FSR POC" "Medical Science Liaison"
```

Optional instance URLs for first-time browser login:

```bash
python3 lsc_profile_bootstrap.py GOLDEN_ORG MY_DEV_ORG "Custom: FSR POC" "MSL Profile" \
  --source-instance-url https://golden.my.salesforce.com \
  --dest-instance-url https://dev.my.salesforce.com
```

| # | Parameter | Example |
|---|-----------|---------|
| 1 | Source org alias | `GOLDEN_ORG` |
| 2 | Dev org alias | `MY_DEV_ORG` |
| 3 | Source profile **API name** | `Custom: FSR POC` |
| 4 | Target profile **display Name** | `Medical Science Liaison` |

Find source API names:

```bash
sf org list metadata --metadata-type Profile --target-org GOLDEN_ORG
```

---

## After the script completes

The terminal prints an **EXECUTION SUMMARY** including:

- Admin Console settings retrieved from the source org
- **Admin Console settings generated** for the target profile (count + file list)
- DbSchema profile assignments added
- Profile layout / app / tab assignment counts

In your **dev org**:

1. Admin Console → Mobile → Object Metadata Cache → **Validate**
2. **Generate Metadata Cache** for the new profile
3. Assign **LSC permission sets** to test users
4. iPad sync smoke test

Output package folder:

```
packages/lsc-bootstrap-<timestamp>/
```

---

## Common errors

| Error | Fix |
|-------|-----|
| `source profile not found in sourceorg` | Use exact API name from **List Profile API Names** task |
| Org not authenticated | Script auto-opens browser login; or run **Login to Source/Dev Org** tasks |
| `sf` not found | Install Salesforce CLI, restart VS Code |
| Extension recommendations | Accept when opening workspace |

---

## Share with your team

1. Push this folder to GitHub
2. Teammates: **clone → open `lsc-profile-bootstrap.code-workspace`**
3. Install recommended extensions when prompted
4. Run tasks from **Cmd/Ctrl + Shift + P → Tasks: Run Task**

Ask your team lead for golden org alias, profile API name, and permission sets.
