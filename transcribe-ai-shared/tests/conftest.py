import sys
from pathlib import Path

SHARED_SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SHARED_SRC))