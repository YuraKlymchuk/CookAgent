import pytest

from agent.tools import ToolBox
from data_load import build_database, open_readonly

from .recipes_fixture import write_csv


@pytest.fixture(scope="session")
def source_csv(tmp_path_factory):
    return write_csv(tmp_path_factory.mktemp("source") / "recipes.csv")


@pytest.fixture(scope="session")
def db_path(tmp_path_factory, source_csv):
    path = tmp_path_factory.mktemp("db") / "recipes.db"
    build_database(source_csv, path)
    return path


@pytest.fixture()
def connection(db_path):
    conn = open_readonly(db_path)
    yield conn
    conn.close()


@pytest.fixture()
def toolbox(connection):
    return ToolBox(connection)
