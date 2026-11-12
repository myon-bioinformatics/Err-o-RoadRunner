"""Package the existing standalone bundle using Aoi's pinned uv builder.

The OLNM-facing ErroRR.py generator remains unchanged. Only the temporary
package module uses a lowercase name, as required by the common builder.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import tempfile

from build_standalone import build as build_standalone

ROOT = Path(__file__).resolve().parents[1]


def build(out: Path) -> dict:
    spec = importlib.util.spec_from_file_location(
        "_err_uv_tool_build", ROOT / "vendor/uv_tool_build.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    with tempfile.TemporaryDirectory(prefix="err-uv-tool-") as temporary:
        entry = Path(temporary) / "erroroadrunner.py"
        entry.write_text(build_standalone(), encoding="utf-8", newline="\n")
        # triage is embedded by build_standalone, not a runtime dependency.
        return builder.build(
            [entry], out, name="err-o-roadrunner", version="0.6.0",
            allow_imports=["triage"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.out), indent=2))


if __name__ == "__main__":
    main()
