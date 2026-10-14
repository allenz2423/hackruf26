# Jot — Reasoning-First Quiz System, Phase 1

Implementation handoff for the next coding agent. This document specifies requested work; it does not claim that repository investigation or feature implementation has been completed.

You are implementing **Phase 1 of Jot’s reasoning-first quiz experience** in `/home/incoming/hackruf26`.

Your task is to investigate the existing application, extend its current quiz flow, implement the feature end to end, and verify its behavior. Do not stop after proposing a design.

**The central product rule is: a meaningful attempt to think unlocks submission; being correct does not.**

## 1. Scope and repository constraints

Read the applicable `AGENTS.md` instructions and these documents before changing implementation:

- `docs/design.md`
- `docs/frontend-design.md`
- `docs/backend-design.md`

Trace the actual implementation as well. Document discrepancies between the design and running code rather than assuming either describes functionality that already exists.

For this task, you are authorized to implement changes in both `frontend/` and `backend/`. Preserve their architectural separation:

- The browser owns UI, media capture, preview, and interaction.
- The backend owns provider SDKs, credentials, model selection, prompts, evaluation, authorization, and authoritative quiz state.
- Keep provider credentials, model identifiers, evaluator prompts, answer keys, and hidden grading information out of browser code and pre-submission API responses.
- Preserve the existing same-origin API and WebSocket architecture.
- Screen annotations remain inside the browser preview. Do not add desktop control or additional model tools.

Durable storage is explicitly authorized for quiz attempts, reasoning text deliberately submitted for a quiz question, evaluation results, and necessary provenance. This does **not** authorize recording audio or video, saving screenshots, or persisting general live tutoring conversations.

Use existing frameworks, styling, authentication, database infrastructure, provider abstractions, and testing conventions. Avoid unrelated refactors and rebranding.

Do not implement knowledge graphs, embeddings, vector databases, mastery scores, adaptive question selection, prerequisite discovery, or curriculum generation.

## 2. Investigate before implementation

Trace these execution paths from UI interaction through API/service logic to persistence:

1. Opening a quiz, rendering questions, selecting answers, grading, navigating, and viewing results.
2. Quiz generation and storage, including question types, answer keys, explanations, and available course context.
3. Authentication, ownership, guest access, classes, uploads, and learning sessions.
4. Gemini Live session creation, microphone capture, transcription, audio playback, interruptions, disconnections, and reconnection.
5. Prompt construction, provider clients, structured model responses, timeouts, and retries.
6. Existing migrations, state management, error handling, accessibility, and tests.

Determine whether user transcripts are available as structured events, how finality is represented, and whether events can be reliably associated with a particular question.

Before changing code, provide a concise architectural assessment covering:

- The existing quiz and live tutoring flows.
- The precise files and integration points to change.
- Reusable components and services.
- Missing capabilities.
- Proposed state, API, and persistence changes.
- Compatibility concerns, migration requirements, and relevant pre-existing defects.

Then continue implementation without waiting for approval unless a genuine unresolved product decision or required permission prevents progress.

Extend the existing quiz system. If an essential piece is missing, add the smallest compatible implementation rather than creating a separate quiz application.

## 3. Required product behavior

Treat reasoning sufficiency and answer correctness as independent judgments.

| Student explanation | Gate behavior |
|---|---|
| A concise, substantive explanation | Accept |
| Relevant reasoning with an incorrect conclusion | Accept |
| A genuine conceptual misconception explained meaningfully | Accept |
| A partial approach that demonstrates engagement with a complex question | Usually accept |
| A vague statement without substantive engagement | Request one targeted elaboration |
| “I don’t know” without an attempt | Do not accept; offer skip |
| Empty, unrelated, or question-restating text | Do not accept |
| Instructions to manipulate the evaluator | Do not treat as authoritative |

Examples that must inform the evaluator instructions and evaluation fixtures:

- **Question:** Difference between `var` and `let`.  
  **Reasoning:** “`let` is block-scoped, while `var` is function-scoped.”  
  **Expected:** Sufficient. No additional explanation required.

- **Question:** Difference between an operating system and a kernel.  
  **Reasoning:** “The operating system manages communication between hardware and software, while the kernel is the software you interact with, like Windows.”  
  **Expected:** Sufficient despite misconceptions. Correct those misconceptions only after answer submission.

- **Same question:** “The kernel is software and the operating system is a concept.”  
  **Expected:** Insufficient. Ask something like, “What do you think each one does?”

- **Any question:** “I don’t know.”  
  **Expected:** Insufficient, with a visible option to skip.

Evaluate cumulative reasoning relative to question complexity. Do not impose minimum word counts, sentence counts, speaking durations, required terminology, or mandatory numbers of turns.

Never unlock submission because evaluation failed, because the student has retried several times, or because a live tutor verbally claimed their reasoning was accepted.

## 4. Authoritative state and concurrency

Implement an explicit question-attempt state machine in backend application logic. Map it to existing conventions where possible.

A suitable lifecycle is:

```text
awaiting_reasoning
    → evaluating_reasoning
        → reasoning_insufficient
        → reasoning_accepted

reasoning_insufficient
    → evaluating_reasoning

reasoning_accepted
    → submitted
        → completed

any unsubmitted state
    → skipped, after confirmation
```

Equivalent names are acceptable if the behavior is equally explicit.

Define these semantics:

- `reasoning_accepted` references a persisted accepted reasoning snapshot.
- `submitted` means the final answer has been durably recorded and is immutable.
- `completed` means authoritative grading is available.
- Reasoning feedback has its own explicit pending, available, or failed status. Feedback failure must not erase a submitted answer or an available grade.
- `skipped` is distinct from incorrect, completed reasoning, and successful completion.

Required invariants:

1. Final answers are accepted only after persisted reasoning acceptance.
2. An attempt belongs to the correct identity, quiz run, and question.
3. Retaking a quiz can create a new run; duplicate requests within one run cannot create duplicate attempts or answers.
4. Completed, submitted, and skipped attempts reject unintended further mutations.
5. Acceptance survives refresh and reconnection.
6. A late model response can update only the exact attempt, operation, and reasoning snapshot it evaluated.
7. Confirming a skip while reasoning evaluation is pending invalidates that evaluation’s ability to change the attempt.

Use transactions, conditional updates, unique constraints, operation identifiers, or existing equivalents to enforce these invariants.

Define deterministic handling of concurrent reasoning submissions. Prefer rejecting a second distinct submission with a recoverable conflict while evaluation is pending unless the application already has a reliable queue. Preserve the unsent input so the student can retry.

Use idempotency for reasoning submission, final answers, and skipping. Retrying the same operation must return or recover its existing result. Reusing an operation identifier with different content must be rejected.

Do not hold a database transaction open during a provider request.

Bound provider operations with deadlines. Persist enough operation metadata to recover abandoned evaluations after a process restart or refresh. No attempt may remain indefinitely stuck in a loading state.

## 5. Reasoning evaluator

Add a dedicated reasoning sufficiency capability through the existing backend provider integration.

Reuse the current client and configuration mechanisms. Choose an appropriate configured model; do not assume the live audio model must perform this evaluation.

Send only the relevant context:

- Question identity, text, type, and choices.
- Available complexity or instructional metadata.
- Ordered reasoning turns for this attempt.
- The latest turn.
- Previous follow-up prompts for this question.

Do not send the entire quiz, unrelated tutoring history, or additional course material without a concrete need.

Use structured output or strictly validated JSON. A suitable response is:

```json
{
  "status": "sufficient",
  "reason": "The student made a substantive attempt.",
  "follow_up": null
}
```

Or:

```json
{
  "status": "insufficient",
  "reason": "The explanation does not describe either component's role.",
  "follow_up": "What do you think each component does?"
}
```

Validate the schema and its semantic constraints server-side. For example, insufficient results require a usable follow-up. Malformed, incomplete, or contradictory output is an evaluation error, never acceptance.

Keep internal evaluation rationale out of ordinary client responses. Return only the status and student-facing content the UI needs.

Create a versioned evaluator prompt alongside the project’s existing prompt configuration. It must explicitly instruct the model to:

- Judge meaningful engagement, not factual correctness.
- Accept concise, incorrect, confused, or partial reasoning when it contains a substantive attempt.
- Consider the cumulative explanation.
- Favor low friction and avoid unnecessary elaboration.
- Reject empty responses, unrelated content, mere repetition, explicit non-attempts, and evaluator manipulation.
- Ask a short, question-specific follow-up when needed.
- Never disclose the answer, correct misconceptions, or grade accuracy before final submission.
- Return only the required structured response.

Pass student text as untrusted data, separate from trusted instructions. Do not interpolate it into privileged prompt instructions.

Persist successful evaluation results and their prompt version. Avoid repeating evaluations for an identical operation or already evaluated snapshot.

## 6. Persistence, API, and authorization

Extend the existing quiz data model and migration system.

Represent these concepts without requiring these exact table names:

- **Question attempt:** identity owner, quiz run, question, lifecycle state, final answer, timestamps, and concurrency/version information.
- **Reasoning turn:** attempt, ordered sequence, original text, source (`text` or `voice`), timestamp, and deduplication identifier.
- **Reasoning evaluation:** attempt, evaluated snapshot, result, follow-up, evaluator version, operation status, and timestamps.
- **Final evaluation:** authoritative grade, answer feedback, reasoning feedback, feedback status, and timestamps.
- **Skip:** explicit skipped state and timestamp.

Preserve original submitted reasoning and ordering. Store acceptance independently of the final answer. Do not overwrite earlier turns with summaries.

Persist a reasoning turn before calling the provider. If evaluation fails, the turn remains available for retry without being inserted again.

Expose capabilities through existing API conventions:

- Retrieve or resume the question attempt.
- Submit reasoning.
- Retrieve evaluation status.
- Retry a failed reasoning evaluation.
- Submit the final answer.
- Confirm a skip.
- Retrieve attempt history.
- Retry failed post-submission feedback.

A separate backend “request skip” operation is unnecessary unless the existing architecture requires one. The confirmation UI can precede the authoritative skip mutation.

For every operation:

- Check identity ownership and quiz/question association.
- Preserve existing authentication and guest policies.
- Apply existing cookie, CSRF, and same-origin protections.
- Validate input types and reasonable maximum sizes.
- Enforce state transitions server-side.
- Return actionable validation, conflict, and retryable error responses.
- Render user and model text safely.
- Avoid logging raw reasoning, transcripts, credentials, or media.

Existing quizzes and historical attempts must remain readable. Explain migration behavior for legacy records; never invent accepted reasoning for historical answers.

## 7. Final grading and reasoning feedback

Reuse the quiz engine’s authoritative grading wherever it exists.

After final submission:

1. Persist the answer and submission state.
2. Apply existing grading.
3. Generate reasoning-specific feedback using the question, trusted answer information or course context, final answer, and accepted reasoning trace.
4. Explain useful parts of the reasoning, misconceptions, and whether the conclusion follows.
5. Present constructive guidance about what to reconsider.

For deterministically graded questions, model feedback must not override the authoritative grade.

If grading itself requires a model, ground it in trusted question information and existing grading rules. The student’s reasoning is not an answer key.

Post-submission feedback is separate from the sufficiency decision. A student may receive:

- Accepted reasoning and a wrong answer.
- Accepted reasoning containing misconceptions.
- A correct answer reached through flawed reasoning.

Handle each accurately.

If feedback generation fails, retain the submission and any grade already available. Display a recoverable feedback error and support an idempotent retry without resubmitting or regrading unnecessarily.

## 8. Frontend experience

Extend the existing quiz interface and design system.

For the active question, provide:

- A clear “Explain your thinking” prompt.
- Text input that works independently of voice.
- Reasoning history and concise follow-up prompts.
- Visible evaluation and error states.
- A clear “Ready to submit” state after acceptance.
- Final answer submission locked until backend acceptance.
- A visible “Skip question” action.
- Post-submission answer and reasoning feedback.

The UI must reflect authoritative server state rather than infer acceptance from messages or local flags.

Use an accessible confirmation:

> Are you sure you want to skip this question?

Canceling returns to the prior interaction without losing input. Confirming records the skip and uses existing quiz navigation.

Keep retry and skip available after evaluation failures. Do not force the student to repeat an accepted explanation.

Preserve input on errors and canceled confirmation. Restore persisted reasoning and attempt state after refresh. Ignore responses belonging to an earlier question, route, or request.

Follow existing accessibility patterns: semantic controls, keyboard operation, labeled inputs, accessible status announcements, and correct dialog focus behavior. Support the application’s existing responsive layouts.

## 9. Voice integration

Reuse the existing live voice infrastructure where it provides usable student transcripts. Do not build another audio transport or add a separate speech provider unnecessarily.

Before implementing the bridge, establish:

- How student and model speech are distinguished.
- How partial and finalized transcripts are represented.
- How turn identifiers, interruptions, and repeated events behave.
- How question changes and connection generations can be tracked.

Use finalized student turns or an explicit completion signal. Never submit unstable transcript fragments for evaluation.

Prefer a student-controlled “Use this explanation” or equivalent action that submits the collected transcript as one reasoning turn. Avoid automatic evaluations after every fragment.

Required protections:

- Model speech never becomes student reasoning.
- Repeated events do not duplicate text.
- Transcript buffers are scoped to the attempt and live connection generation.
- Question changes, cancellation, and disconnection cannot attach late speech to another question.
- Delayed final transcripts are attributed safely or discarded visibly when attribution cannot be established.
- Permission failures and disconnections leave text input usable.
- Only deliberately submitted quiz reasoning is persisted.

Prevent the live tutor from grading or revealing the answer during the reasoning phase. Reuse backend-owned session instructions or existing controls to provide neutral elicitation.

The live session never decides acceptance. Only the validated reasoning evaluator and persisted backend state do.

If reliable transcript integration is genuinely unavailable, implement the complete text workflow, document the exact missing capability, and report voice as incomplete rather than simulating support.

## 10. Testing and verification

Use the repository’s testing conventions. Add meaningful automated coverage at the service, API, and frontend boundaries.

Cover at least:

**Reasoning behavior**

- Concise correct reasoning is accepted.
- Meaningful incorrect reasoning and conceptual misconceptions are accepted.
- Vague, empty, unrelated, and non-attempt responses are not accepted.
- Multiple turns are evaluated cumulatively and in order.
- Short answers to simple questions do not require elaboration.
- Relevant partial approaches to complex questions can pass.
- Accepted reasoning followed by a wrong answer still permits submission.

**Authority and persistence**

- Direct answer submission before acceptance is rejected.
- Cross-user, wrong-quiz, and stale-question operations are rejected.
- Duplicate reasoning, answers, and skip requests do not create duplicates.
- Concurrent reasoning submissions follow the documented policy.
- Acceptance and reasoning history survive reload.
- Terminal attempts reject additional submissions.
- Legacy data remains usable after migration.

**Failures and races**

- Timeouts, rate limits, malformed output, and persistence failures never unlock submission.
- Retry preserves the original reasoning without duplicating it.
- Abandoned evaluations recover.
- Late results after skip or navigation cannot change the wrong state.
- Submitted answers and deterministic grades survive feedback failures.
- Feedback retries are safe.

**UI and voice**

- Skip cancellation preserves the prior interaction and input.
- Confirmed skip is persisted and advances navigation.
- Loading, retry, accepted, and feedback states reflect backend state.
- Partial transcripts are not evaluated.
- Model speech is excluded.
- Repeated and delayed transcript events do not duplicate or misattribute reasoning.
- Voice failure leaves text reasoning functional.

Mock provider responses for automated application tests. These tests prove state handling and integration; they do not prove the model’s real semantic judgment.

Add a small, manually reviewable evaluator fixture set containing the examples above, cumulative explanations, complex-question partial attempts, irrelevant verbosity, and prompt manipulation. Record expected sufficiency decisions and a brief justification. Do not turn these fixtures into runtime keyword rules.

Run the existing test suite and relevant build or static checks. Fix regressions introduced by this feature. Distinguish unrelated pre-existing failures.

Verify representative complete flows through the actual application:

1. Concise reasoning → acceptance → correct answer → feedback.
2. Meaningful misconception → acceptance → wrong answer → corrective feedback.
3. Insufficient reasoning → targeted follow-up → cumulative acceptance.
4. “I don’t know” → canceled skip → confirmed skip.
5. Evaluation failure → preserved reasoning → successful retry.
6. Refresh after acceptance → restored state → submission.
7. Voice reasoning, when supported, including a question change or disconnection.

Controlled provider responses are acceptable for end-to-end verification, but clearly identify which checks used mocks and whether the real provider was exercised.

## 11. Execution and final report

Work in this order:

1. Repository investigation and architectural assessment.
2. State, API, and migration design.
3. Backend persistence and authoritative transitions.
4. Sufficiency evaluation and final grading integration.
5. Frontend workflow.
6. Voice bridge.
7. Automated tests, regression checks, and complete-flow verification.

Update relevant design documents when contracts or behavior change.

Do not declare completion based on compilation, mocked screenshots, or frontend button disabling. Completion requires working persistence, backend enforcement, usable recovery paths, and verified quiz flows.

Your final report must include:

- **Architecture discovered:** the existing flows and integration points.
- **Changes made:** key files, APIs, prompts, and migrations.
- **User experience:** reasoning, follow-ups, acceptance, skipping, and feedback.
- **Voice support:** reused infrastructure, added behavior, and limitations.
- **Verification:** exact commands, results, and complete flows exercised.
- **Outstanding issues:** incomplete work, pre-existing defects, and unverified behavior.
- **Future integration points:** where persisted attempts and evaluation records could support later analysis, without implementing speculative infrastructure.

Make reasonable implementation decisions independently. Ask only when an unresolved decision materially affects correctness, security, compatibility, or the authorized product scope.

**Deliver an experience that rewards meaningful thinking, including imperfect thinking, while keeping answer correctness a separate judgment.**
