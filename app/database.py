from __future__ import annotations

import psycopg

from psycopg.conninfo import (
    conninfo_to_dict,
)


class DatabaseError(RuntimeError):
    pass


class Database:
    """
    PostgreSQL connection factory.

    DATABASE_URL may point to:

        local PostgreSQL
        hosted PostgreSQL
        SSL-required PostgreSQL

    DATABASE_URL parameters take precedence over the
    SEEFIX_DATABASE_* defaults.
    """

    def __init__(
        self,
        *,
        database_url: str,
        sslmode: str,
        sslrootcert: str,
        connect_timeout_seconds: int,
        application_name: str,
    ) -> None:
        self.database_url = (
            database_url.strip()
        )

        self.sslmode = (
            sslmode.strip().lower()
        )

        self.sslrootcert = (
            sslrootcert.strip()
        )

        self.connect_timeout_seconds = (
            connect_timeout_seconds
        )

        self.application_name = (
            application_name.strip()
            or
            "seefix-agents"
        )

        if not self.database_url:
            raise DatabaseError(
                "DATABASE_URL is required."
            )

        try:
            self._dsn_options = (
                conninfo_to_dict(
                    self.database_url
                )
            )
        except Exception as exc:
            raise DatabaseError(
                "DATABASE_URL is not a valid "
                f"PostgreSQL connection string: {exc}"
            ) from exc

    def connect(
        self,
        *,
        row_factory=None,
    ):
        options: dict[str, object] = {}

        if (
            "connect_timeout"
            not in self._dsn_options
        ):
            options[
                "connect_timeout"
            ] = (
                self.connect_timeout_seconds
            )

        if (
            "application_name"
            not in self._dsn_options
        ):
            options[
                "application_name"
            ] = (
                self.application_name
            )

        if (
            "sslmode"
            not in self._dsn_options
        ):
            options[
                "sslmode"
            ] = self.sslmode

        if (
            self.sslrootcert
            and
            "sslrootcert"
            not in self._dsn_options
        ):
            options[
                "sslrootcert"
            ] = self.sslrootcert

        try:
            if row_factory is not None:
                return psycopg.connect(
                    self.database_url,
                    row_factory=row_factory,
                    **options,
                )

            return psycopg.connect(
                self.database_url,
                **options,
            )

        except Exception as exc:
            raise DatabaseError(
                "Unable to connect to PostgreSQL: "
                f"{exc}"
            ) from exc

    def health(self) -> bool:
        try:
            with self.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1"
                    )

                    row = cur.fetchone()

                    return bool(
                        row
                        and
                        row[0] == 1
                    )

        except Exception:
            return False


