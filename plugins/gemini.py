import base64
from google import genai
from google.genai import types
import PIL.Image as pi
import io
import aiohttp
import asyncio
import os
import tempfile

from config import *
from aiohttp import ClientTimeout

_client = genai.Client(api_key="AIzaSyBppZhcGES4MI1JwgFpSZWsdSw4kxiOEmk")
_MODEL = "gemini-2.0-flash"


async def image_to_text(image):
    if image is None:
        return "Error: Unable to fetch image"

    if not QWEN_API_KEY:
        return "[未配置千问 API Key，无法识别图片]"

    try:
        print("[Qwen]Generating text from image...")
        # Convert to JPEG bytes via PIL
        img = pi.open(io.BytesIO(image))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG")
        jpeg_bytes = buf.getvalue()

        # Encode to base64 data URL
        b64_image = base64.b64encode(jpeg_bytes).decode("utf-8")
        data_url = f"data:image/jpeg;base64,{b64_image}"

        payload = {
            "model": "qwen3-vl-plus",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": "请用中文描述这张图片的内容。要求：1) 先概括图片整体主题 2) 详细列举画面中出现的所有物品、人物、文字 3) 描述场景、环境、氛围 4) 如果有文字请逐字识别并给出翻译（如果是外语）5) 最后给出2-3个可用于搜索这张图片的关键标签。"},
                    ],
                }
            ],
        }

        async with aiohttp.ClientSession(timeout=ClientTimeout(total=60)) as session:
            async with session.post(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {QWEN_API_KEY}",
                    "Content-Type": "application/json",
                },
            ) as response:
                result = await response.json()
                if response.status == 200:
                    text = result["choices"][0]["message"]["content"]
                    print(f"[Qwen]Generated text: {text[:100]}...")
                    return text
                else:
                    error_msg = result.get("error", {}).get("message", str(result))
                    print(f"[Qwen]API Error ({response.status}): {error_msg}")
                    return f"抱歉，图片识别失败：{error_msg}"

    except asyncio.TimeoutError:
        print("[Qwen]Timeout while generating text from image")
        return "抱歉，图片处理超时，请稍后再试。"
    except Exception as e:
        print(f"[Qwen]Error processing image: {e}")
        return f"抱歉，图片处理出现错误：{str(e)}"


async def analyze_video(video_url):
    temp_video_path = None
    try:
        print(f"[Gemini] Fetching video from URL: {video_url}")

        async with aiohttp.ClientSession(timeout=ClientTimeout(total=60)) as session:
            async with session.get(video_url) as response:
                if response.status != 200:
                    return "[视频下载失败]"
                video_data = await response.read()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
            tmp.write(video_data)
            temp_video_path = tmp.name

        print("[Gemini] Uploading video to Gemini...")
        video_file = await _client.aio.files.upload(file=temp_video_path)

        while video_file.state.name == "PROCESSING":
            print("[Gemini] Waiting for video processing...")
            await asyncio.sleep(2)
            video_file = await _client.aio.files.get(name=video_file.name)

        if video_file.state.name == "FAILED":
            return "[视频处理失败]"

        print("[Gemini] Generating text from video...")
        response = await _client.aio.models.generate_content(
            model=_MODEL,
            contents=[
                video_file,
                "请详细描述这个视频的内容，包括发生的事件、人物动作、声音（如果有）等。",
            ],
        )

        print(f"[Gemini] Generated video description: {response.text[:100]}...")
        return f" [视频内容: {response.text}] "

    except Exception as e:
        print(f"[Gemini] Error processing video: {e}")
        return f" [视频分析错误: {str(e)}] "
    finally:
        if temp_video_path and os.path.exists(temp_video_path):
            os.remove(temp_video_path)


if __name__ == "__main__":
    pass
