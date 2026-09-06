"""Shared, bounded query contract. No client-provided SQL or column expressions."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConditionRule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: str = Field(min_length=1, max_length=64)
    operator: str = Field(min_length=1, max_length=32)
    value: str | int | float | None | list[str | int | float] = None


class ConditionGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    logic: Literal["and", "or"] = "and"
    rules: list[ConditionRule] = Field(default_factory=list, max_length=50)


class SortRule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: str = Field(min_length=1, max_length=64)
    direction: Literal["asc", "desc"] = "asc"


class ConditionSearch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    filters: ConditionGroup = Field(default_factory=ConditionGroup)
    page: int = Field(default=1, ge=1, le=100000)
    page_size: int = Field(default=10, ge=1, le=100)
    sort: list[SortRule] = Field(default_factory=list, max_length=5)
