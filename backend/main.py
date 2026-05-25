import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from single_instance import acquire
from server import run

if __name__ == "__main__":
    if not acquire("server"):
        sys.exit(0)  # another server instance is already running
    run()
