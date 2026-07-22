from db.orm import Base
from shared.db import get_engine


def init():
    Base.metadata.create_all(get_engine())
    print("Tables created.")


if __name__ == "__main__":
    init()
