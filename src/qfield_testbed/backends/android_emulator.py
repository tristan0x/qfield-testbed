"""The `android-emulator` backend: budtmo/docker-android on the deployment network, plus a socat relay.

The emulator runs inside its container, so from Android ``10.0.2.2`` is that container; the relay turns its
port 8000 into QFieldCloud's ``app:8000`` on the shared network. noVNC and adb are published on the slot's ports.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined

from qfield_testbed.ports import PortBlock
from qfield_testbed.runtimes import docker
from qfield_testbed.runtimes.adb import Adb
from qfield_testbed.runtimes.shell import Runner

_TEMPLATES = Environment(
    loader=PackageLoader("qfield_testbed", "assets/templates"),
    undefined=StrictUndefined,
    autoescape=False,  # noqa: S701 - renders compose YAML
    keep_trailing_newline=True,
)

QFIELDCLOUD_URL_FROM_ANDROID = "http://10.0.2.2:8000/"


@dataclass(frozen=True)
class AndroidEmulatorBackend:
    runner: Runner
    deployment: str
    directory: Path
    network: str
    ports: PortBlock

    @property
    def project(self) -> str:
        return f"qft-{self.deployment}-tb"

    @property
    def container(self) -> str:
        return f"{self.project}-android-1"

    @property
    def compose_file(self) -> Path:
        return self.directory / "compose.yml"

    @property
    def novnc_url(self) -> str:
        return f"http://127.0.0.1:{self.ports.port('novnc')}/?autoconnect=true"

    @property
    def adb_connect(self) -> str:
        return f"127.0.0.1:{self.ports.port('adb')}"

    def adb(self) -> Adb:
        return Adb(self.runner, self.container)

    def compose(self) -> docker.Compose:
        return docker.Compose(runner=self.runner, project=self.project, files=[self.compose_file])

    def render(self, *, image: str, device: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        template = _TEMPLATES.get_template("testbed.compose.yml.j2")
        self.compose_file.write_text(
            template.render(
                name=self.deployment,
                android={"image": image, "device": device},
                ports=self.ports.all_ports(),
                network=self.network,
            )
        )

    def start(self) -> None:
        docker.ensure_network(self.runner, self.network)
        self.compose().up()

    def stop(self) -> None:
        self.compose().stop()

    def down(self) -> None:
        self.compose().down(volumes=True)

    def is_running(self) -> bool:
        result = self.runner.run(["docker", "inspect", "--format", "{{.State.Running}}", self.container], check=False)
        return result.returncode == 0 and result.stdout.strip() == "true"
