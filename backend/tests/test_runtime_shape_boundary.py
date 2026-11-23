"""Keep request and service access on the declared runtime shapes."""
import ast
from os import fsdecode
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
APP = BACKEND / "app"
API = APP / "api"
MODULES = APP / "modules"
DATABASE_RUNTIME = APP / "runtime" / "database.py"
DATABASE_PACKAGE = "app.database"
REMOVED_MODULE = f"{DATABASE_PACKAGE}.workshop"
REMOVED_NAME = "Workshop" + "Repository"
REMOVED_REFERENCE = re.compile(rf"\b(?:{re.escape(REMOVED_MODULE)}|{REMOVED_NAME})\b")
RUNTIME_TYPES = {"DatabaseServices", "ApplicationRuntime"}


def _python_files():
    # Follow the repository's ignore rules for local release and build artifacts.
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.py"], cwd=ROOT,
    )
    for name in names.split(b"\0"):
        path = ROOT / fsdecode(name)
        if path.is_file():
            yield path


def _reference_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.rsplit(".", 1)[-1]
    return ""


def _removed_repository(node, path):
    if isinstance(node, ast.Import):
        return any(alias.name == REMOVED_MODULE for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if node.level and path.is_relative_to(BACKEND):
            package = path.parent.relative_to(BACKEND).parts
            prefix = package[:len(package) - node.level + 1]
            module = ".".join((*prefix, module)).rstrip(".")
        return (module == REMOVED_MODULE or
                any(alias.name == REMOVED_NAME or
                    (module == DATABASE_PACKAGE and alias.name == "workshop") for alias in node.names))
    if isinstance(node, (ast.Name, ast.Attribute)):
        return _reference_name(node) == REMOVED_NAME
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name == REMOVED_NAME
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return bool(REMOVED_REFERENCE.search(node.value))
    return False


def _api_probe(node, path):
    name = _reference_name(node.func)
    if (len(node.args) < 2 or not isinstance(node.args[1], ast.Constant)
            or not isinstance(node.args[1].value, str)):
        return False
    if name == "hasattr":
        return True
    if name != "getattr" or len(node.args) != 3:
        return False
    subject = ast.unparse(node.args[0])
    # Request-local values can be absent before first use.
    if subject == "request.state":
        return False
    # The workload logs accept callable instances as well as ordinary functions.
    if path == API / "workloads.py" and subject == "operation" and node.args[1].value == "__name__":
        return False
    return True


def _is_api(path: Path) -> bool:
    return path.is_relative_to(API) or (
        path.is_relative_to(MODULES)
        and (path.name in {"routes.py", "report_reads.py"} or path.name.endswith("_routes.py"))
    )


def _runtime_casts(tree, path):
    if _is_api(path):
        scope = tree
    elif path == DATABASE_RUNTIME:
        scope = next(node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == "database_context")
    else:
        return
    aliases = {"cast"} | {alias.asname for node in ast.walk(tree)
                          if isinstance(node, ast.ImportFrom) and node.module == "typing"
                          for alias in node.names if alias.name == "cast" and alias.asname}
    yield from (node for node in ast.walk(scope) if isinstance(node, ast.Call)
                and _reference_name(node.func) in aliases and node.args
                and _reference_name(node.args[0]) in RUNTIME_TYPES)


def test_runtime_shape_boundary():
    errors = []
    for path in sorted(_python_files()):
        tree = ast.parse(path.read_bytes(), filename=str(path))
        rel = path.relative_to(ROOT)
        for node in ast.walk(tree):
            if _removed_repository(node, path):
                errors.append(f"{rel}:{node.lineno}: removed repository")
            if _is_api(path) and isinstance(node, ast.Call) and _api_probe(node, path):
                errors.append(f"{rel}:{node.lineno}: runtime shape probe")
        errors.extend(f"{rel}:{node.lineno}: runtime shape cast" for node in _runtime_casts(tree, path))
    assert not errors, "\n".join(sorted(set(errors)))
