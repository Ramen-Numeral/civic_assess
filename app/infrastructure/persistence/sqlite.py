import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


SCHEMA_VERSION = 2
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
MIGRATIONS_PATH = Path(__file__).with_name("migrations")


class SQLiteDatabase:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError(
                    f"Unsupported SQLite schema version {version}; "
                    f"latest supported version is {SCHEMA_VERSION}"
                )
            if version == 0:
                self._execute_script(connection, SCHEMA_PATH, version=1)
                version = 1
            while version < SCHEMA_VERSION:
                target = version + 1
                migrations = tuple(MIGRATIONS_PATH.glob(f"{target:03d}_*.sql"))
                if len(migrations) != 1:
                    raise ValueError(
                        f"Expected one SQLite migration for version {target}"
                    )
                migration = migrations[0]
                self._execute_script(connection, migration, version=target)
                version = target

    @staticmethod
    def _execute_script(
        connection: sqlite3.Connection,
        path: Path,
        *,
        version: int,
    ) -> None:
        script = path.read_text(encoding="utf-8")
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            f"{script}\n"
            f"PRAGMA user_version = {version};\n"
            "COMMIT;"
        )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()
