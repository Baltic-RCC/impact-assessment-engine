import logging
from pydantic import BaseModel, Field, AliasChoices, field_validator
from typing import Optional, List, Any
import uuid

# Start logger
logger = logging.getLogger(__name__)


class Contingency(BaseModel):
    mRID: Optional[str] = Field(default_factory=lambda: f"{uuid.uuid4()}")
    name: Optional[str] = ""
    type: Optional[str] = Field(default="Ordinary", validation_alias=AliasChoices("type", "Type"))
    equipment: List[str]
    #
    # @field_validator('mRID', 'equipment')
    # def validate_mrids(cls, value):
    #     return remove_leading_underscore(value)

    def model_post_init(self, __context: Any) -> None:
        if len(self.equipment) > 1 and self.type != 'OutOfRange':
            self.type = "Exceptional"


class AssessedElement(BaseModel):
    mRID: Optional[str] = Field(default_factory=lambda: f"{uuid.uuid4()}")
    name: Optional[str] = ""
    type: Optional[str] = Field(default="AssessedElement", validation_alias=AliasChoices("type", "Type"))
    equipment: str = Field(validation_alias=AliasChoices("equipment", "ConductingEquipment"))
    contingency: Optional[List[str]] = None
    remedial_action: Optional[List[str]] = None


class InfluencingElement(BaseModel):
    """Influencing element can be only single elements because its flow used to calculate influence factors"""
    mRID: Optional[str] = Field(default_factory=lambda: f"{uuid.uuid4()}")
    name: Optional[str] = ""
    equipment: str = Field(validation_alias=AliasChoices("equipment", "grid_element_id"))


class GridSateAlteration(BaseModel):
    mRID: Optional[str] = Field(default_factory=lambda: f"{uuid.uuid4()}", validation_alias=AliasChoices("alt_mrid"))
    name: Optional[str] = Field(default="", validation_alias=AliasChoices("alt_name"))
    type: Optional[str] = Field(default="TopologyAction", validation_alias=AliasChoices("alt_type"))
    equipment: str = Field(validation_alias=AliasChoices("equipment"))
    property: Optional[str] = ""
    value: Optional[float] = Field(default=None, validation_alias=AliasChoices("normal_value"))


class GridSateAlterationRemedialAction(BaseModel):
    mRID: Optional[str] = Field(default_factory=lambda: f"{uuid.uuid4()}")
    name: Optional[str] = ""
    type: Optional[str] = Field(default="GridSateAlterationRemedialAction", validation_alias=AliasChoices("type"))
    alteration: List[GridSateAlteration]


class PowerRemedialAction(BaseModel):
    mRID: Optional[str] = Field(default_factory=lambda: f"{uuid.uuid4()}")
    name: Optional[str] = ""
    type: Optional[str] = Field(default="CountertradeRemedialAction", validation_alias=AliasChoices("type", "Type"))
    bidding_zone: Optional[str] = ""
    bidding_zone_border: Optional[str] = ""


class ScheduledRemedialAction(BaseModel):

    class Config:
        # Allow passing by original attribute name and not only by alias
        populate_by_name = True

    mRID: Optional[str] = Field(default_factory=lambda: f"{uuid.uuid4()}", validation_alias=AliasChoices("registered_resource"))
    name: Optional[str] = ""
    description: Optional[str] = ""
    status: Optional[str] = Field(default="proposed", validation_alias=AliasChoices("status_kind"))
    value: float
    contingency: str = Field(validation_alias=AliasChoices("co_id"))
    remedial_action: str = Field(validation_alias=AliasChoices("ra_id"))
    alteration: Optional[str] = Field(default="", validation_alias=AliasChoices("alt_id"))
    region: Optional[str] = Field(default="")
    entity: Optional[str] = Field(default="", validation_alias=AliasChoices("operator"))
    at_time: Optional[str] = Field(default="")

    @field_validator('mRID', 'contingency', 'remedial_action', 'alteration')
    @classmethod
    def validate_mrids(cls, value):
        return value if value.startswith("_") else f"_{value}"


class OperatorStrategyAction(BaseModel):
    mRID: Optional[str] = Field(default_factory=lambda: f"{uuid.uuid4()}")
    name: Optional[str] = ""
    type: str
    equipment: str = Field(validation_alias=AliasChoices("equipment"))
    value: int | float | None = None
    relative: bool | None = None  # False means value will be applied as absolute value
    opening: bool | None = None  # False means close terminals, True - open


class OperatorStrategy(BaseModel):
    mRID: Optional[str] = Field(default_factory=lambda: f"{uuid.uuid4()}")
    name: Optional[str] = ""
    contingency: str = Field(validation_alias=AliasChoices("contingency", "co_id"))
    action: List[OperatorStrategyAction]


def remove_leading_underscore(values: str | List[str]) -> str | List[str]:
    def __lstrip_value(v):
        if v.startswith('_'):
            return v.lstrip('_')
        return v

    if isinstance(values, list):
        return [__lstrip_value(item) for item in values]
    else:
        return __lstrip_value(values)
