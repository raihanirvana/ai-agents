"""Supervisor-owned UI vocabulary. No arbitrary selector syntax in new contracts."""
import json
import re
from pydantic import BaseModel, ConfigDict, Field, model_validator

TESTID = r'^[A-Za-z][A-Za-z0-9_.:-]{0,79}$'
# Public Playwright get_by_role vocabulary; container/widget roles are not restricted
# to the controls seen in the demos. Role identity does not imply an action is supported.
ROLES = ('alert', 'alertdialog', 'application', 'article', 'banner', 'blockquote', 'button',
         'caption', 'cell', 'checkbox', 'code', 'columnheader', 'combobox', 'complementary',
         'contentinfo', 'definition', 'deletion', 'dialog', 'directory', 'document', 'emphasis',
         'feed', 'figure', 'form', 'generic', 'grid', 'gridcell', 'group', 'heading', 'img',
         'insertion', 'link', 'list', 'listbox', 'listitem', 'log', 'main', 'marquee', 'math',
         'meter', 'menu', 'menubar', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'navigation',
         'none', 'note', 'option', 'paragraph', 'presentation', 'progressbar', 'radio',
         'radiogroup', 'region', 'row', 'rowgroup', 'rowheader', 'scrollbar', 'search',
         'searchbox', 'separator', 'slider', 'spinbutton', 'status', 'strong', 'subscript',
         'superscript', 'switch', 'tab', 'table', 'tablist', 'tabpanel', 'term', 'textbox',
         'time', 'timer', 'toolbar', 'tooltip', 'tree', 'treegrid', 'treeitem')


class UiControl(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    testid: str = Field(pattern=TESTID)
    purpose: str = Field(min_length=1, max_length=240)
    role: str | None = Field(default=None, max_length=30, json_schema_extra={'enum': [*ROLES, None]})
    name: str | None = Field(default=None, min_length=1, max_length=160)
    label: str | None = Field(default=None, min_length=1, max_length=160)
    text: str | None = Field(default=None, min_length=1, max_length=160)
    scope_testid: str | None = Field(default=None, pattern=TESTID)
    dynamic_text: bool = False

    @model_validator(mode='after')
    def semantic_identity(self):
        if self.role is not None and self.role not in ROLES:
            raise ValueError('unsupported UI role')
        if self.name is not None and self.role is None:
            raise ValueError('accessible name requires a role')
        if not (self.role or self.label or self.text):
            raise ValueError('each control needs role, associated label or literal text')
        if any(any(ord(c) < 32 for c in value) for value in (self.name, self.label, self.text) if value):
            raise ValueError('UI names/labels/text cannot contain control characters')
        return self


class UiContract(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(default=1, ge=1, le=1)
    controls: list[UiControl] = Field(min_length=1, max_length=96)

    @model_validator(mode='after')
    def unique(self):
        ids = {control.testid for control in self.controls}
        if len(ids) != len(self.controls):
            raise ValueError('duplicate UI testid')
        scopes = {c.testid: c.scope_testid for c in self.controls}
        for control in self.controls:
            parent, seen = control.scope_testid, {control.testid}
            while parent:
                if parent not in ids or parent in seen:
                    raise ValueError('UI scopes must refer to declared controls without cycles')
                seen.add(parent)
                parent = scopes[parent]
        return self

    def selectors(self, inputs=()):
        """Exact vocabulary plus scoped literal text derived only from original inputs."""
        result = set()
        for control in self.controls:
            root = 'testid=' + control.testid
            result.update((root, '[data-testid=' + json.dumps(control.testid) + ']'))
            prefix = ('testid=' + control.scope_testid + ' >> ') if control.scope_testid else ''
            if control.role:
                role = 'role=' + control.role
                if control.name is not None:
                    role += '[name=' + json.dumps(control.name, ensure_ascii=False) + ']'
                result.add(prefix + role)
            for kind in ('label', 'text'):
                value = getattr(control, kind)
                if value is not None:
                    result.add(prefix + kind + '=' + json.dumps(value, ensure_ascii=False))
            if control.dynamic_text:
                for value in inputs:
                    if isinstance(value, str) and value and len(value) <= 160:
                        result.add(root + ' >> text=' + json.dumps(value, ensure_ascii=False))
            if control.scope_testid:
                parent = next(c for c in self.controls if c.testid == control.scope_testid)
                if parent.dynamic_text:
                    local = {root}
                    if control.role:
                        local.add(role)
                    for kind in ('label', 'text'):
                        value = getattr(control, kind)
                        if value is not None:
                            local.add(kind + '=' + json.dumps(value, ensure_ascii=False))
                    for value in inputs:
                        if isinstance(value, str) and value and len(value) <= 160:
                            scope = 'testid=' + parent.testid + ' >> has_text=' + json.dumps(value, ensure_ascii=False) + ' >> '
                            result.update(scope + item for item in local)
        return result

    def check_suite(self, suite):
        suite = suite.materialize_fixtures()
        for test in suite.tests:
            inputs = {s.value for s in test.steps if s.action == 'fill'}
            allowed = self.selectors(inputs)
            for index, step in enumerate(test.steps):
                if step.selector is None:
                    continue
                selector = step.selector
                # Positional disambiguation is bounded, and only extends a declared locator.
                base = re.sub(r' >> nth=(?:[0-9]|[1-9][0-9])$', '', selector)
                if base not in allowed:
                    raise ValueError(f'test {test.id}, step {index}: locator is outside ui_contract: {selector}. '
                                     'Use a declared exact role/name, associated label, text or testid; '
                                     'dynamic text must come from original fill input within a declared container.')
