# Jot — Shared Classes, Permissions, Forking, and Quiz Generation Context

You are working on **Jot**, an existing AI-powered educational application.

Your task is to implement collaborative class functionality, granular resource permissions, independent class forking, and optional quiz-generation instructions.

**This is an implementation task, not a proposal.** Inspect the repository, understand the existing architecture, implement the features, add appropriate tests, and verify that existing functionality remains intact.

Do not rewrite working systems unnecessarily. Reuse existing authentication, class management, quiz generation, file storage, and database infrastructure wherever appropriate.

## 1. Inspect the existing application

Before modifying code:

- Identify the current authentication and authorization mechanisms.
- Inspect how classes, users, resources, quizzes, exams, and submissions are represented.
- Identify the existing quiz/exam generation pipeline and prompt construction.
- Inspect file ownership, storage, and deletion behavior.
- Identify existing API patterns, frontend components, database migrations, and tests.

Adapt the implementation to the existing architecture. Do not assume specific frameworks, schemas, or endpoints without verifying them.

Preserve backward compatibility and existing user data.

## 2. Shared classes

Allow multiple authenticated users to participate in the same class workspace.

Class members should be able to access shared course resources, quizzes, and exams according to their permissions.

Each user must retain independent:

- Quiz and exam attempts
- Scores and performance history
- Reasoning submissions
- Tutor conversations
- Learning preferences
- Personal progress

Joining a class must not expose another student's private educational data.

Existing personal classes should continue working after the migration.

### Invitations

Implement a code-based invitation system.

Invitation codes must support **both**:

1. Time-based expiration
2. Maximum successful uses

A code becomes invalid when either condition is reached.

Requirements:

- Generate codes using cryptographically secure randomness.
- Associate codes with a stable internal class identifier.
- Allow authorized users to create and revoke codes.
- Validate expiration, usage count, class status, and authorization server-side.
- Prevent duplicate memberships.
- Rate-limit guessing and redemption attempts.
- Ensure concurrent redemptions cannot exceed the usage limit.
- Expiration or revocation must never remove existing class members.
- Support generating new codes without disrupting existing membership.

Invitation codes are temporary credentials, not permanent class identifiers.

## 3. Capability-based permissions

Implement granular, server-enforced capabilities instead of relying exclusively on fixed roles.

At minimum, support:

- `read`: View shared resources and take quizzes/exams.
- `create`: Upload resources and create quizzes/exams.
- `edit`: Modify shared resources where authorized.
- `delete`: Delete shared resources where authorized.
- `invite`: Generate and revoke invitation codes.
- `fork`: Create independent copies of forkable classes.
- `manage_members`: Remove members and manage memberships.
- `manage_permissions`: Configure class-level permissions.

Creating and deleting must be independent capabilities.

Examples of valid combinations:

- Read only
- Read + create
- Read + delete
- Read + create + delete

Do not assume someone who can create content can also delete other users' content.

Distinguish deleting one's own resources from deleting resources belonging to other users.

The class creator should initially receive administrative capabilities. Other members should receive conservative default permissions.

Implement permission presets in the frontend where useful, while retaining granular capabilities internally.

Every protected operation must enforce authorization on the backend. Hiding frontend controls is insufficient.

Ensure users cannot escalate their own permissions, grant capabilities they are not authorized to grant, or bypass class restrictions through direct API requests.

Keep class deletion and other administrative operations separate from ordinary resource deletion.

## 4. Independent class forking

Implement GitHub-inspired class forking.

A fork creates a new, independent class workspace based on permitted content from an existing class.

### Forking policy

- Forking is **disabled by default**.
- The class creator or appropriately authorized manager can enable or disable forking.
- Only authorized members may initiate a fork.
- Uploaded resources must also be eligible for redistribution by the uploader.
- A class-level forking setting must not override individual resource restrictions.

### Independence guarantees

Once a fork has been successfully created:

- It has its own permanent class ID.
- It has independent membership.
- It has independent invitation codes.
- It has independent permissions and administrative control.
- It has independent quizzes, exams, and shared-resource records.
- It has independent progress and leaderboard data.
- The fork creator controls the new workspace according to its permission model.

**The original class owner must have no administrative authority over an independent fork merely because they created its parent.**

### Non-retroactive revocation

Disabling forking prevents future forks.

It must NOT:

- Delete existing forks.
- Revoke access to existing forks.
- Remove their resources.
- Change their permissions.
- Modify their membership.

Deleting the original class must not cascade into existing forks.

Preserve provenance through an optional reference such as `forked_from_class_id`, but never make access to the fork depend on the continued existence or authorization state of its parent.

### Fork implementation

Use snapshot-based forking rather than live synchronization.

Subsequent modifications to the original class must not automatically propagate to its forks.

Files may use content-addressed storage or reference counting for deduplication, but deleting the original class or resource must not remove file bytes still required by a fork.

Do not copy private tutor conversations, quiz attempts, scores, reasoning submissions, or other student-specific records.

Ensure fork creation is atomic or safely recoverable from partial failures.

## 5. Shared quizzes and exams

Allow authorized class members to create quizzes and exams accessible to other members.

Shared assessments must maintain independent attempts for each student.

Requirements:

- Students cannot modify another student's submissions.
- Quiz and exam attempts must remain associated with the correct user and assessment.
- Sharing an assessment must not automatically share individual results.
- Deleting or editing a shared assessment must not silently corrupt historical attempts.
- Existing reasoning-first functionality must remain intact.

Consider immutable assessment versions or equivalent safeguards if the existing architecture requires them.

## 6. Optional progress sharing and competition

Allow students to opt into sharing selected performance metrics with classmates.

Progress sharing must be disabled by default.

Potential metrics include:

- Quiz performance
- Practice points
- Weekly activity
- Improvement over time

Implement a class leaderboard using appropriately comparable metrics.

Do not expose private reasoning submissions, tutor conversations, or detailed personal learning history through the leaderboard.

Students must be able to disable progress sharing later.

Class forks must maintain independent leaderboards.

Avoid rewarding raw time spent or sheer question volume without considering meaningful learning activity.

Keep the initial implementation minimal rather than building a large gamification system.

## 7. Optional quiz/exam generation context

Add an optional free-text instruction field to the existing quiz and exam generation interfaces.

Suggested label:

**Additional instructions (optional)**

Suggested placeholder:

"Focus on chapters 3–5. My professor emphasizes application questions rather than definitions. Include experimental scenarios and avoid questions about material we haven't covered."

Students should be able to specify:

- Topics to emphasize
- Topics to exclude
- Desired difficulty
- Question style
- Professor-specific expectations
- Exam preparation goals
- Other relevant generation preferences

### Generation behavior

Pass the optional instructions into the existing generation pipeline as a clearly separated, lower-trust user-input field.

Preserve existing system-level requirements for correctness, safety, source grounding, and valid assessment structure.

Do not allow user instructions to override system constraints, authorization rules, or required output schemas.

When selected course materials are the designated source of truth:

- Ground generated questions in those materials.
- Do not invent source coverage.
- Handle requests for unsupported topics explicitly.
- Do not silently introduce outside knowledge as though it came from uploaded materials.

If the field is empty, preserve existing generation behavior.

Ensure the additional instructions are included in generation requests, validated, and stored as appropriate for reproducibility.

For shared assessments, avoid unintentionally exposing private information from the creator's generation instructions.

Do not replace the existing quiz generator with a new implementation unless there is a compelling architectural reason.

## 8. Frontend requirements

Integrate these capabilities into the existing interface and design language.

Provide appropriate UI for:

- Creating and joining shared classes
- Entering invitation codes
- Configuring expiration and usage limits
- Viewing class members
- Managing member permissions
- Enabling or disabling class forking
- Forking an eligible class
- Viewing shared resources and assessments
- Opting into or out of leaderboard participation
- Supplying optional quiz/exam generation instructions

Show only actions the current user is authorized to perform, while enforcing all permissions independently on the backend.

Keep ordinary studying simple. Do not overwhelm students with administrative controls.

## 9. Security and data integrity

Pay particular attention to:

- Cross-class authorization
- IDOR vulnerabilities
- Permission escalation
- Invitation-code enumeration
- Concurrent invitation redemption
- Ownership of uploaded resources
- File lifecycle and reference counting
- Fork independence
- Database migration compatibility
- Accidental deletion of shared content
- Privacy of student attempts and progress

Treat user-provided quiz-generation instructions and uploaded content as untrusted input.

## 10. Tests and verification

Add automated tests covering at least:

1. Invitation expiration and maximum-use enforcement.
2. Concurrent invitation redemption.
3. Permission combinations, including create without delete and delete without create.
4. Attempts to modify or delete another user's protected content.
5. Unauthorized class access and privilege escalation.
6. Forking disabled by default.
7. Successful forking with eligible resources.
8. Existing forks surviving parent deletion.
9. Existing forks surviving subsequent forking disablement.
10. Private student data remaining excluded from forks.
11. Shared quizzes maintaining separate student attempts.
12. Opt-in and opt-out leaderboard behavior.
13. Quiz generation with and without additional instructions.
14. Existing class and assessment functionality remaining operational.

Run the relevant tests, linters, and build checks supported by the repository.

## 11. Implementation priorities

Prioritize correctness and a working end-to-end implementation.

Recommended order:

1. Inspect existing architecture.
2. Implement optional quiz/exam generation context.
3. Introduce shared-class membership and capability enforcement.
4. Implement temporary, usage-limited invitations.
5. Implement shared resources and assessments.
6. Implement independent snapshot-based class forking.
7. Implement opt-in progress sharing and leaderboards.
8. Complete integration tests and verification.

If existing architecture requires a different order, adapt accordingly.

Avoid unrelated refactoring and speculative features.

## 12. Completion report

When finished, report:

- What was implemented.
- Database schema and migration changes.
- New or modified API endpoints.
- Frontend changes.
- Permission and ownership decisions.
- How fork independence is guaranteed.
- Tests run and their results.
- Known limitations or incomplete work.

Do not claim a feature works unless it has been implemented and verified.

**Primary design principle: Students should be able to collaborate freely without surrendering control of their own learning data or becoming permanently dependent on another student's class workspace.**
