from pathlib import Path
from pydantic import BaseModel, Field

import asyncio
from PIL import Image
import base64
from io import BytesIO

from src.client.mymodel_client import (
            load_profile,
            build_client,
            build_async_client,
            save_response_json,
        )

TOOL_NAME = "imageread"
IS_READ_ONLY = True
TOOL_DIR = Path(__file__).resolve().parent
DEFAULT_VISION_PROFILE = "qwen3.8"


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 2560 * 1440

FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}

class InputSchema(BaseModel):
    image_path: str | None = Field(default=None, description="本地图片的绝对路径")
    image_url: str | None = Field(default=None, description="可由视觉模型访问的图片 URL")
    question: str = Field(default="描述这张图片的内容", description="针对图片的问题")

class ImageReadResult(BaseModel):
    image_url: str = Field(description="被分析图片的 URL 或本地路径")
    question: str = Field(description="针对图片的问题")
    answer: str = Field(description="模型给出的回答")

class OutputSchema(BaseModel):
    ok: bool = Field(description="工具是否执行成功")
    error: str = Field(default="", description="错误信息，成功时为空字符串")
    data: ImageReadResult | None = Field(default=None, description="工具返回的结构化数据")


def get_input_schema() -> dict:
    return InputSchema.model_json_schema()

def get_output_schema() -> dict:
    return OutputSchema.model_json_schema()
    
def validate_input(**kwargs) -> tuple[bool, str]:
    try:
        input_data = InputSchema(**kwargs)
    except Exception as error:
        return False, str(error)

    image_path = (
        input_data.image_path.strip()
        if input_data.image_path
        else ""
    )
    image_url = (
        input_data.image_url.strip()
        if input_data.image_url
        else ""
    )

    if not image_path and not image_url:
        return False, "必须提供 image_path 或 image_url其中的一个。"

    if image_path and image_url:
        return False, "image_path 和 image_url 只能提供一个。"

    return True, ""

def resolve_image_source(
    input_data: InputSchema,
) -> tuple[str, str, dict]:
    if input_data.image_url:
        image_url = input_data.image_url.strip()
        return image_url, image_url, {
            "source_type": "url",
        }

    image_path = (input_data.image_path or "").strip()
    return prepare_local_image(image_path)

def prepare_local_image(image_path: str) -> tuple[str, str, dict]:
    path = Path(image_path).expanduser().resolve()

    if not path.exists():
        raise ValueError(f"图片不存在：{path}")

    if not path.is_file():
        raise ValueError(f"不是文件：{path}")

    file_size = path.stat().st_size
    if file_size > MAX_IMAGE_BYTES:
        raise ValueError(
            f"图片文件过大：{file_size} bytes，"
            f"最大允许 {MAX_IMAGE_BYTES} bytes"
        )

    image_bytes = path.read_bytes()

    # 防止检查文件大小后，文件内容发生变化
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"图片文件过大：{len(image_bytes)} bytes，"
            f"最大允许 {MAX_IMAGE_BYTES} bytes"
        )

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image_format = (image.format or "").upper()
            width, height = image.size

            if image_format not in FORMAT_TO_MIME:
                raise ValueError(
                    f"不支持的图片格式：{image_format or 'unknown'}"
                )

            pixel_count = width * height
            if pixel_count > MAX_IMAGE_PIXELS:
                raise ValueError(
                    f"图片分辨率过大：{width}x{height}，"
                    f"共 {pixel_count} 像素，"
                    f"最大允许 {MAX_IMAGE_PIXELS} 像素"
                )

            # 确认文件确实可以被 Pillow 识别
            image.verify()

    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f"无法识别或读取图片：{error}") from error

    mime_type = FORMAT_TO_MIME[image_format]
    encoded = base64.b64encode(image_bytes).decode("ascii")
    model_url = f"data:{mime_type};base64,{encoded}"

    metadata = {
        "source": str(path),
        "format": image_format,
        "bytes": len(image_bytes),
        "width": width,
        "height": height,
        "pixels": width * height,
    }

    return model_url, str(path), metadata

def build_extra_body(profile: dict) -> dict:
    extra_body = dict(profile.get("extra_body") or {})

    mm_processor_kwargs = dict(
        extra_body.get("mm_processor_kwargs") or {}
    )
    mm_processor_kwargs["max_pixels"] = MAX_IMAGE_PIXELS

    extra_body["mm_processor_kwargs"] = mm_processor_kwargs
    return extra_body

def call(_profile_name: str = DEFAULT_VISION_PROFILE,**kwargs) -> dict:
    ok, error_message = validate_input(**kwargs)
    if not ok:
        return OutputSchema(
            ok=False,
            error=error_message,
            data=None,
        ).model_dump()

    try:
        input_data = InputSchema(**kwargs)

        profile = load_profile(_profile_name)
        model_url, source_label, metadata = resolve_image_source(input_data)
        client = build_client(profile, 180)
        extra_body = build_extra_body(profile)

        request_data = {
            "model": profile["model"],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": input_data.question,
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": model_url,
                            },
                        },
                    ],
                }
            ],
            "extra_body": extra_body,
        }


        response = client.chat.completions.create(**request_data)

        safe_request_data = {
            "model": profile["model"],
            "image_source": source_label,
            "image_metadata": metadata,
            "extra_body": extra_body,
        }
        
        answer = response.choices[0].message.content or ""


        return OutputSchema(
            ok=True,
            error="",
            data=ImageReadResult(
                image_url=source_label,
                question=input_data.question,
                answer=answer,
            ),
        ).model_dump()
    except Exception as e:
        return OutputSchema(
            ok=False,
            error=str(e),
            data=None,
        ).model_dump()

async def acall(_profile_name: str = DEFAULT_VISION_PROFILE,**kwargs) -> dict:
    ok, error_message = validate_input(**kwargs)
    if not ok:
        return OutputSchema(
            ok=False,
            error=error_message,
            data=None,
        ).model_dump()

    try:
        input_data = InputSchema(**kwargs)

        profile = load_profile(_profile_name)
        model_url, source_label, metadata = resolve_image_source(input_data)
        extra_body = build_extra_body(profile)

        request_data = {
            "model": profile["model"],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": input_data.question,
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": model_url,
                            },
                        },
                    ],
                }
            ],
            "extra_body": extra_body,
        }


        async with build_async_client(
            profile,
            timeout=180,
        ) as client:
            response = await client.chat.completions.create(
                **request_data
            )

        safe_request_data = {
            "model": profile["model"],
            "image_source": source_label,
            "image_metadata": metadata,
            "extra_body": extra_body,
        }

        answer = response.choices[0].message.content or ""


        return OutputSchema(
            ok=True,
            error="",
            data=ImageReadResult(
                image_url=source_label,
                question=input_data.question,
                answer=answer,
            ),
        ).model_dump()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return OutputSchema(
            ok=False,
            error=str(e),
            data=None,
        ).model_dump()

def render_result_for_llm(result: dict) -> str:
    output = OutputSchema(**result)

    if not output.ok:
        return (f"工具执行失败：{output.error}"
                "本次图片分析已经失败."
                "可直接说明图片分析超时或失败原因，暂时无法完成"
            )

    return output.data.answer if output.data else "工具执行成功，但没有返回数据。"
