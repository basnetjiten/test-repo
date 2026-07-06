# Multi-Agent Platform & Orchestration Architecture

This document provides a comprehensive view of the **ebprocess-development** system. It describes how the stateful pipeline coordinates multiple specialist agents to execute complex, multi-project, multi-platform development tasks using LangGraph, OpenCode, and **SPOQ (Specialist Orchestrated Queuing)** (arXiv:2606.03115v1).

---

## 1. High-Level System Architecture

The core of `ebprocess-development` is a **stateful orchestration graph** built on **LangGraph**. The pipeline coordinates workspace setup, epic planning, wave-based task execution, code validation (10-metric scoring), contract verification, and publishing.

Multiple independent projects run **concurrently** because all per-project state — epics, tasks, journals — is **isolated by `space_name`** inside the `.ebpearls/` directory.

### LangGraph Stateful Pipeline

```mermaid
graph TD
    Start([Start]) --> Prepare["prepare_node"]
    Prepare --> Orchestrate["orchestrate_node"]
    Orchestrate --> Plan["plan_node"]

    Plan --> RoutePlan{Route After Plan?}
    RoutePlan -->|Success| Generate["generate_node"]
    RoutePlan -->|Failure| Repair["repair_node"]

    Generate --> RouteGen{Route After Generate?}
    RouteGen -->|Success| Validate["validate_node"]
    RouteGen -->|Failure| Repair

    Validate --> RouteVal{Route After Validate?}
    RouteVal -->|SPOQ & Remaining Tasks| Plan
    RouteVal -->|Failure| Repair
    RouteVal -->|Success / Non-SPOQ Done| Contract["contract_node"]

    Contract --> RouteContract{Route After Contract?}
    RouteContract -->|Success| Publish["publish_node"]
    RouteContract -->|Failure| Repair

    Repair --> RouteRepair{Route After Repair?}
    RouteRepair -->|Retries Remaining| Generate
    RouteRepair -->|Max Retries Exceeded| Finalize["finalize_node"]

    Publish --> Finalize
    Finalize --> End([End])

    style Start fill:#4F46E5,stroke:#312E81,stroke-width:2px,color:#fff
    style End fill:#4F46E5,stroke:#312E81,stroke-width:2px,color:#fff
    style Prepare fill:#0F172A,stroke:#38BDF8,stroke-width:2px,color:#fff
    style Orchestrate fill:#0F172A,stroke:#38BDF8,stroke-width:2px,color:#fff
    style Plan fill:#0F172A,stroke:#38BDF8,stroke-width:2px,color:#fff
    style Generate fill:#0F172A,stroke:#38BDF8,stroke-width:2px,color:#fff
    style Validate fill:#0F172A,stroke:#38BDF8,stroke-width:2px,color:#fff
    style Contract fill:#0F172A,stroke:#38BDF8,stroke-width:2px,color:#fff
    style Repair fill:#991B1B,stroke:#F87171,stroke-width:2px,color:#fff
    style Publish fill:#0F172A,stroke:#38BDF8,stroke-width:2px,color:#fff
    style Finalize fill:#0F172A,stroke:#38BDF8,stroke-width:2px,color:#fff
```

> [!NOTE]
> **Dual-System Architecture**: The system consists of two separate components running side-by-side:
> 1. **Python LangGraph Pipeline**: A stateful orchestration host (listening on port `8001`). It manages job parameters, branches, code compilation triggers, and coordinates the wave-based topological dispatch loops.
> 2. **OpenCode Headless Server**: A separate agent execution engine (running on port `4096`). The pipeline never executes agent LLM/tool loops directly; it instead delegates planning, building, and evaluation steps to OpenCode via REST API calls.

Each `generate_node` invocation dispatches a builder agent, which writes code and then triggers the validation node. The **`@code_evaluator`** agent independently scores the output against 10 quality metrics before marking the task complete.

---

## 2. Multi-Project Workspace Isolation

Each project is identified by a **`space_name`** (e.g. `"ebsprinter"`, `"ebprocess"`, `"AgentSwipe"`). All pipeline nodes resolve storage paths through `JobContext.project_storage_dir()`, ensuring **zero cross-project collisions**.

### Directory Layout

```text
workspace/                               ← runtime project checkouts
└── {space_name}/                        ← e.g. AgentSwipe
    ├── .ebpearls/                       ← Isolated runtime storage
    │   ├── ROADMAP.md                   ← Cross-epic registry
    │   ├── Epic-{id}/                   ← e.g. Epic-44445
    │   │   ├── EPIC.md                  ← Goal, architecture, DAG, wave assignments
    │   │   ├── {task-name}.md           ← Task plan Markdown (e.g. api-impl-41831.md)
    │   │   ├── context_api.json         ← Platform context (generated)
    │   │   ├── context_flutter.json     ← Platform context (generated)
    │   │   └── journals/                ← Confidence-scored session journals
    │   │       ├── 2026-07-06_development_api-builder.md
    │   │       └── JOURNAL.md           ← Consolidated epic journal
    │   └── Epic-{id}/                   ← Multiple epics can coexist
    │       └── ...
    │
    ├── {space_name}-services/           ← API platform (NestJS)
    └── {space_name}_flutter/            ← Flutter platform
```

### Isolation Rule

`JobContext.project_storage_dir()` resolves to `<workspace_dir>/<space_name>/.ebpearls/`. All SPOQ data uses paths relative to this isolated subdirectory. No two projects share state.

---

## 3. Orchestration Strategies & Execution Modes

The `orchestrate_node` parses ticket properties to choose an `OrchestrationStrategy`.

### Decision Process

1. **LLM Evaluation**: Dispatches a single-turn prompt to the `multi_agent_orchestrator` agent to evaluate ticket complexity and return a structured `OrchestrationStrategy` schema.
2. **Rule-Based Heuristic Fallback**: If the LLM call fails, applies regex keyword classification:
   - **Offline-First Detection**: Scans for `offline`, `local storage`, `sqlite`, `hive`, `drift`, `isar`, `cache`.
   - **UI/UX-Only Detection**: Presentation keywords (`style`, `screen`, `widget`) with no backend elements (`api`, `db`, `migration`).

> [!IMPORTANT]
> **Dual Orchestration & Payload Bypass**:
> - The `orchestrate_node` dispatches to OpenCode's `multi_agent_orchestrator` only for the initial complexity and execution mode classification (using a single-turn prompt), not to manage the step-by-step SPOQ lifecycle.
> - The actual wave topological sorting, dispatch scheduling, retry/repair counts, and node transitions are driven entirely by the Python LangGraph code.
> - If the incoming request payload contains a predefined list of tasks (`tasks[]` is present, e.g. in `dummy_request.json`), the LLM orchestrator classification is completely bypassed. The execution mode is automatically set to `spoq`.

### `OrchestrationStrategy` Schema

| Field | Values | Description |
|:---|:---|:---|
| `complexity` | `low` / `medium` / `high` | Ticket complexity rating |
| `execution_mode` | `spoq` / `parallel` / `sequential` | Pipeline execution mode |
| `mocking_level` | `live` / `mock_repositories` / `ui_stubs` | Frontend mocking strategy |
| `offline_first` | `bool` | Enable offline-first architecture |
| `ui_ux_only` | `bool` | Skip backend if pure UI ticket |
| `max_repair_iterations` | `int` | Repair loop budget (default: 3) |
| `stages` | `List[List[str]]` | Platform execution waves (sequential/parallel mode) |

### Core Execution Modes

| Mode | Description |
|:---|:---|
| **Sequential** | Platforms execute one after another (wave-based `stages` list) |
| **Parallel** | All platforms run concurrently with `asyncio.gather` |
| **SPOQ** | Wave-based DAG dispatch with topological sort, code validation gate, and epic lifecycle |

---

## 4. Specialist Orchestrated Queuing (SPOQ)

SPOQ is a methodology (arXiv:2606.03115v1) for multi-agent software engineering. It combines wave-based topological dispatch, dual validation gates, confidence-scored session journals, and epic lifecycle management.

### Four-Stage Pipeline

```mermaid
graph LR
    subgraph "Stage 1: Epic Planning"
        A["Orchestrator: Create context JSONs"] -->|Dispatch planners| B["@api_planner / @flutter_planner: Write plans"]
    end
    subgraph "Stage 2: Wave Execution"
        B -->|Topological Sort| C["Orchestrator: Compute waves & dispatch builders"]
    end
    subgraph "Stage 3: Code Validation"
        C -->|avg ≥ 95, min ≥ 80| D["Builder + @code_evaluator"]
    end
    subgraph "Stage 4: Epic Completion"
        D --> E["Orchestrator: Commit & squash-merge"]
    end

    style A fill:#1E293B,stroke:#0EA5E9,stroke-width:2px,color:#fff
    style B fill:#1E293B,stroke:#0EA5E9,stroke-width:2px,color:#fff
    style C fill:#1E293B,stroke:#10B981,stroke-width:2px,color:#fff
    style D fill:#1E293B,stroke:#10B981,stroke-width:2px,color:#fff
    style E fill:#1E293B,stroke:#0EA5E9,stroke-width:2px,color:#fff
```

### Epic Directory Structure

Each epic occupies its own directory under `.ebpearls/`:

```
.ebpearls/
  ROADMAP.md                    ← Centralized epic registry (status: planned → in-progress → done)
  Epic-{id}/                    ← e.g. Epic-44445 (one directory per epic)
    EPIC.md                     ← Goal, architecture, dependency DAG, wave assignments
    {task-name}.md              ← Task plan Markdown (e.g. api-impl-41831.md)
    context_api.json            ← Platform context (generated by orchestrator)
    context_flutter.json
    journals/                   ← Agent session journals
```

### Concrete Task DAG Example (from dummy_request.json)

For ticket `Epic-44445` containing platform configurations, the tasks are decomposed topologically into distinct waves:

```mermaid
graph TD
    subgraph "Wave 0 (Phase 0): API Contracts"
        T1["contract-41831<br>(Define API Contracts)"]
    end
    subgraph "Wave 1 (Phase 1): Platform Implementations"
        T2["api-impl-41831<br>(NestJS Backend logic)"]
        T3["flutter-impl-41831<br>(Flutter UI & Logic)"]
        T4["flutter-impl-41863<br>(User List Logic)"]
    end
    subgraph "Wave 2 (Phase 2): End-to-End Integration"
        T5["integration-41831<br>(Form submission validation)"]
        T6["integration-41863<br>(User List search checks)"]
    end

    T1 --> T2
    T1 --> T3
    T2 --> T5
    T3 --> T5
    T4 --> T6
```

### Code Validation Gate (10 Metrics)

After each task is implemented, the `@code_evaluator` agent independently scores the output against 10 metrics:

| # | Metric | What It Checks | Platform-Specific |
|---|--------|---------------|-------------------|
| 1 | **SC** — Syntactic Correctness | Compiles without errors? | `tsc --noEmit` / `flutter analyze` |
| 2 | **TE** — Test Existence | Unit/widget/integration tests exist? | Check test directories |
| 3 | **TP** — Test Pass Rate | Test suites execute and pass successfully? | `npm run test` / `flutter test` |
| 4 | **RF** — Requirements Fidelity | Matches task `acceptance_criteria`? | Compare code to Markdown spec |
| 5 | **SA** — SOLID Adherence | Follows SOLID principles? | NestJS module pattern / Clean Architecture |
| 6 | **SE** — Security | OWASP Top 10 free? | Guards, validation, no injection |
| 7 | **EH** — Error Handling | Failures handled gracefully? | `@Catch()` / `handleAPICall` |
| 8 | **SL** — Scalability | Hot-path complexity? | Pagination, indexes, `ListView.builder` |
| 9 | **CC** — Code Clarity | Readable and self-documenting? | Project convention conformance |
| 10| **CO** — Completeness | No TODOs/stubs? | No `FIXME`, no placeholders |

**Pass criteria:** `avg(M₁…M₁₀) ≥ 95 AND min(M₁…M₁₀) ≥ 80`

On failure: evaluator returns a **≤20 line remediation** with `file:line` references and numbered action items. The builder applies fixes and re-submits (max 3 iterations).

### Confidence Scoring & Session Journals

Every agent work session produces a journal entry — a structured Markdown file with YAML frontmatter that captures what was done, why, how confident the agent is, and what remains. Journals accumulate per-epic and provide the audit trail for the validation gate, multi-agent coordination, and post-mortem analysis.

#### Journal File Naming

```
.ebpearls/Epic-{id}/
  journals/
    {YYYY-MM-DD}_{session-type}_{agent-short}.md  ← Per-session entries
    JOURNAL.md                                     ← Consolidated epic journal
```

Each session file follows the pattern `{date}_{type}_{role}.md`:
- `date`: ISO 8601 date, e.g. `2026-07-06`
- `type`: One of `development`, `refactor`, `bugfix`, `validation`, `review`
- `role`: Short agent name, e.g. `builder`, `planner`, `evaluator`

Examples:
```
2026-07-06_development_api-builder.md
2026-07-06_validation_evaluator.md
2026-07-07_bugfix_api-fixer.md
```

#### YAML Frontmatter

```yaml
---
agent: Claude Code (Opus 4.5)
start_time: 2026-07-06T10:00:00Z
end_time: 2026-07-06T11:30:00Z
confidence: 0.88
session_type: development
files_modified:
  - libs/data-access/src/enquiry/enquiry.schema.ts
  - libs/data-access/src/enquiry/enquiry.repository.ts
  - apps/api/src/modules/enquiry/enquiry.module.ts
tasks_completed: 1
tasks_total: 3
---
```

| Field | Type | Description |
|:---|:---|:---|
| `agent` | `str` | Agent name and model tier, e.g. `"Claude Code (Opus 4.5)"` |
| `start_time` | `ISO 8601` | Session start timestamp in UTC |
| `end_time` | `ISO 8601` | Session end timestamp in UTC |
| `confidence` | `float (0.0–1.0)` | Calibrated self-assessment score |
| `session_type` | `str` | `development` / `refactor` / `bugfix` / `validation` / `review` |
| `files_modified` | `list[str]` | Every file touched during the session |
| `tasks_completed` | `int` | Number of tasks finished this session |
| `tasks_total` | `int` | Total tasks assigned in the current wave |

#### Confidence Score Calibration

Agents self-assess their output using a calibrated 0.0–1.0 scale before handing off to the `@code_evaluator`. The score is a subjective quality assessment, not an automated metric — it captures edge-case awareness, testing thoroughness, and known gaps the agent is aware of.

| Range | Label | When to Use |
|-------|-------|-------------|
| **0.95–1.0** | Production-ready | All acceptance criteria met, build/lint passes, edge cases handled, tests written and passing. Ready for merge without human review. |
| **0.85–0.94** | Well tested | Core functionality works with tests. Minor edge cases may be untested. Small refactors may be needed but no blocking issues. |
| **0.75–0.84** | Functional | Happy path works. Some edge cases untested, minor TODOs remain, or a few non-critical acceptance criteria are unmet. Needs additional validation. |
| **0.65–0.74** | Needs validation | Works in ideal conditions only. Known gaps in error handling, testing, or completeness. Flag for targeted review. |
| **< 0.65** | Experimental | Incomplete, known defects, or untested assumptions. Flag as requiring human review before proceeding to next wave. |

The evaluator cross-references the agent's confidence score against its own 10-metric scoring. A significant gap (e.g. agent claims 0.90 but evaluator scores below 80) triggers deeper review.

#### Journal Body (Markdown)

After the frontmatter, every journal entry follows a standardized Markdown body:

```markdown
## Summary
Brief 1–2 sentence overview of what was accomplished this session.

## Work Completed
- Task contract-41831: Defined Enquiry Mongoose schema with timestamps and soft-delete fields
- Task contract-41831: Created EnquiryRepository extending BaseRepo<EnquiryDocument>

## Changes Made
**Data Access Layer**
- `libs/data-access/src/enquiry/enquiry.schema.ts` — Created schema with title (required), description (required), isDeleted, deletedAt
- `libs/data-access/src/enquiry/enquiry.repository.ts` — Created repository extending BaseRepo with createEnquiry method

**Module Layer**
- `apps/api/src/modules/enquiry/enquiry.module.ts` — Wired MongooseModule.forFeature, providers, exports

**Registrations**
- `libs/data-access/src/index.ts` — Added `export * from './enquiry'`
- `libs/data-access/src/data-access.models.ts` — Registered { name: Enquiry.name, schema: EnquirySchema }
- `apps/api/src/app.module.ts` — Added EnquiryModule to imports and GraphQL include

## Issues Encountered
- None

## Testing
- Build check: `npm run build:api` — PASSED (0 errors)
- Lint check: `npm run lint` — PASSED (0 warnings)
- Manual schema verification: confirmed enquiry collection created in MongoDB with correct fields

## Next Steps
1. Builder must create CreateEnquiryInput DTO, EnquiryService, and EnquiryResolver
2. Builder must add i18n keys in en/enquiry.json and ne/enquiry.json
3. Flutter builder waits for API contract to be available before generating data layer
```

#### How Journals Drive the Pipeline

1. **Agent writes journal** → stored in `journals/{date}_{type}_{role}.md`
2. **Evaluator reads journal** → cross-references confidence score against 10-metric results
3. **Orchestrator consolidates** → merges per-session journals into `JOURNAL.md` at epic completion
4. **Audit trail** → every decision, file change, and issue is traceable across sessions

#### Consolidation

When all waves complete, the orchestrator produces `JOURNAL.md` — a chronological merge of all session journals with added sections:

```markdown
# Epic Journal: Epic-44445

## Session Index
| # | Date | Agent | Type | Confidence | Tasks |
|---|------|-------|------|------------|-------|
| 1 | 2026-07-06 | api_planner | plan | 0.92 | 1/1 |
| 2 | 2026-07-06 | api_builder | development | 0.88 | 1/3 |
| 3 | ... | ... | ... | ... | ... |

## Metrics Summary
- Average confidence across sessions: 0.87
- Evaluator pass rate: 100% (4/4 tasks passed 10-metric gate)

## Final Status
- Epic status: done
- Branch: feature/Epic-44445-enquiry → main (squash-merged)
- PR: https://bitbucket.org/.../pull-requests/42
```

### Epic Lifecycle

1. **Creation:** Orchestrator creates EPIC.md and `context_{platform}.json` context files → dispatches planners to write `{active_task_id}.md` plans → `.ebpearls/Epic-{id}/`
2. **Execution:** Orchestrator computes waves, dispatches builders, invokes evaluator per task. ROADMAP.md → `in-progress`
3. **Validation:** Each task scored against 10 metrics; failed tasks enter remediation loop
4. **Completion:** All tasks passed → ROADMAP.md → `done`. No filesystem move needed.
5. **Commit:** Branch-per-epic with squash-merge to main. Commits at wave boundaries.

---

## 5. Specialist Agent Pool & Execution Bridge

All agent profiles live in `.opencode/agents/`. Primary agents are invoked directly by pipeline nodes. Subagents are delegated via `@agent-name` syntax.

### OpenCode Execution Bridge

Agents are defined as Markdown files (`.opencode/agents/*.md`) containing YAML frontmatter configuration (such as permitted tools, OpenAI/Anthropic model selections) and system prompt rules. 

The Python orchestrator communicates with the OpenCode server over HTTP REST endpoints:
1. **Create Session**: `POST /session` -> yields a unique `session_id`.
2. **Execute Agent**: `POST /session/{session_id}/message` -> sends JSON payload:
   ```json
   {
     "agent": "api_builder",
     "parts": [{"text": "hydrated prompt context"}]
   }
   ```
3. **SSE Progress Stream**: `GET /event` -> streams real-time console log and agent action deltas.
4. **Parse Output**: The session concludes when the agent outputs a final JSON block, which the bridge translates into a `JobResult`.

Platform and pipeline phase map directly to target agent profiles:
- Flutter + Planning -> `flutter_planner`
- Flutter + Building -> `flutter_builder`
- API + Planning -> `api_planner`
- API + Building -> `api_builder`
- Validation Gate -> `code_evaluator`

### Primary Agents

| Agent | Platform | Responsibility |
|:---|:---|:---|
| `multi_agent_orchestrator` | Cross-Platform | Creates epics, computes waves, dispatches builders, manages lifecycle |
| `code_evaluator` | Cross-Platform | Independent 10-metric code reviewer (read-only) |
| `api_planner` | API (NestJS) | Audits modules, writes `{active_task_id}.md` implementation plans |
| `api_builder` | API (NestJS) | Implements schemas, DTOs, services, resolvers, modules |
| `flutter_planner` | Flutter | Reviews widget trees, writes `{active_task_id}.md` implementation plans |
| `flutter_builder` | Flutter | Generates domain/data/state/UI layers |
| `web_planner` | React/Next.js | Plans components and routing |
| `web_builder` | React/Next.js | Scaffolds pages and styles |
| `api_bug_fixer` | API | Diagnoses and patches backend failures |
| `flutter_bug_fixer` | Flutter | Diagnoses and patches Flutter failures |

### Subagents (Delegated via `@`)

| Subagent | Delegated From | Responsibility |
|:---|:---|:---|
| `@api_schema_builder` | `api_builder` | Mongoose schemas, BaseRepo repos |
| `@api_dto_generator` | `api_builder` | GraphQL InputType/ObjectType, validation |
| `@api_service_builder` | `api_builder` | Business logic, @Transactional(), i18n |
| `@api_route_builder` | `api_builder` | GraphQL resolvers, REST controllers, guards |
| `@api_module_integrator` | `api_builder` | Module wiring, mongoose-models.ts + providers.ts |
| `@api_linter` | `api_builder` | ESLint + Prettier on changed files |
| `@api_localization` | `api_builder` | i18n JSON catalog management |
| `@api_contract_verifier` | `contract_node` | Cross-platform GraphQL contract checks |
| `@flutter_domain` | `flutter_builder` | Domain models + abstract repository interfaces |
| `@flutter_graphql` | `flutter_builder` | .graphql operation files, schema refresh |
| `@flutter_data` | `flutter_builder` | Freezed models, data sources, repo impls |
| `@flutter_state` | `flutter_builder` | SimplexCubit + freezed state |
| `@flutter_ui` | `flutter_builder` | Pages and widgets, Bloc wiring |
| `@flutter_ui_refiner` | `flutter_builder` | Visual polish, spacing, tokens |
| `@flutter_design_system` | `flutter_builder` | Token/spacing review |
| `@flutter_localization` | `flutter_builder` | ARB file management |
| `@flutter_linter` | `flutter_builder` | `flutter analyze`, targeted fixes |
| `@flutter_figma_assets` | `flutter_planner` | Figma design token extraction |

### Skill Framework

Reusable capabilities live in `.opencode/skills/`:

| Skill | Purpose |
|:---|:---|
| `agent-validation` | 10-metric code scoring rubric (SC, TE, TP, RF, SA, SE, EH, SL, CC, CO) |
| `journal-tracker` | Session journal format with confidence calibration |
| `api-scaffolder` | NestJS module, service, resolver patterns |
| `nestjs-graphql-resolvers` | Code-first GraphQL types and resolvers |
| `nestjs-i18n-localization` | Translation key management |
| `feature-scaffolder` | Flutter Clean Architecture directory scaffolding |
| `api-integration` | Freezed models, GraphQL sources, repos |
| `state-management` | SimplexCubit, FormMixin, handleAPICall |
| `ui-generator` | Flutter page and widget generation |
| `design-system` | Token migration, responsive sizing |
| `localization` | ARB extraction and l10n refactoring |
| `graphql-client-codegen` | Schema sync and Ferry codegen |
| `compiler-diagnostics-resolver` | TypeScript/Flutter error pattern matching |

---

## 6. End-to-End Pipeline Execution Lifecycle

The sequence diagram below shows the runtime pipeline execution loop, mapping host HTTP requests to the stateful LangGraph engine, and subsequent REST/SSE interactions with the OpenCode container.

```mermaid
sequenceDiagram
    autonumber
    actor User as User (curl POST)
    participant FastAPI as FastAPI Host (Port 8001)
    participant Graph as LangGraph Engine
    participant Git as Git Service
    participant OpenCode as OpenCode Server (Port 4096)
    participant Workspace as Isolated Workspace

    User->>FastAPI: POST /execute (dummy_request.json)
    FastAPI->>Graph: Initialize state & trigger execution graph

    %% prepare phase
    Note over Graph,Workspace: 1. Prepare Phase
    Graph->>Git: Clone/fetch repositories concurrently
    Git->>Workspace: Initialize sub-projects, checkout branch, run install/pub get

    %% orchestrate phase
    Note over Graph,Workspace: 2. Orchestrate Phase
    Graph->>Graph: Bypasses LLM classification if tasks[] defined. Sets SPOQ mode.
    Graph->>Graph: Decomposes tasks topologically into Waves

    %% plan/generate loop
    Note over Graph,Workspace: 3. Wave Execution Loop
    loop Every computed Wave
        %% plan phase
        Note over Graph,OpenCode: Planning (Concurrent)
        Graph->>OpenCode: POST /session (Create api/flutter planner sessions)
        Graph->>OpenCode: POST /session/{id}/message (Pass requirements + active_task_id)
        OpenCode->>Workspace: Audit code & write plan to {active_task_id}.md
        OpenCode-->>Graph: Return JobResult (Success)

        %% generate phase
        Note over Graph,OpenCode: Implementation (Concurrent)
        Graph->>OpenCode: POST /session (Create api/flutter builder sessions)
        Graph->>OpenCode: POST /session/{id}/message (Pass plan path)
        OpenCode->>Workspace: Write code implementations
        OpenCode-->>Graph: Return JobResult (Success)

        %% validation gate
        Note over Graph,OpenCode: Quality Gate Validation
        Graph->>OpenCode: POST /session (Create code_evaluator session)
        OpenCode->>Workspace: Read plan & modified code
        OpenCode->>OpenCode: Run tsc/flutter analyze & score 10 metrics
        alt quality gate failed
            Note over Graph,OpenCode: Retry / Remediation Loop
            OpenCode-->>Graph: Return Fail + remediation text
            Graph->>OpenCode: Invoke builder with remediation (max 3 repair iterations)
        else quality gate passed
            OpenCode->>Workspace: Write validation journal
            OpenCode-->>Graph: Return Pass
        end

        Graph->>Git: Commit wave boundaries
    end

    %% contract auditing
    Note over Graph,Workspace: 4. Contract Verification
    Graph->>OpenCode: Invoke contract_verifier (Verify GraphQL schema matches client operations)
    OpenCode-->>Graph: Return status (pass / failure details)

    %% publish phase
    Note over Graph,Workspace: 5. Publishing Phase
    Graph->>Git: Squash-merge feature branch to main & create Pull Request
    
    %% finalize phase
    Note over Graph,Workspace: 6. Finalization Phase
    Graph->>FastAPI: Pipeline done (status & outputs)
    FastAPI-->>User: Return response JSON (PR URLs & wave status summary)
```

---

## 7. Key Data Schemas

### `JobContext` — Pipeline Execution Context

| Field | Type | Description |
|:---|:---|:---|
| `task_id` | `str` | Unique task identifier |
| `space_name` | `str` | Project identifier — drives workspace and storage resolution |
| `ticket_id` | `str` | Ticket/epic identifier (e.g. `ENQ-5`) |
| `ticket` | `SprintTicket` | Full ticket data with nested EpicTask list |
| `repo_path` | `str` | Resolved host path: `workspace/<space_name>/` |
| `platforms` | `List[str]` | Active platforms: `api`, `flutter`, `web` |
| `spoq_epic_dir` | `Optional[str]` | Path to active SPOQ epic directory |
| `active_task_id` | `Optional[str]` | Current task within the epic |
| `starter_types` | `Dict[str, str]` | Per-platform scaffold: `{"api": "nestjs", "flutter": "flutter"}` |
| `mocking_level` | `str` | `live` / `mock_repositories` / `ui_stubs` |
| `offline_first` | `bool` | Enable offline-first patterns |

### `SprintTicket` — Epic / Ticket Model

| Field | Type | Description |
|:---|:---|:---|
| `id` | `str` | Ticket identifier |
| `title` | `str` | Human-readable title |
| `tasks` | `List[EpicTask]` | Nested tasks with per-platform hour estimates |

### `EpicTask` — Task Within an Epic

| Field | Type | Description |
|:---|:---|:---|
| `id` | `int` | Task identifier |
| `name` | `str` | Task name |
| `status` | `str` | `pending` / `in_progress` / `completed` |
| `hours` | `List[EpicTaskHour]` | Per-platform hour estimates |
| `active_platforms` | `List[str]` (property) | Platforms with > 0 estimated hours |

### `SPOQTask` — Task Schema

| Field | Type | Description |
|:---|:---|:---|
| `id` | `str` | Unique task ID (e.g. `api-impl-41831`) |
| `phase` | `int` | Wave assignment (0 = no dependencies) |
| `dependencies` | `List[str]` | Prerequisite task IDs |
| `skills_required` | `List[str]` | Required domain skills |

### `GraphState` — LangGraph Node State

| Field | Type | Description |
|:---|:---|:---|
| `context` | `JobContext` | Active job parameters |
| `strategy` | `OrchestrationStrategy` | Execution strategy from orchestrate node |
| `current_stage` | `int` | Current SPOQ/sequential wave index |
| `platform_results` | `Dict[str, JobResult]` | Per-platform build results |
| `done_platforms` | `Dict[str, bool]` | Validation pass status per platform |
| `opencode_session_ids` | `Dict[str, str]` | Resumable session IDs per platform |
| `is_spoq` | `bool` (property) | True when execution_mode is `"spoq"` |

---

## 8. Project Codebase Layout

```
.
├── Architecture.md                   ← This file
├── docker-compose.yml                ← Multi-container local execution setup
├── pyproject.toml                    ← Python package configuration (Poetry/Pyright)
├── .gitignore                        ← Excludes workspace/, .opencode/, .env
├── .env                              ← Local environment configuration file
│
├── test/
│   ├── dummy_request.json            ← Sample API execution payload
│   ├── test_pipeline.py              ← Concurrent pipeline simulation dry run
│   └── test_opencode_api.py          ← OpenCode bridge path isolation verification
│
├── workspace/                        ← (gitignored) Runtime project checkouts
│   └── <space_name>/
│       ├── .ebpearls/                ← Isolated epic context and plans
│       │   ├── ROADMAP.md            ← Progress tracker
│       │   └── Epic-{id}/
│       └── <platform>/
│
├── .opencode/                        ← (gitignored) Agent state and profiles
│   ├── agents/                       ← Agent profile instructions (.md)
│   │   ├── multi_agent_orchestrator.md
│   │   ├── code_evaluator.md         ← Independent 10-metric reviewer
│   │   ├── api_builder.md / flutter_builder.md
│   │   ├── api_planner.md / flutter_planner.md
│   │   └── ...
│   ├── skills/                       ← Reusable skill definitions
│   │   ├── agent-validation/SKILL.md
│   │   └── ...
│   ├── sessions.json
│   └── jobs.json
│
└── src/
    └── ebdev/
        ├── api/
        │   └── main.py               ← FastAPI server exposing the /execute endpoint
        ├── config.py                 ← Environment configuration loader
        ├── core/
        │   ├── exceptions.py         ← Domain exceptions
        │   ├── graph.py              ← LangGraph StateGraph pipeline & routing
        │   ├── name_utils.py         ← Shared feature name extraction & sanitization
        │   ├── spoq_map.py           ← Task DAG and SPOQ waves builder
        │   ├── spoq_utils.py         ← Task loading & epic lifecycle helper
        │   ├── nodes/
        │   │   ├── prepare.py        ← Workspace clone & dependency setup
        │   │   ├── orchestrate.py    ← Strategy selection & SPOQ DAG generation
        │   │   ├── plan.py           ← Concurrent planner invocation
        │   │   ├── generate.py       ← Concurrent builder invocation
        │   │   ├── validate.py       ← Quality gate validator dispatcher
        │   │   ├── contract.py       ← Cross-platform schema verifier
        │   │   ├── repair.py         ← Failure analysis and repair
        │   │   ├── publish.py        ← Branch commit and PR creation
        │   │   └── finalize.py       ← Job status persistence
        │   └── logger.py
        ├── models/
        │   └── schemas.py            ← JobContext, GraphState, SPOQTask, EpicTask, ...
        ├── platforms/
        │   ├── base.py               ← PlatformStrategy abstract interface
        │   ├── flutter.py            ← FlutterStrategy
        │   └── api.py                ← ApiStrategy
        └── services/
            ├── db.py                 ← Job tracking & JSON fallback
            ├── git.py                ← Git repository, branch, and PR provider
            ├── opencode.py           ← SSE-streaming OpenCode client
            ├── prompts.py            ← Prompt builders with path translation
            └── starter.py            ← Project skeleton bootstrapping
```
