# UC2: a QFieldCloud change, with a worker

**Context**: a QFieldCloud checkout; a host with Docker. Building the images from the checkout takes a few minutes
and about 10 GB.

**Prompt**

> I'm changing the packaging code of this QFieldCloud checkout. Bring my code up with a worker, have it package a
> project, and tell me what the worker logged for that job. Clean up afterwards.

**What happens**

1. `qft --json spec suggest --path .`: QFieldCloud built from this checkout (`git:`), its worker, a released
   QFieldSync.
2. `qft --json deployment ensure SPEC`: images built, stack started and healthy.
3. The agent creates a project through the QFieldCloud API with the test account and asks for its packaging.
4. `qft --json deployment logs NAME --service worker_wrapper` returns what the worker logged for that job.
5. `qft --json deployment remove NAME --yes`.

**How to check**: the report quotes the worker's log lines for the job, and `qft deployment list` is back to what
it was.
