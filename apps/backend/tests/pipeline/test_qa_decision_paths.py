"""Coverage-gap revision failures still yield a derived diagnosis (fake stubs, no model/QA)."""
from types import SimpleNamespace

import pytest

from app.agents.models import ModelError, ModelTimeout
from app.agents.outputs import InvalidOutput
from app.pipeline.runtime import PipelineRuntime
from app.workers.runtime import Outcome


def runtime(monkeypatch, behaviour):
    rt = object.__new__(PipelineRuntime)
    rt.redactor = SimpleNamespace(redact=lambda text: text)
    derived = []
    monkeypatch.setattr(rt, '_revise_suite', lambda *args: behaviour())
    monkeypatch.setattr(rt, '_unknown_diagnosis', lambda *args, **kw: derived.append(args) or 'derived')
    return rt, derived


def revise(rt):
    return rt._revise_coverage_gap(None, {}, None, {}, None, {}, 'verification', [], [], {}, ['feature-add'])


@pytest.mark.parametrize('error', [InvalidOutput(['unparseable twice']), ValueError('persisted proposal differs'),
                                   ModelError('provider rejected')])
def test_non_retryable_revision_exception_creates_derived_diagnosis_then_reraises(monkeypatch, error):
    def fail():
        raise error
    rt, derived = runtime(monkeypatch, fail)
    with pytest.raises(type(error)):
        revise(rt)
    assert len(derived) == 1 and derived[0][4] == ['feature-add'] and derived[0][6] == 'coverage_gap_unresolved'


def test_retryable_revision_failures_wait_for_the_retry(monkeypatch):
    def timeout():
        raise ModelTimeout('slow provider')
    rt, derived = runtime(monkeypatch, timeout)
    with pytest.raises(ModelTimeout):
        revise(rt)
    rt, derived2 = runtime(monkeypatch, lambda: Outcome('failed', error='quota', retryable=True))
    assert revise(rt).retryable and derived == derived2 == []


def test_failed_and_successful_revision_outcomes(monkeypatch):
    rt, derived = runtime(monkeypatch, lambda: Outcome('failed', error='abstained'))
    assert revise(rt).status == 'failed' and len(derived) == 1
    rt, derived = runtime(monkeypatch, lambda: Outcome('succeeded', {}))
    assert revise(rt).status == 'succeeded' and derived == []
