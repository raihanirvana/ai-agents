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
   Read browser_capabilities and the Step schema before planning interactions. The DSL supports:
   - `select_option` for native select controls; `value` is one string or a list for select[multiple],
     `select_by` is `value` (default) or `label`. Never use fill on select; custom dropdowns use clicks.
   - `check`/`uncheck` for checkboxes, `check` for radios, and `assert_checked`/`assert_unchecked`.
     Radio deselection means selecting a different radio, never uncheck.
   - `assert_enabled`, `assert_disabled`, `assert_hidden`, `assert_contains_text`, `assert_attribute`
     (attribute name plus exact string value), and `assert_values` for multiple selects.
   - `hover`, `double_click`, `focus`, and `navigate`/`assert_url` with same-origin paths only.
   - `click_dialog` with an exact `dialog` object: type alert/confirm/prompt, message, accept boolean,
     optional prompt_text for an accepted prompt. Assert the resulting application behavior too.
   - `upload_file` with an inline `file` object: name, mime_type text/plain/text/csv/application/json,
     content at most 10000 characters. Never supply a host file path or secret data.
   - `download` clicks the given selector and captures one file. A later `assert_download` has no
     selector/value and requires a `download` expectation object with filename, exact text,
     contains_text and/or exact csv_rows (including header). CSV comparison uses parsed cells,
     so quoted commas, quotes and newlines are checked correctly. Downloads are capped at 1 MiB;
     each test starts with no captured download. Do not substitute clicking Export for verifying its file.
     `csv_rows` contains original cell values AFTER parsing, never serialized CSV fields. For a fill
     value `Kopi, Susu`, the expected cell is `Kopi, Susu`, without wrapping quotes. For input
     `Gaji "Bulanan"`, keep those literal quotes once; do not add wrapping quotes or double them.
     JSON escaping still applies to literal quotes. Use `text` or `contains_text` when checking
     serialized CSV syntax itself. Derive expectations from approved criteria and fixture/input data,
     never from the application's observed output. Planning rejects CSV-escaped tokens that can be
     traced to a previous fill input; fix the suite semantics before submitting again.
   Every test still requires an explicit assert_* step. Mandatory UAC coverage cannot be dropped.
   The DSL does not support arbitrary JS, regex/code expressions, iframe/popup, drag/drop,
   external authentication/API integration, binary uploads or backend DB access. If a UAC needs
   an unavailable capability, raise a runner capability blocker during planning; do not invent an
   action, mislabel it manual, omit the UAC, or change the application to make the test easier.
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
- Use a selector scoped to the specific form/alert, not a global `.error-message` visibility check.
  The trusted runner can diagnose an ambiguous visibility assertion with exactly one visible alert
  and hidden alternatives. The platform may qualify that assertion to its observed unique alert ID,
  retaining all test IDs, actions, values, and UAC. It creates a new suite/target and executes baseline
  and candidate again. Other ambiguity needs diagnosis; it does not justify rewriting the app.
  Runner infrastructure failures stay in QA for retry and do not consume application repair cycles.
- Wrong-control operations are action_contract errors, not proof of an application bug. A trusted
  runner observation can replace fill with select_option only when the same selector resolves to
  one visible enabled native select with exactly one enabled option of the requested value.
  IDs, UAC, selector/value and all assertions stay unchanged; the old failure is retained, a new
  suite/target is pinned, and the full candidate/baseline execution is required again. Other
  action mistakes and mixed failures stay in QA for diagnosis; never rewrite valid controls.
- Parsed CSV mismatches stay in QA for diagnosis. Read the bounded expected/actual cell differences,
  approved criteria, original fill inputs and the selected comparison mode. A CSV mismatch alone
  does not prove which side is wrong. If the suite used serialized tokens as parsed cells, correct
  the suite from its inputs and create a new target with fresh baseline/candidate execution. Do not
  copy actual output into expected values, unquote legitimate literal input, or ask the developer
  to break valid CSV escaping. When evidence demonstrates an application bug, send that diagnosis
  and reproduction through the existing request-changes workflow.

## Rules

- A report written by the candidate's own code is not acceptance evidence.
- A failure on the baseline (before this change) is reported as such; only the user can waive it.
- Describe bugs so a developer can reproduce them without asking you anything.
