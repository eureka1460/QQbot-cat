import jmcomic
import os
import glob
import re
import asyncio
from PIL import Image

# 创建 jmcomic 配置
def _create_option():
    """创建 jmcomic 配置对象"""
    option = jmcomic.create_option_by_file("plugins/option.yml")
    return option


def _natural_key(path):
    """自然排序: 1.jpg < 2.jpg < 10.jpg"""
    parts = re.split(r"(\d+)", os.path.normpath(path).replace(os.sep, "/"))
    return [int(part) if part.isdigit() else part.lower() for part in parts]


async def get_pdf(code):
    """
    使用 jmcomic 下载本子并合成 PDF
    
    Args:
        code: JM 漫画编号 (字符串或数字)
    
    Returns:
        str: PDF 文件路径, 失败返回 0
    """
    temp_dir = os.path.join("Bot", "tmp", str(code))
    pdf_name = os.path.join(temp_dir, f"{code}.pdf")
    
    try:
        # 确保临时目录存在
        os.makedirs(temp_dir, exist_ok=True)
        
        print(f"[JM] 开始下载本子 {code}...")
        
        # Step 1: 使用 jmcomic 下载本子
        option = _create_option()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, jmcomic.download_album, str(code), option)
        
        # Step 2: 收集所有下载的图片 (jpg/png)
        image_files = sorted(
            glob.glob(os.path.join(temp_dir, "**", "*.jpg"), recursive=True)
            + glob.glob(os.path.join(temp_dir, "**", "*.png"), recursive=True),
            key=_natural_key,
        )
        
        if not image_files:
            print(f"[JM] 没有找到下载的图片文件")
            return 0
        
        print(f"[JM] 找到 {len(image_files)} 张图片，开始合成 PDF...")
        
        # Step 3: 用 PIL 打开所有图片, 转为 RGB 模式
        images = []
        for img_path in image_files:
            try:
                img = Image.open(img_path)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                images.append(img)
            except Exception as e:
                print(f"[JM] 跳过文件 {img_path}: {e}")
        
        if not images:
            print(f"[JM] 没有有效的图片")
            return 0
        
        # Step 4: 合成 PDF (第一张为主, 其余附加)
        images[0].save(pdf_name, save_all=True, append_images=images[1:])
        
        # 清理打开的图片
        for img in images:
            img.close()
        
        print(f"[JM] PDF 生成完成: {pdf_name}")
        return pdf_name
        
    except Exception as e:
        print(f"[JM] 下载或生成 PDF 失败: {e}")
        import traceback
        traceback.print_exc()
        return 0