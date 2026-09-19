from app.ingestion import embedder
from app.query import llm


def warm_up_clients() -> None:
    llm.warm_up()
    embedder.warm_up()
