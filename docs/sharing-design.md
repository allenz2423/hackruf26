# Sharing Classes With Other Users — Design Proposal

**Status:** Proposal for team review. Nothing here is implemented yet.
**Scope:** Let a student share a class (its uploads and generated study materials) with other Jot accounts.

## Why

Study groups and classmates want the same notes, syllabus, and practice tests. Today every class belongs to exactly one account, so the only way to share is to upload the same files again.

## Proposed first version (MVP)

The smallest version that is useful and safe:

- **Share a class, not single files.** Sharing is per class. Everything in the class comes along: uploads, the syllabus outline, and generated study materials.
- **View-only.** People you share with can open the class, read uploads, use study materials, take quizzes and practice tests, and start a class tutor session with the class materials. They cannot upload, edit, rename, generate new material, or delete anything.
- **By username.** The owner types a classmate's exact Jot username. No email lookup and no public links in the MVP.
- **Stop sharing anytime.** The owner sees who has access and can remove anyone, which takes effect on that person's next request.
- **Your attempts stay yours.** Quiz and practice-test attempts and scores belong to whoever took them. The owner does not see a viewer's scores, and viewers do not see each other's.

Out of scope for the MVP: edit access, share links, sharing a single upload, notifications, and sharing with guests.

## Data model

One new table:

```text
class_shares
  class_id    TEXT  references course_classes(id) ON DELETE CASCADE
  user_id     TEXT  references users(id) ON DELETE CASCADE   -- who it is shared with
  role        TEXT  'viewer' (room to add 'editor' later)
  created_at  TEXT
  PRIMARY KEY (class_id, user_id)
```

Deleting a class or an account removes its shares automatically. The owner is still `course_classes.owner_id`; an owner never appears in `class_shares` for their own class.

## API

| Endpoint | Who | Purpose |
| --- | --- | --- |
| `GET /api/classes` | Logged in | Adds a `shared_with_me` list next to the owner's own classes (each with the owner's display name). |
| `GET /api/classes/{id}/shares` | Owner | Who the class is shared with. |
| `POST /api/classes/{id}/shares` `{username}` | Owner | Share with an account. Idempotent. |
| `DELETE /api/classes/{id}/shares/{user_id}` | Owner | Stop sharing with that account. |
| `DELETE /api/classes/{id}/shares/me` | Viewer | Leave a class that was shared with you. |

Existing read endpoints (class detail, uploads list and detail, syllabus, study materials, signed file links, starting a class tutor session) accept owners **and** viewers. Every write endpoint stays owner-only.

## Authorization: the one rule that matters

Replace the owner-only lookup used by class endpoints with a single helper that returns the class and the caller's role:

```text
class_access(user_id, class_id) -> "owner" | "viewer" | not found
```

- Read endpoints require `owner` or `viewer`.
- Write endpoints require `owner`.
- A class the caller cannot access returns **404**, never 403, so class ids cannot be probed.

Every class endpoint must go through this helper; none may compare `owner_id` directly. The existing "no route answers a guest" test should be extended with a matching "no write route accepts a viewer" test that walks every route the same way, so a new endpoint cannot forget the rule.

## Frontend

- **Class page:** a *Share* button for the owner opens a small dialog with a username field, the current list of people, and *Remove* next to each.
- **Classes page:** a *Shared with me* section under your own classes, with the owner's name and a *Leave* action on each card.
- **Read-only view:** in a shared class, upload, generate, rename, and delete controls are hidden, and a short note says who shared it.
- Cached class data is still cleared on logout and account switch, as it is today.

## Privacy and abuse

- Sharing by exact username reveals whether that username exists. That is acceptable for a classroom app, but the share endpoint should be rate limited like login.
- Shares are explicit and revocable; there are no public links in the MVP.
- Signed file links already expire quickly. A viewer who loses access cannot mint new links, and old ones expire on their own.
- The demo account can share like any other account.

## Testing plan

- Owner, viewer, and stranger each against every class endpoint: viewer reads succeed, viewer writes are refused, strangers get 404.
- Revoking access takes effect on the next request; deleting the class or either account removes the share.
- Attempts and scores are private per account.
- A class tutor session works for a viewer and uses the shared class's materials.

## Open questions for the team

1. Should viewers be able to generate new study materials (stored as theirs, not the owner's)?
2. Is username-only invite enough, or do we want invite links soon after?
3. Should the owner see a count of how many times a shared quiz was taken (without names or scores)?
