"""Bounded acceptance DSL: QA describes browser assertions, never arbitrary runner code."""
from typing import Literal
import hashlib
import json
from pydantic import ConfigDict, Field, model_validator, model_serializer, StrictInt, StrictStr, StrictBool
from app.agents.outputs import Contract, ID


def digest_of(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def tool_schema(contract):
    """Inline local references before embedding in a tool parameter (refs otherwise point at the tool root)."""
    schema = contract.model_json_schema()
    definitions = schema.get('$defs', {})
    def expand(value):
        if isinstance(value, list):
            return [expand(v) for v in value]
        if isinstance(value, dict):
            if '$ref' in value:
                name = value['$ref'].removeprefix('#/$defs/')
                return expand(definitions[name])
            return {k: expand(v) for k, v in value.items() if k != '$defs'}
        return value
    return expand(schema)


KEYS = ('Enter', 'Tab', 'Escape', 'Space', 'Backspace', 'Delete',
        'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End')


class UploadFixture(Contract):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    name: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')
    mime_type: Literal['text/plain', 'text/csv', 'application/json']
    content: StrictStr = Field(max_length=10000)


class DownloadExpectation(Contract):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    filename: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    text: StrictStr | None = Field(default=None, max_length=10000)
    contains_text: StrictStr | None = Field(default=None, min_length=1, max_length=10000)
    csv_rows: list[list[StrictStr]] | None = Field(default=None, min_length=1, max_length=101)

    @model_validator(mode='after')
    def bounded_expectations(self):
        if all(getattr(self, field) is None for field in ('filename', 'text', 'contains_text', 'csv_rows')):
            raise ValueError('assert_download requires at least one explicit expectation')
        if self.csv_rows is not None and any(not 1 <= len(row) <= 32 or
                any(len(cell) > 1000 for cell in row) for row in self.csv_rows):
            raise ValueError('CSV expectations require 1..32 cells per row, at most 1000 characters per cell')
        if len(self.model_dump_json().encode()) > 65536:
            raise ValueError('download expectations exceed 64 KiB')
        return self


class DialogExpectation(Contract):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    type: Literal['alert', 'confirm', 'prompt']
    message: StrictStr = Field(max_length=1000)
    accept: StrictBool = True
    prompt_text: StrictStr | None = Field(default=None, max_length=1000)

    @model_validator(mode='after')
    def prompt_only(self):
        if self.prompt_text is not None and (self.type != 'prompt' or not self.accept):
            raise ValueError('prompt_text requires an accepted prompt dialog')
        return self


class Step(Contract):
    action: Literal['click', 'fill', 'press', 'reload', 'select_option', 'check', 'uncheck',
                    'hover', 'double_click', 'focus', 'navigate', 'upload_file', 'download', 'click_dialog',
                    'assert_text', 'assert_contains_text', 'assert_count', 'assert_visible',
                    'assert_hidden', 'assert_value', 'assert_values', 'assert_checked',
                    'assert_unchecked', 'assert_enabled', 'assert_disabled', 'assert_attribute',
                    'assert_url', 'assert_download']
    selector: str | None = Field(default=None, min_length=1, max_length=300)
    value: StrictStr | StrictInt | list[StrictStr] | None = Field(default=None,
        description='For press, one named key only: ' + ', '.join(KEYS) + '. fill does not press keys.')
    select_by: Literal['value', 'label'] | None = None
    attribute: str | None = Field(default=None, pattern=r'^[A-Za-z_:][A-Za-z0-9_.:-]{0,79}$')
    file: UploadFixture | None = None
    download: DownloadExpectation | None = None
    dialog: DialogExpectation | None = None

    @model_serializer(mode='wrap')
    def legacy_serialization(self, handler):
        # Adding optional DSL fields must not change persisted legacy suite digests.
        document = handler(self)
        for field in ('select_by', 'attribute', 'file', 'download', 'dialog'):
            if document.get(field) is None:
                document.pop(field, None)
        return document

    @model_validator(mode="after")
    def arguments(self):
        for field, action in (('select_by', 'select_option'), ('attribute', 'assert_attribute'),
                              ('file', 'upload_file'), ('download', 'assert_download'), ('dialog', 'click_dialog')):
            if getattr(self, field) is not None and self.action != action:
                raise ValueError(f'{field} is only valid for {action}')
        if self.action == 'reload':
            if self.selector is not None or self.value is not None:
                raise ValueError('reload takes no selector or value')
            return self
        if self.action in ('navigate', 'assert_url'):
            if (self.selector is not None or not isinstance(self.value, str) or len(self.value) > 1000 or
                    not self.value.startswith('/') or self.value.startswith('//') or
                    '\\' in self.value or any(ord(c) < 32 for c in self.value)):
                raise ValueError('navigation/URL assertion requires a same-origin path beginning with / and no selector')
            return self
        if self.action == 'assert_download':
            if self.selector is not None or self.value is not None or self.download is None:
                raise ValueError('assert_download requires download expectations and no selector/value')
            return self
        if self.selector is None:
            raise ValueError('this step requires a selector')
        if self.action in ('fill', 'assert_text', 'assert_contains_text', 'assert_value', 'assert_attribute') and (
                not isinstance(self.value, str) or len(self.value) > 1000):
            raise ValueError("step needs a string value of at most 1000 characters")
        if self.action == 'assert_contains_text' and not self.value:
            raise ValueError('assert_contains_text needs nonempty text')
        if self.action == 'assert_attribute' and self.attribute is None:
            raise ValueError('assert_attribute requires attribute and expected string value')
        if self.action == 'upload_file' and (self.file is None or self.value is not None):
            raise ValueError('upload_file requires an inline text file fixture and no value/host path')
        if self.action == 'click_dialog' and (self.dialog is None or self.value is not None):
            raise ValueError('click_dialog requires exact dialog expectations and no value')
        if self.action in ('select_option', 'assert_values'):
            values = self.value if isinstance(self.value, list) else [self.value]
            if (not 1 <= len(values) <= 20 or any(not isinstance(v, str) or len(v) > 1000 for v in values)
                    or len(values) != len(set(values))):
                raise ValueError('selection needs 1..20 unique strings, at most 1000 characters each')
            if self.action == 'assert_values' and not isinstance(self.value, list):
                raise ValueError('assert_values requires a list of strings')
        if self.action == "assert_count" and (type(self.value) is not int or not 0 <= self.value <= 100):
            raise ValueError("assert_count needs an integer 0..100")
        if self.action in ('click', 'assert_visible', 'assert_hidden', 'check', 'uncheck',
                           'assert_checked', 'assert_unchecked', 'assert_enabled', 'assert_disabled',
                           'hover', 'double_click', 'focus', 'download', 'click_dialog') and self.value is not None:
            raise ValueError("this step takes no value")
        if self.action == "press" and self.value not in KEYS:
            raise ValueError("press requires a supported named key: " + ', '.join(KEYS))
        return self


class BrowserTest(Contract):
    id: str = Field(pattern=ID)
    uac: list[str] = Field(max_length=20)
    purpose: Literal["feature", "bug", "regression", "smoke"] = "regression"
    steps: list[Step] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def assertions(self):
        if not any(s.action.startswith("assert_") for s in self.steps):
            raise ValueError("every mandatory browser test needs an assertion")
        if len(set(self.uac)) != len(self.uac):
            raise ValueError("duplicate UAC")
        downloaded = False
        for step in self.steps:
            if step.action == 'download':
                downloaded = True
            if step.action == 'assert_download' and not downloaded:
                raise ValueError('assert_download requires a preceding download in the same test')
        return self


class QaPlan(Contract):
    kind: Literal["qa_plan"]
    summary: str = Field(min_length=1, max_length=1000)
    tests: list[BrowserTest] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def unique(self):
        if len({t.id for t in self.tests}) != len(self.tests):
            raise ValueError("duplicate test ID")
        if len(self.model_dump_json().encode()) > 512 * 1024:
            raise ValueError('acceptance suite exceeds 512 KiB')
        return self

    def check_criteria(self, criteria):
        known = {c["id"] for c in criteria}
        required = {c["id"] for c in criteria if c.get("mode", "automated") == "automated"}
        covered = {u for t in self.tests for u in t.uac}
        if not covered <= known or not required <= covered:
            raise ValueError("unknown UAC or automated UAC coverage missing")

    @property
    def digest(self):
        return digest_of(self.model_dump())


def browser_capabilities():
    return {'revision': 2, 'actions': Step.model_json_schema()['properties']['action']['enum'],
            'rules': ['fill is for text/date/number inputs, textarea or contenteditable, never select/checkbox',
                      'select_option matches explicit option value or label; check/uncheck operate on checkbox/radio',
                      'download captures one click-triggered download; assert_download checks exact filename/text/CSV rows',
                      'upload_file accepts only an inline bounded text/CSV/JSON fixture; no host path',
                      'click_dialog clicks a control and verifies/accepts or dismisses one native alert/confirm/prompt',
                      'navigate/assert_url accept same-origin paths only; each test has isolated storage',
                      'No arbitrary JavaScript, shell, external auth/API, iframe/popup, drag/drop or backend DB access',
                      'Unsupported requirements need a runner capability decision; do not invent or weaken assertions'],
            'download_max_bytes': 1024 * 1024, 'suite_max_bytes': 512 * 1024}


class Review(Contract):
    kind: Literal["review"]
    accept: StrictBool
    summary: str = Field(min_length=1, max_length=2000)
    findings: list[str] = Field(default_factory=list, max_length=20)


def validate_report(report, *, invocation_id, target_digest, suite: QaPlan):
    """Only the isolated runner's output can be admitted; exact identities/counts are mandatory."""
    incomplete = {"status": "incomplete", "counts": {}, "executed": [], "coverage": {}}
    if not isinstance(report, dict) or type(report.get('schema')) is not int or any(report.get(k) != v for k, v in {
        "schema": 1, "invocation_id": invocation_id, "target_digest": target_digest, "suite_digest": suite.digest}.items()):
        return incomplete
    tests = report.get("tests")
    if not isinstance(tests, list) or not tests or any(not isinstance(t, dict) for t in tests):
        return incomplete
    ids = [t.get("id") for t in tests]
    wanted = {t.id: t for t in suite.tests}
    if any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids) or set(ids) != set(wanted):
        return incomplete
    if any(t.get("status") not in ("passed", "failed") or t.get("uac") != wanted[t["id"]].uac for t in tests):
        return incomplete
    passed = sum(t["status"] == "passed" for t in tests)
    counts = {"discovered": len(tests), "executed": len(tests), "passed": passed, "failed": len(tests)-passed, "skipped": 0}
    if any(type(report.get(k)) is not int or report[k] != v for k, v in counts.items()):
        return incomplete
    coverage = {}
    for t in tests:
        for uac in t["uac"]:
            coverage.setdefault(uac, []).append(t["id"])
    return {"status": "passed" if passed == len(tests) else "failed", "counts": counts, "executed": ids, "coverage": coverage}
