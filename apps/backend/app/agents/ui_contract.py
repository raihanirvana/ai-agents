"""Supervisor-owned UI vocabulary. No arbitrary selector syntax in new contracts."""
import json
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer

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
    revision: int = Field(default=1, ge=1, le=1000)
    action_locators: Literal['testid'] = 'testid'
    controls: list[UiControl] = Field(min_length=1, max_length=96)

    @model_serializer(mode='wrap')
    def historical_shape(self, handler):
        document = handler(self)
        if 'action_locators' not in self.model_fields_set:
            document.pop('action_locators', None)
        return document

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
            result.update(self.control_selectors(control, inputs))
        return result

    def control_selectors(self, control, inputs=()):
        """Every locator this one control contributes to the vocabulary."""
        result = set()
        root = 'testid=' + control.testid
        result.update((root, '[data-testid=' + json.dumps(control.testid) + ']'))
        prefix = ('testid=' + control.scope_testid + ' >> ') if control.scope_testid else ''
        # Match the declared parent/child testid pair, as the runner already does.
        # Requiring a text filter here falsely reports an existing child as missing.
        if control.scope_testid:
            result.add(prefix + root)
        role = None
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
                if role:
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

    def in_repeated_row(self, testid):
        """The control is a repeated container (dynamic_text) or lives inside one."""
        scopes = {c.testid: c.scope_testid for c in self.controls}
        repeated = {c.testid for c in self.controls if c.dynamic_text}
        seen = set()
        while testid and testid not in seen:
            if testid in repeated:
                return True
            seen.add(testid)
            testid = scopes.get(testid)
        return False

    def check_suite(self, suite, *, strict_rows=False):
        """strict_rows (new plans/revisions): every locator into a repeated row must pick one
        row, for assertions too (except assert_count). Pinned suites keep the action rule only."""
        suite = suite.materialize_fixtures()
        # dynamic_text containers are repeated records (rows/cards) in this vocabulary.
        repeated = {c.testid for c in self.controls if c.dynamic_text}
        for test in suite.tests:
            inputs = {value for s in test.steps if s.action in ('fill', 'select_option')
                      for value in (s.value if isinstance(s.value, list) else [s.value])
                      if isinstance(value, str)}
            allowed = self.selectors(inputs)
            for index, step in enumerate(test.steps):
                if step.selector is None:
                    continue
                selector = step.selector
                # Positional disambiguation is bounded, and only extends a declared locator.
                base = re.sub(r' >> nth=(?:[0-9]|[1-9][0-9])$', '', selector)
                if 'action_locators' in self.model_fields_set and not step.action.startswith('assert_') and not (
                        base.rsplit(' >> ', 1)[-1].startswith('testid=') or base.startswith('[data-testid=')):
                    raise ValueError(f'test {test.id}, step {index}: actions require a declared testid locator; '
                                     'role/label/text locators are for assertions')
                if base not in allowed:
                    raise ValueError(f'test {test.id}, step {index}: locator is outside ui_contract: {selector}. '
                                     'Use a declared exact role/name, associated label, text or testid; '
                                     'dynamic text must come from original fill/select input within a declared container.')
                if strict_rows and step.action != 'assert_count' and base == selector:
                    first = base.split(' >> ', 1)[0]
                    narrowed = ' >> has_text=' in base or ' >> text=' in base
                    if first.startswith('testid=') and self.in_repeated_row(first[7:]) and not narrowed:
                        raise ValueError(f'test {test.id}, step {index}: {selector} can match every repeated row. '
                            'Pick one row with has_text from this test\'s original input (the declared container, '
                            'then >> has_text="<original input>" >> the child testid) or a bounded nth=; '
                            'assert_count may address all rows.')
                if ('action_locators' in self.model_fields_set and not step.action.startswith('assert_')
                        and base == selector):
                    parent = base.split(' >> ', 1)[0] if ' >> ' in base else None
                    if (parent and parent.startswith('testid=') and parent[7:] in repeated
                            and not base.startswith(parent + ' >> has_text=')):
                        raise ValueError(f'test {test.id}, step {index}: {parent} is a repeated row; an action inside '
                            'it must select one row with has_text from this test\'s original input '
                            f'(e.g. {parent} >> has_text="<original input>" >> ...) or a bounded nth=. '
                            'A parent/child pair alone matches every row.')


def with_vocabulary(contract, extra_controls):
    """QA vocabulary: this ticket's contract plus pinned accepted-app controls.

    The extras are existing controls QA may use (typically setup from earlier
    features). They are not new requirements for this ticket's build and are not
    subject to the 96-control cap of a TL-authored contract. Identity and scopes of
    the union are still checked (unique testids, declared parents, no cycles)."""
    if contract is None or not extra_controls:
        return contract
    own = {c.testid for c in contract.controls}
    extras = [UiControl.model_validate(c) for c in extra_controls if c['testid'] not in own]
    if not extras:
        return contract
    controls = [*contract.controls, *extras]
    ids = [c.testid for c in controls]
    if len(set(ids)) != len(ids):
        raise ValueError('UI vocabulary has duplicate testids')
    scopes = {c.testid: c.scope_testid for c in controls}
    for control in controls:
        parent, seen = control.scope_testid, {control.testid}
        while parent:
            if parent not in scopes or parent in seen:
                raise ValueError(f'UI vocabulary scope of {control.testid} is undeclared or cyclic')
            seen.add(parent)
            parent = scopes[parent]
    return UiContract.model_construct(_fields_set=set(contract.model_fields_set), revision=contract.revision,
                                      action_locators=contract.action_locators, controls=controls)


def used_accepted_controls(suite, contract, accepted):
    """Accepted controls a suite uses outside the ticket contract, with scope parents.

    Attribution is by every locator a control contributes (testid, [data-testid],
    role/name, label, text, scoped dynamic text), not only the testid= form."""
    own = {c.testid for c in contract.controls}
    by_id = {c['testid']: c for c in accepted if c['testid'] not in own}
    if not by_id:
        return []
    vocabulary = with_vocabulary(contract, list(by_id.values()))
    used = set()
    for test in suite.materialize_fixtures().tests:
        inputs = {value for s in test.steps if s.action in ('fill', 'select_option')
                  for value in (s.value if isinstance(s.value, list) else [s.value]) if isinstance(value, str)}
        located = {re.sub(r' >> nth=(?:[0-9]|[1-9][0-9])$', '', s.selector) for s in test.steps if s.selector}
        for control in vocabulary.controls:
            if control.testid in by_id and vocabulary.control_selectors(control, inputs) & located:
                used.add(control.testid)
    for testid in list(used):  # scope parents travel with their children
        parent = by_id[testid].get('scope_testid')
        while parent in by_id and parent not in used:
            used.add(parent)
            parent = by_id[parent].get('scope_testid')
    return [c for k, c in by_id.items() if k in used]
