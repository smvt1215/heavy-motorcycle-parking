from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    database: str
    redis: str


class VersionResponse(BaseModel):
    version: str
    environment: str
