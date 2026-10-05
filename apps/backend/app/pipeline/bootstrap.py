"""Bounded reference lockfile generation, without executing npm or target code on the host."""
import copy
import json
from pathlib import Path

from app.workspace.manifest import digest_of
from app.workspace.dependencies import registry_tarballs

CATALOG = Path(__file__).resolve().parents[4] / 'contracts/bootstrap/react-vite'


def reference_catalog():
    package = json.loads((CATALOG / 'package.json').read_text())
    lock = json.loads((CATALOG / 'package-lock.json').read_text())
    registry_tarballs(lock)  # Require public HTTPS tarballs and SHA512 for every pinned package.
    versions = {**package.get('dependencies', {}), **package.get('devDependencies', {})}
    return versions, lock


def bootstrap_contract():
    versions, lock = reference_catalog()
    return {'kind': 'react-vite-reference-v1', 'versions': versions, 'catalog_digest': digest_of(lock),
            'instructions': 'Create package.json with these exact dependency versions. Then call '
            'run_command phase bootstrap to generate package-lock.json; do not write a lockfile by hand. '
            'Next call install, test and build. Additional packages/versions need a qualified runner catalog.'}


def generate_lock(package):
    """Adapt the vetted lock root; retain the bounded reference closure and integrity pins."""
    versions, template = reference_catalog()
    if not isinstance(package, dict):
        raise ValueError('package.json must be an object')
    unsupported = set(package) & {'workspaces', 'overrides', 'optionalDependencies', 'peerDependencies', 'bundledDependencies', 'bundleDependencies'}
    if unsupported:
        raise ValueError('reference bootstrap does not support: ' + ', '.join(sorted(unsupported)))
    root = {}
    for key in ('name', 'version'):
        value = package.get(key)
        if value is not None:
            if not isinstance(value, str) or not value or len(value) > 214:
                raise ValueError(f'package.json {key} must be a non-empty string')
            root[key] = value
    for section in ('dependencies', 'devDependencies'):
        deps = package.get(section, {})
        if not isinstance(deps, dict):
            raise ValueError(f'package.json {section} must be an object')
        for name, version in deps.items():
            if name not in versions or version != versions[name]:
                raise ValueError(f'unsupported bootstrap dependency {name}; use exact reference versions: {versions}')
        if deps:
            root[section] = deps
    lock = copy.deepcopy(template)
    lock['packages'][''] = root
    for key in ('name', 'version'):
        lock.pop(key, None)
        if key in root:
            lock[key] = root[key]
    return lock
