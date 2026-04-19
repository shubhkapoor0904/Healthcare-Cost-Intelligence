import sys
from pathlib import Path

# Add project root to python path so we can import server
sys.path.append(str(Path(__file__).resolve().parent.parent))

from server import Handler

class handler(Handler):
    pass
