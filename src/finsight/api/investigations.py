from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..agents.investigator.service import InvestigationError, make_service
from ..agents.text_to_sql.llm import make_client
from ..config import get_settings
from .auth import Principal, require


class StartRequest(BaseModel):
    alert_id: int = Field(gt=0)


class DecisionRequest(BaseModel):
    decision: str
    note: str | None = Field(None, max_length=500)


def build_router() -> APIRouter:
    s = get_settings()
    state: dict = {}

    def svc():
        if "s" not in state:
            state["s"] = make_service(s, make_client(s.anthropic_api_key, s.investigator_model))
        return state["s"]

    analyst = Depends(require("analyst"))
    r = APIRouter(prefix="/investigations", tags=["investigator"], dependencies=[analyst])

    def call(fn, *a):
        try:
            return fn(*a)
        except InvestigationError as e:
            raise HTTPException({"not_found": 404, "already_decided": 409, "decision_in_progress": 409}.get(e.code, 422), e.message) from None

    @r.post("")
    def start(req: StartRequest):
        return call(svc().start, req.alert_id)

    @r.get("")
    def list_runs(limit: int = 50):
        return svc().list(min(max(limit, 1), 200))

    @r.get("/{run_id}")
    def get(run_id: str):
        return call(svc().get, run_id)

    @r.post("/{run_id}/decision")
    def decide(run_id: str, req: DecisionRequest, request: Request):
        p: Principal = request.state.principal  # authenticated identity, never taken from the body
        return call(svc().decide, run_id, req.decision, p.identity, req.note)

    return r
