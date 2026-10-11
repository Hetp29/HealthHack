"""Shared Pydantic building blocks for v2 request and response bodies."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    """Unknown fields are rejected, so a client typo is a 422 and not a silent drop."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())


#: Same bounds contract v1.0 applies to `MealInput`. A confirmed meal that passes here
#: is therefore always acceptable to the frozen predictor.
Grams = Annotated[float, Field(ge=0, le=1000, allow_inf_nan=False)]
FiberGrams = Annotated[float, Field(ge=0, le=200, allow_inf_nan=False)]
Kcal = Annotated[float, Field(ge=0, le=10000, allow_inf_nan=False)]

GlucoseUnit = Literal["mg/dL", "mmol/L"]


class Macros(Strict):
    carbs_g: Grams
    protein_g: Grams
    fat_g: Grams
    fiber_g: FiberGrams
    calories_kcal: Kcal


MACRO_FIELDS = ("carbs_g", "protein_g", "fat_g", "fiber_g", "calories_kcal")
