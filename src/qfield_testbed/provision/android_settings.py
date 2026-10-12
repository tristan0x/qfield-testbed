"""Pre-filling QField's QFieldCloud settings on an Android device through adb (as root).

QField stores its settings with QSettings (INI format on Android) under the app's private data directory,
organization ``OPENGIS.ch``, application ``QField`` (``files/settings/OPENGIS.ch/QField.conf``): ``[QFieldCloud] url=…``
is what the login screen reads.
"""

from __future__ import annotations

import logging
import time

from qfield_testbed.provision.qfield_settings import CONF_RELATIVE_PATH, manual_steps, merge_qfieldcloud_section
from qfield_testbed.runtimes.adb import Adb

log = logging.getLogger(__name__)

PACKAGE = "ch.opengis.qfield"
# where QField 4.x keeps QSettings on Android (observed on a release APK); a `find` confirms it at run time
DEFAULT_CONF = f"/data/data/{PACKAGE}/files/settings/{CONF_RELATIVE_PATH}"


def seed_qfield_settings(adb: Adb, url: str, username: str) -> str | None:
    """Write the QFieldCloud URL and username into QField's settings; returns the file path or None."""
    if not adb.root() and "root" not in adb.su("id").stdout:
        log.warning("no root access on the emulator; QField settings cannot be pre-filled")
        return None
    # a first launch creates the app's data directories (and possibly a settings file)
    adb.launch(PACKAGE)
    time.sleep(6)
    adb.force_stop(PACKAGE)
    found = adb.find_files(f"/data/data/{PACKAGE}", "QField.conf")
    conf = found[0] if found else DEFAULT_CONF
    existing = adb.read_file(conf) if found else ""
    content = merge_qfieldcloud_section(existing, url, username)
    adb.write_file(conf, content, owner=adb.package_user(PACKAGE))
    log.info("QField settings seeded in %s", conf)
    return conf


__all__ = ["DEFAULT_CONF", "PACKAGE", "manual_steps", "merge_qfieldcloud_section", "seed_qfield_settings"]
