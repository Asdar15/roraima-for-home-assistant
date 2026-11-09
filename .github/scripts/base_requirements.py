"""Print the requirements Home Assistant needs to read its service descriptions.

Home Assistant resolves the field filters of services.yaml (light.ColorMode.HS, ...) by importing a
fixed set of entity components (helpers/service.py, _base_components), whose requirements a real
installation always has and the test harness does not. This prints them, with the requirements of
their dependencies, read from the installed Home Assistant itself.
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib

import homeassistant
from homeassistant.helpers import service


def _base_components() -> list[str]:
    tree = ast.parse(inspect.getsource(service._base_components))  # noqa: SLF001
    return [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "homeassistant.components"
        for alias in node.names
    ]


def main() -> None:
    components = pathlib.Path(homeassistant.__file__).parent / "components"
    todo = _base_components()
    seen: set[str] = set()
    requirements: set[str] = set()
    while todo:
        domain = todo.pop()
        if domain in seen:
            continue
        seen.add(domain)
        manifest = json.loads((components / domain / "manifest.json").read_text(encoding="utf-8"))
        requirements.update(manifest.get("requirements", []))
        todo.extend(manifest.get("dependencies", []))
    print("\n".join(sorted(requirements)))


if __name__ == "__main__":
    main()
