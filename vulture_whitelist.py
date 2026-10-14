"""Names vulture reports as unused although something outside the code reads them.

Listed in `[tool.vulture] paths`; a name only goes here with the reason it is used.
"""

from qfield_testbed.bisect.scenario import BisectScenario
from qfield_testbed.provision.qfield_settings import _CaseSensitiveParser
from qfield_testbed.spec import DeploymentSpec

# fields of the YAML files people write: accepted so that `extra="forbid"` does not reject them
BisectScenario.description  # noqa: B018
DeploymentSpec.description  # noqa: B018

# configparser calls this hook to normalise option names
_CaseSensitiveParser.optionxform  # noqa: B018
