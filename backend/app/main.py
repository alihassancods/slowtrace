import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

load_dotenv()

# Import the application from src/api/main.py which wires up all routes.
from api.main import app  # noqa: F401 — re-exported as the uvicorn entry point
