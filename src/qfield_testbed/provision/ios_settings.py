"""Pre-filling QField's QFieldCloud settings in the iOS Simulator (NSUserDefaults plist, edited with plutil)."""

from __future__ import annotations

import logging

from qfield_testbed.backends.ios_simulator import IosSimulatorBackend
from qfield_testbed.runtimes.ssh import Ssh

log = logging.getLogger(__name__)

BUNDLE = "ch.opengis.qfield"


def seed_qfield_settings(ssh: Ssh, backend: IosSimulatorBackend, udid: str, url: str, username: str) -> str | None:
    """Write ``QFieldCloud.url``/``QFieldCloud.username`` in the app's preferences; returns the plist path or None.

    Qt maps the QSettings key ``/QFieldCloud/url`` to the NSUserDefaults key ``QFieldCloud.url`` on Apple
    platforms. The app is launched once so the container exists, then stopped before the plist is edited.
    """
    backend.launch(udid, BUNDLE)
    ssh.run("sleep", "5", check=False)
    backend.terminate(udid, BUNDLE)
    container = backend.app_container(udid, BUNDLE)
    if not container:
        log.warning("QField has no data container in the simulator yet; settings not pre-filled")
        return None
    plist = f"{container}/Library/Preferences/{BUNDLE}.plist"
    for key, value in (("QFieldCloud.url", url), ("QFieldCloud.username", username), ("QFieldCloud.urls", url)):
        if ssh.run("plutil", "-replace", key, "-string", value, plist, check=False).returncode != 0:
            # a missing plist: create it with the first key
            created = ssh.run("plutil", "-create", "xml1", plist, check=False).returncode == 0
            if not created or ssh.run("plutil", "-replace", key, "-string", value, plist, check=False).returncode != 0:
                log.warning("cannot write %s in %s", key, plist)
                return None
    return plist
