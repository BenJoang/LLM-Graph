from pathlib import Path

from pydantic import BaseModel, Field

from PIL import Image
import base64
from io import BytesIO
import requests

from src.client.mymodel_client import (
            load_profile,
            build_client,
            save_response_json,
        )

TOOL_NAME = "imageread"
IS_READ_ONLY = True
TOOL_DIR = Path(__file__).resolve().parent


class InputSchema(BaseModel):
    image_url: str = Field(description="要分析的图片 URL")
    question: str = Field(default="描述这张图片的内容", description="针对图片的问题")

class ImageReadResult(BaseModel):
    image_url: str = Field(description="被分析的图片 URL")
    question: str = Field(description="针对图片的问题")
    answer: str = Field(description="模型给出的回答")
    mime_type: str = Field(description="图片的 MIME 类型")
    image_size: int = Field(description="图片的大小（字节）")

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

def download_image(image_url: str) -> tuple[bytes, str]:
    response = requests.get(image_url, timeout=20)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "").split(";")[0].strip()

    if not content_type.startswith("image/"):
        raise ValueError(f"URL 返回的不是图片，content-type={content_type}")

    return response.content, content_type

def resize_image_if_needed(
    image_bytes: bytes,
    mime_type: str,
    max_side: int = 864,
) -> tuple[bytes, str]:
    image = Image.open(BytesIO(image_bytes))
    image.load()

    width, height = image.size
    longest_side = max(width, height)

    if longest_side <= max_side:
        return image_bytes, mime_type

    scale = max_side / longest_side
    new_width = int(width * scale)
    new_height = int(height * scale)

    resized = image.resize((new_width, new_height), Image.Resampling.LANCZOS)

    output = BytesIO()

    if mime_type in ["image/jpeg", "image/jpg"]:
        resized = resized.convert("RGB")
        resized.save(output, format="JPEG", quality=90)
        return output.getvalue(), "image/jpeg"

    if mime_type == "image/png":
        resized.save(output, format="PNG")
        return output.getvalue(), "image/png"

    resized = resized.convert("RGB")
    resized.save(output, format="JPEG", quality=90)
    return output.getvalue(), "image/jpeg"

def call(**kwargs) -> dict:
    ok, error_message = validate_input(**kwargs)
    if not ok:
        return OutputSchema(
            ok=False,
            error=error_message,
            data=None,
        ).model_dump()

    try:
        input_data = InputSchema(**kwargs)

        image_bytes, mime_type = download_image(input_data.image_url)

        image_bytes, mime_type = resize_image_if_needed(
            image_bytes, 
            mime_type,
            max_side=640)

        image_size = len(image_bytes)

        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        image_data_uri = f"data:{mime_type};base64,{image_base64}"

        profile = load_profile("qwen3.6")
        client = build_client(profile, 180)

        actual_request_data = {
            "model":profile["model"],
            "messages":[
                {
                    "role":"user",
                    "content": [
                        {"type":"text", "text": input_data.question},
                        {"type":"image_url",
                          "image_url":{
                            "url":image_data_uri,
                          }
                        }
                    ]
                }
            ]
        }

        log_request_data = {
            "model": profile["model"],
            "messages":[
                {
                    "role":"user",
                    "content": [
                        {"type":"text", "text": input_data.question},
                        {"type":"image", "text": input_data.image_url},
                    ]
                }
            ]
        }
        response = client.chat.completions.create(**actual_request_data)

        save_response_json(
            response, 
            input_data.question, 
            log_request_data,
            filename="imageread_response.json")
        
        answer = response.choices[0].message.content or ""


        return OutputSchema(
            ok=True,
            error="",
            data=ImageReadResult(
                image_url=input_data.image_url,
                question=input_data.question,
                answer=answer,
                mime_type=mime_type,
                image_size=image_size,
            ),
        ).model_dump()
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
