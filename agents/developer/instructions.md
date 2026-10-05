# Developer task instructions

Developer and QA work runs through the Hermes runtime with the supervisor's tools (wired in DEV-010).
This file defines the task contract; the structured PO/lead runtime does not run developer jobs.

## Task: `implement`

1. Read the approved scope and acceptance criteria. Read the existing code before changing it.
   An empty source_files list on a new project is expected: create the scoped app and Node tests in this
   snapshot. There is no host repository to search. Use read_file path "." once; never guess absolute paths.
   If reference_bootstrap is available, use its exact dependency versions in package.json, then call
   run_command phase "bootstrap" to generate package-lock.json. Do not fabricate a lockfile or use npm install.
   run_command accepts a phase, not arbitrary shell commands. Installation remains fixed npm ci.
2. If a product decision is missing, request input from the user. If a technical direction is missing,
   send a directed question to the technical lead and wait for the answer; do not guess.
3. Implement the change and add or update tests that cover the acceptance criteria.
   The reference catalog has no Vitest/Jest. Use Node's built-in node:test and node:assert/strict;
   npm test must execute actual .test.js/.test.cjs files with node --test. Test imported application logic,
   not a duplicate implementation inside the test. An echo success script or zero tests is incomplete.
   Inspect repository_gate returned by run_command phase test before submitting the candidate.
   read_file accepts only path; to create/update files call patch_file with path and full content.
   Preserve existing repository tests and their assertions. Use the installed repository test framework; do not
   replace unit tests with browser tests or import a package that is not in the locked dependencies. QA's independent
   browser harness owns acceptance tests. A missing mandatory baseline test is a failure, even if your new test passes.
4. Run the project's install/test/build through the run tool, inspect the diff, and fix failures.
5. Submit exactly one candidate once the repository checks pass. Include how to run it and any gaps.

## Messages

- A question to the lead is a directed message with a clear question and the context needed to answer it.
- Do not broadcast. A message that needs no answer (a note or a log) never triggers another role.
- After you receive an answer, continue from your checkpoint; do not restart the whole task.

## Limits

- Respect the model-call, tool-call and time limits you are given. If you are stopped because a limit
  was reached, a person decides whether to continue.
- Never modify files outside the project or the acceptance criteria. Existing lockfiles are preserved;
  a new-project lockfile is generated through the reference bootstrap and reviewed with the candidate.
