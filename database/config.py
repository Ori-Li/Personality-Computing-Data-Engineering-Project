from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MySQLConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    creator_id: int

    @classmethod
    def from_env(cls) -> "MySQLConfig":
        password = os.getenv("MYSQL_PASSWORD") or os.getenv("MYSQL_ROOT_PASSWORD")
        if not password:
            raise RuntimeError(
                "缺少 MYSQL_PASSWORD（或 MYSQL_ROOT_PASSWORD）环境变量。"
            )
        return cls(
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=password,
            database=os.getenv("MYSQL_DATABASE", "rgmj"),
            creator_id=int(os.getenv("DATASET_CREATOR_ID", "0")),
        )
