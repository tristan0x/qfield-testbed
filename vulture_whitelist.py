"""Names vulture reports as unused although something outside the code reads them.

Listed in `[tool.vulture] paths`; a name only goes here with the reason it is used.
"""

from types import SimpleNamespace

from qfield_testbed.bisect.scenario import BisectScenario
from qfield_testbed.provision.qfield_settings import _CaseSensitiveParser
from qfield_testbed.runtimes.shell import ProcessResult
from qfield_testbed.spec import DeploymentSpec

# fields of the YAML files people write: accepted so that `extra="forbid"` does not reject them
BisectScenario.description  # noqa: B018
DeploymentSpec.description  # noqa: B018

# configparser calls this hook to normalise option names
_CaseSensitiveParser.optionxform  # noqa: B018

# serialized into the JSON result of `qft deployment run` and the details of its timeout error
ProcessResult.stdout_tail  # noqa: B018
ProcessResult.stderr_tail  # noqa: B018

# MCP tools: registered by the @mcp.tool decorator, called by MCP clients by name
_mcp_tools = SimpleNamespace()
_mcp_tools.testbed_doctor  # noqa: B018
_mcp_tools.bisect_step  # noqa: B018
_mcp_tools.bisect_status  # noqa: B018
_mcp_tools.job_logs  # noqa: B018
_mcp_tools.job_list  # noqa: B018

# serialized into job_status answers
_mcp_tools.started_at  # noqa: B018
_mcp_tools.finished_at  # noqa: B018
