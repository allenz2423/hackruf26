# Shared Classes, Resource Publication, and Authorization Design

**Status: implemented for membership, invitations, capabilities, resource ownership/publication, and private assessment attempts.** This document records the schema and security rationale; implementation details live in `backend/app/services/class_access.py`, `backend/app/api/class_memberships.py`, `backend/app/api/class_library.py`, and the frontend class/library modules. Forking, leaderboards, and organization hierarchies remain deferred. Fork sections below are future design notes only and do not describe available endpoints or behavior.

## Goals and boundaries

The design extends the existing SQLite-backed class library without replacing its authentication, upload processing, study-material generation, quiz grading, or same-origin API. Existing classes remain usable by their current owner throughout migration. Class membership does not share personal quiz runs, attempts, reasoning traces, tutor memory, or tutoring conversations. **Leaderboards are deferred entirely.**

The migration retains `course_classes.owner_id` as the stable class administrator while adding a unique active membership per user/class. Authorization also covers uploads, assessments, study materials, attempts, tutor retrieval, and file serving. The current schema and service code are authoritative where older examples later in this document use a more granular future model.

## Resource ownership model

Every resource has a clear owner/scope independent of who can read it:

| Scope | Meaning | Default visibility | Who can change it |
| --- | --- | --- | --- |
| `class_owned` | Content owned by the class workspace, normally created by its owner or a manager | Active class members, but only while published | Class owner for edits/deletes; owner or permission manager for release settings |
| `personal` | A student's private quiz, practice exam, or other personal study material; it may have been generated from class sources | Its `owner_id` only | Its owner |
| `student_contribution` | A member's class contribution; authorship remains with that member | Not visible to ordinary members until published | Author for their own draft; a manager can moderate it within granted capabilities |

Class-owned resources and student contributions are stored with a class ID. Personal study materials have a user owner and no class ownership; `origin_class_id` is provenance only and grants no access. A personal quiz generated from a class is a private snapshot. It remains the student's resource if their membership ends or the source class is deleted; source links can become unavailable without deleting the generated content.

New student contributions start as drafts. A manager-created class-owned upload can retain the current single-owner behavior and publish immediately. A generator defaults to a personal destination, including for the class owner, so creating a quiz does not silently publish it; the author must explicitly request a class contribution and hold the relevant capability. Managers may publish a contribution only within the authority delegated to them. Class membership alone never implies resource authorship.

## Implemented storage model

The SQLite migration uses the `schema_migrations` ledger and preserves existing IDs and `course_classes.owner_id`. The class row does not currently carry fork or fork-provenance fields; class forking is deferred.

### Classes, memberships, and capabilities

`course_classes` retains its existing identity and owner columns. Membership rows use:

```text
class_memberships(
  class_id TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('active', 'left', 'removed')),
  joined_at TEXT NOT NULL,
  added_by TEXT NULL REFERENCES users(id) ON DELETE SET NULL,
  ended_at TEXT NULL,
  PRIMARY KEY (class_id, user_id)
)

class_membership_capabilities(
  class_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  capability TEXT NOT NULL CHECK (capability IN (
    'read', 'create', 'edit', 'delete', 'delete_others', 'invite', 'manage_members',
    'manage_permissions'
  )),
  granted_by TEXT NULL REFERENCES users(id) ON DELETE SET NULL,
  granted_at TEXT NOT NULL,
  PRIMARY KEY (class_id, user_id, capability),
  FOREIGN KEY (class_id, user_id)
    REFERENCES class_memberships(class_id, user_id) ON DELETE CASCADE
)
```

Capabilities are independent. `create` does not imply edit or delete. The backend resolves them centrally; the owner is always treated as a full administrator. A non-owner with `edit` or `delete` can change only their own contribution. A separate `delete_others` grant permits deletion of another member's published `student_contribution`; it does not permit editing that contribution, deleting class-owned resources, or accessing unreleased drafts. The class owner can administer all class resources. `manage_permissions` alone does not grant cross-author deletion or access to a student's unreleased assessment key.

An active member with only `read` can view published class resources and generate personal quizzes/exams from those resources. Personal generation checks `read` and source publication for each selected resource; it does not check class `create`. An author with `create` can publish their own contribution; the owner or a `manage_permissions` manager can publish class resources.

### Invitations

```text
class_invitations(
  id TEXT PRIMARY KEY,
  class_id TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
  code_digest TEXT NOT NULL UNIQUE,
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  max_uses INTEGER NOT NULL CHECK (max_uses BETWEEN 1 AND 100),
  successful_uses INTEGER NOT NULL DEFAULT 0,
  revoked_at TEXT NULL,
  CHECK (successful_uses BETWEEN 0 AND max_uses)
)

class_invitation_capabilities(
  invitation_id TEXT NOT NULL REFERENCES class_invitations(id) ON DELETE CASCADE,
  capability TEXT NOT NULL CHECK (capability IN (
    'read', 'create', 'edit', 'delete', 'delete_others', 'invite', 'manage_members',
    'manage_permissions'
  )),
  PRIMARY KEY (invitation_id, capability)
)
```

Generate a high-entropy code with a cryptographic RNG and persist only its SHA-256 digest. Return the clear code once. Default grants are `read`; adding stronger grants requires the inviter to hold each capability. Expiration, revocation, and use count are checked server-side. Redemption uses a short transaction with a conditional atomic use increment and unique membership constraint. Duplicate membership does not consume another use. Rate-limit attempts by account and IP, never log codes, and make expiry/revocation independent of current memberships.

### Resource ownership and publication

The migration adds these ownership and release fields to `course_notes`:

```text
resource_scope TEXT NOT NULL DEFAULT 'class_owned'
  CHECK (resource_scope IN ('class_owned', 'student_contribution')),
created_by TEXT NULL REFERENCES users(id) ON DELETE SET NULL,
publication_state TEXT NOT NULL DEFAULT 'published'
  CHECK (publication_state IN ('draft', 'scheduled', 'published')),
release_at TEXT NULL,
published_at TEXT NULL
```

`uploaded_assessments` inherits resource visibility from its source note. A row with student responses or a grading result is treated as private to its recorded owner and cannot be released as a shared answer key. If a legacy row cannot be reliably attributed, its owner remains null and the row is not exposed to other members. The current implementation does not add a separate imported-assessment submissions table; independent quiz submissions use `quiz_runs` and owner-bound attempt rows.

`uploaded_assessments` stores its uploader/owner and has independent answer-key publication state and release time. A row with student responses or a grade is treated as private to its recorded owner; legacy rows with missing or ambiguous attribution are not exposed to other members. Generated shared quizzes/exams use immutable `study_materials` content and per-user `quiz_runs`/attempt rows.

Rebuild `study_materials` to support personal output without binding its lifetime to a source class:

```text
class_id TEXT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
resource_scope TEXT NOT NULL CHECK (
  resource_scope IN ('class_owned', 'personal', 'student_contribution')
),
owner_id TEXT NULL REFERENCES users(id) ON DELETE CASCADE,
created_by TEXT NULL REFERENCES users(id) ON DELETE SET NULL,
origin_class_id TEXT NULL, -- provenance only; deliberately no FK/cascade
publication_state TEXT NOT NULL DEFAULT 'published'
  CHECK (publication_state IN ('draft', 'scheduled', 'published')),
release_at TEXT NULL,
published_at TEXT NULL,
context_truncated INTEGER NOT NULL DEFAULT 0
```

Enforce these row invariants in the migration and service layer: `personal` requires `owner_id`, has `class_id IS NULL`, and is visible only to its owner; `class_owned` and `student_contribution` require `class_id`; only class-scoped rows use publication state, and personal rows are never publishable. Class-owned rows are managed by class capabilities, while contributions retain `created_by` and ownership-sensitive edit/delete permissions. `origin_class_id` never authorizes a read. Publishing a personal study set creates a separate class contribution with a new ID; it does not change the personal row's scope. Keep the `generation_instructions` column added by the first implementation private and out of public response models.

For class-scoped rows, `scheduled` requires a non-null UTC `release_at`; draft and published rows have no pending release time. Schedule changes and unpublishing require the owner/permission-manager path or the author's own-content permission. The application currently does not persist a publication audit log. A due scheduled row is visible at `release_at` even if lazy promotion has not run. Personal resources are private by ownership regardless of their source class's publication or membership state.

`study_material_sources` must be rebuilt so a personal material can retain non-authorizing source provenance without a cascading class foreign key. Store `material_id`, nullable `source_note_id` (`ON DELETE SET NULL`), source class ID as provenance, source title/kind snapshot, and position. The generated content remains a private snapshot if its source is later removed. All generation at request time still rechecks access to the live source.

Once ready, generated study-material content is immutable. No content-edit API mutates an assessment snapshot. Shared quizzes/practice exams with recorded runs reject individual deletion, preserving attempt history. `quiz_runs.material_id` identifies the immutable quiz version used by the student's attempt.

Add `owner_id TEXT NULL REFERENCES users(id) ON DELETE CASCADE` to legacy `study_material_attempts` only for new compatibility writes; do **not** backfill old rows. All newly started Standard and Reasoning Mode attempts use owner-bound `quiz_runs`. The old no-run Standard submission route remains available only for the existing single-owner path and is rejected for non-owner members of a shared class. Legacy rows with null `owner_id` stay unassigned and are never shown to other members; a dedicated owner-only compatibility view may retain the old sole-owner display without asserting ownership of each row. No leaderboard or cross-member query includes them.

The existing `quiz_question_attempts`, `quiz_reasoning_turns`, `quiz_reasoning_evaluations`, and `quiz_feedback_operations` remain run-scoped. Each request verifies both current class/material access and that the quiz run belongs to the authenticated user. Tutor memory remains keyed by `(owner_id, class_id)`, and conversation transcripts remain ephemeral.

### Publication rules

`draft` and not-yet-released `scheduled` resources are available to their author and class managers for management. They are absent from ordinary member lists, details, counts, files, AI retrieval, and quiz/exam generation. A scheduled resource becomes visible only when its UTC `release_at` has passed. `class_access.py` centralizes the effective-release check; due schedules are readable without a background promotion job.

Every read path uses the same rule: authenticated active membership with `read`, correct resource scope, and effective publication. Search (if added) filters authorization and publication before ranking or returning snippets. Tutor context and generation build source context only from explicitly selected, authorized, published resources. A hidden resource must not leak through filenames, source IDs, counts, citations, error details, or source-warning fields. Inaccessible IDs return the normal `404 not_found` response.

For migration compatibility, existing class resources remain `published` so the original owner retains their existing behavior; existing classes remain private to that owner until invitations are redeemed. New student contributions begin in `draft`; ordinary class browse and AI paths cannot use them until release. Redistribution authorization and forking are not implemented in this milestone.

### Deferred forking design

Forking is disabled on every class by default. Only a user with `manage_forking` can enable or disable it. Initiation requires active `read` and `fork`, an enabled source class, and at least one eligible resource. A resource is eligible only when it is published, its creator/uploader explicitly authorized redistribution, and no student response, grade, personal attempt, reasoning, or tutor data is embedded. Class-level enablement cannot override a resource restriction.

The first implementation copies file bytes to fresh UUID storage keys. It does not use content-addressed storage or reference counting. Each fork receives new class/resource/material IDs, independent permissions and invitation state, and only an informational `forked_from_class_id`. The parent ID has no foreign key, and the fork contains its own file bytes, so parent deletion or later fork disablement cannot alter an existing fork.

Use these recovery records:

```text
class_fork_operations(
  id TEXT PRIMARY KEY,
  requested_by TEXT NOT NULL REFERENCES users(id),
  source_class_id TEXT NOT NULL,
  target_class_id TEXT NOT NULL UNIQUE,
  idempotency_key TEXT NOT NULL,
  source_authorization_version INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('copying', 'completed', 'failed')),
  error_code TEXT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT NULL,
  UNIQUE (requested_by, source_class_id, idempotency_key)
)

class_fork_operation_items(
  operation_id TEXT NOT NULL REFERENCES class_fork_operations(id) ON DELETE CASCADE,
  source_resource_id TEXT NOT NULL,
  target_resource_id TEXT NOT NULL,
  source_storage_key TEXT NULL,
  staging_storage_key TEXT NULL,
  content_digest TEXT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'copied', 'committed', 'failed')),
  PRIMARY KEY (operation_id, source_resource_id)
)
```

Under a short SQLite transaction, reserve the operation and target class, snapshot eligible rows into `class_fork_operation_items`, and capture `authorization_version`. Copy file bytes outside the transaction to operation-scoped staging keys. In a second short transaction, verify the source class still exists, forking remains enabled, the requester still has `read` and `fork`, the class authorization version is unchanged, and each source remains published and redistribution-authorized. Then insert copied rows with remapped IDs and keys, create the fork owner's membership/capabilities, and mark the fork and operation complete. Promote staging files to independent final keys atomically where storage supports it. Only completed forks appear in class listings. On failure, remove staging files and the pending target; on process restart, recovery verifies each digest and either completes a still-authorized operation or deletes its partial target/files. Increment `authorization_version` when membership/capabilities, publication/redistribution permission, or fork enablement changes so an in-flight copy cannot commit against stale authorization. Never hold a database transaction open during file copying.

Disabling `fork_enabled` only blocks future operations. It does not update, delete, or reauthorize existing forks. Parent deletion does not cascade through provenance or storage. Forking copies published class-owned resources and individually authorized contributions; it never copies personal study materials, student submissions, `quiz_runs`, `study_material_attempts`, reasoning traces, tutor recaps, or leaderboard data.

## Authorization boundaries and API changes

Replace scattered `owner_id` predicates with one backend authorization service that resolves the class, active membership, capability set, resource scope, and publication state. Apply it to class listing/detail/patch/delete; upload create/list/detail/edit/delete/retry/original-file/document downloads; imported assessments; study material create/list/detail/delete/download/flags; quiz start/history/submission/review; study tutor; and class-scoped Live session ticket creation and WebSocket connection. Frontend visibility is convenience only.

| Operation | Required backend rule |
| --- | --- |
| Read class/published resource | Owner invariant or active membership + `read` + effective `published` state |
| Upload/create class resource | `create`; new student contribution defaults to draft |
| Generate personal quiz/exam | `read` each selected published source; no class `create`; destination is private and owner-bound |
| Generate class contribution | `read` selected sources + `create`; starts as draft unless an authorized publication action is separately requested |
| Edit/delete own contribution | `edit` / `delete` and matching `created_by` |
| Delete another member's published contribution | `delete_others`; author must be known; unreleased, private, and class-owned resources remain protected |
| Edit another user's or delete a class-owned resource | Owner-only; `create`, `manage_permissions`, and `delete_others` do not imply these rights |
| Publish/schedule/unpublish | Author with `create`, or class owner/`manage_permissions`; answer keys have their own release state |
| Create/revoke invitations | `invite`; capability grants must be within the caller's delegable set |
| Remove member/change capabilities | `manage_members` / `manage_permissions`; cannot remove or demote the stable owner |
| Start/view quiz run | Published immutable material + class read (or personal owner) and run `owner_id` equals caller |
| View legacy unowned attempt | Original owner-only compatibility view; never another member; no synthetic `owner_id` |
| Start class tutor/AI retrieval | Active read access, plus only published uploads explicitly selected or allowed by the class policy |
| Download original file | Recheck membership and effective publication at content fetch; do not issue a long-lived URL that bypasses a later release/permission check |

`GET /api/classes` returns the caller's own classes and active memberships with effective capabilities. Add operations equivalent to:

```text
GET    /api/classes/{class_id}/members
PATCH  /api/classes/{class_id}/members/{user_id}/capabilities
DELETE /api/classes/{class_id}/members/{user_id}
POST   /api/classes/{class_id}/invitations
GET    /api/classes/{class_id}/invitations
DELETE /api/classes/{class_id}/invitations/{invitation_id}
POST   /api/class-invitations/redeem
PATCH  /api/resources/{resource_type}/{resource_id}/publication -- authorized schedule/publish transition
PATCH  /api/assessments/{assessment_id}/answer-key-release
GET    /api/me/study-materials                 -- caller's personal study resources
```

Extend existing upload/material endpoints rather than creating a parallel quiz system. Upload mutations require `create`; ordinary member browse returns published rows (authors and managers may see drafts). `POST /api/classes/{class_id}/study-materials` accepts a destination (`personal` by default or `class_contribution`) and optional additional instructions. Personal generation is authorized by source `read`, not class `create`. A class contribution requires `create` and begins as a draft. Public responses never include private generation instructions or another user's attempts. Standard and Reasoning Mode submissions use owner-bound quiz runs; the legacy no-run submission path remains limited to the class owner. Reasoning Mode keeps its backend gate and immutable reasoning snapshots.

Origin checks, cookie/session authorization, request validation, rate limits, and same-origin API/WebSocket contracts remain in force. Signed upload URL issuance and file serving recheck current publication/access. Membership removal invalidates pending class-context tickets and closes active class tutor sessions for that member; it does not delete the member's personal study materials or personal quiz history.

## Migration and compatibility behavior

The versioned SQLite migrations preserve class, resource, and attempt IDs. They backfill an active membership with full capabilities for each resolvable class owner, rebuild legacy material/source tables transactionally where necessary, and preserve private generation instructions. Migration 3 rebuilds the two capability tables to add `delete_others` while preserving existing grants, then records the owner's full administrative capability. It adds owner attribution for new legacy attempt writes but deliberately leaves old attempt owners null. Existing notes/materials remain published for their original owners; contributions created by non-managers begin as drafts. An older migration marker does not skip the shared-class backfill. Migrations are idempotent and check foreign keys after table rebuilds.

## Verification coverage

- Existing owner-only class, upload, quiz, guide export, tutor, and delete flows continue to pass before and after migration.
- Migration preserves IDs and content; known owners receive full capabilities; unresolved legacy owners are not assigned to arbitrary users.
- Read-only members can list/open published resources and generate private quizzes/exams from selected published sources without `create`; class contribution creation is rejected without `create`.
- Personal outputs are visible only to their owner and survive membership removal/source-class deletion; class members cannot enumerate their IDs or generation instructions.
- Draft and future-scheduled resources are absent from member APIs, AI context, generation sources, source citations, and file downloads. Due schedules become visible at the UTC release time.
- A published contribution remains author-owned; `create` does not imply delete; `delete` stays author-scoped, while `delete_others` can remove only a published contribution with known authorship and cannot remove class-owned content, unreleased drafts, private assessments, or assessments with attempt history.
- A class member cannot inspect another member's `quiz_runs`, `study_material_attempts`, question attempts, reasoning turns, feedback, uploaded assessment responses, or tutor memory.
- Legacy attempts with null attribution remain null, remain private, and are never included in another member's result or any metric. Tests include deliberately ambiguous legacy rows.
- Invite expiry and revocation work; maximum successful use is exact under concurrent redemption; duplicate membership does not consume a use; capabilities cannot be escalated through invitation creation or direct API calls.
- Shared assessment attempts are isolated per user; non-owner no-run compatibility submissions are rejected, and shared assessments with runs cannot be deleted.
- Existing quiz grading and Reasoning Mode gates work for immutable shared assessment snapshots.
- Live-session tickets and file requests require current class access and published context; revocation prevents later class-context use.
- Leaderboard routes, schema, metrics, and UI are absent from this phase.

## Current implementation boundary

Implemented routes and UI support class membership, invitation lifecycle, capability assignment (including explicit moderation of other members' published contributions), shared resources, personal generation, independent quiz runs/attempts, publication schedules, answer-key release, and access revocation. The migration backfills class owners as administrative members but intentionally leaves legacy attempt attribution null where it cannot be proven. Files are served only after a fresh access and release check. Forking, leaderboards, competitive progress sharing, and institutional hierarchy are not implemented. Future fork requirements in this design are non-operative until separately authorized and implemented.
