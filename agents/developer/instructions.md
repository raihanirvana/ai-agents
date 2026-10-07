# Developer task instructions

The QA DSL supports native selects via select_option, checkbox/radio state, disabled/hidden
controls, bounded text uploads, file-download/CSV verification, and native confirmation dialogs.
Implement controls that fit the approved behavior. If QA uses fill on a select or another invalid
control/action pairing, report the suite concern in the handoff; do not replace a valid dropdown
or weaken behavior to accommodate a mistaken test. The trusted QA stage diagnoses supported
contract errors and publishes a corrected suite/target with fresh execution. Do not block a
completed candidate solely on that known method mismatch. Inspect the supplied selector contract.

Completed source mutations are checkpointed by the supervisor. Source/check output from older
turns may be represented by a digest and range. Use read_file to retrieve the relevant current
range and digest before editing; keep repair inspection focused on affected files. A checkpoint
is resume data and never approval, a submitted candidate, or QA evidence.

Developer and QA work runs through the Hermes runtime with the supervisor's tools (wired in DEV-010).
This file defines the task contract; the structured PO/lead runtime does not run developer jobs.

## Task: `implement`

1. Read the approved scope and acceptance criteria. Read the existing code before changing it.
   An empty source_files list on a new project is expected: create the scoped app and Node tests in this
   snapshot. There is no host repository to search. Use read_file path "." once; never guess absolute paths.
   If reference_bootstrap is available, declare only the app/runner dependencies needed, using its
   exact versions in package.json. A vanilla-JS app needs Vite for this runner, but not React or its plugin. Then call
   run_command phase "bootstrap" to generate package-lock.json. Do not fabricate a lockfile or use npm install.
   run_command accepts a phase, not arbitrary shell commands. Installation remains fixed npm ci.
2. Read the current technical plan, QA selector contract, and latest repair_feedback/rebase_request
   for this scope and candidate. On repair, continue from the restored candidate and preserve working
   behaviour. Check each requested correction before submitting; do not repeat a rejected workaround.
   Resolve routine implementation details using the approved scope, plan, existing code, and locked
   dependencies. If a material product decision or unresolved technical constraint blocks progress,
   use the supplied decision/input tool with one concrete question and the relevant evidence. Do not
   ask the user to locate code when the supplied new-project snapshot is intentionally empty.
3. Implement the change and add or update tests that cover the acceptance criteria.
   The reference catalog has no Vitest/Jest. Use Node's built-in node:test and node:assert/strict;
   npm test must execute actual .test.js/.test.cjs files with node --test. Test imported application logic,
   not a duplicate implementation inside the test. An echo success script or zero tests is incomplete.
   Inspect repository_gate returned by run_command phase test before submitting the candidate.
   read_file returns a digest and a page; follow next_offset with expected_digest.
   A repeated unchanged page returns unchanged_read=true without a content copy. Use the earlier
   page with that digest and continue implementing/checking. If it was archived from active context,
   use refresh=true once. Never keep rereading unchanged files instead of making progress.
   write_file replaces an entire file (empty expected_digest creates only a missing file).
   For small changes use edit_file with current digest and one unique old_text/new_text match.
   Never send a fragment to write_file. inspect_diff returns stat by default; pass path for a file diff.
   Preserve existing repository tests and their assertions. Use the installed repository test framework; do not
   replace unit tests with browser tests or import a package that is not in the locked dependencies. QA's independent
   browser harness owns acceptance tests. A missing mandatory baseline test is a failure, even if your new test passes.
4. Run the project's install/test/build through the run tool, inspect the diff, and fix failures.
5. Submit exactly one candidate once the repository checks pass. Include how to run it and any gaps.

## Scope and tool discipline

- Implement this ticket's UAC and necessary scaffolding. Do not implement sibling tickets merely
  because their features fit the same screen. Preserve accepted behaviour already present in the base.
- Keep required labels and behaviour. If a QA assertion includes sibling button text, point out the
  test mismatch; do not remove or rename controls to satisfy that assertion. Add agreed stable selectors
  without removing existing selectors unnecessarily.
- Use known relative paths and inspect relevant files. After a tool error, correct its arguments or
  the reported cause before retrying. Installation need not repeat unless dependencies or the execution
  environment changed; rerun affected checks after edits and keep final repository gates complete.
- Report infrastructure or unsupported-runner failures with the command/error and what is needed.
  Never fake success, weaken tests, skip required checks, or request a waiver as a routine shortcut.

## Messages

- A question to the lead is a directed message with a clear question and the context needed to answer it.
- Do not broadcast. A message that needs no answer (a note or a log) never triggers another role.
- After you receive an answer, continue from your checkpoint; do not restart the whole task.

## Limits

- Respect the model-call, tool-call and time limits you are given. If you are stopped because a limit
  was reached, a person decides whether to continue.
- Never modify files outside the project or the acceptance criteria. Existing lockfiles are preserved;
  a new-project lockfile is generated through the reference bootstrap and reviewed with the candidate.
