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
            'instructions': 'Create package.json using only the dependencies needed by the app and runner, '
            'at these exact versions. React and its plugin are optional for vanilla JS; the configured '
            'Vite build/preview still requires Vite. Then call '
            'run_command phase bootstrap to generate package-lock.json; do not write a lockfile by hand. '
            'Next call install, test and build. Additional packages/versions need a qualified runner catalog.'}


def generate_lock(package):
    """Adapt the vetted root and prune unreachable packages; preserve integrity pins."""
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
    entries = lock['packages']

    def resolve(owner, name):
        # npm's nearest installed dependency, including nested/scoped packages.
        prefix = owner
        while prefix:
            candidate = prefix + '/node_modules/' + name
            if candidate in entries:
                return candidate
            prefix = prefix.rpartition('/node_modules/')[0] if '/node_modules/' in prefix else ''
        candidate = 'node_modules/' + name
        return candidate if candidate in entries else None

    pending = [resolve('', name) for section in ('dependencies', 'devDependencies')
               for name in root.get(section, {})]
    reachable = set()
    while pending:
        path = pending.pop()
        if path is None:
            raise ValueError('required dependency is missing from the reference catalog')
        if path in reachable:
            continue
        reachable.add(path)
        entry = entries[path]
        optional = entry.get('optionalDependencies', {})
        required = set(entry.get('dependencies', {})) - set(optional)
        peer_meta = entry.get('peerDependenciesMeta', {})
        for name in entry.get('peerDependencies', {}):
            if not peer_meta.get(name, {}).get('optional', False):
                required.add(name)
            elif resolve(path, name) is not None:
                pending.append(resolve(path, name))
        for name in required:
            dependency = resolve(path, name)
            if dependency is None:
                raise ValueError(f'reference catalog is missing required dependency {name} of {path}')
            pending.append(dependency)
        for name in optional:
            dependency = resolve(path, name)
            if dependency is not None:
                pending.append(dependency)
    lock['packages'] = {path: entry for path, entry in entries.items() if path in reachable}
    lock['packages'][''] = root
    for key in ('name', 'version'):
        lock.pop(key, None)
        if key in root:
            lock[key] = root[key]
    return lock
