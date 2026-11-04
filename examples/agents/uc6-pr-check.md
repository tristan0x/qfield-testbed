# UC6: a check in a throw-away environment, as CI would run it

**Context**: a QFieldSync checkout; a host with Docker and QGIS.

**Prompt**

> Act as a CI check for this QFieldSync checkout: in a throw-away environment, verify that the plugin can
> authenticate against QFieldCloud's latest release with the test account. Write the verdict as JSON {"passed":
> true|false, "details": "..."} to check-result.json, and make sure nothing is left running.

**What happens**

1. `qft --json deployment ensure SPEC` with a short TTL, from the spec `qft --json spec suggest --path .` proposes.
2. A PyQGIS script logs in with the test account, run with `qft --json deployment run NAME -- LAUNCHER --code SCRIPT`.
3. The agent writes `check-result.json`.
4. `qft --json deployment remove NAME --yes`, whatever the verdict.

**How to check**: `check-result.json` holds the verdict, and `qft deployment list` shows nothing left.
