from typing import Any

from pymongo import ASCENDING, IndexModel
from pymongo.database import Database

from src.database import get_mongo_database


def ensure_event_indexes(database: Database[dict[str, Any]]) -> list[str]:
    """Создать индексы коллекции событий для обработки и аналитики."""
    return database["events"].create_indexes([
        IndexModel([
            ("experiment_id", ASCENDING),
            ("variant_key", ASCENDING),
            ("event_type", ASCENDING),
        ]),
        IndexModel([("experiment_id", ASCENDING), ("timestamp", ASCENDING)]),
        IndexModel([("processed", ASCENDING)]),
        IndexModel([("user_id", ASCENDING), ("experiment_id", ASCENDING)]),
    ])


if __name__ == "__main__":
    for index_name in ensure_event_indexes(get_mongo_database()):
        print(index_name)
