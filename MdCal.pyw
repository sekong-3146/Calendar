"""Double-click launcher (no console)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdcal.app import main  # noqa: E402

sys.exit(main())
