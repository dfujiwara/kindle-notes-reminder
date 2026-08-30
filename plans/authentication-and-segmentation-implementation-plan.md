# Authentication and Data Segmentation Implementation Plan

This plan implements [`authentication-and-segmentation-architecture.md`](./authentication-and-segmentation-architecture.md).

## How to use this plan

Implement the phases in order. Phase 0 decisions are prerequisites for application code. Each later phase includes the main work and the checks that should pass before moving on.

The ownership boundary is `Workspace`. Users access workspaces through `WorkspaceMembership`; ownership must not be represented only by a field on `User`.

## At a glance

| Phase | Focus | Outcome |
| --- | --- | --- |
| 0 | Decisions | Authentication, ownership, and database policies are explicit. |
| 1 | Identity and authorization | Users can authenticate and receive a verified `DataAccessScope`. |
| 2 | Migration and bootstrap | Existing data is assigned safely to the bootstrap workspace. |
| 3 | Scoped persistence | Models, repositories, and services enforce workspace scope. |
| 4 | Routes and streams | Every data route, search, random query, and SSE stream is protected. |
| 5 | Background work | Workers use serializable, revalidated authorization context. |
| 6 | Tests | Isolation, authentication, migrations, concurrency, and revocation are verified. |

## Glossary

- **Active workspace:** The workspace a signed-in user is currently authorized to use.
- **Bootstrap workspace:** A temporary, specially protected workspace that receives the application’s existing data. Only the approved first user may claim it.
- **CSRF (cross-site request forgery):** An attack that tricks a browser into sending an unwanted authenticated request. CSRF tokens and origin checks help prevent it.
- **`DataAccessScope`:** The verified authorization context for an operation: user, workspace, membership, and role. It is created by the server and cannot be changed by the request.
- **Enumeration-resistant:** Designed so a requester cannot learn whether a particular account or resource exists from response differences.
- **Idempotency key:** A unique task or request identifier that lets a retry avoid performing the same operation twice.
- **Owned data:** Application content that belongs to a workspace, including books, notes, URLs, tweets, and evaluations.
- **RLS (PostgreSQL row-level security):** A database feature that limits which rows a database role can read or change. It provides defense in depth; it does not replace application authorization.
- **Scope-aware repository:** A repository constructed with a `DataAccessScope`, so every query automatically limits data to the authorized workspace.
- **SSE (server-sent events):** The one-way streaming protocol used by the browser’s native `EventSource` client.
- **Workspace isolation:** The rule that one workspace cannot read, change, search, or infer another workspace’s data.
- **Write barrier:** A deployment or maintenance control that prevents writes while existing data is being backfilled or constraints are being changed.
- **Vector search:** Similarity search over embeddings, used to find semantically related content. `pgvector` is the PostgreSQL extension used for this search.
- **Upsert/savepoint:** Database techniques for safely handling concurrent inserts. An upsert inserts or updates on conflict; a savepoint lets one failed statement roll back without losing the whole transaction.

## Non-negotiable invariants

- Normal owned-data repositories require an immutable authorization scope.
- Clients never provide trusted ownership or workspace scope.
- Every owned row has a valid, non-null workspace and matching parent scope.
- Every data operation independently enforces authorization; router checks alone are insufficient.
- Inaccessible resources behave as nonexistent, normally with `404` after authentication.
- Searches, counts, deduplication, random selection, streams, and background work are workspace-scoped.
- System, migration, bootstrap, and admin access use explicit operations, never a normal unscoped repository constructor.

## Phase 0 — Lock implementation decisions

Resolve and document these decisions before writing application code.

### Authentication

- Prefer opaque server-side sessions over JWT cookies unless there is a strong reason otherwise.
- Define session storage, token hashing, absolute and idle expiration, rotation, revocation, logout, password-reset invalidation, and disabled-account behavior.
- Use a strong password hash such as Argon2id and document its parameters.
- Select and explicitly add the required password-hashing, signed-cookie/session (for example `itsdangerous`), and CSRF libraries with `uv add`.
- Define email normalization, case sensitivity, verification, password reset, and one-time-token storage. Store only hashed tokens with expiry and single-use semantics. Preserve the entered email separately if it is needed for display.
- Define rate limits for signup, login, verification, reset, and SSE/resource abuse. Use IP and account identifiers where appropriate, and store counters in a shared system such as the database or Redis—not process memory.

### Browser security

- Define CSRF tokens, `Origin`/`Referer` checks, CORS origins, credential support, cookie attributes (`Secure`, `HttpOnly`, `SameSite`, `Path`, and domain), and native `EventSource` behavior.
- Confirm whether deployment is same-origin or cross-origin and test that native SSE works in that model.

### Workspace and authorization policy

- Define the controlled first-user bootstrap gate, approved-user mechanism, and concurrent-claim locking.
- The bootstrap workspace is the only exception to normal signup: the first approved user claims it; every later signup receives a separate workspace and owner membership.
- Define workspace status, deletion/retention, membership revocation, account disablement, owner transfer, and last-owner protection.
- Define the initial permission matrix, even if the only role is `owner`.
- Define future workspace selection using trusted server-side session state or a trusted token claim. Membership must be checked on every request.

### Data and database policy

- Define normalization for URLs, whitespace, hashes, titles, authors, and other deduplication values.
- Define database cascades and workspace-leading indexes.
- Define vector-search filtering and benchmark it separately from ordinary B-tree indexes.
- Accept the initial cost of workspace-scoped deduplication: identical content is stored and embedded independently in each workspace. Cross-workspace content/embedding reuse can be revisited later.
- Decide whether and when to enable PostgreSQL RLS. If enabled, define transaction handling, database roles, system access, and PostgreSQL integration tests.

## Current code touchpoints

| Area | Paths |
| --- | --- |
| Models | `src/repositories/models.py` |
| Dependencies | `src/dependencies.py` |
| Routers | `src/routers/` |
| Repositories | `src/repositories/`, `src/url_ingestion/repositories/`, `src/tweet_ingestion/repositories/` |
| Ingestion | `src/notebook_processing/`, `src/url_ingestion/url_processor.py`, `src/tweet_ingestion/tweet_processor.py` |
| Background evaluation | `src/evaluation_service.py`, `src/routers/random.py` |
| Database and migrations | `src/database.py`, `migrations/` |
| Tests and fakes | `src/test_utils.py`, repository fixtures, router fixtures |
| Search and streams | `src/routers/search.py`, `src/routers/random.py`, `src/routers/urls.py`, `src/routers/tweets.py` |

## Phase 1 — Identity, sessions, and authorization dependencies

### Persistence

Add:

- `User`: integer ID, normalized unique email, password hash, verification state, timestamps, account status, and reset/deactivation metadata.
- `Workspace`: integer ID, metadata, timestamps, status, and deletion/retention fields.
- `WorkspaceMembership`: user ID, workspace ID, role, status, and timestamps.
- Session storage if opaque sessions are selected.
- Email-verification and password-reset token storage, or an explicitly documented equivalent.

Enforce normalized unique email and unique `(user_id, workspace_id)` membership pairs. Roles belong to memberships, not users.

A normal signup creates exactly one workspace and one active owner membership atomically. The bootstrap claim is the only exception. Use database constraints and transaction-safe retry behavior for concurrent signups.

### Authentication API

Add routes for:

- signup;
- email verification;
- login;
- logout;
- session refresh/rotation, if applicable;
- password-reset request and completion; and
- account-status handling.

Make authentication responses enumeration-resistant. Password changes and resets revoke existing sessions according to the Phase 0 policy.

### Authorization dependencies

Implement:

- `current_user`: validates the session/token, account status, and authentication state.
- `data_access_scope`: verifies workspace status and active membership.
- Immutable `DataAccessScope` containing the authenticated user, workspace, membership, and role.
- Repository dependencies that construct repositories with `(session, scope)`.

The scope is never built from workspace IDs in request bodies, query parameters, or paths. Future workspace selection must use trusted session state or a trusted token claim and re-check membership on every request.

### Cookies, CSRF, and CORS

Configure secure, HTTP-only cookies with explicit attributes. Enforce CSRF protection on every state-changing cookie-authenticated request, including applicable authentication-state changes. Configure exact CORS origins and credential support.

## Phase 2 — Schema migration and bootstrap data

Run Alembic against representative PostgreSQL data. Do not rely on `SQLModel.metadata.create_all`; current SQLite fixtures do not validate production migrations, composite foreign keys, or pgvector behavior.

### Migration order

1. Add `users`, `workspaces`, and `workspace_memberships`.
2. Add session/token tables if selected.
3. Add nullable `workspace_id` to `book`, `note`, `url`, `urlchunk`, `tweetthread`, `tweet`, and `evaluation`.
4. Create a special bootstrap workspace in a pending/claimable state.
5. Backfill every existing owned row into that workspace.
6. Verify that no rows have null or inconsistent parent scopes.
7. Update application writes and reads to use scoped repositories.
8. Remove global uniqueness constraints from books, notes, URLs, URL chunks, tweet threads, and tweets.
9. Add workspace-scoped uniqueness constraints.
10. Add unique `(id, workspace_id)` parent keys and composite parent/child foreign keys.
11. Add explicit `ON DELETE` behavior and workspace-leading indexes.
12. Make all `workspace_id` columns non-null.
13. Atomically allow only the approved first user to claim the bootstrap workspace.
14. Enable ordinary signup only after the bootstrap claim succeeds.

Use maintenance mode, a coordinated deployment transaction, or another explicit write barrier to prevent unscoped writes between backfill and deployment. Document rollback behavior.

### Bootstrap concurrency

Serialize bootstrap claims with a database lock or equivalent invariant. The claim transaction must verify that:

- the account is approved and eligible;
- the user is verified and active;
- the bootstrap workspace is still claimable; and
- no owner claim has already committed.

A concurrent ordinary signup must never receive the bootstrap workspace or its existing data.

## Phase 3 — Scoped models and repositories

### Models and constraints

Add non-null `workspace_id` to every owned persistence model. Keep ownership out of client request models. Keep workspace IDs out of public response models unless explicitly required.

Use these workspace-scoped uniqueness constraints:

- books: `(workspace_id, title, author)`;
- notes: `(workspace_id, content_hash)`;
- URLs: `(workspace_id, url)`;
- URL chunks: `(workspace_id, content_hash)`;
- tweet threads: `(workspace_id, root_tweet_id)`; and
- tweets: `(workspace_id, tweet_id)`.

Normalize values consistently before comparison and storage. The database is the final uniqueness authority. Handle concurrent insert conflicts with an upsert or a savepoint; roll back the failed statement scope before re-querying.

Add composite parent/child constraints:

- `note(book_id, workspace_id)` → `book(id, workspace_id)`;
- `urlchunk(url_id, workspace_id)` → `url(id, workspace_id)`;
- `tweet(thread_id, workspace_id)` → `tweetthread(id, workspace_id)`; and
- `evaluation(note_id, workspace_id)` → `note(id, workspace_id)`.

Child creation copies scope from the verified parent and never accepts ownership from the client.

### Repository contract

Every owned-data repository requires `DataAccessScope`, for example:

```python
BookRepository(session, scope)
NoteRepository(session, scope)
```

Update repository protocols, implementations, services, processors, fixtures, and fakes together. Every method must enforce `scope.workspace_id`, including:

- direct gets, ID lists, and list methods;
- parent/child lookups;
- additions and deduplication lookups;
- updates and destructive operations;
- counts and grouped counts;
- random selection;
- related-content queries; and
- vector similarity searches before ordering and limiting.

Use affected-row checks for updates and deletes. Avoid `session.get()` for owned rows unless it is followed by a scope check; prefer scoped queries.

### Services and processors

Pass scoped repositories through notebook/book upload, URL and tweet ingestion, semantic search, random selection, streaming context endpoints, evaluations, and deletes. A child operation verifies its parent through the same scope. Objects passed between services must carry workspace context or be re-fetched through a repository bound to the current scope.

### Optional PostgreSQL RLS

Add RLS only after the application scope contract is stable:

- After authorization, set `SET LOCAL app.workspace_id` for each transaction. Never use a client-provided value.
- Add `USING` and `WITH CHECK` policies for reads, inserts, updates, and deletes on every owned table.
- Use an application role that is neither a superuser nor table owner. Use separate access paths for migrations, bootstrap, and system operations.
- Ensure pooling cannot leak settings between transactions. Missing or invalid workspace context must deny access.
- Benchmark RLS with pgvector searches before production enablement.

## Phase 4 — Routes, search, random selection, and SSE

Protect every data route: books, notes, URLs, tweets, search, random selection, evaluations, streams, ingestion, and deletion. Only health, authentication, and explicitly public routes remain open.

Return a deliberately non-enumerating response—normally `404` after authentication—for inaccessible IDs. Avoid logging that reveals whether such resources exist.

### Search

Apply workspace predicates before vector ordering and `limit()`. Fetch books, URLs, and tweet threads through the same scoped repositories.

Do not pass a request-bound SQLModel `Session` through `asyncio.to_thread`, as the current search route does. Keep database work synchronous in the route or adopt an appropriate async-session design.

Benchmark filtered PostgreSQL vector search and evaluate pgvector filtering separately from ordinary B-tree indexes.

### Random selection

Scope all counts and random queries for notes, URL chunks, and tweets. Preserve correct weighted-selection and fallback behavior when a row is deleted or authorization changes between count and selection.

### SSE

Authenticate and authorize before opening every stream. Define the revocation boundary: either periodically revalidate membership or terminate the stream through an explicit mechanism. Stream metadata, related content, errors, and generated prompts must contain only active-workspace data.

## Phase 5 — Background work

Replace detached request objects with serializable task inputs:

- workspace ID;
- originating user/actor ID;
- originating membership ID, plus role/context where needed;
- resource IDs; and
- task ID or idempotency key where needed.

Workers must:

1. open their own database session;
2. verify account and membership status;
3. construct scoped repositories;
4. re-fetch resources through those repositories;
5. reject revoked or deleted work; and
6. persist results with the same scope.

Update `src/evaluation_service.py` so it does not trust a detached `NoteRead` from the request session. Background writes must use a workspace-scoped evaluation repository and verify that the note still belongs to the workspace.

## Phase 6 — Tests

### Authentication

- Missing and invalid authentication return `401`.
- Expired, revoked, rotated, and disabled-account sessions are rejected.
- CSRF failures reject state-changing requests.
- Cookie, CORS, and native SSE behavior works in the supported deployment model.
- Verification and reset tokens expire and are single-use.
- Signup, login, verification, and reset rate limits work.
- Authentication responses do not enumerate accounts.

### Authorization and repositories

Use at least two workspaces. Prove that users cannot read, modify, delete, search, count, deduplicate, or randomly select another workspace's data. Also test that users cannot:

- access a child through a parent in another workspace;
- create a child with mismatched workspace scope; or
- retrieve an evaluation for another workspace's note.

Test public repository interfaces directly. All repository and router fakes must require and enforce scope.

### Database and concurrency

- Composite foreign keys reject cross-workspace parent/child rows.
- Scoped uniqueness permits identical content in separate workspaces and creates independent embeddings.
- Concurrent deduplication produces one row per workspace.
- Workspace and initial membership creation are atomic.
- Bootstrap claiming is serialized and idempotent.
- Workspace deletion follows the selected cascade/retention policy.
- Alembic upgrade/backfill succeeds against representative PostgreSQL data.
- Filtered vector search is scoped before ordering and limiting.
- If RLS is enabled, policies block cross-workspace reads/writes, missing context fails closed, pooling does not leak context, and background/vector operations work.

### Background work and streams

- Task scopes survive serialization.
- Workers reject revoked memberships and disabled accounts.
- Workers re-fetch resources through scoped repositories.
- SSE rejects unauthenticated or inaccessible requests.
- Streams stop unauthorized work at the selected revocation boundary.

## Incremental rollout

If a single maintenance-window cutover is not acceptable, use this sequence:

1. Add identity tables and nullable `workspace_id` columns. Do not yet enforce `NOT NULL`, final uniqueness constraints, or RLS.
2. Deploy code that writes `workspace_id` for all new and changed rows. Any temporary legacy access must use an explicit bootstrap scope; never restore a normal unscoped constructor.
3. Backfill existing rows and verify parent/child scope consistency.
4. Deploy authentication and scoped repositories. Protect routes gradually with feature flags or an allowlist, while retaining a documented rollback mode.
5. Enable authentication for the existing account. Verify isolation, background work, search, random selection, and streams.
6. Enforce `NOT NULL`, scoped uniqueness, composite foreign keys, and other final constraints.
7. Enable ordinary signup only after final constraints and bootstrap ownership checks pass.
8. Enable RLS separately, if selected, after transaction context and database roles are tested.
9. Remove temporary bootstrap/legacy mode after all clients and workers use scoped paths.

Every stage needs monitoring and a rollback plan. After final constraints are enforced, use forward migrations rather than returning to unscoped access.

## Production acceptance gate

Before enabling ordinary signup:

1. Apply and verify migrations.
2. Confirm every existing row has the bootstrap workspace.
3. Deploy scoped repositories and protected routes.
4. Verify that the approved bootstrap user can claim the workspace.
5. Verify that unrelated users receive separate workspaces.
6. Enable free signup.
7. If RLS is enabled, verify that the application role cannot bypass policies and that every transaction sets workspace context.
8. Monitor authentication failures, authorization denials, migration errors, and background-task rejection.

The implementation is production-ready only when:

- normal owned-data repositories cannot be constructed without scope;
- no request model accepts ownership/workspace scope;
- every owned row has valid non-null scope;
- parent/child relationships enforce matching scope;
- all data routes are protected;
- bootstrap ownership is controlled and verified;
- scoped uniqueness, search, counts, random selection, and background work pass multi-workspace tests; and
- production fails closed when authentication configuration is missing.
