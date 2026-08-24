from typing import Literal , Optional
from pydantic import BaseModel

class Chunk(BaseModel):
    chunk_id: str
    text: str
    source_doc: str
    page: Optional[int] = None
    location: Optional[str] = None
    chunk_type: Literal["text", "table", "chart", "image_caption"]
    extraction_method: Literal["vision", "ocr", "text"]
    corpus_scope: str
    dense_vector_id: Optional[str] = None
    embedding_model: Optional[str] = None