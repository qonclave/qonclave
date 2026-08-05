"""
test_layering.py — enforce the dependency ladder from CONVENTIONS.md.

A layering rule that lives only in a document is a layering rule that gets broken during the first
tired refactor, and the breakage is invisible until someone installs `qonclave[edge]` on a sensor
and finds Flask in the dependency tree.

This walks the package with `ast` — no imports executed, so a missing optional dependency cannot
make the check pass vacuously.

Two rules:

1. A layer may import only from layers below it.
2. Role packages (edge, hub, compute, archive) may never import each other. A hub reaches a
   compute node over `transport`, not by importing `qonclave.compute` — that is what makes the
   Compute role genuinely optional.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "qonclave"

#: Ascending. Each layer may import from any layer strictly below it.
LAYERS: list[tuple[str, ...]] = [
    ("core",),
    ("transport", "security"),
    ("discovery",),
    ("placement",),
    ("inference", "storage"),
    ("edge", "hub", "compute", "archive"),
]

ROLES = {"edge", "hub", "compute", "archive"}

#: Top-level modules that sit above every layer and may import anything.
TOP_LEVEL_ANY = {"app", "cli"}

_RANK = {pkg: i for i, tier in enumerate(LAYERS) for pkg in tier}


def _package_of(path: pathlib.Path) -> str | None:
    rel = path.relative_to(SRC)
    return rel.parts[0] if len(rel.parts) > 1 else None


def _qonclave_imports(tree: ast.AST, own_package: str) -> set[str]:
    """Every qonclave subpackage this module imports, absolute or relative."""
    found: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level and node.level >= 2:
                # `from ..transport import x` — level 2 escapes the package, so the first
                # component of the module path is the sibling package being reached for.
                if node.module:
                    found.add(node.module.split(".")[0])
            elif node.level == 0 and node.module and node.module.startswith("qonclave"):
                parts = node.module.split(".")
                if len(parts) > 1:
                    found.add(parts[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("qonclave."):
                    parts = alias.name.split(".")
                    if len(parts) > 1:
                        found.add(parts[1])

    found.discard(own_package)
    return {f for f in found if f in _RANK}


def _modules() -> list[tuple[pathlib.Path, str]]:
    out = []
    for path in sorted(SRC.rglob("*.py")):
        pkg = _package_of(path)
        if pkg is not None and pkg in _RANK:
            out.append((path, pkg))
    return out


@pytest.mark.parametrize("path,package", _modules(), ids=lambda v: str(v))
def test_no_upward_imports(path: pathlib.Path, package: str) -> None:
    """A module may not import from its own layer's peers or from any layer above it."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    own_rank = _RANK[package]

    for imported in _qonclave_imports(tree, package):
        rank = _RANK[imported]
        assert rank < own_rank, (
            f"{path.relative_to(SRC)} is in layer '{package}' (rank {own_rank}) but imports "
            f"'{imported}' (rank {rank}). Imports must go strictly downward — see CONVENTIONS.md."
        )


@pytest.mark.parametrize("path,package", _modules(), ids=lambda v: str(v))
def test_roles_never_import_siblings(path: pathlib.Path, package: str) -> None:
    """edge/hub/compute/archive are peers and must not reach for one another."""
    if package not in ROLES:
        pytest.skip("not a role package")

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    siblings = _qonclave_imports(tree, package) & ROLES

    assert not siblings, (
        f"{path.relative_to(SRC)} is the '{package}' role but imports the sibling role(s) "
        f"{sorted(siblings)}. Roles communicate over `transport`, never by import — this is what "
        f"keeps the Compute and Archive roles optional."
    )


def test_core_imports_nothing_from_qonclave() -> None:
    """core is the floor. If it acquires a dependency, every layer above inherits it."""
    offenders = []
    for path, package in _modules():
        if package != "core":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found = _qonclave_imports(tree, "core")
        if found:
            offenders.append((path.relative_to(SRC), sorted(found)))

    assert not offenders, f"core must not import from qonclave: {offenders}"


def test_every_layer_is_present() -> None:
    """Guards against a package being renamed or dropped while the rule silently keeps passing."""
    missing = [pkg for pkg in _RANK if not (SRC / pkg).is_dir()]
    assert not missing, f"declared layers with no package on disk: {missing}"
