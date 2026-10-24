"""Side-effect-free environment parsing, shared by startup and diagnostics."""
from __future__ import annotations

from pathlib import Path

from dotenv.parser import parse_stream
from dotenv.variables import parse_variables


def read_env_file(path: Path, *, environment: dict[str, str], interpolate: bool) -> tuple[dict, list[str]]:
    values: dict[str, str | None] = {}
    errors = []
    try:
        with path.open(encoding="utf-8") as stream:
            for binding in parse_stream(stream):
                if binding.error:
                    errors.append(f"第 {binding.original.line} 行格式无效")
                elif binding.key is not None:
                    value = binding.value
                    if interpolate and value is not None:
                        scope = {**values, **environment}
                        value = "".join(atom.resolve(scope) for atom in parse_variables(value))
                    values[binding.key] = value
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError):
        errors.append("文件无法读取")
    return values, errors


def load_environment(root: Path, process: dict[str, str]) -> tuple[dict, dict, list[str]]:
    disabled = {"1", "true", "t", "yes", "y"}
    if process.get("PYTHON_DOTENV_DISABLED", "").casefold() in disabled:
        return dict(process), dict.fromkeys(process, "process"), []
    base, base_errors = read_env_file(root / ".env", environment=process, interpolate=True)
    base_process = {**{key: value for key, value in base.items() if value is not None}, **process}
    if base_process.get("PYTHON_DOTENV_DISABLED", "").casefold() in disabled:
        runtime, runtime_errors = {}, []
    else:
        runtime, runtime_errors = read_env_file(root / "data/settings.env", environment={}, interpolate=False)
    values = {key: value for key, value in base.items() if value is not None}
    sources = dict.fromkeys(values, ".env")
    values.update(process)
    sources.update(dict.fromkeys(process, "process"))
    for key, value in runtime.items():
        if value is not None:
            values[key], sources[key] = value, "data/settings.env"
    errors = [f".env：{message}" for message in base_errors]
    errors.extend(f"data/settings.env：{message}" for message in runtime_errors)
    return values, sources, errors
