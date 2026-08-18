from pathlib import Path

from pydantic import BaseModel, Field
import asyncio

from PIL import Image
import base64
from io import BytesIO
import requests

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

class InputSchema(BaseModel):
    image_url: str = Field(description="要分析的图片 URL")
    question: str = Field(default="描述这张图片的内容", description="针对图片的问题")

class ImageReadResult(BaseModel):
    image_url: str = Field(description="被分析的图片 URL")
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
        InputSchema(**kwargs)
    except Exception as e:
        return False, str(e)

    return True, ""

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
        image_url = str(input_data.image_url)

        profile = load_profile(_profile_name)
        client = build_client(profile, 180)
        extra_body = dict(profile.get("extra_body") or {})
        extra_body["mm_processor_kwargs"] = {
            "min_pixels": 360 * 128,
            "max_pixels": 1280 * 720,
        }

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
                                "url": image_url,
                            },
                        },
                    ],
                }
            ],
        }


        response = client.chat.completions.create(**request_data)

        save_response_json(
            response, 
            input_data.question, 
            request_data,
            filename="imageread_response.json")
        
        answer = response.choices[0].message.content or ""


        return OutputSchema(
            ok=True,
            error="",
            data=ImageReadResult(
                image_url=input_data.image_url,
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
        image_url = str(input_data.image_url)

        profile = load_profile(_profile_name)
        extra_body = dict(profile.get("extra_body") or {})
        extra_body["mm_processor_kwargs"] = {
            "min_pixels": 360 * 128,
            "max_pixels": 1280 * 720,
        }

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
                                "url": image_url,
                            },
                        },
                    ],
                }
            ],
        }


        async with build_async_client(
            profile,
            timeout=180,
        ) as client:
            response = await client.chat.completions.create(
                **request_data
            )

        save_response_json(
            response, 
            input_data.question, 
            request_data,
            filename="imageread_response.json")
        
        answer = response.choices[0].message.content or ""


        return OutputSchema(
            ok=True,
            error="",
            data=ImageReadResult(
                image_url=input_data.image_url,
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
                "本次图片分析已经失败，不要再次调用imageread。"
                "可直接说明图片分析超时或失败原因，暂时无法完成"
            )

    return output.data.answer if output.data else "工具执行成功，但没有返回数据。"
