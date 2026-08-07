# ADR 0001 — React 19 instead of the React 18 named in the specification

**Status:** accepted
**Date:** 2026-08-07
**Deviates from:** `docs/spec.md` section 3 (Tech stack)

## Context

The specification's tech stack table names "React 18 + Vite + TypeScript". React
19 has been the stable release for well over a year by the time implementation
started, and React 18 is now a major version behind.

The version numbers in the specification describe the state of the ecosystem
when the document was written, not a constraint that was reasoned about. Nothing
elsewhere in the specification depends on a React 18 behaviour.

## Decision

Use React 19. The rest of the stack moves to the versions that pair with it:

| Package | Specification | Chosen |
|---|---|---|
| `react`, `react-dom` | 18 | 19 |
| `react-router-dom` | (unpinned) | 7 |
| `maplibre-gl` | (unpinned) | 5 |

## Consequences

**Why this is the cheaper direction.** Starting a greenfield project on the
previous major means an upgrade is owed from day one, and that upgrade only gets
more expensive as feature code accumulates. Phase 1 is the point at which the
cost of choosing React 19 is lowest — there is almost no UI code to migrate.

**What React 19 changes for this codebase.**

* `ref` is an ordinary prop on function components, so the `forwardRef` wrapper
  disappears. This matters for the MapLibre integration in Phase 1 and the
  segment editor in Phase 3, both of which hold imperative handles.
* The `use` hook and improved Suspense integration are available for the lazy
  stream loading described in spec section 8.2, where the detailed per-second
  data is fetched only when an activity detail page opens.
* React Compiler is usable when the project wants it. Not adopted now — it is
  a separate decision with its own trade-offs — but it is not reachable at all
  from React 18.

**Risks.** React 19 removed several long-deprecated APIs (string refs, legacy
context, `ReactDOM.render`). None are used here, and none are reachable through
the chosen dependencies, all of which declare React 19 support.

**Follow-up.** `docs/spec.md` section 3 still says React 18. The specification is
the record of decisions and is not rewritten retroactively (the same principle
section 8.4 applies to migrations); this ADR is the amendment.
