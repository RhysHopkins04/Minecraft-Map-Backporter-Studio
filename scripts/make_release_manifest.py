from __future__ import annotations

import argparse
import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

VALID_STATUSES = (
    "community-validated",
    "dev-validated",
    "unsigned-dev",
    "verified",
)

parser = argparse.ArgumentParser()
parser.add_argument("--version", required=True)
parser.add_argument("--platform", required=True)
parser.add_argument("--arch", required=True)
parser.add_argument("--status", required=True, choices=VALID_STATUSES)
parser.add_argument("--output", required=True)
parser.add_argument("files", nargs="+")
args = parser.parse_args()

entries = []
for raw in args.files:
    path = Path(raw)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    entries.append(
        {
            "file": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": digest.hexdigest(),
        }
    )

output = {
    "product": "WG Map Backporter Studio",
    "version": args.version,
    "platform": args.platform,
    "architecture": args.arch,
    "release_status": args.status,
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "builder_python": platform.python_version(),
    "artifacts": entries,
}

output_path = Path(args.output)
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
print(output_path.resolve())
