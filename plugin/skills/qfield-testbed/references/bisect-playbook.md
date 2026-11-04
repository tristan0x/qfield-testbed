# Bisect playbook

Find the first revision of **one** component (`qfieldcloud`, `qfieldsync` or `qfield-android`) that breaks a
test, the other components staying fixed.

1. Write a test script that reproduces the bug against `$QFT_QFIELDCLOUD_URL` / `$QFT_QGIS_LAUNCHER` / … and exits
   like `git bisect run`: **0 good, 125 skip (untestable candidate), 1–124 bad, ≥ 128 aborts the session**.
   Make it fail for the reason of the bug only; a setup error should exit 125 or ≥ 128, not 1.
   Write it with your file tool (not a heredoc), then `chmod +x` it as a command of its own.
2. Start from `qft bisect example`, set `deployment:` (fixed components), `bisect:` (component, `good`, `bad`,
   `candidates: first-parent | tags:"v26.*" | [list]`, `source: git | release | artifact`) and `test.script`.
   A relative `test.script` is resolved **from the scenario file's directory**; keep both files side by side
   (`script: ./repro.sh`) and make the script executable.
   `qft spec schema --scenario` is its JSON Schema; `bisect start` reports an invalid scenario with path and line.
3. `qft --json bisect start scenario.yaml`, then `qft --json bisect run` (resumes where it stopped).
   Each step swaps the candidate in the deployment `bs-<name>` and runs the script with `QFT_BISECT_COMPONENT`,
   `QFT_BISECT_INDEX`, `QFT_BISECT_CANDIDATE`, `QFT_BISECT_SHA` set.
4. `qft --json bisect status` and `qft bisect log` give the verdicts and each step's output; report the first bad
   candidate with its sha and the log of that step.
5. `qft bisect remove NAME` deletes the session and its deployment.

`source: release` (tags) is much faster than `git` (one image build per candidate): bisect releases first, then
the commits between the two releases. Details: `qft docs bisect`.
