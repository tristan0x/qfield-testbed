# UC1: a QFieldSync change, checked end to end

**Context**: a QFieldSync checkout; a host with Docker and QGIS (`qft doctor` shows `qfieldcloud` and `qgis:host`).

**Prompt**

> I'm working on this QFieldSync checkout. Check end to end that, with my code, QGIS can log in to a QFieldCloud
> running the latest release and list the cloud projects of the test user. Run that check for real, tell me the
> result, and clean up everything afterwards.

**What happens**

1. `qft --json doctor`: the host can run QFieldCloud and QGIS.
2. `qft --json spec suggest --path .`: QFieldCloud at its latest release, QGIS, and this checkout as
   `qfieldsync: worktree:.`, live.
3. `qft --json deployment ensure SPEC`: the deployment is created, started and healthy.
4. The agent writes a PyQGIS script in the deployment's `scratch/` directory and runs it with
   `qft --json deployment run NAME -- LAUNCHER --code SCRIPT`: log in with the test account, list the projects.
5. `qft --json deployment remove NAME --yes`.

**How to check**: the report names the projects of the test user (or the error QGIS gave), and
`qft deployment list` no longer shows the deployment.
