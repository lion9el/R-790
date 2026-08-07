"""Schémas Pydantic partagés par tous les routers."""
from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterIn(BaseModel):
    """Corps attendu pour POST /api/register."""
    username: str = Field(min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=6, max_length=100)


class LoginIn(BaseModel):
    """Corps attendu pour POST /api/login."""
    username: str
    password: str


class ProfileIn(BaseModel):
    """Corps attendu pour PATCH /api/me."""
    bio: str = Field(max_length=200)


class PostIn(BaseModel):
    """Référence documentation (POST /api/posts utilise Form/File)."""
    content: str = Field(min_length=1, max_length=1000)


class PostEditIn(BaseModel):
    """Corps attendu pour PATCH /api/posts/{post_id}."""
    content: str = Field(min_length=1, max_length=1000)


class CommentIn(BaseModel):
    """Corps attendu pour POST /api/posts/{post_id}/comments."""
    content: str = Field(min_length=1, max_length=500)


class AgentRunIn(BaseModel):
    """Corps attendu pour POST /api/agent/run."""
    instruction: str = Field(min_length=1, max_length=2000)


class MessageIn(BaseModel):
    """Corps attendu pour POST /api/messages."""
    to: str
    content: str = Field(min_length=1, max_length=2000)
