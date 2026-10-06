import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv(Path(__file__).with_name(".env"))


def connect():
    return psycopg.connect(
        os.environ.get("DATABASE_URL", "dbname=mikan"),
        row_factory=dict_row,
        connect_timeout=5,
    )


def get_db():
    with connect() as connection:
        yield connection


@contextmanager
def transfer_connection():
    with connect() as connection:
        connection.autocommit = True
        yield connection


def transfer_database():
    return transfer_connection


async def connect_async():
    return await psycopg.AsyncConnection.connect(
        os.environ.get("DATABASE_URL", "dbname=mikan"),
        autocommit=True,
        connect_timeout=5,
    )