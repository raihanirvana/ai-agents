"""Bounded acceptance DSL: QA describes browser assertions, never arbitrary runner code."""
from typing import Literal
import hashlib
import json
from pydantic import Field, model_validator, StrictInt, StrictStr, StrictBool
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


class Step(Contract):
    action: Literal["click", "fill", "press", "reload", "assert_text", "assert_count", "assert_visible", "assert_value"]
    selector: str | None = Field(default=None, min_length=1, max_length=300)
    value: StrictStr | StrictInt | None = Field(default=None,
        description='For press, one named key only: ' + ', '.join(KEYS) + '. fill does not press keys.')

    @model_validator(mode="after")
    def arguments(self):
        if self.action == 'reload':
            if self.selector is not None or self.value is not None:
                raise ValueError('reload takes no selector or value')
            return self
        if self.selector is None:
            raise ValueError('this step requires a selector')
        if self.action in ("fill", "assert_text", "assert_value") and (not isinstance(self.value, str) or len(self.value) > 1000):
            raise ValueError("step needs a string value of at most 1000 characters")
        if self.action == "assert_count" and (type(self.value) is not int or not 0 <= self.value <= 100):
            raise ValueError("assert_count needs an integer 0..100")
        if self.action in ("click", "assert_visible") and self.value is not None:
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
        return self


class QaPlan(Contract):
    kind: Literal["qa_plan"]
    summary: str = Field(min_length=1, max_length=1000)
    tests: list[BrowserTest] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def unique(self):
        if len({t.id for t in self.tests}) != len(self.tests):
            raise ValueError("duplicate test ID")
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
