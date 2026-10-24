from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from threading import RLock

from dotenv import dotenv_values
from app.services.process_lock import settings_file_lock


# Keep thread reentrancy as well as the per-file interprocess lock.
runtime_settings_lock = RLock()


def read_runtime_env(path: Path) -> dict:
    with runtime_settings_lock, settings_file_lock(path):
        return dict(dotenv_values(path, interpolate=False)) if path.is_file() else {}


def _dotenv_quote(value: object) -> str:
    text = str(value)
    if any(character in text for character in ("\r", "\n", "\x00")):
        raise ValueError("配置值不能包含换行或空字符")
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def write_runtime_env(path: Path, values: dict[str, object]) -> None:
    with runtime_settings_lock, settings_file_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        original = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        key_pattern = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=")
        written: set[str] = set()
        output: list[str] = []
        for line in original:
            match = key_pattern.match(line)
            key = match.group(1) if match else None
            if key in values:
                output.append(f"{key}={_dotenv_quote(values[key])}")
                written.add(key)
            else:
                output.append(line)
        if output and output[-1] != "":
            output.append("")
        if not original:
            output.extend([
                "# 系统管理的运行配置：自动化/SMTP 热生效，数据库参数重启生效。",
                "# 包含密码，不要提交到代码库或对外发送。", "",
            ])
        for key, value in values.items():
            if key not in written:
                output.append(f"{key}={_dotenv_quote(value)}")
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                            prefix=".settings-", suffix=".tmp", delete=False) as handle:
                temporary_path = Path(handle.name)
                handle.write("\n".join(output).rstrip() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
