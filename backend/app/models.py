from sqlalchemy import Column, Integer, String, Text, Float, DateTime, func
from .db import Base

class RequestLog(Base):
    __tablename__ = "request_logs"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(50), index=True)
    model = Column(String(100), index=True)
    prompt = Column(Text)
    output = Column(Text)
    latency_ms = Column(Float)
    status = Column(String(50), default="ok")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
