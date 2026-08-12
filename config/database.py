from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE_DATABASE_PATH = PROJECT_ROOT / "data" / "civic_assess.sqlite3"


def resolve_database_path(value: object) -> Path:
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path
