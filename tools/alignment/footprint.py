"""Record the installed research environment, including bundled wheel licence file hashes."""

import argparse
import ast
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

from avsync_core import FUNCTIONS, SOURCE_SHA256
from corpus import digest


def inventory(source: Path) -> dict:
    if digest(source) != SOURCE_SHA256:
        raise ValueError("expected the pinned AVSync source")
    packages = []
    for distribution in sorted(
        importlib.metadata.distributions(), key=lambda d: d.metadata["Name"].lower()
    ):
        files = [Path(str(distribution.locate_file(f))) for f in distribution.files or []]
        packages.append(
            {
                "name": distribution.metadata["Name"],
                "version": distribution.version,
                "installed_bytes": sum(p.stat().st_size for p in files if p.is_file()),
                "requires_dist": distribution.metadata.get_all("Requires-Dist", []),
                "license_metadata": distribution.metadata.get("License-Expression")
                or (distribution.metadata.get("License") or "unspecified").splitlines()[0],
                "license_files": [
                    {
                        "path": str(p.relative_to(str(distribution.locate_file("")))),
                        "sha256": digest(p),
                    }
                    for p in files
                    if p.is_file()
                    and any(v in p.name.lower() for v in ("license", "licence", "copying"))
                ],
            }
        )
    functions = [
        {"name": node.name, "lines": (node.end_lineno or node.lineno) - node.lineno + 1}
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS
    ]
    return {
        "python_version": sys.version,
        "platform": platform.platform(),
        "opencv_variant": "headless OpenCV for isolated visual functions",
        "packages": packages,
        "total_installed_bytes": sum(p["installed_bytes"] for p in packages),
        "extracted_functions": functions,
        "extracted_lines": sum(f["lines"] for f in functions),
        "source_sha256": SOURCE_SHA256,
        "container_delta_measured": False,
        "redistribution_cleared": False,
        "license_scope": "Metadata and licence file hashes only. Native audit pending.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    with args.report.open("x") as output:
        json.dump(inventory(args.source), output, indent=2)
        output.write("\n")
