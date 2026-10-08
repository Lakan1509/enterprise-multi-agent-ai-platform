"""Shared pytest bootstrap for the CASI test suite.

Inserts the project root (``~/workspace/casi-ai-os/``) at the front of
``sys.path`` so ``import casi`` works no matter which directory pytest is
invoked from.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
