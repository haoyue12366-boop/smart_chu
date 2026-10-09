from pydantic import Field

from app.domain.base import FrozenModel, NonEmpty
from app.domain.resources import ResourceUse
from app.domain.time import Interval


class ResourceBlock(FrozenModel):
    occupancy_use: ResourceUse | None = Field(default=None, exclude_if=lambda v: v is None)
    resource_id: NonEmpty
    physical_resource_id: NonEmpty
    component_id: NonEmpty
    interval: Interval
    reason: NonEmpty
    evidence_refs: tuple[NonEmpty, ...] = ()
