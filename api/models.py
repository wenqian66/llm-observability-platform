"""
api/models.py
Pydantic models for API
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


class Message(BaseModel):
    """Single message in a conversation."""

    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    """
    OpenAI-compatible chat completion request.

    model is optional — if omitted or set to 'auto', the gateway
    auto-detects the correct model from the upstream LLM.
    """

    model: Optional[str] = "auto"
    messages: List[Union[Message, Dict[str, Any]]]

    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 4096
    stream: Optional[bool] = False
    top_p: Optional[float] = 1.0
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    stop: Optional[Union[str, List[str]]] = None

    # Gateway-specific
    semantic_cache: Optional[bool] = True

    class Config:
        extra = "allow"


class StandardResponse(BaseModel):
    """Standard API response wrapper."""

    success: bool
    data: Optional[Any] = None
    error: Optional[Dict[str, str]] = None
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ErrorResponse(BaseModel):
    error: Dict[str, Any]
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class StreamChunk(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: List[Dict[str, Any]]


class CompletionUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[Dict[str, Any]]
    usage: Optional[CompletionUsage] = None

    class Config:
        extra = "allow"


# ============================================
# User / App Management Models
# ============================================


class CreateUserRequest(BaseModel):
    """Admin creates a user record — no key is issued at this point."""

    first_name: str
    last_name: str
    net_id: str
    is_admin: bool = False


class CreateAppRequest(BaseModel):
    """Admin creates an app record — no key is issued at this point."""

    app_name: str
    description: Optional[str] = None
    created_by: Optional[str] = None  # net_id of creating admin
    semantic_cache_enabled: bool = True


# ============================================
# Key Management Models
# ============================================


class SelfGenerateRequest(BaseModel):
    """
    Regular (non-admin) user generates their own key.
    No auth header — net_id is the only input.
    Only succeeds if the user exists, is active, is not an admin,
    and has no key yet (api_key_hash IS NULL).
    """

    net_id: str

    class Config:
        json_schema_extra = {"example": {"net_id": "divya8"}}


class AdminSelfBootstrapRequest(BaseModel):
    """
    Admin generates their own key using the bootstrap secret.
    Only succeeds if api_key_hash IS NULL for this admin.
    Another admin must null it before re-bootstrap is possible.
    """

    net_id: str

    class Config:
        json_schema_extra = {"example": {"net_id": "abhinay4"}}


class AdminGenerateUserKeyRequest(BaseModel):
    """
    Admin generates/regenerates a key for a specific non-admin user.
    Admins cannot target other admins — use NullKeyRequest for that.
    """

    net_id: str

    class Config:
        json_schema_extra = {"example": {"net_id": "divya8"}}


class AdminGenerateAppKeyRequest(BaseModel):
    """
    Admin generates/regenerates a key for an app.
    Always overwrites the existing hash (first-issue and regen share this path).
    """

    app_name: str

    class Config:
        json_schema_extra = {"example": {"app_name": "Atlas"}}


class NullKeyRequest(BaseModel):
    """
    Admin nulls a key for any user (including other admins) or app.

    entity_type : 'user' or 'app'
    identifier  : net_id for users, app_name for apps

    After nulling:
      - Regular user → self-generates via POST /keys/self-generate
      - Admin        → re-bootstraps via POST /admin/keys/self-bootstrap
      - App          → admin re-generates via POST /admin/keys/app/generate
    """

    entity_type: str  # 'user' or 'app'
    identifier: str

    class Config:
        json_schema_extra = {
            "example": {"entity_type": "user", "identifier": "abhinay4"}
        }
