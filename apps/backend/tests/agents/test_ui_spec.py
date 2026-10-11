"""UiSpec schema, validators and the derived UI contract (DEV-019). Pure validators, no model."""
import copy as copylib
import json

import pytest
from pydantic import TypeAdapter, ValidationError

from app.agents.ui_contract import UiContract
from app.agents.ui_spec import NoUiChange, UiSpec, UiSpecOutput, add_technical_controls

SPEC = {
    'kind': 'ui_spec', 'revision': 1, 'summary': 'Katalog dan peminjaman buku',
    'tokens': {
        'direction': 'Kartu katalog perpustakaan sekolah: kertas tebal, tinta biru tua, tenang dan rapi.',
        'colors': [{'name': 'background', 'value': '#F7F5EF'}, {'name': 'text', 'value': '#1C2A44'},
                   {'name': 'accent', 'value': '#2F5D8A'}, {'name': 'danger', 'value': '#A23B2A'}],
        'fonts': [{'role': 'display', 'family': 'Source Serif 4', 'fallback': 'serif'},
                  {'role': 'body', 'family': 'Inter', 'fallback': 'sans-serif'}],
        'spacing_px': [4, 8, 12, 16, 24, 32],
        'radius': [{'name': 'control', 'px': 6}],
    },
    'screens': [{'id': 'catalog', 'purpose': 'Daftar buku dan peminjaman', 'components': [
        {'testid': 'catalog-title', 'role': 'heading', 'name_key': 'catalog.title', 'text_key': 'catalog.title',
         'purpose': 'judul halaman'},
        {'testid': 'title-input', 'label_key': 'book.title_label', 'purpose': 'judul buku baru'},
        {'testid': 'add-book', 'role': 'button', 'name_key': 'book.add', 'purpose': 'menambah buku'},
        {'testid': 'book-item', 'role': 'listitem', 'repeated': True, 'purpose': 'satu baris buku'},
        {'testid': 'book-status', 'role': 'status', 'scope_testid': 'book-item', 'purpose': 'ketersediaan'},
        {'testid': 'delete-book', 'role': 'button', 'name_key': 'book.delete', 'scope_testid': 'book-item',
         'purpose': 'menghapus buku'},
        {'testid': 'form-error', 'role': 'alert', 'purpose': 'pesan error form'},
    ]}],
    'copy': [
        {'key': 'catalog.title', 'text': 'Katalog Buku'},
        {'key': 'book.title_label', 'text': 'Judul buku'},
        {'key': 'book.add', 'text': 'Tambah buku'},
        {'key': 'book.delete', 'text': 'Hapus'},
        {'key': 'book.added', 'text': 'Buku "{title}" ditambahkan'},
        {'key': 'book.title_required', 'text': 'Isi judul buku terlebih dahulu'},
        {'key': 'book.delete_confirm', 'text': 'Hapus buku ini dari katalog?'},
        {'key': 'catalog.empty', 'text': 'Belum ada buku. Tambahkan buku pertama.'},
    ],
    'states': [{'screen': 'catalog', 'kind': 'empty', 'copy_key': 'catalog.empty'}],
    'feedback': [
        {'action_testid': 'add-book', 'outcome': 'success', 'kind': 'toast', 'copy_key': 'book.added'},
        {'action_testid': 'add-book', 'outcome': 'error', 'kind': 'inline', 'copy_key': 'book.title_required',
         'target_testid': 'form-error'},
        {'action_testid': 'delete-book', 'outcome': 'confirm', 'kind': 'native_confirm',
         'copy_key': 'book.delete_confirm'},
    ],
}


def spec(**changes):
    document = copylib.deepcopy(SPEC)
    document.update(changes)
    return document


def test_valid_spec_round_trips_with_copy_field_name():
    parsed = UiSpec.model_validate(SPEC)
    dumped = parsed.model_dump()
    assert 'copy' in dumped and 'copy_entries' not in dumped
    assert UiSpec.model_validate(json.loads(json.dumps(dumped))) == parsed
    assert parsed.copy_map()['book.added'] == 'Buku "{title}" ditambahkan'


def test_output_union_accepts_no_ui_change():
    adapter = TypeAdapter(UiSpecOutput)
    assert isinstance(adapter.validate_python({'kind': 'no_ui_change', 'reason': 'Hanya perubahan API.'}), NoUiChange)
    assert isinstance(adapter.validate_python(SPEC), UiSpec)


def broken(mutate):
    document = copylib.deepcopy(SPEC)
    mutate(document)
    return document


@pytest.mark.parametrize('mutate, match', [
    (lambda d: d['copy'].append({'key': 'book.add', 'text': 'Lagi'}), 'duplicate copy key'),
    (lambda d: d['copy'].append({'key': 'bad', 'text': 'Kurung {Judul} salah'}), 'placeholders'),
    (lambda d: d['copy'].append({'key': 'bad', 'text': 'Kurung { tidak tutup'}), 'placeholders'),
    (lambda d: d['feedback'].append({'action_testid': 'delete-book', 'outcome': 'success', 'kind': 'toast',
                                     'copy_key': 'missing.key'}), 'unknown copy key'),
    (lambda d: d['feedback'].append({'action_testid': 'nowhere', 'outcome': 'success', 'kind': 'toast',
                                     'copy_key': 'book.added'}), 'unknown action'),
    (lambda d: d['feedback'][1].update(target_testid=None), 'inline feedback needs'),
    (lambda d: d['feedback'][2].update(kind='toast'), 'native_confirm'),
    (lambda d: d['feedback'][0].update(kind='native_confirm'), 'native_confirm'),
    (lambda d: d['feedback'].append(dict(d['feedback'][0])), 'one feedback per action'),
    (lambda d: d['screens'][0]['components'][4].update(scope_testid='missing'), 'undeclared or cyclic'),
    (lambda d: d['screens'][0]['components'][3].update(scope_testid='book-status'), 'undeclared or cyclic'),
    (lambda d: d['screens'][0]['components'].append(dict(d['screens'][0]['components'][0])), 'duplicate component'),
    (lambda d: d['screens'][0]['components'][2].update(name_key='book.added'), 'must be static text'),
    (lambda d: d['screens'][0]['components'][2].update(name_key='nope'), 'is not a copy key'),
    (lambda d: d['states'].append({'screen': 'other', 'kind': 'error', 'copy_key': 'catalog.empty'}), 'unknown screen'),
])
def test_reference_validators(mutate, match):
    with pytest.raises(ValidationError, match=match):
        UiSpec.model_validate(broken(mutate))


@pytest.mark.parametrize('mutate', [
    lambda d: d['tokens']['colors'][0].update(value='blue'),
    lambda d: d['tokens']['colors'][0].update(value='#FFF'),
    lambda d: d['tokens']['colors'].append({'name': 'accent', 'value': '#000000'}),
    lambda d: d['tokens']['fonts'][0].update(family='Inter; color:red'),
    lambda d: d['tokens']['fonts'].append({'role': 'body', 'family': 'Lora', 'fallback': 'serif'}),
    lambda d: d['tokens'].update(spacing_px=[4, 8, 8]),
    lambda d: d['tokens'].update(spacing_px=[4, 8, 400]),
    lambda d: d['tokens']['radius'].append({'name': 'control', 'px': 2}),
    lambda d: d['tokens']['radius'][0].update(px=200),
])
def test_token_values_are_validated(mutate):
    with pytest.raises(ValidationError):
        UiSpec.model_validate(broken(mutate))


def test_ui_contract_is_derived_verbatim_from_spec_copy():
    contract = UiSpec.model_validate(SPEC).to_ui_contract()
    by_id = {c.testid: c for c in contract.controls}
    assert 'action_locators' in contract.model_fields_set
    assert by_id['add-book'].name == 'Tambah buku' and by_id['title-input'].label == 'Judul buku'
    assert by_id['book-item'].dynamic_text and by_id['delete-book'].scope_testid == 'book-item'
    assert 'testid=book-item >> has_text="Laskar" >> testid=delete-book' in contract.selectors({'Laskar'})


@pytest.mark.parametrize('field', ['name_key', 'label_key', 'text_key'])
@pytest.mark.parametrize('length', [160, 161])
def test_component_copy_is_valid_for_the_derived_contract(field, length):
    document = spec()
    document['copy'].append({'key': 'boundary', 'text': 'x' * length})
    document['screens'][0]['components'].append({
        'testid': 'boundary', 'purpose': 'copy at the contract boundary',
        'role': 'heading', field: 'boundary',
    })
    if length > 160:
        with pytest.raises(ValidationError, match='160'):
            UiSpec.model_validate(document)
    else:
        parsed = UiSpec.model_validate(document)
        control = parsed.to_ui_contract().controls[-1]
        assert getattr(control, field.removesuffix('_key')) == 'x' * length


def test_long_feedback_copy_does_not_use_the_control_text_limit():
    document = spec()
    document['copy'][4]['text'] = 'x' * 280 + ' {title}'
    parsed = UiSpec.model_validate(document)
    assert parsed.copy_entries[4].placeholders() == ['title']
    assert parsed.to_ui_contract()


def two_screens():
    document = spec()
    document['screens'].append({
        'id': 'settings', 'purpose': 'Pengaturan', 'components': [
            {'testid': 'settings-panel', 'role': 'region', 'purpose': 'Panel pengaturan'},
            {'testid': 'settings-status', 'role': 'status', 'purpose': 'Status pengaturan',
             'scope_testid': 'settings-panel'},
        ],
    })
    return document


def test_component_scope_cannot_cross_screens():
    document = two_screens()
    document['screens'][0]['components'][3]['scope_testid'] = 'settings-panel'
    with pytest.raises(ValidationError, match='same screen'):
        UiSpec.model_validate(document)


def test_state_component_must_belong_to_its_screen():
    document = two_screens()
    document['states'][0]['testid'] = 'settings-status'
    with pytest.raises(ValidationError, match='same screen'):
        UiSpec.model_validate(document)


def test_multiple_screens_allow_scopes_and_states_on_their_own_screen():
    document = two_screens()
    document['states'][0]['testid'] = 'catalog-title'
    document['states'].append({'screen': 'settings', 'kind': 'success',
                               'testid': 'settings-status', 'copy_key': 'catalog.title'})
    parsed = UiSpec.model_validate(document)
    assert parsed.to_ui_contract().controls[-1].scope_testid == 'settings-panel'


def technical(*controls, revision=1):
    return UiContract.model_validate({'revision': revision, 'controls': list(controls)})


def test_technical_lead_may_only_add_controls_without_new_wording():
    parsed = UiSpec.model_validate(SPEC)
    wrapper = {'testid': 'catalog-panel', 'role': 'region', 'purpose': 'instrumentation container'}
    merged = add_technical_controls(parsed, technical(wrapper))
    assert [c.testid for c in merged.controls][-1] == 'catalog-panel'
    # Restating a spec control verbatim is harmless.
    same = parsed.to_ui_contract().controls[2].model_dump()
    assert len(add_technical_controls(parsed, technical(same, wrapper)).controls) == len(merged.controls)
    with pytest.raises(ValueError, match='cannot redefine'):
        add_technical_controls(parsed, technical({**same, 'name': 'Simpan'}))
    with pytest.raises(ValueError, match='outside the UI spec copy'):
        add_technical_controls(parsed, technical({**wrapper, 'role': 'button', 'name': 'Kirim'}))
    reused = {'testid': 'title-text', 'text': 'Katalog Buku', 'purpose': 'reuses spec copy'}
    assert add_technical_controls(parsed, technical(reused)).controls[-1].text == 'Katalog Buku'
