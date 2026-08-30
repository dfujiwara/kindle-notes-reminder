# Authentication and Data Segmentation Implementation Plan

## Scope

Implement the architecture described in [`authentication-and-segmentation-architecture.md`](./authentication-and-segmentation-architecture.md).

The current application has unscoped repositories, SQLModel models, FastAPI routers, Alembic migrations, ingestion processors, vector search, native SSE endpoints, and background evaluation tasks. The implementation must update all of them together.

## Phase 0: Lock implementation decisions

Resolve and document these decisions before writing application code:

- Prefer opaque server-side sessions over JWT cookies unless there is a strong reason otherwise.
- Define session storage, token hashing, absolute and idle expiration, rotation, revocation, logout, password-reset invalidation, and disabled-account behavior.
- Use a strong password hashing scheme such as Argon2id with a documented parameter policy.
- Select and add the concrete libraries this design requires (a password hashing library, a signed-cookie/session library such as `itsdangerous`, a CSRF library). None of these are currently installed, even transitively; add them explicitly via `uv add` in this phase rather than picking them ad hoc during later phases.
- Define normalized email storage and case-sensitivity rules. Preserve the user-entered email separately if needed for display.
- Define email verification, password-reset, and one-time-token storage. Store only hashed tokens with expiry and single-use semantics.
- Define rate limits for signup, login, verification, reset, and SSE/resource abuse. Use both IP and account identifiers where appropriate. Define where rate-limit counters are stored (for example a database table or Redis) — in-process memory does not work correctly once the app runs more than one worker or replica.
- Define CSRF tokens, Origin/Referer checks, CORS origins, cookie attributes, and native `EventSource` credential behavior.
- Define the controlled first-user bootstrap gate, including the approved-user mechanism and concurrent-claim locking.
- Define workspace status, deletion/retention, membership revocation, owner transfer, last-owner protection, and account disablement.
- Define the initial role permission matrix, even if the only role is `owner`.
- Define whether future workspace selection uses server-side session state or a trusted token claim.
- Define database cascade behavior and vector-search filtering/index strategy.
- Decide whether and when to enable PostgreSQL RLS. If enabled, define transaction handling, database roles, system access, and PostgreSQL integration tests.

## Current code touchpoints

Update these areas as a coordinated change:

- Models: `src/repositories/models.py`
- Dependencies: `src/dependencies.py`
- Routers: `src/routers/`
- Repository interfaces and implementations:
  - `src/repositories/`
  - `src/url_ingestion/repositories/`
  - `src/tweet_ingestion/repositories/`
- Ingestion processors:
  - `src/notebook_processing/`
  - `src/url_ingestion/url_processor.py`
  - `src/tweet_ingestion/tweet_processor.py`
- Background evaluation: `src/evaluation_service.py` and `src/routers/random.py`
- Database and migrations: `src/database.py`, `migrations/`
- Test fakes and fixtures: `src/test_utils.py`, repository fixtures, router fixtures
- Search and streaming behavior: `src/routers/search.py`, `src/routers/random.py`, `src/routers/urls.py`, `src/routers/tweets.py`

Do not retain a normal unscoped repository constructor as a transitional default. Use an explicit system/migration abstraction where unscoped access is genuinely required.

## Phase 1: Identity, sessions, and authorization dependencies

### Persistence

Add:

- `User`
- `Workspace`
- `WorkspaceMembership`
- session storage if using opaque sessions
- email-verification and password-reset token storage, or an explicitly documented equivalent

Use integer IDs. Enforce normalized unique email and `(user_id, workspace_id)` membership uniqueness.

Create the initial workspace and owner membership atomically in one transaction. Handle concurrent signup attempts through database constraints and transaction-safe retry behavior.

### Authentication API

Add routes for:

- signup
- email verification
- login
- logout
- session refresh/rotation if applicable
- password reset request
- password reset completion
- account status handling

Authentication routes must be enumeration-resistant where appropriate. Password changes and resets should revoke existing sessions according to the Phase 0 policy.

### Dependencies

Implement:

- `current_user`: validates the session/token, account status, and authentication state.
- `data_access_scope`: resolves the active membership and verifies workspace status and membership status.
- scope-aware repository dependencies that construct repositories with `(session, scope)`.

The scope must be immutable and must not use workspace IDs from request bodies, query parameters, or paths without membership verification.

### Cookie and CSRF behavior

Configure secure, HTTP-only cookies with explicit `Secure`, `SameSite`, `Path`, and domain behavior. Add CSRF enforcement to all state-changing cookie-authenticated requests, including authentication state changes where applicable.

Configure CORS with exact allowed origins and credentials support. Verify that native `EventSource` requests work in the intended same-origin or cross-origin deployment model.

## Phase 2: Schema migration and bootstrap data

Use Alembic against representative PostgreSQL data. Do not rely on `SQLModel.metadata.create_all`; current test fixtures do not validate production migrations, composite foreign keys, or pgvector behavior.

### Migration order

1. Add `users`, `workspaces`, and `workspace_memberships` tables.
2. Add session/token tables if selected by the authentication design.
3. Add nullable `workspace_id` columns to `book`, `note`, `url`, `urlchunk`, `tweetthread`, `tweet`, and `evaluation`.
4. Create the bootstrap workspace in a controlled pending/claimable state.
5. Backfill every existing owned row into the bootstrap workspace.
6. Verify there are no null or inconsistent parent scopes.
7. Update application writes and reads to use scoped repositories.
8. Remove global uniqueness constraints on books, notes, URLs, URL chunks, tweet threads, and tweets.
9. Add workspace-scoped uniqueness constraints.
10. Add unique `(id, workspace_id)` parent keys and composite parent/child foreign keys.
11. Add explicit `ON DELETE` behavior and workspace-leading indexes.
12. Make all `workspace_id` columns non-null.
13. Atomically allow only the approved first user to claim the bootstrap workspace.
14. Enable ordinary free signup only after the bootstrap claim succeeds.

The cutover must prevent writes from bypassing scope between backfill and deployment. Use maintenance mode, a coordinated deployment transaction, or another explicit write barrier. Document rollback behavior.

### Bootstrap concurrency

Serialize bootstrap claiming with a database lock or equivalent invariant. The transaction must verify:

- the account is the approved/eligible first user;
- the user is verified and active;
- the bootstrap workspace is still claimable;
- no owner claim has already committed.

A concurrent ordinary signup must never receive the bootstrap workspace or existing data.

## Phase 3: Scoped models and repositories

### Models

Add `workspace_id` to all owned persistence models. Keep ownership out of client request models. Child creation derives workspace scope from the scoped repository and verified parent.

Add composite constraints and foreign keys for:

- `note(book_id, workspace_id)` -> `book(id, workspace_id)`
- `urlchunk(url_id, workspace_id)` -> `url(id, workspace_id)`
- `tweet(thread_id, workspace_id)` -> `tweetthread(id, workspace_id)`
- `evaluation(note_id, workspace_id)` -> `note(id, workspace_id)`

Keep workspace IDs out of public response models unless there is an explicit product requirement.

### Repository contract

Change every owned-data repository constructor to require `DataAccessScope`:

```python
BookRepository(session, scope)
NoteRepository(session, scope)
```

Update protocols, implementations, services, processors, fixtures, and test fakes together.

Every method must enforce `scope.workspace_id`, including:

- `get`, `get_by_id`, `get_by_ids`, and list methods
- parent/child lookups
- `add` and deduplication lookups
- updates and destructive operations
- counts and grouped counts
- random selection
- related-content queries
- vector searches

Use affected-row checks for updates/deletes. Do not use `session.get()` for owned rows unless followed by a scope check; prefer scoped queries.

For concurrent deduplication, use PostgreSQL upserts or a nested transaction/savepoint around the insert. After a uniqueness conflict, roll back the failed statement scope before re-querying.

### Database RLS (if enabled)

Add RLS after the application scope contract is stable:

- Set the verified workspace ID for each transaction with `SET LOCAL app.workspace_id`. Never use a client-provided workspace ID.
- Add policies for every owned table. Policies must control reads, inserts, updates, and deletes with `USING` and `WITH CHECK`.
- Use an application database role that is not a superuser or table owner. Use separate access paths for migrations, bootstrap, and system operations.
- Ensure connection pooling cannot carry a workspace setting into another transaction. Missing or invalid workspace context must deny access.
- Benchmark RLS with pgvector searches before enabling it in production.

### Service and processor changes

Pass scoped repositories through:

- notebook/book upload
- URL ingestion and chunk creation
- tweet ingestion
- semantic search
- random selection
- streaming note, URL, and tweet context endpoints
- evaluations
- delete operations

A child operation must verify the parent through the same scope. Never rely only on client-provided parent IDs.

## Phase 4: Router, search, random, and SSE enforcement

Protect every data route:

- books
- notes
- URLs
- tweets
- search
- random selection
- evaluations
- streaming endpoints
- ingestion and deletion endpoints

Keep only health, authentication, and explicitly public endpoints open.

Ensure inaccessible IDs consistently produce a non-enumerating response, normally `404` after authentication. Avoid resource-specific logging that reveals existence.

### Search

Apply workspace predicates before vector ordering and `limit()`. Ensure parent fetches (`book`, `url`, and tweet thread) use the same scoped repositories.

Do not pass a request-bound SQLModel `Session` through `asyncio.to_thread`, as currently done in `src/routers/search.py`. Either make the database work synchronous in the route or move to an appropriate async session design.

Benchmark filtered vector search on PostgreSQL. Add workspace-leading indexes and evaluate pgvector filtering behavior separately from ordinary B-tree indexes.

### Random selection

Scope all counts and random queries for notes, URL chunks, and tweets. Verify that weighted selection and fallback behavior remain correct when a row is deleted or authorization changes between count and selection.

### SSE

Authenticate and authorize before opening each stream. Define behavior when a membership is revoked during a long-lived stream; either periodically revalidate or terminate the stream through an explicit mechanism.

Ensure SSE metadata, related content, errors, and generated prompts contain only active-workspace data.

## Phase 5: Background work

Replace detached request objects with serializable task inputs:

- workspace ID
- originating user/actor ID
- resource IDs
- task ID or idempotency key where needed

Workers must:

1. open their own session;
2. verify account and membership status;
3. construct scoped repositories;
4. re-fetch the resource through those repositories;
5. reject revoked or deleted work;
6. persist results with the same scope.

Update evaluation processing in `src/evaluation_service.py` so it no longer trusts a detached `NoteRead` from the request session. Background writes must use a workspace-scoped evaluation repository and verify that the note still belongs to that workspace.

## Phase 6: Tests

### Authentication tests

- missing and invalid authentication return `401`;
- expired, revoked, rotated, and disabled-account sessions are rejected;
- CSRF failures reject state-changing requests;
- cookie/CORS/SSE behavior works in the supported deployment model;
- verification and reset tokens are single-use and expire;
- login, signup, verification, and reset rate limits work;
- authentication responses do not enumerate accounts.

### Authorization and repository tests

Use at least two workspaces and prove that users cannot:

- read, modify, or delete another workspace's data;
- search, count, deduplicate, or randomly select another workspace's data;
- access a child through a parent in another workspace;
- create a child with a mismatched workspace;
- retrieve evaluations for another workspace's note.

Test repository enforcement directly through the public repository interfaces, not private methods.

### Database and concurrency tests

- composite foreign keys reject cross-workspace parent/child rows;
- scoped uniqueness permits identical content in separate workspaces;
- concurrent deduplication produces one row per workspace;
- workspace and initial membership creation are atomic;
- bootstrap claiming is serialized and idempotent;
- workspace deletion follows the selected cascade/retention policy;
- Alembic upgrade/backfill succeeds against representative PostgreSQL data;
- filtered vector search is scoped before ordering and limiting;
- when RLS is enabled, PostgreSQL policies block cross-workspace reads and writes;
- when RLS is enabled, missing workspace context fails closed and transaction context does not leak through connection pooling;
- when RLS is enabled, background tasks and vector searches work with the policies in place.

### Background and stream tests

- task scopes survive serialization;
- workers reject revoked memberships and disabled accounts;
- workers re-fetch resources through scoped repositories;
- SSE endpoints reject unauthenticated/inaccessible requests;
- streams do not continue unauthorized work after the selected revocation boundary.

Update all existing repository and router fakes so they require and enforce a scope. Do not leave unscoped test doubles that hide production authorization bugs.

## Incremental rollout

The changes should be deployable in stages. The phases above describe implementation order; they do not by themselves provide a safe production rollout. If a single maintenance-window cutover is not acceptable, use this sequence:

1. Add the identity tables and nullable `workspace_id` columns. Do not enforce `NOT NULL`, new uniqueness constraints, or RLS yet.
2. Deploy code that writes `workspace_id` for all new and changed rows. During this stage, any legacy single-workspace access must use an explicit, temporary bootstrap scope; do not restore a normal unscoped repository constructor.
3. Backfill existing rows into the bootstrap workspace and verify parent/child scope consistency.
4. Deploy the authentication and scope-aware repository code. Enable protected routes gradually with feature flags or an allowlist, while keeping the legacy mode available for rollback.
5. Enable authentication for the existing account, then verify workspace isolation, background work, search, random selection, and streams.
6. Enforce `NOT NULL`, scoped uniqueness, composite foreign keys, and other final constraints after the backfill and application checks pass.
7. Enable ordinary signup only after the final constraints and bootstrap ownership checks pass.
8. Enable RLS separately, if selected, after transaction context and database roles have been tested.
9. Remove the temporary bootstrap/legacy mode after all clients and workers use the scoped path.

Each stage needs monitoring and a rollback plan. After final constraints are enforced, use forward migrations rather than rolling back to unscoped access.

## Deployment and production acceptance

Before enabling ordinary signup:

1. apply and verify migrations;
2. confirm all existing rows have the bootstrap workspace;
3. deploy scoped repositories and protected routes;
4. verify the approved bootstrap user can claim the workspace;
5. verify unrelated users receive separate workspaces;
6. enable free signup;
7. if RLS is enabled, verify the application role cannot bypass policies and that workspace context is set for every transaction;
8. monitor authentication failures, authorization denials, migration errors, and background-task rejection.

The implementation is production-ready when:

- normal owned-data repositories cannot be constructed without a scope;
- no request model accepts ownership/workspace scope;
- every owned row has valid non-null workspace scope;
- all parent/child relationships enforce matching scope;
- all data routes are protected;
- bootstrap ownership is controlled and verified;
- scoped uniqueness, search, counts, random selection, and background work pass multi-workspace tests;
- production fails closed when authentication configuration is missing.
