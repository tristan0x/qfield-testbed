# Bisect

A **bisect session** finds the first bad **candidate** of one component, exactly like `git bisect`, while the
other components stay fixed. Each step **swaps** the varying component's revision inside the session's dedicated
deployment, waits for it to be healthy and wired, then runs the **test**.

## Scenario

```yaml
name: qfs-crash-on-qfc
deployment:                      # fixed components (same schema as a spec, without `name`)
  qgis: { version: host }
  qfieldsync: { version: worktree:~/src/github.com/opengisch/qfieldsync }
bisect:
  component: qfieldcloud         # the one that varies — exactly one per session
  good: v26.20                   # a ref known good
  bad: master                    # a ref known bad
  candidates: first-parent       # first-parent (default) | tags:"v26.*" | list: [ref, ref, …]
  source: git                    # git = build each candidate from a checkout | release = official images (tags only)
  reset: volumes                 # wipe the component's data on each swap (default) | none
test:
  script: ./tests/repro.sh       # exit 0 = good, 125 = skip, anything else = bad, ≥ 128 aborts the session
  timeout: 900
  # manual: true                 # instead of `script`: you are prompted good / bad / skip after each swap
```

Candidate rules: `first-parent` lists `git rev-list --first-parent good..bad` (one candidate per merged pull
request, which is what QFieldCloud's history looks like); `tags:<glob>` lists matching tags between `good` and
`bad` in creation order; `list` is explicit. Candidates that cannot be materialized (a CI artifact that
expired, a build failure) are skipped automatically.

## Running

```sh
qft bisect start scenario.yaml [--session NAME]   # creates the session and its deployment `qft-bs-<name>`
qft bisect run [NAME]                              # loops until the first bad candidate is known
qft bisect status [NAME]                           # candidates, verdicts, what comes next
qft bisect log [NAME]                              # test output of every step
qft bisect reset [NAME] [--remove-deployment]
```

For a manual test, `run` prepares a candidate, prints the deployment summary (`show`) and prompts; or step
from another terminal while QGIS stays open: `qft bisect next`, then `qft bisect good|bad|skip`.

Sessions are resumable: every verdict is written to `$QFT_HOME/bisect/<session>/state.json` before the next
swap, so `Ctrl-C` and `qft bisect run` again continues where it stopped.

## The test script

It runs with the session deployment's environment:

| Variable | Meaning |
|---|---|
| `QFT_DEPLOYMENT`, `QFT_DEPLOYMENT_DIR`, `QFT_NETWORK` | the deployment |
| `QFT_QFIELDCLOUD_URL`, `QFT_QFIELDCLOUD_URL_INTERNAL` | API base from the host / from the Docker network |
| `QFT_QFIELDCLOUD_USERNAME`, `QFT_QFIELDCLOUD_PASSWORD`, `QFT_QFIELDCLOUD_TOKEN` | the test account |
| `QFT_QGIS_LAUNCHER`, `QFT_QGIS_PROFILE` | QGIS with the deployment's profile |
| `QFT_QFIELD_ANDROID_ADB`, `QFT_QFIELD_ANDROID_QFIELDCLOUD_URL` | `adb` against the emulator; the URL as seen from Android |
| `QFT_REVISION_<COMPONENT>` | the revision under test |
| `QFT_BISECT_INDEX`, `QFT_SESSION_DIR` | step number and where to leave extra logs |

Exit codes follow `git bisect run`: 0 good, 125 skip, 1–124 and 126–127 bad, 128 and above abort.

## Report

When the search ends, `report.md` in the session directory (and the terminal) names the first bad revision
(sha, label, date, subject, GitHub link), lists skipped neighbours when the answer is ambiguous, and links the
logs. `qft bisect status --json` gives the same as data.

## More than one component

A session varies one component: good/bad is only monotone along one history, so a search over two histories
at once has no bisection structure. To find a pair, bisect sequentially — fix the first finding in
`deployment:` and start a second session on the other component — or enumerate hand-picked combinations with
`candidates: list`.
