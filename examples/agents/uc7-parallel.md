# UC7: two agents on one host

**Context**: a QFieldSync checkout, and two agent sessions on the same host at the same time.

**Prompt**, given to each session:

> Set up a QFieldCloud (latest release) with QGIS and this QFieldSync checkout, confirm it is running and tell me
> its URL. Then remove what you created — and only that: other people may be using this machine.

**What happens**

1. Each session runs `qft --json deployment ensure SPEC`: each gets its own deployment, slot and ports, owned by
   its agent session.
2. Each reports its own URL.
3. Each runs `qft --json deployment remove NAME --yes` on its own deployment only. Removing the other one would be
   refused (`REFUSED`, exit 8) without `--force`, which an agent does not pass on its own.

**How to check**: two different URLs were reported, and `qft deployment list --owner agent` is empty afterwards.
