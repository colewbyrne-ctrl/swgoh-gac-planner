import sys
from pathlib import Path

# Make the in-tree ``swgoh`` package importable without an editable install
# so ``python -m pytest`` works from a fresh checkout.
sys.path.insert(0, str(Path(__file__).parent))
