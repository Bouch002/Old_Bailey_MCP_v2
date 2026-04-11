import sys
from pathlib import Path

# Allow tests to import server.py from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))
