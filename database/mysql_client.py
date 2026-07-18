from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
import os

import pymysql
from pymysql.connections import Connection

from database.config import MySQLConfig


def connect(config: MySQLConfig) -> Connection:
    """Open a UTF-8 MySQL connection with manual transaction control."""
    return pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
        read_timeout=int(os.getenv("MYSQL_READ_TIMEOUT", "120")),
        write_timeout=int(os.getenv("MYSQL_WRITE_TIMEOUT", "120")),
    )


@contextmanager
def transaction(config: MySQLConfig) -> Iterator[Connection]:
    connection = connect(config)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
