"""The identity of a spec: what `qft deployment ensure` compares to reuse a deployment or refuse a different one.

Two specs are the same deployment when their *normalized* forms are equal: every option with its default, keys
in order, and moving refs (`git:<ref>`, `artifact:<ref>`) replaced by the commit they point to now. Release tags,
paths and Docker tags are taken as written; the name and the description are not part of it. The QGIS installed
on the host is not either: `ensure` only warns when its version changed.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from typing import TYPE_CHECKING, Any

import yaml

from qfield_testbed.spec import ArtifactSource, DeploymentSpec, GitSource

if TYPE_CHECKING:
    from qfield_testbed.deployment import Testbed

IGNORED_KEYS = ("name", "description")


def normalize(testbed: Testbed, spec: DeploymentSpec) -> dict[str, Any]:
    from qfield_testbed.deployment import Deployment  # noqa: PLC0415 - the orchestrator imports this module

    data = spec.model_dump(mode="json", by_alias=True)
    for key in IGNORED_KEYS:
        data.pop(key, None)
    for name, component_spec in spec.components().items():
        version = getattr(component_spec, "version", None)
        if isinstance(version, GitSource | ArtifactSource):
            revision = Deployment.probe(testbed, spec, name, component_spec).resolve()
            data[name]["version"] = {"kind": version.kind, "ref": revision.sha or version.ref}
    return data


def spec_hash(normalized: dict[str, Any]) -> str:
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def spec_diff(old: dict[str, Any], new: dict[str, Any]) -> str:
    """What changed between two normalized specs, as a unified diff of their YAML."""
    before = yaml.safe_dump(old, sort_keys=True).splitlines(keepends=True)
    after = yaml.safe_dump(new, sort_keys=True).splitlines(keepends=True)
    return "".join(difflib.unified_diff(before, after, fromfile="existing", tofile="requested"))
