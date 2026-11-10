"""Node sources must use accepted upstream main commits and exact bytes."""
import hashlib, json
from pathlib import Path


def test_canonical_node_sources():
    root = Path(__file__).resolve().parents[1]
    entries = {e["destination"]: e for e in json.loads((root / "vendor.lock.json").read_text())["files"]}
    expected = {'vendor/xprobe.py': '1ffbb7b984075c683690bbe4e9deb06199cdfe94', 'vendor/cli_args.py': '9ff06c4326fc7ba934112abecbc59451ab07be6a'}
    for path, commit in expected.items():
        entry = entries[path]
        assert entry["ref"] == entry["commit"] == commit
        data = (root / path).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        assert hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == entry["blob_sha"]
