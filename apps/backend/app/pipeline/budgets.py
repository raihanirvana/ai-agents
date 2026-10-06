"""Explicit local demo policy; ordinary deployments retain finite defaults."""
from app.persistence import apply_change, EventSpec
from app.persistence.models import Project
from app.workers.queue import LIMIT_KEYS, OPTIONAL_LIMIT_KEYS


def unlimited_limits():
    return {**{key: None for key in LIMIT_KEYS + OPTIONAL_LIMIT_KEYS}, 'unlimited_budgets': True}


def project_limits(project, defaults):
    return unlimited_limits() if project.workflow.get('demo_unlimited_budgets') is True else dict(defaults)


def install_demo_policy(session, project, actor):
    from app.config import DEMO_UNLIMITED_BUDGETS
    if not DEMO_UNLIMITED_BUDGETS:
        return
    apply_change(session, Project, project.id, expected_revision=project.revision,
        values={'workflow': {**project.workflow, 'demo_unlimited_budgets': True}},
        event=EventSpec('project.demo_budget_authorized', actor,
            {'unlimited_budgets': True, 'source': 'explicit local DEMO_UNLIMITED_BUDGETS configuration'}))
