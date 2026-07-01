# Multi-Agent Platform & Orchestration Architecture

This document provides a comprehensive view of the system architecture of the **ebprocess-development** project. It outlines how the stateful pipeline coordinates multiple specialist agents to execute complex, multi-platform development tasks using LangGraph and **SPOQ (Specialist Orchestrated Queuing)**.

---

## 1. High-Level System Architecture

The core of `ebprocess-development` is a stateful orchestration graph built on top of **LangGraph**. The pipeline coordinates repository setup, ticket analysis, specialist planning, source code generation, automated verification (linters/tests), contract checking, and publishing.

### LangGraph Stateful Pipeline

The workflow starts in the `prepare` node and dynamically routes through planning, generation, validation, contract verification, and error repair before publishing changes:

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

---

## 2. Orchestration Strategies & Execution Modes

The `orchestrate` node is responsible for parsing input ticket properties (summary, description, and acceptance criteria) and active platforms (API, Flutter, Web, CMS) to choose the optimal `OrchestrationStrategy`. 

### The Decision Maker
1. **LLM Evaluation**: A session is created with the `multi_agent_orchestrator` agent on the OpenCode server to parse details and return an evaluation schema.
2. **Rule-Based Heuristic Fallback**: If the LLM call fails, the node falls back to regex-based classification:
   - **Offline-First Detection**: Scans for keywords like `offline`, `local storage`, `sqlite`, `hive`, `drift`, or `cache`.
   - **UI/UX-Only Detection**: Scans for presentation keywords (`style`, `screen`, `widget`, `padding`) and verifies they contain no backend elements (`api`, `db`, `migration`).

### Core Execution Modes
* **Sequential**: Executes task planning, generation, and validation for each platform one after another.
* **Parallel**: Runs tasks across all requested platforms concurrently (suitable for low-complexity or UI-only edits).
* **SPOQ (Specialist Orchestrated Queuing)**: A wave-based topological dependency dispatch system for complex, multi-platform epics.

---

## 3. Specialist Orchestrated Queuing (SPOQ)

SPOQ organizes multi-platform epics by breaking them down into a Direct Acyclic Graph (DAG) of task files (e.g. `.yml` format). Tasks are dispatched in **waves** based on completed dependencies.

For example, when building a feature that requires both database, backend API, and mobile/web frontends:

```mermaid
graph TD
    subgraph Wave 1: Core API Contract
        T1["Task 1: Design backend models, schemas, and endpoints"]
    end
    subgraph Wave 2: Concurrent Frontend Feature Builders
        T2["Task 2: Build Flutter Client (Depends on Task 1)"]
        T3["Task 3: Build React/Web Client (Depends on Task 1)"]
    end
    subgraph Wave 3: Integration & Alignment
        T4["Task 4: Contract Verifier Validation (Depends on Tasks 2 & 3)"]
    end

    T1 --> T2
    T1 --> T3
    T2 --> T4
    T3 --> T4

    style T1 fill:#1E293B,stroke:#0EA5E9,stroke-width:2px,color:#fff
    style T2 fill:#1E293B,stroke:#0EA5E9,stroke-width:2px,color:#fff
    style T3 fill:#1E293B,stroke:#0EA5E9,stroke-width:2px,color:#fff
    style T4 fill:#1E293B,stroke:#10B981,stroke-width:2px,color:#fff
```

* **Dependency Resolution**: In each pass through the `validate` loop, if execution mode is `spoq`, `get_active_wave_tasks` scans the SPOQ epic task directory and extracts tasks that are `pending` or `blocked` but have all their listed `dependencies` marked as `completed`.
* **State Advancement**: The graph routes back to `plan` and `generate` to execute these tasks. Once all tasks are complete, the pipeline advances to systemic `contract` verifications.

---

## 4. Specialist Agent Profiles

The system interacts with a pool of headless agent profiles defined in `.opencode/agents/`. Each agent possesses dedicated system prompts and allowed tools:

| Agent Profile | Mode | Target Platform / Layer | Primary Focus |
|:---|:---|:---|:---|
| **api_planner** | Primary | API (FastAPI / NestJS) | Audits existing models/modules and writes the execution plan (`plan.md`). |
| **api_builder** | Primary | API (FastAPI / NestJS) | Implements schemas, repositories, resolvers, controllers, and tests. |
| **flutter_planner** | Primary | Flutter / Dart Mobile | Reviews widget trees and state schemas, outlines mobile screen layouts. |
| **flutter_builder** | Primary | Flutter / Dart Mobile | Generates Dart widgets, models, controllers, and runs `build_runner` tasks. |
| **web_planner** | Primary | React / Next.js Web | Plans components, state hooks, and routing hooks for the web framework. |
| **web_builder** | Primary | React / Next.js Web | Scaffolds and writes TypeScript files, pages, styles, and web integration tests. |
| **contract_verifier**| Subagent| Cross-Platform | Compares Pydantic schemas/routes against TypeScript models and Dart classes. |
| **figma_assets** | Subagent| Design Assets | Extracts visual styles and outputs scaffolding assets based on Figma URLs. |

---

## 5. End-to-End Pipeline Execution Lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant Pipeline as LangGraph Worker
    participant DB as DB Service (Postgres/JSON)
    participant git as Git Service
    participant OpenCode as OpenCode Server (LLM)
    participant Repo as Target Project Repo

    Note over Pipeline,Repo: 1. Setup & Environment Verification
    Pipeline->>DB: Fetch job details & context
    Pipeline->>git: Clone target repository & prepare branch
    Pipeline->>Repo: Run environment checks (pub get / npm install / pip install)

    Note over Pipeline,Repo: 2. Strategy Architecture Selection
    Pipeline->>OpenCode: Request architectural review (Multi-Agent Orchestrator Agent)
    OpenCode-->>Pipeline: Return complexity, mode (e.g. SPOQ), and mocking details

    Note over Pipeline,Repo: 3. Planning & Building Iteration Loop
    loop Every wave task / platform
        Pipeline->>OpenCode: Dispatch plan task to specialized planner (e.g., api_planner)
        OpenCode->>Repo: Audit repository directory structure
        OpenCode-->>Pipeline: Save feature plan to plans/plan.md
        
        Pipeline->>OpenCode: Dispatch build task to specialist builder (e.g., api_builder)
        OpenCode->>Repo: Implement models, routes, widgets, or controllers
        OpenCode-->>Pipeline: Complete code execution step
        
        Pipeline->>Repo: Run automated tests/linters (pytest / npm run test / flutter analyze)
        alt Test Fails
            Pipeline->>OpenCode: Send test logs to repair agent
            OpenCode->>Repo: Modify files to resolve compiler or test errors
        end
    end

    Note over Pipeline,Repo: 4. Contract Auditing & Verification
    Pipeline->>OpenCode: Invoke contract_verifier subagent
    OpenCode->>Repo: Check backend Pydantic models against frontend TypeScript/Dart models
    OpenCode-->>Pipeline: Verification Status (Success / Failed details)

    Note over Pipeline,Repo: 5. Publishing Changes
    Pipeline->>git: Commit generated changes
    Pipeline->>git: Push branch to remote and create Pull Request (GitHub/Bitbucket)
    Pipeline->>DB: Write final status and exit
```

---

## 6. Project Codebase Layout

```
.
├── docker-compose.yml              # Multi-container local execution setup
├── pyproject.toml                  # Python package configuration (Poetry/Pyright)
├── src
│   └── ebdev
│       ├── config.py               # Environmental configuration loader
│       ├── core
│       │   ├── constants.py        # System constants and regex patterns
│       │   ├── exceptions.py       # Decoupled custom exceptions (GitServiceError, etc.)
│       │   ├── graph.py            # LangGraph StateGraph pipeline routing logic
│       │   ├── nodes/              # Pipeline node steps (prepare, plan, validate, etc.)
│       │   └── spoq_utils.py       # SPOQ task parsing and wave resolution helpers
│       ├── models
│       │   └── schemas.py          # Decoupled schemas and GraphState definition
│       └── services
│           ├── db.py               # DB tracking and local JSON fallback engine
│           ├── flutter_cmd.py      # Headless Flutter CLI executor
│           ├── git.py              # Git repository and branch provider
│           ├── opencode.py         # SSE-streaming client connection to OpenCode
│           ├── prompts.py          # Prompt generators for active nodes
│           └── starter.py          # Starter skeleton seed coordination
```
