from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class CorpusCitation(BaseModel):
    kind: Literal["corpus"] = "corpus"
    source_doc: str
    page: int | None = None
    chunk_id: str


class WebCitation(BaseModel):
    kind: Literal["web"] = "web"
    source_url: str
    title: str


Citation = Annotated[Union[CorpusCitation, WebCitation], Field(discriminator="kind")]