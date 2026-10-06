# QA task instructions

Developer and QA work runs through the Hermes runtime with the supervisor's tools (wired in DEV-010).
This file defines the task contract; the structured PO/lead runtime does not run QA jobs.

## Task: `verify`

1. Read the approved acceptance criteria for the ticket and the candidate's target identity.
2. Propose test cases that cover every criterion; mark which are automated and which need a person.
   Keep the suite proportional to the scope: a coherent interaction sequence may cover several UAC,
   with explicit assertions and mapping for each. Preserve required regression coverage; do not add
   redundant cases, cosmetic requirements, or tests for sibling features absent from approved scope.
   For `qa_plan`, inspect the baseline source/DOM with `inspect_app` before choosing selectors and expected text.
   If source_files is empty, the new-project base intentionally has no DOM. Plan feature tests from approved
   UAC and the technical plan, defining selectors/text as an explicit contract for the developer. Do not
   repeatedly search other paths or ask the user for code that does not exist. Do not create regression cases
   for features absent from the base. A missing codebase alone is not a product clarification request.
   `assert_text` compares the whole selected element exactly: a menu item that also displays a price is not just its name.
   Select the element containing the item name when a row also contains controls. For example, assert
   text on an agreed `.item-name` child instead of requiring a shopping-list row with a Delete button
   to equal "Apples". Check the button separately if required. Never ask for valid controls to be
   removed or renamed to accommodate an incorrect assertion.
   Cover the approved behavior without inventing a DOM structure or new product requirements. For attributes such as
   href, use a CSS attribute selector with a visibility/count assertion; the DSL has no arbitrary JavaScript assertion.
   Mark new behavior as `feature` (or a fix as `bug`) so its base comparison is checked; use `regression` for existing
   behavior to preserve. Read the actual baseline before adding a regression selector.
   Every browser test starts with a fresh context and page. Reproduce the whole interaction sequence inside that
   test: a two-click toggle case needs two clicks, with the intermediate and final assertions in the same test.
   Do not rely on state from an earlier test. Prefer approved test-id selectors; avoid global tag counts or invented
   tag requirements unless the approved criteria require them. Preserve previously accepted behavior.
   `fill` only changes text. For Enter submission, fill the input then use
   `{"action":"press","selector":"input selector","value":"Enter"}` and assert the result.
   Never substitute a newline or literal `\\n` for a keyboard event. `press` accepts only the named keys
   listed in the tool schema; it does not execute JavaScript or arbitrary commands.
   For persistence, create/change state, assert it, use `{"action":"reload"}`, then assert
   the restored items and states in the same test. Reload has no selector/value and preserves
   that test's browser storage. Adding an item and checking it before reload does not test restoration.
3. Request a run of the independent acceptance harness for the exact target. You cannot run it yourself
   against a different build.
4. Read the harness report. Check that the mandatory tests were all discovered and executed, that none
   were skipped, and that every automated criterion is covered.
5. Report: passed (with the evidence ids), or failed with reproduction steps, or incomplete with what is missing.

## Planning completion and failure diagnosis

- `qa_plan` ends after one validated suite is submitted through `propose_tests`; it does not execute
  acceptance or declare a pass. Execution/reporting uses the supplied task and trusted harness.
- Read the file list and relevant components before proposing selectors. Do not repeatedly inspect
  unchanged paths or search host directories. On a tool validation error, correct the schema before retrying.
- Report each failure with its UAC/test id, exact target/evidence, reproduction, expected result, and
  observed result. Distinguish an application defect from a suite/selector error or runner/start failure.
  If the source or report is missing, say what is unavailable rather than guessing the cause.
- Suite corrections go through the authorized planning/repair workflow and require fresh evidence for
  the resulting suite and target. Do not edit immutable evidence, loosen an assertion to hide a real
  defect, waive a gate, or rerun an unchanged failure indefinitely.

## Rules

- A report written by the candidate's own code is not acceptance evidence.
- A failure on the baseline (before this change) is reported as such; only the user can waive it.
- Describe bugs so a developer can reproduce them without asking you anything.
