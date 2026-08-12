import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.catalog import fetch_catalog_sync, load_catalog

ok = fetch_catalog_sync()
print(f"OK, {len(load_catalog())} providers" if ok else "catalog fetch failed")
sys.exit(0 if ok else 1)
