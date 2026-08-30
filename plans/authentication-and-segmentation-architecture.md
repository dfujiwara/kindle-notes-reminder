# Authentication and Data Segmentation Architecture

## Purpose

Establish authentication now while creating a durable workspace ownership boundary for future multi-user and multi-tenant support.

This document defines the architectural boundaries and security invariants. Concrete implementation steps, migrations, and test tasks are in [`authentication-and-segmentation-implementation-plan.md`](./authentication-and-segmentation-implementation-plan.md).

## Goals

- Establish authenticated user identity.
- Ensure every owned-data operation independently enforces authorization.
- Prevent users from reading, modifying, deleting, searching, deduplicating, counting, or randomly selecting data outside the active workspace.
- Preserve a straightforward path to shared workspaces and multiple memberships later.
- Keep existing integer identifiers.

## Core ownership model

`Workspace` is the ownership boundary, even though the initial product has one user per workspace.

The identity model consists of:

- `User`: authenticated account.
- `Workspace`: ownership and isolation boundary.
- `WorkspaceMembership`: relationship between a user and workspace, including role and status.
- `DataAccessScope`: immutable request/task authorization context containing the authenticated user, workspace, membership, and role.

The application must not model ownership as only `users.workspace_id`. Memberships are the relationship that will support shared workspaces later.

### Scope invariants

- A scope is created only after authentication and membership verification.
- A scope is never constructed from request-provided workspace data.
- Repositories operating on owned data are scope-bound.
- Ownership is derived from the scope or a verified parent resource, never from client input.
- System, migration, and admin access use explicitly named separate operations rather than ordinary unscoped repositories.

## Authentication and authorization

Authentication establishes identity. Authorization independently verifies that the identity has an active membership in the workspace represented by the scope.

The initial client is browser-based and existing SSE endpoints use native `EventSource`. Therefore authentication should use a secure cookie-based mechanism rather than an authorization-header-only flow.

The exact session format remains a Phase 0 decision:

- Prefer an opaque server-side session for straightforward revocation, logout, account disablement, and future workspace selection.
- A short-lived JWT in a secure cookie is possible, but requires a deliberate refresh, revocation, and stale-claim strategy.

Cookie authentication requires CSRF protection for state-changing requests. Cookie attributes, CORS, cross-origin SSE behavior, expiry, refresh, revocation, logout, clock skew, and disabled-account behavior must be defined before implementation.

Identity is email/password based:

- Email addresses are normalized according to an explicit, documented rule.
- Normalized email is unique.
- Passwords are stored only as strong password hashes.
- Email verification, password reset, rate limiting, and account disablement are part of the authentication boundary.

### Request behavior

- Missing or invalid authentication: `401`.
- Authenticated but inaccessible resources: `404` or another deliberately non-enumerating response.
- Lists, counts, searches, random selection, and streams contain only active-workspace data.
- Logs and errors must not reveal whether inaccessible resources exist.
- All data routes are protected; only health, authentication, and explicitly public routes remain open.

## Workspace selection

Initially, a newly created user receives exactly one workspace and one owner membership. The server resolves the only active membership automatically.

A future workspace-switching mechanism must use trusted session state or a trusted token claim and verify membership on every request. Arbitrary workspace IDs from request paths, query parameters, or bodies are never trusted without membership validation.

Roles belong to memberships, not users. The implementation must define the permission matrix before additional roles are introduced.

## Data model

### Identity tables

- `users`: integer ID, normalized unique email, password hash, verification state, timestamps, account status, and required reset/deactivation metadata.
- `workspaces`: integer ID, metadata, timestamps, and explicit status/deletion fields.
- `workspace_memberships`: user ID, workspace ID, role, status, and timestamps; unique `(user_id, workspace_id)`.

Every workspace starts with an `owner`. Ownership rules must cover disabled users, revoked memberships, owner changes, last-owner protection, and workspace deletion.

### Owned content

The following tables carry a non-null `workspace_id` referencing `workspace.id`:

- `book`
- `note`
- `url`
- `urlchunk`
- `tweetthread`
- `tweet`
- `evaluation`

Child rows retain explicit workspace scope because repositories query child tables directly. Parent/child relationships enforce matching workspace IDs with composite parent keys and composite foreign keys:

- `note(book_id, workspace_id)` -> `book(id, workspace_id)`
- `urlchunk(url_id, workspace_id)` -> `url(id, workspace_id)`
- `tweet(thread_id, workspace_id)` -> `tweetthread(id, workspace_id)`
- `evaluation(note_id, workspace_id)` -> `note(id, workspace_id)`

Application code copies the parent scope when creating children. A child does not independently accept workspace ownership from a client.

### Scoped uniqueness

Ownership uniqueness is workspace-scoped:

- books: `(workspace_id, title, author)`
- notes: `(workspace_id, content_hash)`
- URLs: `(workspace_id, url)`
- URL chunks: `(workspace_id, content_hash)`
- tweet threads: `(workspace_id, root_tweet_id)`
- tweets: `(workspace_id, tweet_id)`

Clearly define how URLs, whitespace, hashes, titles, authors, and letter case are normalized before they are compared or stored. The database is the final authority for uniqueness, and application code must safely handle concurrent requests that try to insert the same record.

Workspace-scoped uniqueness trades away cross-workspace content sharing: today dedup keys (content hash, URL, tweet ID) are global, so identical content ingested by two workspaces will, after scoping, be stored and embedded independently in each. This repeats embedding cost per workspace. The implementation plan should treat this as an accepted cost for the initial design; cross-workspace content/embedding reuse can be revisited later without changing the ownership model.

## Repository and service boundary

Prefer constructing repositories with the scope:

```python
BookRepository(session, scope)
NoteRepository(session, scope)
```

Repository interfaces, implementations, services, processors, and test fakes use the same scoped abstraction.

Every repository query over owned data includes the scope. This includes:

- direct gets and lists
- parent and child lookups
- deletes and counts
- deduplication
- random selection
- vector similarity search before ordering and limiting
- related-content queries

Router checks alone are insufficient because repositories may be called by services, processors, workers, or tests.

When an object is passed between services, it must either carry its workspace information with it or be checked again by a repository tied to the current scope before it is used.

## Database defense in depth

The database must enforce the following:

- Every record belongs to a valid workspace and, when applicable, a valid parent record.
- Deletion behavior is explicitly defined.
- Parent and child records always belong to the same workspace.
- Values that must be unique are unique within each workspace.
- Indexes begin with the workspace ID to keep lists, lookups, counts, and deletions efficient.

Vector indexes require separate performance evaluation because an HNSW embedding index alone does not guarantee efficient workspace filtering.

PostgreSQL row-level security (RLS) is a possible second layer of protection. It can block accidental cross-workspace access from an unscoped query or another database client. It does not replace application-level authentication or authorization.

If RLS is enabled:

- After authorization, the application sets the workspace ID for the transaction with `SET LOCAL app.workspace_id`. It never takes this value from client input.
- Policies cover every owned table and control reads, inserts, updates, and deletes with `USING` and `WITH CHECK`.
- The application database role is not a superuser or table owner. Migrations, bootstrap, and system operations use separate access paths.
- Connection pooling must not leak a workspace setting between transactions. Missing or invalid workspace context must deny access.
- PostgreSQL integration tests cover cross-workspace access, background tasks, and vector searches. SQLite tests cannot test RLS.

Enable RLS after the application scope contract is stable, and benchmark its effect on pgvector searches.

## Background work and streams

Background jobs must receive trusted context identifying the workspace, user, membership, and resources they belong to. Workers create their own database session, use workspace-scoped repositories, and load resources again before saving results.

Reject queued jobs if the user or membership no longer has access. For long-running SSE streams, define how quickly access changes take effect: close the stream immediately, or check authorization periodically or when events occur.

## Assigning ownership of existing data

Put existing data in a special bootstrap workspace. Only the first approved user can become its owner through a controlled process.

Normal signup must not let an arbitrary user claim this existing private data. After the bootstrap workspace has an owner, each new user gets a separate workspace and becomes its owner.

The ownership claim must happen as one atomic operation so that concurrent signup attempts cannot claim the workspace at the same time.

## Architectural decisions required before implementation

1. Opaque server-side session or JWT cookie.
2. Session expiration, refresh, revocation, logout, and account-disable behavior.
3. Email normalization and verification rules.
4. Password hashing and reset-token strategy.
5. CSRF and cross-origin SSE policy.
6. Controlled first-user bootstrap mechanism.
7. Workspace status, deletion, retention, and membership-revocation semantics.
8. Role/permission matrix and last-owner behavior.
9. Future workspace-switching mechanism.
10. Database cascade strategy and vector-search filtering strategy.
11. Whether and when to enable PostgreSQL RLS, including transaction handling, database roles, system access, and PostgreSQL integration tests.

## Security requirements

The system is ready for production only when:

- Every normal repository for owned data requires an authorization scope.
- Clients cannot set ownership or workspace scope in requests.
- Every owned record has a valid workspace.
- Parent and child records cannot belong to different workspaces.
- All data routes require authentication and authorization.
- Users cannot tell whether resources they cannot access exist.
- Searches, random selections, counts, deduplication, streams, and background jobs are limited to the active workspace.
- The production system blocks access when authentication settings are missing.
