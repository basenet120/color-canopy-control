# cloud-session (research pass 2026-09-26)

**HEADLINE:** Claude Code cloud sessions are fully available for autonomous multi-agent Hubspace control work on your GitHub account—run long tasks while your laptop is off, with native support for Afero API domains via custom network access configuration.

## Findings
- [HIGH] **Cloud sessions are available and work for long autonomous multi-agent work**
  - Cloud sessions run on Anthropic-managed VMs and keep working after you close your laptop. They support the full Claude Code harness including subagents, dynamic workflows (orchestrating 5-100+ agents), and the Workflow tool for autonomous multi-agent tasks. Sessions run until they complete or hit idle expiry, then you can resume them.
  - src: https://code.claude.com/docs/en/claude-code-on-the-web.md and https://code.claude.com/docs/en/workflows.md
- [HIGH] **You can start cloud sessions from your GitHub account without needing your laptop running**
  - Start with `claude --cloud "task"` from the CLI, or from claude.ai/code in your browser. Sessions run in the cloud and keep running if you close your terminal/laptop. You can monitor/steer from your phone or browser. GitHub integration is one-time setup; then sessions automatically clone your repo and push branches.
  - src: https://code.claude.com/docs/en/web-quickstart.md and https://code.claude.com/docs/en/claude-code-on-the-web.md
- [HIGH] **Network access to Afero Hubspace cloud APIs requires custom domain allowlisting**
  - Default 'Trusted' access only allows package registries and common cloud SDKs. To reach Afero's API endpoints (api2.afero.net, semantics2.afero.net, accounts.hubspaceconnect.com, conclave-stream1.afero.net), you must create a custom cloud environment with 'Custom' network access and add these domains explicitly to the allowed list.
  - src: https://code.claude.com/docs/en/cloud-environments.md (default-allowed-domains and access-levels sections)
- [HIGH] **Setup scripts run once and are cached; use SessionStart hooks for per-session Python dependency installs**
  - Setup scripts (bash) run at environment creation and are filesystem-cached (~5 min max, must finish in ~5 min). SessionStart hooks in .claude/settings.json run at every session start. Best practice: setup scripts install system tools (apt, toolchains), SessionStart hooks run 'npm install' / 'pip install' with CLAUDE_CODE_REMOTE check to run only in cloud.
  - src: https://code.claude.com/docs/en/cloud-environments.md (setup-scripts and setup-scripts-vs-sessionstart-hooks)
- [MEDIUM] **Preinstalled: Python 3.x, Node 20/21/22, docker, git, gh. NOT: .NET, custom Python 3.12 globally**
  - Python 3.x is preinstalled but specific version (3.12) availability UNVERIFIED. For guaranteed Python 3.12, install via setup script or SessionStart hook. Node 22 is on PATH by default. PostgreSQL 16 and Redis 7 are preinstalled but not running. Docker, docker-compose, git, gh (GitHub CLI) are ready to use.
  - src: https://code.claude.com/docs/en/cloud-environments.md (installed-tools section)
- [MEDIUM] **Cloud sessions run Python 3.x but exact version UNVERIFIED for 3.12 requirement**
  - The docs list 'Python 3.x with pip, poetry, uv, black, mypy, pytest, ruff' without specifying patch versions. To guarantee Python 3.12.x for aioafero compatibility, add to setup script: `apt update && apt install -y python3.12 python3.12-venv python3.12-dev` or use SessionStart hook to run `python3.12 -m pip install -r requirements.txt`.
  - src: https://code.claude.com/docs/en/cloud-environments.md (installed-tools table)
- [HIGH] **Sessions expire after idle timeout; you can teleport them back to your terminal or resume them**
  - Idle cloud sessions stop after a period of inactivity and the VM is reclaimed. Conversation history is saved; re-open the session at claude.ai/code or teleport it to your terminal with `claude --teleport <session-id>`. The branch and changes are preserved. No explicit session duration limit documented; runs continue until task completes or idle expiry.
  - src: https://code.claude.com/docs/en/claude-code-on-the-web.md (environment-expired, from-cloud-to-terminal)
- [HIGH] **Git behavior: Claude can only push to claude/* branches, not to your default branch**
  - Sessions push changes to `claude/`-prefixed branches freely. To push to your default branch (main/master), it's protected and Claude cannot push there directly. Instead, Claude creates PRs; you review and merge. This is enforced by the GitHub proxy architecture.
  - src: https://code.claude.com/docs/en/cloud-environments.md (github-proxy section)
- [HIGH] **CLAUDE.md committed to repo loads in every cloud session; use it for Hubspace API instructions**
  - Cloud sessions clone your repo fresh and automatically load ./CLAUDE.md and ./.claude/CLAUDE.md. This is the place to put aioafero API patterns, Hubspace metadevice structure, rate-limit guidance, and Afero cloud facts so every autonomous run has context. Keep it under 200 lines for best adherence.
  - src: https://code.claude.com/docs/en/memory.md (what-carries-over-from-your-setup)
- [HIGH] **.claude/settings.json hooks and permission rules load only in single-repo cloud sessions**
  - Project-level .claude/settings.json loads in cloud sessions with one repository. A session with multiple repositories (e.g., project threads) doesn't load them. Use CLAUDE.md instead for multi-repo guidance, or put repo-specific hooks in a SessionStart hook inside .claude/settings.json with a check for the repo.
  - src: https://code.claude.com/docs/en/cloud-environments.md (what-carries-over-from-your-setup)
- [HIGH] **.claude/skills/, .claude/agents/, .claude/workflows/ are loaded in cloud sessions**
  - Custom skills, agents, and saved workflows committed to your repo are available in cloud sessions. If you save a workflow with `/workflows` select and press 's', it goes to `.claude/workflows/` and is reusable in future cloud sessions (and locally).
  - src: https://code.claude.com/docs/en/cloud-environments.md (what-carries-over-from-your-setup) and https://code.claude.com/docs/en/workflows.md (save-the-workflow-for-reuse)
- [HIGH] **Subagents and dynamic workflows (Workflow tool) work in cloud sessions**
  - Subagents spawn with the Agent tool; workflows fan out 5-100+ agents autonomously. Both run in cloud sessions. A workflow script runs in the background while your session stays responsive. Subagent tools and MCP connectors work in cloud the same way as locally (subject to network access rules).
  - src: https://code.claude.com/docs/en/workflows.md (bundled-workflows, when-to-use-a-workflow) and https://code.claude.com/docs/en/claude-code-on-the-web.md
- [HIGH] **API credentials on Pro/Max plans keep API keys outside the session VM**
  - On Pro/Max cloud environments, add an API credential via the environment settings. The agent proxy attaches your key to outbound requests for hosts you list, so Afero API calls never see the key. UNVERIFIED on Team/Enterprise (docs note API credentials 'aren't available yet' on those plans).
  - src: https://code.claude.com/docs/en/cloud-environments.md (add-api-credentials)
- [HIGH] **Model selection: /model opus or claude --model opus selects Opus 5.5 for cloud sessions**
  - Opus 5.5 is the default strongest model on most plans. In cloud sessions, use `/model opus` to ensure it, or launch with `claude --cloud --model opus "task"`. Extended context ([1m]) is available: `/model opus[1m]` gives 1M token window.
  - src: https://code.claude.com/docs/en/model-config.md (available-model-aliases, using-fable-models)
- [HIGH] **Usage and rate limits: cloud sessions share account limits; multi-agent work counts toward usage**
  - A workflow with 50 agents counts as proportionally more tokens than a single-agent task. Runs hit your daily/weekly subscription limit the same way interactive sessions do. No separate cloud compute charge; it's included in plan usage.
  - src: https://code.claude.com/docs/en/claude-code-on-the-web.md (limitations) and https://code.claude.com/docs/en/workflows.md (cost)
- [HIGH] **Routines (scheduled cloud tasks) are available and can run workflows autonomously on a schedule**
  - Create a routine at claude.ai/code/routines with a schedule (hourly/daily/weekly) or API trigger. The routine runs as an autonomous cloud session. You can enable workflows in the routine prompt with the keyword `ultracode`. Routines run on your account's daily cap but are available on Pro/Max/Team/Enterprise.
  - src: https://code.claude.com/docs/en/routines.md
- [HIGH] **Claude GitHub App must be installed on private repos for cloud sessions to access them**
  - If your Hubspace controller repo is private, install the Claude GitHub App from https://github.com/apps/claude on your GitHub account/org. Cloud sessions can clone any public repo; private repos require the app installation.
  - src: https://code.claude.com/docs/en/web-quickstart.md (connect-github)

## Numbers
- Cloud session VM resources (CPU/RAM/disk) = **4 vCPUs, 16 GB RAM, 30 GB disk** (https://code.claude.com/docs/en/cloud-environments.md (resource-limits))
- Setup script max runtime before caching fails = **~5 minutes (roughly)** (https://code.claude.com/docs/en/cloud-environments.md (environment-caching))
- SessionStart hook default timeout = **600 seconds (10 minutes), configurable** (https://code.claude.com/docs/en/cloud-environments.md (time-limits))
- CLAUDE.md target max lines for best adherence = **Under 200 lines per file** (https://code.claude.com/docs/en/memory.md (write-effective-instructions))
- Max concurrent agents in a workflow (default) = **16 by default (configurable 1-256)** (https://code.claude.com/docs/en/workflows.md (behavior-and-limits))
- Max total agents per workflow run = **1,000 agents** (https://code.claude.com/docs/en/workflows.md (behavior-and-limits))
- Max items in single pipeline/parallel call = **4,096 items** (https://code.claude.com/docs/en/workflows.md (behavior-and-limits))
- Aioafero (aioafero) version on PyPI = **9.3.0 (as of Sep 15 2026, from user memory)** (User memory: hubspace-api-facts.md)
- Afero Hubspace string light min zones per fixture (func class known) = **Novolink holiday string = '20-segment' hint (inference from earlier dumps in user memory)** (User memory: hubspace-api-facts.md — UNVERIFIED current capability)

## Payloads / code
### Custom Cloud Environment Configuration (Afero APIs)
source: User requirement + https://code.claude.com/docs/en/cloud-environments.md (allow-specific-domains)
```
{
  "network_access": "Custom",
  "allowed_domains": [
    "api2.afero.net",
    "semantics2.afero.net",
    "accounts.hubspaceconnect.com",
    "conclave-stream1.afero.net",
    "pypi.org",
    "files.pythonhosted.org",
    "github.com"
  ],
  "include_default_list": true
}
```
### Setup Script (Install Python 3.12 for aioafero)
source: Standard Ubuntu setup + https://code.claude.com/docs/en/cloud-environments.md (setup-scripts)
```
#!/bin/bash
# Install system dependencies
apt update && apt install -y python3.12 python3.12-venv python3.12-dev || true

# Make python3.12 default (optional)
update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 || true
```
### SessionStart Hook (pip install in cloud sessions only)
source: https://code.claude.com/docs/en/cloud-environments.md (install-dependencies-with-a-sessionstart-hook)
```
"SessionStart": [
  {
    "matcher": "startup|resume",
    "hooks": [
      {
        "type": "command",
        "command": "bash \"$CLAUDE_PROJECT_DIR\"/scripts/install_cloud_deps.sh"
      }
    ]
  }
]
```
### Cloud-Only Dependency Install Script
source: https://code.claude.com/docs/en/cloud-environments.md (install-dependencies-with-a-sessionstart-hook)
```
#!/bin/bash
# Only run in cloud sessions
if [ "$CLAUDE_CODE_REMOTE" != "true" ]; then
  exit 0
fi

# Install aioafero and dependencies
pip install aioafero==9.3.0
pip install -r requirements.txt
exit 0
```
### CLAUDE.md for Hubspace Cloud Multi-Agent Work
source: User project context + https://code.claude.com/docs/en/memory.md (write-effective-instructions)
```
# Hubspace Lighting Canopy — Cloud Control

This repo orchestrates the 9-module Hampton Bay LED canopy via Hubspace cloud API.

## Key Facts
- **API**: 100% cloud (Afero); no local API
- **Auth**: Keycloak OAuth at accounts.hubspaceconnect.com (realm: 'thd', client: 'hubspace_android')
- **Library**: aioafero==9.3.0 (github.com/Expl0dingBanana/aioafero)
- **Endpoints**: api2.afero.net/v1/accounts/{aid}/metadevices/{id}/state; Conclave TLS push for realtime
- **Rate limits**: Community baseline ~2s polling; throttling can be silent

## String Light Model
- **SKU**: SQHDK03-15PSL (Hampton Bay 30-LED 50ft outdoor)
- **Per-module**: One complete kit = 30 heads in 8/7/8/7 staggered pattern
- **Functions**: color-individual (custom per-head), color-sequence-v2, party-mode, speed effect
- **Zones**: Multi-zone string lights split into per-zone Light clones in aioafero

## Modes (Lowcountry)
1. LOWCOUNTRY_CALM: Warm ivory + relaxed glow (static)
2. RAINBOW_ROW_SUNSET: Orange/peach + warm white (static sequence)
3. GARDEN_GATHERING: Warm pathway + canopy (2-layer static)
4. HOLIDAY_AT_HOME: Warm living-room + holiday (static)
5. GOLDEN_HOUR: Rich amber wash MOVING across canopy (animation)
6. GOODNIGHT_GLOW: Soft warm dimmed (static)

## Cloud Session Setup
- Custom env vars: HUBSPACE_USER, HUBSPACE_PASS (or OAuth token)
- Install: `pip install aioafero==9.3.0`
- Test: `python scripts/test_lights.py` (requires credentials)

```
### Starting a Cloud Session from CLI
source: https://code.claude.com/docs/en/claude-code-on-the-web.md (from-terminal-to-cloud)
```
# Direct cloud session from terminal
claude --cloud --model opus "Build and test multi-agent workflow to cycle Hubspace canopy through all Lowcountry modes and validate each head color matches expected output"

# Or interactively plan first, then execute in cloud
claude --permission-mode plan
# (Review plan, save to file, commit)
git add docs/plan.md && git commit -m "Plan: canopy mode-cycling workflow"
claude --cloud --model opus "Execute the plan in docs/plan.md"
```
### Teleport a Cloud Session Back to Local Terminal
source: https://code.claude.com/docs/en/claude-code-on-the-web.md (from-cloud-to-terminal)
```
# After cloud work is done, pull it back to your laptop
claude --teleport session_01DiUkqY2kzbUbDmW1w96rfi

# Or interactively pick from list
claude --teleport
```
### API Credential Configuration (Pro/Max Plans)
source: https://code.claude.com/docs/en/cloud-environments.md (add-api-credentials)
```
Environment: 'Default' or custom
→ API Credentials section
→ Add credential:
  Name: "Afero API (if needed for non-standard auth)"
  Allowed websites: api2.afero.net, semantics2.afero.net
  Credential type: Bearer
  Header Name: Authorization
  Prefix: Bearer
  Value: [your-afero-api-token]
```
### Routine for Nightly Canopy Health Check
source: https://code.claude.com/docs/en/routines.md (create-from-the-web)
```
Name: "Hubspace Canopy Nightly Health Check"
Prompt: "Run the full mode-cycling test suite for the 9-module canopy. For each mode, verify all 270 heads respond, check color match against expected palette, and log latency. If any mode fails or latency exceeds 2s, open a draft PR with diagnostics."
Schedule: Daily 2am (your timezone)
Repositories: [your-hubspace-repo]
Environment: "Default" (with custom domains configured)
Connectors: (none needed for this run)
Model: Opus 5.5
```

## Bench tests
- Start a cloud session with `claude --cloud "test message"` and confirm it runs on Anthropic-managed infrastructure, not your machine, and persists after you close the terminal.
- Create a custom cloud environment at claude.ai/code with 'Custom' network access and add api2.afero.net and semantics2.afero.net to the allowed domains. Verify a cloud session can reach those hosts by running `curl https://api2.afero.net/v1/status` (or appropriate Afero endpoint).
- Run `pip install aioafero==9.3.0` in a cloud session (either via setup script or SessionStart hook) and confirm it installs without errors and aioafero can import.
- Test a simple aioafero OAuth login flow in a cloud session: run a script that authenticates to accounts.hubspaceconnect.com and lists metadevices. Verify latency and that the session doesn't timeout mid-request.
- Start a multi-agent workflow in a cloud session (using the `/deep-research` bundled workflow or a custom workflow) and confirm it runs 5+ agents in parallel while your session stays responsive, without your laptop needing to stay on.
- Create a routine at claude.ai/code/routines with a daily schedule and a prompt that includes the `ultracode` keyword to trigger a workflow. Run it manually ('Run now') and confirm the workflow executes autonomously in the cloud.
- Clone your repo in a cloud session, make a change, and verify Claude can push to a `claude/*` branch but cannot push to your default branch (should fail with a protected branch error).
- Commit a CLAUDE.md file to your repo with Hubspace API patterns and instructions. Start a cloud session, run `/context`, and confirm CLAUDE.md appears under **Memory files** and Claude can reference it mid-conversation.
- Run a workflow that spawns 20+ subagents (e.g., testing 270 LED heads in groups) and confirm in `/workflows` that all agents complete, token counts are tracked per-agent, and the session remains interactive.
- Test that SessionStart hooks with the `CLAUDE_CODE_REMOTE` check run only in cloud sessions: commit a hook that runs `echo 'CLOUD'` when remote, then run it locally (should skip) and in a cloud session (should print 'CLOUD').

## Open questions
- **Exact Python 3.12 availability**: The docs list 'Python 3.x' but don't confirm if 3.12.x is available by default. Does `python --version` or `python3 --version` in a cloud session return 3.12, or require a setup script to install it?
- **Afero rate-limit behavior in aioafero**: The user memory notes 'throttling can be silent' on community polling (~2s baseline). Does the Hubspace cloud API enforce hard rate limits, or return 429 errors? Is aioafero's retry logic sufficient for a 9-device + 270-head orchestration loop?
- **Conclave TLS push notification delivery SLA**: For 'GOLDEN_HOUR' animation mode (moving colors across canopy), does the Conclave TLS push guarantee per-frame delivery latency, or is it best-effort? What's the observed latency from API call to on-device effect?
- **Multi-tenant isolation on Afero cloud**: When running workflows that spin up 20+ subagents making simultaneous Hubspace API calls from the same cloud session, does Afero's cloud infrastructure throttle or isolate traffic? Is there a per-session or per-account API call concurrency limit?
- **Team/Enterprise API credential support**: Docs note API credentials are 'not available yet' on Team/Enterprise plans. If deploying this to a team, will API creds become available by Oct 2026, or will the setup require environment variables (exposing keys)?
- **Idle session timeout SLA**: How long does a cloud session remain idle before the VM is reclaimed? Can you leave a workflow running unattended for 30+ minutes, or is there a strict timeout?
- **Environment cache persistence across teardown**: If a cloud environment is archived or deleted, do cached filesystem snapshots persist, or are they lost? Does environment caching data get backed up?
- **Workflow agent resource limits under load**: A workflow with 100 concurrent agents (testing all 270 heads in ~2-3 agent bundles) will consume memory and CPU. Does the 4-vCPU 16GB RAM limit per cloud session still apply, or are there per-agent resource pools?
- **MCP server availability in workflows**: Can a workflow spawn subagents that use MCP servers defined in .mcp.json, or are those restricted to interactive sessions?

## Goldmines
- https://code.claude.com/docs/en/web-quickstart.md — Start here to connect GitHub and launch your first cloud session
- https://code.claude.com/docs/en/claude-code-on-the-web.md — Full cloud session reference: starting sessions, moving between cloud/local, permissions, auto-fix PRs, security isolation
- https://code.claude.com/docs/en/cloud-environments.md — Network access, environment variables, setup scripts, installed tools, resource limits, time limits, and caching
- https://code.claude.com/docs/en/workflows.md — Dynamic workflows and multi-agent orchestration; how to write workflows, approve them, save them, run them autonomously
- https://code.claude.com/docs/en/routines.md — Scheduled cloud tasks; schedule triggers, API triggers, GitHub event triggers; running workflows inside routines
- https://code.claude.com/docs/en/memory.md — CLAUDE.md files, how to write them, where they load, best practices for cloud-ready repos
- https://code.claude.com/docs/en/model-config.md — Model selection (Opus 5.5, extended context), effort levels, per-model configuration
- https://code.claude.com/docs/en/settings-reference.md — Which settings files cloud sessions load, hooks configuration, permissions
- User memory at ~/.claude/projects/[repo]/memory/ — Auto-memories from prior Hubspace work (if any) and findings from Sep 19 2026 Afero API research