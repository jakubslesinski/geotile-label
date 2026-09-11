from typing import Literal

from pydantic import BaseModel, Field, model_validator


PreprocessingModality = Literal["SAR", "EO", "AERIAL_EO"]
RadiometricTransform = Literal["none", "linear", "log1p", "db10"]
RgbConversion = Literal["native_rgb", "first_three_bands", "grayscale_rgb"]
OutputDtype = Literal["uint8"]


class PreprocessingProfile(BaseModel):
    profile_id: str
    name: str
    description: str = ""
    profile_version: int = Field(default=1, ge=1)
    processor_version: int = Field(default=1, ge=1)
    profile_hash: str = ""
    modality: PreprocessingModality
    input_quantity: str
    radiometric_transform: RadiometricTransform = "linear"
    percentile_stretch: bool = True
    percentile_scope: Literal["tile"] = "tile"
    stretch_low: float = Field(default=2.0, ge=0.0, le=100.0)
    stretch_high: float = Field(default=98.0, ge=0.0, le=100.0)
    gamma: float = Field(default=1.0, gt=0.0, le=10.0)
    brightness: float = Field(default=1.0, gt=0.0, le=10.0)
    contrast: float = Field(default=1.0, gt=0.0, le=10.0)
    rgb_conversion: RgbConversion = "native_rgb"
    output_dtype: OutputDtype = "uint8"
    builtin: bool = False

    @model_validator(mode="after")
    def validate_stretch(self):
        if self.stretch_high <= self.stretch_low:
            raise ValueError("stretch_high must be greater than stretch_low")
        return self


class PreprocessingProfilesFile(BaseModel):
    schema_name: str = "geotile_preprocessing_profiles"
    schema_version: int = 1
    updated_at: str
    profiles: list[PreprocessingProfile] = Field(default_factory=list)
