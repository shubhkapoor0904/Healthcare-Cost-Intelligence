import sys
from pathlib import Path

# Add project root to python path so we can import server
sys.path.append(str(Path(__file__).resolve().parent.parent))

from server import Handler, init_services

# Initialize database connections and embedding models on cold start
init_services()

class handler(Handler):
    pass
