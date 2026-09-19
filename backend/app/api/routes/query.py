from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.query.pipeline import run_query
from app.shared.session_store import InvalidSessionId, new_session_id, validate_issued_session_id

router = APIRouter()


class QueryRequest(BaseModel):
    question: str
    session_id: str | None = None


@router.post("/query")
async def query(request: QueryRequest):
    if request.session_id is None:
        session_id = new_session_id()
    else:
        try:
            session_id = validate_issued_session_id(request.session_id)
        except InvalidSessionId as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = await run_query(request.question, session_id)
    return {"session_id": session_id, **asdict(result)}
