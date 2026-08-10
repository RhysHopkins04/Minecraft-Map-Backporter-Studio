#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if SRC.exists(): sys.path.insert(0, str(SRC))
from wgmap_backporter_studio.app import main
raise SystemExit(main())
