from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """État minimal exposé aux probes de la plateforme."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["alive", "ready", "not_ready"]
