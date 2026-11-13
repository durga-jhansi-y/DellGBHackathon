"""Contracts for experimental recognition; no model implementation lives here."""
from dataclasses import dataclass, field
import math
import re


class RecognitionError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f'{code}: {message}')


def require(condition, code, message):
    if not condition:
        raise RecognitionError(code, message)


@dataclass(frozen=True)
class RecognitionRequest:
    region: tuple[float, float, float, float]
    variant: str
    model_id: str
    model_version: str
    question: str = 'Describe visible conditions and changes. State what is uncertain; do not infer flood depth.'
    zoom: int = 18
    max_tokens: int = 160

    def validate(self):
        require(isinstance(self.region,tuple) and len(self.region)==4
                and all(type(v) in (int,float) and math.isfinite(v) for v in self.region),
                'RegionInvalid','region must contain four finite WGS84 bounds')
        west,south,east,north = self.region
        require(-180 <= west < east <= 180 and -85.051129 <= south < north <= 85.051129,
                'RegionInvalid','invalid WGS84 bounds')
        require(self.variant in ('before','after','both'),'VariantUnsupported','choose before, after or both')
        require(type(self.zoom) is int and self.zoom==18,'ResolutionUnsupported','only native zoom 18 is supported')
        for value in (self.model_id,self.model_version):
            require(isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_./:@+-]{1,160}',value),
                    'ModelInvalid','supply a model ID and an explicit deployment/version label')
        require(isinstance(self.question,str) and 0 < len(self.question)<=2048,
                'QuestionInvalid','question must contain 1..2048 characters')
        require(type(self.max_tokens) is int and 1 <= self.max_tokens <= 512,
                'ModelInvalid','max_tokens must be 1..512')


@dataclass(frozen=True)
class VerifiedImage:
    generation_id: str
    variant: str
    zoom: int
    x: int
    y: int
    bounds: tuple
    source_image: str
    acquisition_date: str
    stored_sha256: str
    payload: bytes


@dataclass(frozen=True)
class ModelResult:
    text: str
    actual_model_id: str
    backend_settings: dict = field(default_factory=dict)
