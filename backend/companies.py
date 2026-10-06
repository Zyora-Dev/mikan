import base64
import binascii
import io
import re

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, TypeAdapter, field_validator, model_validator

MAX_LOGO_BYTES = 2 * 1024 * 1024
COMPANY_COLUMNS = "id, name, mobile, email, website, address, created_at, updated_at, logo_data IS NOT NULL AS has_logo"


class CompanyInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    mobile: str = Field(min_length=7, max_length=25)
    email: EmailStr = Field(max_length=254)
    website: str = Field(default="", max_length=2048)
    address: str = Field(min_length=1, max_length=2000)
    logo_base64: str | None = Field(default=None, max_length=2796204)
    remove_logo: bool = False

    @field_validator("mobile")
    @classmethod
    def valid_mobile(cls, value):
        if not re.fullmatch(r"\+?[0-9 ()-]+", value) or not 7 <= len(re.sub(r"\D", "", value)) <= 15:
            raise ValueError("Enter a valid mobile number with 7-15 digits.")
        return value

    @field_validator("website")
    @classmethod
    def valid_website(cls, value):
        if not value:
            return ""
        url = TypeAdapter(HttpUrl).validate_python(value)
        if url.username or url.password:
            raise ValueError("Website must not contain credentials.")
        return str(url)

    @model_validator(mode="after")
    def logo_choice(self):
        if self.remove_logo and self.logo_base64 is not None:
            raise ValueError("Choose either a new logo or remove logo.")
        return self


def normalize_logo(encoded, label="logo"):
    if encoded is None:
        return None
    try:
        data = base64.b64decode(encoded, validate=True)
        if not data:
            raise HTTPException(status_code=422, detail=f"The {label} file is empty.")
        if len(data) > MAX_LOGO_BYTES:
            raise HTTPException(status_code=422, detail=f"The {label} file exceeds 2 MB. Upload a smaller file.")
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise HTTPException(status_code=422, detail=f"Unsupported {label} format. Upload a JPG, JPEG, PNG or WebP image.")
            if image.width * image.height > 16000000 or max(image.size) > 8192:
                raise HTTPException(status_code=422, detail=f"{label.capitalize()} dimensions are {image.width} x {image.height} pixels. Resize to at most 16 megapixels and 8192 pixels per side.")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            image = ImageOps.exif_transpose(image).convert("RGBA")
            image.info.clear()
            image.thumbnail((512, 512))
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=90)
            return output.getvalue()
    except Image.DecompressionBombError as error:
        raise HTTPException(status_code=422, detail=f"{label.capitalize()} dimensions are too large. Resize to at most 16 megapixels and 8192 pixels per side.") from error
    except (ValueError, binascii.Error, OSError, UnidentifiedImageError) as error:
        raise HTTPException(status_code=422, detail=f"The {label} could not be decoded. Upload an intact JPG, JPEG, PNG or WebP image.") from error