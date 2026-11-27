"""Load immutable upstream wrapper code without JobService or Qt."""

import importlib.util
import re
import shlex
import sys
from pathlib import Path

UPSTREAM_REVISION = "a66d8d593f6b9ff202f01cd6029ea4cd6a8246fb"


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


_TAG = re.compile(r"\{(\w+)\}|\[(\w+)\]")
#: Characters that may follow a placeholder and still belong to its word, so
#: ``{stem}.out`` is quoted as one ``'my mol.out'``. PowerShell reads a quoted
#: string followed by ``.out`` as a member access: ``'input'.out`` is $null,
#: and redirecting to it discards the output. A comma is PowerShell's array
#: operator and a backslash is the POSIX escape, so each stays outside there.
_POSIX_WORD = re.compile(r"[\w@%+=:,./-]*", re.ASCII)
_WINDOWS_WORD = re.compile(r"[\w%+=:./\\-]*", re.ASCII)
#: A word PowerShell passes on as it is, the counterpart of what shlex.quote
#: leaves bare. It starts with a letter, digit or underscore so it is never a
#: parameter, splat, dot-source or call.
_WINDOWS_BARE = re.compile(r"\w[\w%+=:./\\-]*", re.ASCII)


def _quote_windows(word):
    return word if _WINDOWS_BARE.fullmatch(word) else "'" + word.replace("'", "''") + "'"


def render_command(template, values, windows=False):
    quote = _quote_windows if windows else shlex.quote
    word_chars = _WINDOWS_WORD if windows else _POSIX_WORD

    def tag(match):
        return match.group(1) or match.group(2)

    known = [match for match in _TAG.finditer(template) if tag(match) in values]
    markers = {match.start() for match in known}
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
    # Each placeholder, with the literal characters and placeholders joined to
    # it, becomes one quoted word.
    rendered, position, index = [], 0, 0
    while index < len(known):
        rendered.append(template[position : known[index].start()])
        word, end = str(values[tag(known[index])]), known[index].end()
        index += 1
        while True:
            literal = word_chars.match(template, end).group(0)
            end += len(literal)
            word += literal
            if index < len(known) and known[index].start() == end:
                word += str(values[tag(known[index])])
                end = known[index].end()
                index += 1
            else:
                break
        rendered.append(quote(word))
        position = end
    rendered.append(template[position:])
    return "".join(rendered)


def build_wrapper(spec, workdir, windows=False, setup_commands=()):
    preset_type, shell, powershell = execution_core()
    files = spec["files"]
    input_name = files[0]["path"] if files else ""
    from ._execution.schedulers.base import (
        placeholder_values,
        references_input,
        retired_tags_message,
    )

    retired = retired_tags_message(spec["command"])
    if retired:
        raise ValueError(retired)
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
    # The administrator's setup lines are not a template, so they keep any literal braces.
    rendered = "\n".join(
        [*setup_commands, render_command(spec["command"], values, windows)]
    )
    # Upstream substitution is bypassed after quoting, including literal braces in filenames.
    marker = "__MOLEDITPY_SERVER_PAYLOAD__"
    preset.command_template = marker
    wrapper = (powershell if windows else shell).build_script(
        spec["name"], preset, "", "job.log", remote_dir=str(workdir)
    )
    replacement = rendered
    if windows:
        # The upstream inline payload loses `exit N` in finally and non-terminating
        # cmdlet errors can report success. A separate payload preserves both.
        replacement = (
            "& powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File './payload.ps1'"
        )
    # Replace the standalone payload line only. The command may be '$null' or
    # '0', which also occurs in the supervisor, and the marker can be a job name.
    wrapper, substitutions = re.subn(
        rf"(?m)^([ \t]*){re.escape(marker)}([ \t]*\r?)$",
        lambda match: match.group(1) + replacement + match.group(2),
        wrapper,
    )
    if substitutions != 1:
        raise RuntimeError("Upstream wrapper payload boundary changed")
    if windows:
        return (
            wrapper,
            "$ErrorActionPreference = 'Stop'\n"
            + "$PSDefaultParameterValues['Out-File:Encoding'] = 'ascii'\n"
            + rendered
            + "\nif ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }\n",
        )
    return wrapper, None
