---
name: computer-control
description: "通过 PowerShell 调用 Windows 自带能力（user32.dll / System.Drawing / WScript.Shell）模拟鼠标键盘，配合 PaddleOCR 截图识别，实现无需额外安装包的\"看图操控电脑\"。用于需要点击按钮、键盘输入、浏览网页、玩网页游戏等 GUI 自动化任务；不要用于纯命令行或纯文本文件处理（那些优先用 shell_tool / read_file / grep）。"
---

# Computer Control（Windows 无依赖 GUI 操控）

基于"截屏 + OCR + 模拟输入"的电脑操控方法。全程只使用 Windows 自带组件和一个已装 PaddleOCR 的 conda 环境，不安装任何新包、不生成临时文件。

## 前置环境

- 截图与输入：PowerShell（Windows 自带），使用 System.Drawing、user32.dll、WScript.Shell。
- OCR：`E:\Software\Anaconda3\envs\paddleocr\python.exe`（PaddleOCR 3.x + Paddle 3.x + PIL + numpy）。

## 核心流程

### 1. 截屏（PowerShell，全内存不落盘）

```powershell
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$g.Dispose()
$ms = New-Object System.IO.MemoryStream
$bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
$b64 = [Convert]::ToBase64String($ms.ToArray())
$ms.Dispose()
```

- 截屏尺寸与屏幕物理分辨率一致。DPI=96（100% 缩放）时逻辑坐标=物理坐标，无需换算；若系统缩放非 100% 需换算。

### 2. OCR 识别（PaddleOCR）

把 base64 经管道传给 conda 环境 Python：

```text
$b64 | & "E:\Software\Anaconda3\envs\paddleocr\python.exe" scripts\screen_ocr.py --min-score 0.5
```

也可直接内联脚本（要点见下）：

```python
import sys, base64, io, numpy as np
from PIL import Image
from paddleocr import PaddleOCR
img = Image.open(io.BytesIO(base64.b64decode(sys.stdin.read()))).convert("RGB")
arr = np.array(img)                       # 3.x 只接受 numpy 数组或路径
ocr = PaddleOCR(lang="ch")                # 3.x 不要传 log_level
res = ocr.predict(arr)
r = res[0]
for t, s, box in zip(r["rec_texts"], r["rec_scores"], r["rec_boxes"]):
    x1, y1, x2, y2 = box
    print(f"[{s:.2f}] ({(x1+x2)//2},{(y1+y2)//2}) {t}")
```

要点：
- `PaddleOCR(lang="ch")` 不能传 `log_level`（3.x 报 Unknown argument）。
- 输入必须是 `numpy.ndarray`：先 `Image.open(...).convert("RGB")` 再 `np.array`；传 PIL Image 会被忽略（Not supported input data type）。
- 结果字段：`r["rec_texts"]`、`r["rec_scores"]`、`r["rec_boxes"]`（`[x1,y1,x2,y2]`）。

### 3. 点击（user32.dll）

```powershell
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public class MouseAPI {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, UIntPtr e);
}
"@
[MouseAPI]::SetCursorPos($x, $y)
Start-Sleep -Milliseconds 300
[MouseAPI]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)  # LEFT DOWN
Start-Sleep -Milliseconds 120
[MouseAPI]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)  # LEFT UP
```

### 4. 键盘（keybd_event）

```powershell
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public class KeyAPI {
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
}
"@
# 方向键 VK: UP=0x26 DOWN=0x28 LEFT=0x25 RIGHT=0x27；空格=0x20 回车=0x0D
[KeyAPI]::keybd_event(0x26, 0, 0, [UIntPtr]::Zero)          # press
Start-Sleep -Milliseconds 80
[KeyAPI]::keybd_event(0x26, 0, 0x0002, [UIntPtr]::Zero)     # release (KEYEVENTF_KEYUP)
```

## 关键经验（踩坑记录）

1. **按钮点击要向下偏移**：OCR 识别到的是按钮"文字框"，按钮可点击热区通常比文字大、中心偏下。点文字中心易点到按钮上沿甚至上方元素。经验：在文字中心 y 坐标基础上 **+8~+15px** 再点。
2. **窗口置顶/最大化后再定位**：窗口未最大化时，按钮坐标取决于窗口位置与大小，易偏差。先 `SetForegroundWindow`，必要时 `ShowWindow(hwnd, 3)`（SW_MAXIMIZE）。但用户手动调整过窗口时不要擅自最大化，直接重新 OCR 定位即可。
3. **HTML5/Canvas 游戏要先点击画布获得键盘焦点**：`keybd_event` 发方向键前，若焦点不在游戏画布上则按键无效。先 `SetCursorPos` 到画布中央并左键点击一次。验证方法：截图A → 按键 → 截图B → 图像差分看目标区域是否有像素变化。
4. **OCR 读不到图形角色**：角色/怪物/道具是图片，OCR 只能读文字。用**颜色特征**定位（如蓝色系 `B-R>30, 100<B<230`），用**差分法**（移动前后截图对比）确认角色移动。魔塔移动一格约 12px。
5. **数值变化判断游戏事件**：OCR 能读血/攻/防/金币等数值，变化（如生命 60→40、金币+1）说明发生战斗/拾取，据此推断进展。
6. **教学/对话只按一次空格**：网页游戏教学框按空格/回车推进，每次只按一次再截图读内容，避免连按跳过关键教学。
7. **坐标追踪会漂移**：多轮盲走后绝对坐标因累计误差不可靠，用差分法或颜色定位重新获取真实位置。
8. **前台锁定**：`SetForegroundWindow` 偶尔受 Windows 前台锁限制，可用 `(New-Object -ComObject WScript.Shell).AppActivate(标题或PID)` 兜底。

## 故障排查

- 点了没反应：确认窗口在前台、坐标是否偏到按钮上沿、按钮是否需要 hover。
- 按键无效：先点击画布/目标控件获得焦点。
- OCR 为空：确认传入 numpy 数组、语言参数正确、屏幕确有文字。
- 坐标对不上：检查 DPI 缩放是否 100%、窗口位置/大小是否变化。
