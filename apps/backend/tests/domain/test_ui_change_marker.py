"""PO ui_change marker on scope versions (DEV-023). Real DB; synthetic actors only."""
import pytest
from sqlalchemy import select

from app.domain import Invalid
from app.persistence.models import TicketVersion
from .conftest import SCOPE, world, db, db_path, store  # noqa: F401


def scope(world, t):
    with world.db.read() as s:
        return s.scalar(select(TicketVersion).where(TicketVersion.ticket_id == t.id,
                                                    TicketVersion.version == world.ticket(t.id).current_version)).scope


def test_marker_is_stored_and_inherited_by_a_revision_that_omits_it(world):
    t = world.new({**SCOPE, 'ui_change': False})
    assert scope(world, t)['ui_change'] is False
    revised = world.w.edit_scope(world.user, t.id, world.ticket(t.id).revision, {**SCOPE, 'title': 'Menu v2'})
    assert scope(world, revised)['ui_change'] is False
    changed = world.w.edit_scope(world.user, t.id, world.ticket(t.id).revision, {**SCOPE, 'ui_change': True})
    assert scope(world, changed)['ui_change'] is True


def test_unmarked_scope_keeps_its_historical_shape(world):
    t = world.new(SCOPE)
    assert 'ui_change' not in scope(world, t)


@pytest.mark.parametrize('value', ['yes', 1, 'false'])
def test_marker_must_be_boolean(world, value):
    with pytest.raises(Invalid, match='ui_change'):
        world.new({**SCOPE, 'ui_change': value})


@pytest.mark.parametrize('model_name', ['TicketProposal', 'PoRevision'])
@pytest.mark.parametrize('value', ['false', 'true', 0, 1])
def test_po_outputs_do_not_coerce_non_boolean_markers(model_name, value):
    from pydantic import ValidationError
    from app.agents import outputs
    model = getattr(outputs, model_name)
    document = {'title': 'Menu', 'uac': [{'id': 'UAC-1', 'text': 'Menu bekerja'}], 'ui_change': value}
    document.update({'key': 'menu'} if model_name == 'TicketProposal' else {'kind': 'revision', 'summary': 'Menu'})
    with pytest.raises(ValidationError, match='ui_change'):
        model.model_validate(document)
