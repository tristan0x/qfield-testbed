"""qfield-testbed: sandboxed deployments of the QField stack, and bisect sessions across their versions.

Vocabulary follows ``docs/glossary.md``: a *deployment* runs *components* (QFieldCloud, QGIS, QFieldSync,
QField on Android/iOS/desktop), each materialized from a *version source* into a *revision*; a *bisect
session* swaps one component's revision until the first bad *candidate* is found.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("qfield-testbed")
except PackageNotFoundError:  # pragma: no cover - source checkout without install
    __version__ = "0.0.0"
