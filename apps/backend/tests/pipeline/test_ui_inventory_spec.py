"""Static spec inventory (DEV-022): testids, copy literals and color tokens in the build.

Presence checks on local files only; never browser acceptance or QA evidence.
"""
import json
import pytest

from app.agents.ui_spec import UiSpec
from app.pipeline.ui_inventory import check_build, copy_fragments
from tests.agents.test_ui_spec import SPEC

SPEC_MODEL = UiSpec.model_validate(SPEC)
TESTIDS = [c.testid for c in SPEC_MODEL.components]
COPY = SPEC_MODEL.copy_map()


def bundle(tmp_path, *, drop_copy=(), drop_color=None, css=True):
    site = tmp_path / 'dist'
    (site / 'assets').mkdir(parents=True)
    html = ''.join(f'<div data-testid="{testid}"></div>' for testid in TESTIDS)
    (site / 'index.html').write_text(f'<html><body>{html}<h1>{COPY["catalog.title"]}</h1></body></html>')
    texts = {key: text for key, text in COPY.items() if key not in drop_copy and key != 'catalog.title'}
    # Bundlers emit JSON-escaped strings and template literals for placeholders.
    script = 'const copy = ' + json.dumps({k: v.replace('{title}', '${title}') for k, v in texts.items()}) + ';'
    (site / 'assets' / 'app.js').write_text(script)
    colors = [c.value for c in SPEC_MODEL.tokens.colors if c.name != drop_color]
    rules = ''.join(f'.c{i}{{color:{value.lower()}}}' for i, value in enumerate(colors))
    (site / 'assets' / ('app.css' if css else 'app.txt')).write_text(rules)
    return site


def test_spec_build_passes_with_testids_copy_and_tokens(tmp_path):
    result = check_build(bundle(tmp_path), SPEC_MODEL.to_ui_contract(), SPEC_MODEL)
    assert result['status'] == 'passed' and result['qa_pass'] is False
    assert result['missing_copy_keys'] == [] and result['missing_color_tokens'] == []


def test_missing_copy_and_color_tokens_are_reported(tmp_path):
    site = bundle(tmp_path, drop_copy=('book.delete_confirm',), drop_color='danger')
    result = check_build(site, SPEC_MODEL.to_ui_contract(), SPEC_MODEL)
    assert result['status'] == 'failed'
    assert result['missing_copy_keys'] == ['book.delete_confirm']
    assert result['missing_color_tokens'] == ['danger']


def test_colors_in_non_css_assets_are_not_counted(tmp_path):
    result = check_build(bundle(tmp_path, css=False), SPEC_MODEL.to_ui_contract(), SPEC_MODEL)
    assert set(result['missing_color_tokens']) == {c.name for c in SPEC_MODEL.tokens.colors}


def test_placeholder_copy_requires_each_literal_fragment():
    assert copy_fragments('Buku "{title}" ditambahkan') == ['Buku "', '" ditambahkan']
    assert copy_fragments('{title}') == []


def test_without_a_spec_only_testids_are_checked(tmp_path):
    result = check_build(bundle(tmp_path, drop_copy=tuple(COPY)), SPEC_MODEL.to_ui_contract())
    assert result['status'] == 'passed' and result['missing_copy_keys'] == []


def test_single_character_copy_is_required(tmp_path):
    document = SPEC_MODEL.model_dump()
    document['copy'].append({'key': 'book.icon', 'text': '✓'})
    model = UiSpec.model_validate(document)
    site = bundle(tmp_path)
    assert check_build(site, model.to_ui_contract(), model)['missing_copy_keys'] == ['book.icon']
    with (site / 'assets' / 'app.js').open('a') as stream:
        stream.write('const icon = "✓";')
    assert check_build(site, model.to_ui_contract(), model)['status'] == 'passed'


@pytest.mark.parametrize('value, emitted', [('#FFFFFF', '#fff'), ('#AA33CC', '#a3c'),
                                                        ('#FF0000', 'red'), ('#808080', 'gray')])
def test_minified_color_keeps_its_token_value(tmp_path, value, emitted):
    document = SPEC_MODEL.model_dump()
    document['tokens']['colors'][0]['value'] = value
    model = UiSpec.model_validate(document)
    site = bundle(tmp_path, drop_color='background')
    with (site / 'assets' / 'app.css').open('a') as stream:
        stream.write(f'.background{{color:{emitted}}}')
    assert check_build(site, model.to_ui_contract(), model)['status'] == 'passed'


def test_transparent_color_does_not_prove_an_opaque_token(tmp_path):
    site = bundle(tmp_path, drop_color='background')
    with (site / 'assets' / 'app.css').open('a') as stream:
        stream.write('.background{color:#F7F5EF00}')
    assert check_build(site, SPEC_MODEL.to_ui_contract(), SPEC_MODEL)['missing_color_tokens'] == ['background']


def test_copy_escaped_inside_a_single_quoted_javascript_string(tmp_path):
    document = SPEC_MODEL.model_dump()
    document['copy'].append({'key': 'book.quote', 'text': 'Buku "Laskar" milik D\'Artagnan'})
    model = UiSpec.model_validate(document)
    site = bundle(tmp_path)
    with (site / 'assets' / 'app.js').open('a') as stream:
        stream.write("const quote = 'Buku \"Laskar\" milik D\\'Artagnan';")
    assert check_build(site, model.to_ui_contract(), model)['status'] == 'passed'


@pytest.mark.parametrize('copy, emitted', [('Café baru', r'Caf\xE9 baru'),
                                         ('Buku “Laskar”', r'Buku \u201CLaskar\u201D'),
                                         ('Buku 📚', r'Buku \uD83D\uDCDA'),
                                         ('Buku 📚', r'Buku \u{1F4DA}')])
def test_copy_survives_javascript_unicode_escape_spellings(tmp_path, copy, emitted):
    document = SPEC_MODEL.model_dump()
    document['copy'].append({'key': 'book.unicode', 'text': copy})
    model = UiSpec.model_validate(document)
    site = bundle(tmp_path)
    with (site / 'assets' / 'app.js').open('a') as stream:
        stream.write('const copy = "' + emitted + '";')
    assert check_build(site, model.to_ui_contract(), model)['status'] == 'passed'
