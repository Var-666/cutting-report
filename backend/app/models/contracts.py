"""Shared HTTP response envelopes."""
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

T = TypeVar('T')


class ApiEnvelope(BaseModel, Generic[T]):
    success: Literal[True]
    data: T
    request_id: str


class Items(BaseModel, Generic[T]):
    items: list[T]


class Page(Items[T], Generic[T]):
    total: int
    page: int
    page_size: int
