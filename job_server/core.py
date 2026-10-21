"""Load immutable upstream wrapper code without JobService or Qt."""

import importlib.util
import re
import shlex
import sys
from pathlib import Path

UPSTREAM_REVISION = "110e1e27797f27cd2896e0e9ec388dd940992713"


def execution_core():
    name = "job_server._execution"
    if name not in sys.modules:
        source = Path(__file__).parent / "_execution"
        if not source.exists():
            source = (
                Path(__file__).resolve().parents[1] / "upstream" / "job_manager" / "job_manager"
            )
        if not (source / "__init__.py").exists():
            raise RuntimeError("Initialize upstream submodule or install the built distribution")
        spec = importlib.util.spec_from_file_location(
            name, source / "__init__.py", submodule_search_locations=[str(source)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    from ._execution.models import SubmitPreset
    from ._execution.schedulers import SHELL, WINDOWS

    return SubmitPreset, SHELL, WINDOWS


def render_command(template, values, windows=False):
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'" if windows else shlex.quote(str(value))

    def replace(match):
        tag = match.group(1) or match.group(2)
        return quote(values[tag]) if tag in values else match.group(0)

    markers = {
        match.start()
        for match in re.finditer(r"\{(\w+)\}|\[(\w+)\]", template)
        if (match.group(1) or match.group(2)) in values
    }
    quoting, escaped = None, False
    for index, character in enumerate(template):
        if index in markers and (quoting or escaped):
            raise ValueError("Write placeholders without surrounding shell quotes or escapes")
        if escaped:
            escaped = False
        elif character == ("`" if windows else "\\") and quoting != "'":
            escaped = True
        elif character in {"'", '"'}:
            if quoting == character:
                quoting = None
            elif quoting is None:
                quoting = character
    return re.sub(r"\{(\w+)\}|\[(\w+)\]", replace, template)


def build_wrapper(spec, workdir, windows=False):
    preset_type, shell, powershell = execution_core()
    files = spec["files"]
    input_name = files[0]["path"] if files else ""
    from ._execution.schedulers.base import placeholder_values, references_input

    if not input_name and references_input(spec["command"]):
        raise ValueError("Input placeholders require an input file")
    resources = spec["resources"]
    preset = preset_type(
        command_template="",
        cpus_per_task=resources["cpus_per_task"],
        memory=str(resources["memory_mb"]),
        walltime=str(resources["walltime_seconds"]),
    )
    values = placeholder_values(input_name, preset, spec["name"], str(workdir))
    rendered = render_command(spec["command"], values, windows)
    # Upstream substitution is bypassed after quoting, including literal braces in filenames.
    marker = "__MOLEDITPY_SERVER_PAYLOAD__"
    preset.command_template = marker
    wrapper = (powershell if windows else shell).build_script(
        spec["name"], preset, "", "job.log", remote_dir=str(workdir)
    )
    wrapper = wrapper.replace(marker, rendered)
    if windows:
        # The upstream inline payload loses `exit N` in finally and non-terminating
        # cmdlet errors can report success. A separate payload preserves both.
        wrapper = wrapper.replace(
            rendered,
            "& powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File './payload.ps1'",
        )
        return (
            wrapper,
            "$ErrorActionPreference = 'Stop'\n"
            + "$PSDefaultParameterValues['Out-File:Encoding'] = 'ascii'\n"
            + rendered
            + "\nif ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }\n",
        )
    return wrapper, None
