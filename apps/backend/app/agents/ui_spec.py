"""UI/UX specification (UiSpec): the single source for tokens, screens, copy, states and feedback.

Produced by the ui-ux role after scope approval and before the technical plan.
UiContract (QA/Developer instrumentation) is derived from it; the technical lead may
only add technical controls. Expected values in QA come from this copy or from the
test's own original input. Not an approval and not QA evidence.
See docs/decisions/ui-ux.md.
"""
import re
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ui_contract import ROLES, TESTID, UiContract, UiControl

KEY = r'^[a-z][a-z0-9_.-]{0,63}$'
NAME = r'^[a-z][a-z0-9-]{0,31}$'
HEX = r'^#[0-9A-Fa-f]{6}$'
PLACEHOLDER = re.compile(r'\{([a-z_][a-z0-9_]{0,31})\}')
FONT_FAMILY = r'^[A-Za-z0-9][A-Za-z0-9 \-]{0,63}$'


class _Spec(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ColorToken(_Spec):
    name: str = Field(pattern=NAME, description='e.g. background, surface, text, accent, danger')
    value: str = Field(pattern=HEX, description='#RRGGBB')


class FontToken(_Spec):
    role: Literal['display', 'body', 'mono']
    family: str = Field(pattern=FONT_FAMILY, description='Typeface name without quotes or CSS syntax')
    fallback: Literal['serif', 'sans-serif', 'monospace', 'system-ui']


class RadiusToken(_Spec):
    name: str = Field(pattern=NAME)
    px: int = Field(ge=0, le=64)


class DesignTokens(_Spec):
    direction: str = Field(min_length=1, max_length=400,
        description='Short visual direction specific to this product and audience')
    colors: list[ColorToken] = Field(min_length=3, max_length=8)
    fonts: list[FontToken] = Field(min_length=1, max_length=3)
    spacing_px: list[int] = Field(min_length=3, max_length=10, description='Strictly increasing spacing scale')
    radius: list[RadiusToken] = Field(min_length=1, max_length=6)

    @model_validator(mode='after')
    def coherent(self):
        names = [c.name for c in self.colors]
        if len(set(names)) != len(names):
            raise ValueError('duplicate color token name')
        roles = [f.role for f in self.fonts]
        if len(set(roles)) != len(roles):
            raise ValueError('one font per role')
        if any(not 0 <= value <= 256 for value in self.spacing_px) or any(
                b <= a for a, b in zip(self.spacing_px, self.spacing_px[1:])):
            raise ValueError('spacing_px must be strictly increasing values within 0..256')
        radius = [r.name for r in self.radius]
        if len(set(radius)) != len(radius):
            raise ValueError('duplicate radius token name')
        return self


class CopyEntry(_Spec):
    key: str = Field(pattern=KEY)
    text: str = Field(min_length=1, max_length=300,
        description='Indonesian UI text; {placeholder} is filled only from the user\'s own input')

    def placeholders(self):
        return PLACEHOLDER.findall(self.text)


class SpecComponent(_Spec):
    testid: str = Field(pattern=TESTID)
    purpose: str = Field(min_length=1, max_length=240)
    role: str | None = Field(default=None, max_length=30, json_schema_extra={'enum': [*ROLES, None]})
    name_key: str | None = Field(default=None, pattern=KEY, description='Copy key of the accessible name (needs role)')
    label_key: str | None = Field(default=None, pattern=KEY, description='Copy key of the associated visible label')
    text_key: str | None = Field(default=None, pattern=KEY, description='Copy key of literal visible text')
    repeated: bool = Field(default=False, description='One instance per record (row/card); text varies per record')
    scope_testid: str | None = Field(default=None, pattern=TESTID, description='Containing component')

    @model_validator(mode='after')
    def identity(self):
        if self.role is not None and self.role not in ROLES:
            raise ValueError('unsupported UI role')
        if self.name_key is not None and self.role is None:
            raise ValueError('accessible name requires a role')
        if not (self.role or self.label_key or self.text_key):
            raise ValueError('each component needs role, label or text')
        return self


class Screen(_Spec):
    id: str = Field(pattern=KEY)
    purpose: str = Field(min_length=1, max_length=240)
    components: list[SpecComponent] = Field(min_length=1, max_length=64)


class ScreenState(_Spec):
    screen: str = Field(pattern=KEY)
    kind: Literal['empty', 'error', 'success', 'loading']
    copy_key: str = Field(pattern=KEY)
    testid: str | None = Field(default=None, pattern=TESTID, description='Component that shows this state')


class ActionFeedback(_Spec):
    action_testid: str = Field(pattern=TESTID, description='Component the user acts on')
    outcome: Literal['success', 'error', 'confirm']
    kind: Literal['inline', 'toast', 'native_confirm', 'native_alert']
    copy_key: str = Field(pattern=KEY)
    target_testid: str | None = Field(default=None, pattern=TESTID, description='Where inline feedback appears')


class UiSpec(_Spec):
    # JSON field is "copy"; the attribute avoids shadowing BaseModel.copy.
    model_config = ConfigDict(extra='forbid', validate_by_name=True, validate_by_alias=True,
                              serialize_by_alias=True)
    kind: Literal['ui_spec']
    revision: int = Field(default=1, ge=1, le=1000)
    summary: str = Field(min_length=1, max_length=1000)
    tokens: DesignTokens
    screens: list[Screen] = Field(min_length=1, max_length=12)
    copy_entries: list[CopyEntry] = Field(alias='copy', min_length=1, max_length=200)
    states: list[ScreenState] = Field(default_factory=list, max_length=96)
    feedback: list[ActionFeedback] = Field(default_factory=list, max_length=96)

    @property
    def components(self):
        return [component for screen in self.screens for component in screen.components]

    def copy_map(self):
        return {entry.key: entry.text for entry in self.copy_entries}

    @model_validator(mode='after')
    def references(self):
        keys = [entry.key for entry in self.copy_entries]
        if len(set(keys)) != len(keys):
            raise ValueError('duplicate copy key')
        texts = self.copy_map()
        for entry in self.copy_entries:
            remainder = PLACEHOLDER.sub('', entry.text)
            if '{' in remainder or '}' in remainder:
                raise ValueError(f'copy {entry.key}: placeholders are {{name}} with lowercase letters, digits, _')
            if any(ord(c) < 32 for c in entry.text):
                raise ValueError(f'copy {entry.key}: control characters are not allowed')
        screens = [screen.id for screen in self.screens]
        if len(set(screens)) != len(screens):
            raise ValueError('duplicate screen id')
        components = self.components
        if len(components) > 96:
            raise ValueError('at most 96 components in total (the derived UI contract limit)')
        ids = [c.testid for c in components]
        if len(set(ids)) != len(ids):
            raise ValueError('duplicate component testid')
        scopes = {c.testid: c.scope_testid for c in components}
        component_screens = {c.testid: screen.id for screen in self.screens for c in screen.components}
        for component in components:
            for field in ('name_key', 'label_key', 'text_key'):
                key = getattr(component, field)
                if key is None:
                    continue
                if key not in texts:
                    raise ValueError(f'component {component.testid}: {field} {key} is not a copy key')
                if PLACEHOLDER.search(texts[key]):
                    raise ValueError(f'component {component.testid}: {field} {key} must be static text '
                                     '(placeholders vary per record; use repeated with a static label)')
            parent, seen = component.scope_testid, {component.testid}
            while parent:
                if parent not in scopes or parent in seen:
                    raise ValueError(f'component {component.testid}: scope is undeclared or cyclic')
                if component_screens[parent] != component_screens[component.testid]:
                    raise ValueError(f'component {component.testid}: scope must be on the same screen')
                seen.add(parent)
                parent = scopes[parent]
        for state in self.states:
            if state.screen not in screens:
                raise ValueError(f'state refers to unknown screen {state.screen}')
            if state.copy_key not in texts:
                raise ValueError(f'state {state.screen}/{state.kind}: unknown copy key {state.copy_key}')
            if state.testid is not None and state.testid not in scopes:
                raise ValueError(f'state {state.screen}/{state.kind}: unknown component {state.testid}')
            if state.testid is not None and component_screens[state.testid] != state.screen:
                raise ValueError(f'state {state.screen}/{state.kind}: component must be on the same screen')
        for item in self.feedback:
            where = f'feedback {item.action_testid}/{item.outcome}'
            if item.action_testid not in scopes:
                raise ValueError(f'{where}: unknown action component')
            if item.copy_key not in texts:
                raise ValueError(f'{where}: unknown copy key {item.copy_key}')
            if item.kind == 'inline' and (item.target_testid is None or item.target_testid not in scopes):
                raise ValueError(f'{where}: inline feedback needs a declared target_testid')
            if item.kind != 'inline' and item.target_testid is not None:
                raise ValueError(f'{where}: only inline feedback has a target_testid')
            if (item.kind == 'native_confirm') != (item.outcome == 'confirm'):
                raise ValueError(f'{where}: native_confirm is used exactly for confirm outcomes')
        pairs = [(f.action_testid, f.outcome) for f in self.feedback]
        if len(set(pairs)) != len(pairs):
            raise ValueError('one feedback per action and outcome')
        # Every accepted spec must derive a valid contract, including the stricter
        # UiControl text limits. Keep unbound state/feedback copy at its own limit.
        self.to_ui_contract()
        return self

    def to_ui_contract(self):
        """Deterministic instrumentation contract; wording is the spec copy, verbatim."""
        texts = self.copy_map()
        controls = [UiControl(testid=c.testid, purpose=c.purpose, role=c.role,
                              name=texts.get(c.name_key) if c.name_key else None,
                              label=texts.get(c.label_key) if c.label_key else None,
                              text=texts.get(c.text_key) if c.text_key else None,
                              scope_testid=c.scope_testid, dynamic_text=c.repeated)
                    for c in self.components]
        return UiContract(revision=self.revision, action_locators='testid', controls=controls)


class NoUiChange(_Spec):
    kind: Literal['no_ui_change']
    reason: str = Field(min_length=1, max_length=600)


UiSpecOutput = Union[UiSpec, NoUiChange]


def add_technical_controls(spec, technical):
    """The technical lead may only add instrumentation controls to the derived contract.

    Derived controls stay verbatim; a technical control cannot redefine a spec testid
    and cannot introduce wording that is not already spec copy. Returns UiContract."""
    derived = spec.to_ui_contract()
    own = {c.testid: c for c in derived.controls}
    copy_texts = set(spec.copy_map().values())
    added = []
    for control in technical.controls:
        if control.testid in own:
            if control.model_dump() != own[control.testid].model_dump():
                raise ValueError(f'{control.testid} is defined by the UI spec; the technical plan cannot redefine it')
            continue
        wording = [value for value in (control.name, control.label, control.text) if value is not None]
        if any(value not in copy_texts for value in wording):
            raise ValueError(f'technical control {control.testid} introduces wording outside the UI spec copy')
        added.append(control)
    return UiContract(revision=max(technical.revision, derived.revision),
                      action_locators='testid', controls=[*derived.controls, *added])
