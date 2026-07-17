# dMRV Dashboard — Claude Code Sub-Agents (v2)

**Version**: `v2.2 — 2026-07-16`
**Project**: dMRV Analytical Dashboard, VNV Advisory Services
**Owner**: Denish M

## v2.2 change — per-agent model pinning

Every agent now carries a `model:` field in its frontmatter so Claude Code routes each one to
the right model automatically, no per-invocation choice needed. Rationale: reserve the expensive
model for tasks where a wrong answer is costly and hard to catch; use the mid model for
high-volume mechanical work; use the cheap model for trivial passes.

| Model | Agents | Why |
|---|---|---|
| `opus` | tech-lead-orchestrator, carbon-mrv-vm0047, appsec-reviewer, data-governance-security, webgis-frontend, qa-geospatial-validator, postgis-db, docker-devops | Judgment/correctness/security, novel integration (map), silent-failure detection. postgis-db and docker-devops are pinned to opus because their *design* and *k8s* portions need it even though parts of their work are mechanical. |
| `sonnet` | fastapi-backend, api-integration, frontend-dashboard-dev, geo-remote-sensing, gis-analyst, data-pipeline-qa, qa-backend-tester, qa-frontend-tester, docs-technical-writer | High-volume patterned work, easy to verify (tests run, design pre-approved). Most tokens live here. |
| `haiku` | uiux-reviewer | Surface-level usability flagging. |

Notes / honest limits:
- The `model:` field name and accepted values (`opus`/`sonnet`/`haiku`, and `inherit`) can change
  across Claude Code releases. Verify against https://docs.claude.com before relying on it.
- A single `model:` field can't split within one agent, so postgis-db (design vs migrations) and
  docker-devops (compose vs k8s) are pinned to the higher model rather than split. Accept the small
  overpay on their mechanical output, or split those into two prompts and switch the main-thread
  model manually.
- There is no capability-based auto-router: nothing inspects a task at runtime and downgrades it.
  The routing intelligence is this static assignment, decided before anything runs.

---


## v2.1 corrections (fact-check pass against VM0047 v1.0 PDF)

`carbon-mrv-vm0047.md` had four errors caught by an external fact-check. All fixed and verified
against the Verra VM0047 v1.0 methodology (28 September 2023):

1. **Root:shoot ratio R** — was hardcoded as `R = 0.26`. VM0047 §9.1 has **no fixed default**;
   R must be sourced per-project in a preference order (ecoregion-specific → global by forest
   type or species). Rewritten with the actual sourcing rule.
2. **Uncertainty deduction** — had a fabricated stepped table (15/20/30/50% → 0/6/11/17%/
   ineligible). VM0047 §8.4 actually uses continuous **error propagation** (Eq. 27 area-based,
   Eq. 28 census-based) with a **flat 10% subtraction** and ineligibility at **90% CI half-width
   > 100% of estimate**. Replaced with the correct equations.
3. **Confidence interval width** — said "95% CI." VM0047 §9.2 uses **90% CI** (parameter Up,t
   with Z ≈ 1.645). Corrected.
4. **Leakage** — said "leakage belt (buffer around project area)." That's REDD+ methodology
   language (VM0007/VM0048), not ARR. VM0047 §8.3 delegates leakage to the **separate module
   VMD0054**. Rewritten.

An anti-fabrication rule was added to the agent's RULES section (item 8): never invent stepped
tables, thresholds, or default constants for methodology parameters.

The remaining 17 agents were spot-checked and no equivalent domain-fact errors were found.
GIS/remote-sensing dataset IDs (`LARSE/GEDI/GEDI04_A_002_MONTHLY`, Cloud Score+, S2 SR
Harmonized) were independently verified against the GEE Data Catalog and confirmed correct.

If you find more errors, log them in this section and version-bump.

---

## What this package is

Eighteen Claude Code sub-agent definitions rebuilt from the v1 pack, plus a shared
`PROJECT_CONTEXT.md` reference. Each agent has an isolated system prompt (Claude Code
sub-agents don't share context with each other), so the project context, domain cheat sheet,
rules, and output format are embedded in every agent.

## What changed vs v1

The v1 agents were role-name scaffolding: "you are a GIS analyst, be careful." That gets
you a small lift over base Claude. v2 fixes six specific gaps:

1. **Project context is now inside every agent.** Denish, VNV, 10 microlandscapes, EPSG:32643,
   PostGIS 3.3+, GEDI L4A, VM0047, WSL2 — verbatim in every prompt. Sub-agents don't share
   context, so this has to be redundant on purpose.
2. **Every agent has a `tools:` restriction.** Review-only agents (`appsec-reviewer`,
   `uiux-reviewer`, `data-governance-security`, `qa-geospatial-validator`) cannot `Write`
   or `Edit` — they cannot silently change source while reviewing it. Doers get the tools
   they need. See PROJECT_CONTEXT.md for the full policy table.
3. **Domain cheat sheets replaced vague role claims.** `carbon-mrv-vm0047` now carries the
   VCS uncertainty deduction ladder, root:shoot ratio, tC↔tCO2e factor. `geo-remote-sensing`
   carries Cloud Score+ threshold and GEE dataset IDs. `postgis-db` carries a working schema.
   Not one of these was in v1.
4. **Output format contracts.** Every agent has a concrete response template so results are
   consistent and easy to route into downstream agents.
5. **Escalation sections.** Each agent lists which other agent to route to when the problem
   is out of scope. This is how the sub-agent system compensates for having no orchestrator
   that can invoke sub-agents.
6. **`tech-lead-orchestrator` is now a *planner*, not a delegator.** It reads and routes, it
   does not (cannot) invoke other sub-agents. The v1 version misunderstood this.

## Honest limits

- **Prompt-side ceiling**: about 80–85% of what these agents could do with tool integration
  and eval loops. Getting to 98%+ requires iteration under real load, not more prose.
- **First-use calibration**: expect to tune the tool restrictions and cheat sheets after the
  first week. Don't treat v2 as final.
- **Sub-agents cannot invoke sub-agents.** The main Claude Code thread routes. The
  `tech-lead-orchestrator` returns a plan; it does not execute delegation.
- **Context is redundant on purpose.** Every agent re-embeds the project context because
  each has an isolated conversation. Do not "DRY it up" — you will regret it.

## Install

Claude Code sub-agents live under `.claude/agents/` in your project. From this package:

```bash
# From the project root of the dMRV Dashboard repo
mkdir -p .claude/agents
cp *.md .claude/agents/
# PROJECT_CONTEXT.md is a reference doc, not an agent — you can keep it or delete it
```

Claude Code will discover them on next launch. Invoke by name in a prompt:

```
> use carbon-mrv-vm0047 to review whether monitoring event M-07 satisfies VM0047 §8.3
> use postgis-db to draft a migration for the qa_finding table
> use appsec-reviewer to audit src/api/auth.py
```

Or let the main Claude Code thread pick — with good descriptions in the frontmatter, it
usually does.

## The 18 agents

### Planning / routing
- **tech-lead-orchestrator** — planner. Reads the request, returns a routing plan. Does not
  delegate (sub-agents can't invoke sub-agents).

### Domain
- **carbon-mrv-vm0047** — Verra VCS + VM0047 (ARR) methodology. Uncertainty deductions,
  carbon pools, baseline logic. Maps GIS outputs to VCS requirements.

### GIS / remote sensing
- **geo-remote-sensing** — Google Earth Engine, Sentinel-2, GEDI L4A, Dynamic World V1,
  SAR gap-fill. Writes and runs GEE scripts.
- **gis-analyst** — PyQGIS, ogr2ogr, GDAL. Reprojection, geometry fixing, format conversion.
- **data-pipeline-qa** — KML + Excel tracker QA/QC pipelines. Common Bund Errors, bund width,
  field-team Excel reports.
- **qa-geospatial-validator** — reviews GIS outputs; no writes. CRS discipline, value ranges,
  cross-source consistency, silent-failure detection.

### Backend
- **postgis-db** — PostGIS schema, migrations, spatial queries, role-based DB access.
- **fastapi-backend** — FastAPI (async), SQLAlchemy 2.0, JWT auth, RBAC guards, GeoJSON
  emission.
- **api-integration** — wires frontend to backend via typed OpenAPI client + TanStack Query.

### Frontend
- **webgis-frontend** — React + Leaflet map layer.
- **frontend-dashboard-dev** — React non-map UI, forms, Recharts.
- **uiux-reviewer** — reviews only. Field-team persona bias.

### QA / test
- **qa-backend-tester** — pytest, httpx, testcontainers PostGIS. RBAC matrix, spatial correctness.
- **qa-frontend-tester** — Vitest + RTL + Playwright, axe accessibility.

### Security / governance
- **appsec-reviewer** — OWASP-style code review for FastAPI + React + PostGIS.
- **data-governance-security** — access policy audit; who sees what, draft vs verified,
  locational PII.

### DevOps / docs
- **docker-devops** — docker-compose + Dockerfiles, WSL2 gotchas. No k8s at prototype stage.
- **docs-technical-writer** — SRS, SOP, README. Drift detection between docs and code.

## Update workflow

When something in the project changes (new microlandscape, new methodology version, new
stack component):

1. Update the shared context block in `PROJECT_CONTEXT.md` first.
2. Propagate the change into every agent's `PROJECT CONTEXT` section (yes, all of them —
   there is no shortcut, this is how sub-agents work).
3. Bump the version stamp in `PROJECT_CONTEXT.md` and this README.

A small script can automate the propagation later. Not worth writing until the context
block stabilises.

## Feedback loop for v3

Track these when using v2 in the wild:

- Which agent's cheat sheet is missing a fact you had to teach it in-conversation?
- Which agent's output format is wrong for how you actually consume it?
- Which agent's escalation was wrong (routed to the wrong next agent)?
- Which agent had the wrong tool set?

Each of those is a one-line fix for v3.
