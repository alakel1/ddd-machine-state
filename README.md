# Machine Status · DDD

**Find the edge cases in your domain model before your users do.**

Machine Status is a modelling tool for functional analysts. You describe a domain the DDD way (aggregates, their lifecycles, the rules that tie them together) and it explores *every* reachable combination of states to tell you where the model breaks: forbidden state combinations that can actually happen, cascading effects that conflict, deadlocks, dead actions, unreachable states.

![Edge case analysis](docs/screenshots/analysis.png)

> The UI is available in **English** and **Italian** (switch from the sidebar). The example domain, an Italian e-commerce order with payment, shipment and invoice, ships with a few deliberate gaps so you can run the analysis and see what it finds.

## Features

### Domain overview
A context map of your entities grouped by bounded context, with declared relations, state dependencies (guards) and cascading effects drawn between them.

![Domain overview](docs/screenshots/overview.png)

### Entity editor
Define states (initial, final; active, off or deleted), creation actions, transitions with guards and cascading effects, and the entity's relations with what each composition implies. A live state diagram, a *states × actions* table that exposes unhandled combinations, and a list of everyone who depends on the entity.

The errors the analysis finds on the entity are shown right on top, each with what to fix and a **Fix** button: transitions that other entities' cascades need and the entity lacks can be added in one click.

![Entity editor](docs/screenshots/entity.png)

### Actions and cascades
A cascade is declared in one place: on a "moves to state" effect you pick the child's states it must work from (suggested from the reachable combinations), and the tool creates and maintains the child's cascade-only transitions, named after the action that needs them. The **Actions** page shows each action's whole cascade tree (effects → the children's actions → …), lets you add or remove effects, and highlights in red the states an effect has no transition from: one click covers them.

### Simulator
Set any combination of states, even ones that "shouldn't happen", and see which actions are enabled, blocked (and why) or in conflict. Execute, force, undo, save scenarios, and replay any path found by the analysis step by step.

![Simulator](docs/screenshots/simulator.png)

### Edge case analysis
A breadth-first model checker over the global state space (up to 1,000,000 combinations, cached until the model changes). It reports:

- **Violable invariants**: a sequence of actions reaches a forbidden combination
- **Blocked cascades**: a cascade asks an entity for a transition it can't make, with a suggested fix
- **Missing transitions**: grouped per entity, with the actions that need them
- **Deadlocks**: non-final entities with no enabled action (children of a concluded aggregate don't count), with the guard or action that caused them
- **Unreachable states** and **dead actions**
- **Non-determinism**: the same action leading to different states
- **Creations that skip checks** the normal path enforces
- **Broken compositions**: a child alive without its parent
- **Relations without rules** and **undeclared dependencies**

Every finding comes with the **shortest path** that reproduces it, which you can replay in the simulator. A co-existence matrix shows which state pairs of two entities are reachable together.

Findings are grouped **by cause**, so fixing one cause clears all its symptoms. Each one has a **Fix** button with concrete fixes derived from the model (add the missing transition, skip concluded children, widen a guard, add the effect a composition needs…): **preview** what a fix would solve or introduce, then apply it in one click. Findings that are intended can be **accepted** (or postponed) with a note; they come back if the part of the model involved changes. The exploration runs in the background as soon as the model changes, so results are usually ready when you open them.

![Fix with preview](docs/screenshots/fix.png)

### Expectations
Acceptance tests for the domain: **goals** that must stay reachable ("it must be possible to reach Order DELIVERED with Payment CAPTURED") and **paths** saved from the simulator that must keep working, or stay blocked. They are checked at every analysis, so a change that breaks one shows up right away.

![Expectations](docs/screenshots/expectations.png)

### History and undo
Every change to the model leaves a version: **undo** with one click (or Ctrl+Z), see what each change did, compare any version with today and restore it.

### Specification and export
An always up-to-date **functional specification** page (context map, every entity with states, diagram, actions, guards and effects, invariants, expectations and open points), ready to print or save as PDF. Also JSON (round-trip import) and Markdown.

![Functional specification](docs/screenshots/spec.png)

### Guide
A built-in guide explains every concept of the model with an example; the "?" next to the trickier settings in the editor open the right entry.

![Guide](docs/screenshots/guide.png)

## Concepts

| Concept | Meaning |
|---|---|
| **Entity** | Aggregate, entity or value object, with bounded context, attributes and lifecycle |
| **State** | One initial state, zero or more final states. Each state also says whether the entity is **active** (exists and operates), **off** (disabled but recoverable, e.g. DISABLED) or **deleted** (gone for good, e.g. DELETED) |
| **Creation action** | A way the entity comes into existence: from `DOES_NOT_EXIST` to a state, with its own guards, effects and actor. An entity can have several |
| **Action** | A transition `from → to` (from "any" = from every non-final state), with an actor |
| **State guard** | Precondition: another entity must (or must not) be in given states. Guards can be alternatives (OR), and on a composition child they can apply **for each** parent ("every order has at least one available line") |
| **Rule** | Free-text business condition, shown as "to be verified" |
| **Cascading effect** | The action forces another entity to change state, run an action or be created. If no valid transition exists, it's a **conflict**. A state effect can list the child's starting states: the matching transitions are then generated |
| **Invariant** | A forbidden combination of states (AND of conditions); may use `DOES_NOT_EXIST` |
| **Relation** | 1-1 or 1-N between two entities. A many-to-many link is modelled as a **join entity**, child of both, since it usually has its own states and rules (an imported N-N is flagged, and Fix creates the join entity) |
| **Composition** | The child cannot exist without the parent. Two invariants follow: a child must be deleted when the parent is deleted (or does not exist), and cannot stay active when the parent is off. The tool also checks that creating the child requires the parent, and that the actions deleting or turning off the parent take care of the children |
| **Multiple instances** | On a composition, simulate 2–3 children per parent in the analysis (e.g. "one item confirmed, the other deleted") |

The simulation model has one instance per entity: a single business case, such as one order with its payment, shipment and invoice.

## Getting started

### Docker (development, hot reload)

```bash
docker compose up -d     # frontend http://localhost:5180 · API http://localhost:8010
docker compose logs -f
```

No image builds: `backend/` and `frontend/` are mounted as volumes, so Uvicorn (`--reload`) and Vite (HMR) pick up every change.

### Without Docker

```bash
./run.sh            # installs deps, builds the frontend if missing, serves on http://localhost:8000
./run.sh --build    # force a frontend rebuild
```

For hot reload, run `uvicorn app.main:app --reload` in `backend/` and `npm run dev` in `frontend/` (http://localhost:5173, `/api` is proxied to port 8000).

The database is `backend/ddd.sqlite3` (override with `DDD_DB`). On first start an example domain is seeded.

### Tests

```bash
docker compose exec backend sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q tests"
```

CI (GitHub Actions) runs the backend tests and the frontend build on every push and pull request.

## Tech stack

| Layer | Stack |
|---|---|
| Backend | Python, FastAPI, SQLite (no ORM) |
| Engine | Pure Python state machine + BFS model checker (`backend/app/engine.py`, `relations.py`) |
| Frontend | Vue 3, Vue Router, vue-i18n, Tailwind CSS 4, Mermaid, Font Awesome |

```
backend/app/
  engine.py        state machine, cascade propagation, exploration, analysis
  relations.py     compositions, multiple instances, relation checks
  fixes.py         fixes proposed for each finding ("Fix")
  history.py       model snapshots: undo, restore, compare
  editing.py       multi-row edits and fix operations
  i18n.py          server-side messages (analysis, conflicts, export) in en/it
  db.py            schema, migrations, generic CRUD
  main.py          REST API
  portability.py   JSON import / Markdown export
  seed.py          example domain
backend/tests/     engine and API tests (pytest)
frontend/src/
  views/           Home, Model, Entity, Invariants, Actions, Simulator, Analysis, Expectations, History, Spec, Guide
  components/      Mermaid, FixModal, EffectEditor, ActionNode, AckBadge, HelpLink, StateChip, StatePicker, Trace, Modal, PageHeader
  locales/         UI translations (en.json, it.json)
  diagrams.js      Mermaid generators (state diagram, context map)
  store.js         reactive store and API client
```

## Contributing

Contributions are welcome! Read [CONTRIBUTING.md](CONTRIBUTING.md) to get started. Commits must be signed off under the [Developer Certificate of Origin](https://developercertificate.org/) (`git commit -s`).

## License

Copyright (C) 2026 Luca Orlandelli

This program is free software: you can redistribute it and/or modify it under the terms of the [GNU Affero General Public License v3.0](LICENSE) as published by the Free Software Foundation.

In short: you can use, modify and share it, but if you distribute it or run a modified version as a network service, you must release your source code under the same license.
