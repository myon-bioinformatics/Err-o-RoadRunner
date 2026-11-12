"""The shared packager must preserve the maintained standalone bundle."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_common_package_preserves_bundle_and_command_names(tmp_path):
    olnm = tmp_path / "ErroRR.py"
    subprocess.run([sys.executable, "-S", str(ROOT / "scripts/build_standalone.py"),
                    str(olnm)], check=True)
    out = tmp_path / "package"
    result = subprocess.run([sys.executable, "-S", str(ROOT / "scripts/build_uv_tool.py"),
                             "--out", str(out)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (out / "erroroadrunner.py").read_bytes() == olnm.read_bytes()
    config = tomllib.loads((out / "pyproject.toml").read_text())
    assert config["project"]["name"] == "err-o-roadrunner"
    assert config["project"]["version"] == "0.6.0"
    assert config["project"]["requires-python"] == ">=3.11"
    assert config["project"]["dependencies"] == []
    assert config["project"]["scripts"] == {
        "erroroadrunner": "erroroadrunner:main"}
    assert config["tool"]["setuptools"]["py-modules"] == ["erroroadrunner"]
    run = subprocess.run([sys.executable, "-S", str(out / "erroroadrunner.py"),
                          "ValueError:", "example"], cwd=tmp_path, text=True, capture_output=True)
    assert run.returncode == 0, run.stderr
    assert "possible Python exception" in run.stdout
    second = subprocess.run([sys.executable, "-S", str(ROOT / "scripts/build_uv_tool.py"),
                             "--out", str(out)], capture_output=True, text=True)
    assert second.returncode != 0
    assert (out / "erroroadrunner.py").read_bytes() == olnm.read_bytes()


def test_builder_matches_vendor_pin():
    lock = json.loads((ROOT / "vendor.lock.json").read_text())
    entry, = [e for e in lock["files"] if e["destination"] == "vendor/uv_tool_build.py"]
    assert entry["repository"] == "myon-bioinformatics/Aoi"
    assert entry["ref"] == entry["commit"] == "673fc7b11d94ccad16740c0998683618f2e86254"
    content = (ROOT / entry["destination"]).read_bytes()
    assert hashlib.sha256(content).hexdigest() == entry["sha256"]
    assert hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest() == entry["blob_sha"]
