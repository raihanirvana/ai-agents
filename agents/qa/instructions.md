# QA task instructions

Developer and QA work runs through the Hermes runtime with the supervisor's tools (wired in DEV-010).
This file defines the task contract; the structured PO/lead runtime does not run QA jobs.

## Task: `verify`

1. Read the approved acceptance criteria for the ticket and the candidate's target identity.
2. Propose test cases that cover every criterion; mark which are automated and which need a person.
3. Request a run of the independent acceptance harness for the exact target. You cannot run it yourself
   against a different build.
4. Read the harness report. Check that the mandatory tests were all discovered and executed, that none
   were skipped, and that every automated criterion is covered.
5. Report: passed (with the evidence ids), or failed with reproduction steps, or incomplete with what is missing.

## Rules

- A report written by the candidate's own code is not acceptance evidence.
- A failure on the baseline (before this change) is reported as such; only the user can waive it.
- Describe bugs so a developer can reproduce them without asking you anything.
