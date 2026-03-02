# Engine Layer — Orchestrator to Facilitator

## Architectural Shift

Dark Factory's execution model has evolved from an **orchestrator** pattern to a
**facilitator** pattern. The distinction is fundamental:

| Concern | Orchestrator (old) | Facilitator (new) |
|---------|-------------------|-------------------|
| **Control** | Central loop drives every step | Pipelines are self-contained units |
| **Decisions** | Engine decides *what* to do and *how* | Factory decides *when*; engine decides *how* |
| **Coupling** | Tight — every new feature touches the main loop | Loose — add a pipeline, register it, done |
| **Failure** | One bad stage stalls everything | Each pipeline handles its own retries/failures |

In the old model, `dark-factory.sh` contained a monolithic loop that selected an
issue, ran security scans, invoked agents, built code, tested, and deployed — all
inline. Adding a new capability meant editing the main script.

In the facilitator model, **Dark Factory** (the outer system) owns the *when* — it
watches for issues, triages them, and decides which pipeline to invoke. The
**engine** owns the *how* — it receives a pipeline name, loads its stage
definitions, and executes them with retry logic, state tracking, and metrics.

## The 7 Base Pipelines

Dark Factory defines seven base DOT (Dispatch-Orchestrate-Track) pipelines. Each
pipeline is a named sequence of stages that the engine can execute.

### 1. `sentinel`

**Role**: Security gate — runs before any code changes are accepted.

Scans workspaces for security-relevant file changes (secrets, CI configs,
dependency files) and blocks processing until findings are resolved. Sentinel
**never auto-heals** security issues; it always requires human acknowledgment.

- **Module**: `factory.workspace.manager._run_sentinel_gate()`
- **Stages**: File change detection → secret scan → SAST → security triage
- **Outcome**: PASS (proceed) or SECURITY_BLOCK (pipeline pauses)

### 2. `arch_review_web`

**Role**: Architecture review for web-strategy projects.

Runs 10 specialist agents in parallel (code quality, security, testing,
performance, API design, database, dependencies, DevOps, UX, integration),
feeds results into an SA Lead agent that produces a GO / CONDITIONAL / NO_GO
verdict. Web strategy enables parallel stages and requires manual review.

- **Module**: `factory.pipeline.arch_review.orchestrator.run_arch_review()`
- **Stages**: Specialist agents (parallel) → SA Lead verdict → cache results
- **Configuration**: `StrategyConfig(name="Web", parallel_stages=True, require_manual_review=True)`

### 3. `arch_review_console`

**Role**: Architecture review for console-strategy projects.

Same specialist-based review as `arch_review_web` but configured for simpler
console applications — single agent, sequential stages, auto-approved audit.

- **Module**: `factory.pipeline.arch_review.orchestrator.run_arch_review()`
- **Stages**: Same as `arch_review_web` but sequential
- **Configuration**: `StrategyConfig(name="Console", parallel_stages=False, auto_approve_audit=True)`

### 4. `dark_forge`

**Role**: Core engineering pipeline — issue to pull request.

The primary code-generation pipeline. Takes an issue, acquires a workspace,
generates specs (PRD + design document), runs a TDD pipeline (test → implement →
verify loop), validates contracts, runs security review, and creates a PR.

- **Module**: `factory.pipeline.route_to_engineering.route_to_engineering()`
- **Stages**: Workspace acquisition → spec generation → TDD pipeline → contract
  validation → security review → PR creation
- **On failure**: Issue labeled `blocked`, entry pushed to dead-letter queue

### 5. `crucible`

**Role**: End-to-end integration testing in an isolated environment.

Builds a Docker Compose environment from generated compose files, starts
services, waits for health checks, runs Playwright tests, captures logs and
screenshots, and produces a GO / NO_GO / NEEDS_LIVE verdict.

- **Module**: `factory.crucible.orchestrator.run_crucible()`
- **Stages**: Build → compose up → health check → test execution → capture →
  teardown
- **Verdicts**: `GO` (all tests pass), `NO_GO` (failures), `NEEDS_LIVE` (skips
  present)

### 6. `ouroboros`

**Role**: Self-consumption — the factory processes its own repository.

When Dark Factory works on its own codebase, Ouroboros applies extra validation
layers beyond the normal pipeline. This is the self-forge / self-crucible gate
that ensures changes to the factory itself don't break the factory.

- **Module**: `factory.pipeline.self_forge.run_self_validation()`
- **Stages**: Lint (ruff + mypy) → tests (pytest) → pipeline simulation (gate
  discovery)
- **Detection**: `is_self_repo()` checks `self_onboarded` flag or marker files

### 7. `deploy`

**Role**: Strategy-aware deployment to target environment.

Deployment is strategy-specific. Console projects use PyPI-style publishing.
Web projects trigger GitHub Actions workflows (`deploy.yml`,
`deploy-staging.yml`) that handle ECR push → ECS update → health check. The
deploy pipeline is the final stage after Crucible produces a GO verdict.

- **Module**: Strategy-specific (`factory.strategies.config`)
- **Stages**: Vary by strategy (see deployment pipelines below)

## Two Deployment Pipelines

Dark Factory separates **workspace deployment** (deploying code changes from an
issue) from **factory deployment** (deploying the factory infrastructure itself).

### Workspace Deploy

Deploys the *target project's* code changes produced by the engineering pipeline.
This is the normal flow for issues processed through Dark Forge → Crucible → Deploy:

1. Dark Forge produces a PR with code changes
2. PR merges to main
3. Crucible runs integration tests → GO verdict
4. Strategy-specific deploy triggers:
   - **Console**: Package build + publish
   - **Web**: GitHub Actions `deploy.yml` → ECR → ECS → health check
   - **Staging**: `deploy-staging.yml` triggered by `crucible-passed` label

Workspace deploy operates on **workspaces** managed by the workspace registry
(`factory.workspace.manager`). Each issue gets an isolated workspace with its own
branch, and deployment artifacts come from that workspace's build output.

### Factory Deploy

Deploys changes to the **Dark Factory infrastructure itself** — the scripts,
agents, and automation that run the pipeline. This path goes through Ouroboros
(self-validation) before any deployment:

1. Change to factory codebase detected (`is_self_repo()`)
2. Ouroboros self-validation gate (lint + tests + pipeline simulation)
3. Self-crucible validation (all 3 layers must pass)
4. Factory-specific deployment (update running factory instance)

Factory deploy is guarded by stricter gates because a broken factory means **all**
project pipelines stop working. The Ouroboros pipeline ensures the factory can
still discover gates, run its own tests, and pass lint before accepting changes.

## Engine Execution Model

The engine executes pipelines through a state machine
(`factory.pipeline.orchestrator.PipelineStateMachine`):

```
IDLE → RUNNING → COMPLETED
                → FAILED → IDLE (retry)
                         → RUNNING (retry)
```

Key engine responsibilities:

- **State tracking**: Valid transitions are enforced; invalid transitions raise
  `InvalidTransitionError`
- **Retry logic**: Configurable `max_retries` with delay between attempts
- **Stage filtering**: Bootstrap pipeline runs only plan/implement/test stages
- **History**: Every attempt is recorded for diagnostics
- **Metrics**: Each pipeline stage is timed; results include per-stage and total
  duration

The engine does **not** decide which pipeline to run — that decision belongs to
the dispatch layer (`factory.dispatch.issue_dispatcher`) and the triage system
(`factory.security.triage`). The engine only knows how to execute a pipeline
definition and report results.
