# UC3: QField on the Android emulator

**Context**: a QField checkout; a host with Docker and `/dev/kvm` (`qft doctor` shows `qfield-android`).

**Prompt**

> I work on this QField checkout. With the latest QField release on Android (don't build mine), check that the
> app starts in an emulator next to a QFieldCloud and is pointed at it, then tell me how I could look at the
> emulator. Clean up afterwards.

**What happens**

1. `qft --json spec suggest --path . --need release`: the latest QField release on the emulator rather than a
   build of the checkout, next to a QFieldCloud of the same deployment.
2. `qft --json deployment ensure SPEC`: the emulator boots, the APK is installed and seeded with the
   QFieldCloud URL and the test account.
3. The agent checks the app with the `adb` command the deployment gives (`data.components.qfield-android.adb`).
4. It points at the launcher, `qft deployment launch NAME qfield-android`, to watch the emulator.
5. `qft --json deployment remove NAME --yes`.

**How to check**: the report gives the QField version and the QFieldCloud URL the app uses, and how to open the
emulator.
