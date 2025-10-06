import os
from typing import List, Optional
from datetime import datetime

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import select, desc, and_
from dotenv import load_dotenv

from .db import SessionLocal, engine, Base
from .models import RequestLog
from .schemas import InvokeRequest, InvokeResponse, LogOut
from .providers.base import register, get_provider
from .providers.gemini import GeminiProvider

load_dotenv()

Base.metadata.create_all(bind=engine)

register("gemini", GeminiProvider())

app = FastAPI()

origins = [os.getenv("ALLOWED_ORIGINS", "http://localhost:5173")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def now_ms() -> float:
    return datetime.now().timestamp() * 1000.0

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/api/llm/invoke", response_model=InvokeResponse)
async def invoke(req: InvokeRequest, db: Session = Depends(get_db)):
    provider = get_provider()
    t0 = now_ms()
    try:
        output = provider.generate(req.model, req.prompt)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    latency = now_ms() - t0

    log = RequestLog(
        provider=req.provider,
        model=req.model,
        prompt=req.prompt,
        output=output,
        latency_ms=latency,
        status="ok",
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    return InvokeResponse(
        id=log.id,
        provider=log.provider,
        model=log.model,
        output=log.output or "",
        latency_ms=log.latency_ms,
        created_at=log.created_at,
    )

@app.get("/api/requests", response_model=List[LogOut])
def list_requests(
    model: Optional[str] = None,
    provider: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db),
):
    stmt = select(RequestLog).order_by(desc(RequestLog.created_at))
    filters = []
    if model:
        filters.append(RequestLog.model == model)
    if provider:
        filters.append(RequestLog.provider == provider)
    if date_from:
        try:
            dt_from = datetime.fromisoformat(date_from)
            filters.append(RequestLog.created_at >= dt_from)
        except Exception:
            pass
    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to)
            filters.append(RequestLog.created_at <= dt_to)
        except Exception:
            pass
    if filters:
        stmt = select(RequestLog).where(and_(*filters)).order_by(desc(RequestLog.created_at))

    rows = db.execute(stmt).scalars().all()
    return rows
