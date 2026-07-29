from pydantic import BaseModel


class Capabilities(BaseModel):
    image: bool = False
    audio: bool = False
    video: bool = False
    pdf: bool = False
    reasoning_field: str = "reasoning_content"
