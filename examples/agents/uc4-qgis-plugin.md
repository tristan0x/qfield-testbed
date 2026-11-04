# UC4: any QGIS plugin, in a clean profile

**Context**: the checkout of a QGIS plugin; a host with QGIS or Docker. No QFieldCloud is needed.

**Prompt**

> Check that this QGIS plugin loads in a clean QGIS profile without errors, and report what QGIS says about it.
> Don't touch my own QGIS profile, and clean up afterwards.

**What happens**

1. `qft --json spec suggest --path .`: QGIS with an isolated profile, and the checkout linked into it as a plugin.
2. `qft --json deployment ensure SPEC`.
3. The agent loads the plugin headless with
   `qft --json deployment run NAME -- LAUNCHER --code SCRIPT` and reads the QGIS messages.
4. `qft --json deployment remove NAME --yes`.

**How to check**: the report quotes what QGIS logged while loading the plugin; the user's own QGIS profile is
untouched.
