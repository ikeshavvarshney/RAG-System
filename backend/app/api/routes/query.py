from dataclasses import asdict

from fastapi import APIRouter
from pydantic import BaseModel

from app.query.pipeline import run_query

router = APIRouter()


class QueryRequest(BaseModel):
    question: str
    session_id: str | None = None


@router.post("/query")
async def query(request: QueryRequest):
    return asdict(await run_query(request.question))
