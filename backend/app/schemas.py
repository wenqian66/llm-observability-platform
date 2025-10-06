from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class InvokeRequest(BaseModel):
    provider: str
    model: str
    prompt: str

class InvokeResponse(BaseModel):
    id: int
    provider: str
    model: str
    output: str
    latency_ms: float
    created_at: datetime

class LogOut(BaseModel):
    id: int
    provider: str
    model: str
    latency_ms: float
    status: str
    created_at: datetime
    prompt: str
    output: Optional[str] = None

    class Config:
        from_attributes = True
