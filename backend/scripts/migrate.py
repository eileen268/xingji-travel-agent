from pathlib import Path
import argparse
import json
import os
from dotenv import load_dotenv

from app.migrations import migrate, migration_status

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')


def main():
    parser = argparse.ArgumentParser(description="Journey Notes SQLite migrations")
    parser.add_argument("command", choices=("status", "apply"), nargs="?", default="status")
    args = parser.parse_args()
    path = Path(os.getenv("DATABASE_PATH", str(ROOT / "data/travel.db")))
    if not path.is_absolute():
        path = ROOT / path
    if args.command == "apply":
        migrate(path)
    print(json.dumps(migration_status(path), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
