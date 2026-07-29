import os
import sys
from dotenv import load_dotenv

if __name__ == "__main__":
    load_dotenv()
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from src.ui.web.server import run_web
    run_web()
