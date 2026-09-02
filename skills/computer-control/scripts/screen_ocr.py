#!/usr/bin/env python3
"""从 stdin 读入截屏 PNG 的 base64，用 PaddleOCR 识别，输出带坐标的文本。

用法（PowerShell）：
  $b64 | & "E:/Software/Anaconda3/envs/paddleocr/python.exe" scripts/screen_ocr.py --min-score 0.5

参数：
  --min-score FLOAT  过滤低置信度文本（默认 0.5）
  --min-x/--max-x/--min-y/--max-y  只输出该屏幕矩形内的文本
  --lang LANG        PaddleOCR 语言（默认 ch）
"""
import sys, base64, io, argparse
import numpy as np
from PIL import Image
from paddleocr import PaddleOCR


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-score", type=float, default=0.5)
    ap.add_argument("--min-x", type=int, default=None)
    ap.add_argument("--max-x", type=int, default=None)
    ap.add_argument("--min-y", type=int, default=None)
    ap.add_argument("--max-y", type=int, default=None)
    ap.add_argument("--lang", default="ch")
    args = ap.parse_args()

    data = sys.stdin.read().strip()
    img = Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")
    arr = np.array(img)

    ocr = PaddleOCR(lang=args.lang)  # 3.x: 不要传 log_level
    res = ocr.predict(arr)
    for r in res:
        for t, s, box in zip(r["rec_texts"], r["rec_scores"], r["rec_boxes"]):
            if s < args.min_score:
                continue
            x1, y1, x2, y2 = box
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            if args.min_x is not None and cx < args.min_x: continue
            if args.max_x is not None and cx > args.max_x: continue
            if args.min_y is not None and cy < args.min_y: continue
            if args.max_y is not None and cy > args.max_y: continue
            print(f"[{s:.2f}] ({x1},{y1})-({x2},{y2}) {t}")


if __name__ == "__main__":
    main()

