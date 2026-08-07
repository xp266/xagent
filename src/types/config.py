from pydantic import BaseModel, Field


class ProviderInfo(BaseModel):
    id: str
    name: str
    base_url: str
    api_key: str = ""
    is_custom: bool = False
    models: list[str] = Field(default_factory=list)
    model_meta: dict[str, dict] = Field(default_factory=dict)
    selected_models: list[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    active_provider: str = ""
    active_model: str = ""
    reasoning_effort: dict[str, str] = Field(default_factory=dict)
    providers: dict[str, dict] = Field(default_factory=dict)
    mcp_servers: dict[str, dict] = Field(default_factory=dict)


class Config(BaseModel):
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    reasoning_effort: str = ""
