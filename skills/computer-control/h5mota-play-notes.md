# 实战案例：AI 玩 h5mota 魔塔（H5教程塔）

> 本文是 `computer-control` skill 的一次完整实战记录。写这份文档的目的是：**让一个没有参与过本次对话的新会话，仅凭本文就能复现这套"看图操控网页游戏"的完整方法**，并了解魔塔游戏的具体机制与本次踩过的所有坑。

---

## 0. 一句话总结

在**不安装任何新包、不生成任何临时文件**的前提下，用 Windows 自带 PowerShell（user32.dll / System.Drawing / WScript.Shell）模拟鼠标键盘 + 已装 PaddleOCR 的 conda 环境做"屏幕识别"，成功完成了：
- 打开 h5mota 网站 → 进入"玩塔"列表 → 选择 H5教程塔
- 点击「开始游戏」→ 选「入门篇」→ 真正进入游戏
- 用方向键移动角色、按空格推进教学、按 X 打开怪物手册
- 经历了完整的战斗教学，理解了魔塔的核心战斗公式

---

## 1. 环境与前置条件

| 项目 | 值 | 说明 |
|------|-----|------|
| 系统 | Windows | PowerShell 7 |
| 屏幕 | 2560x1440, DPI=96 | **100% 缩放，逻辑坐标 = 物理坐标**，这是坐标能直接用的关键 |
| 浏览器 | Firefox | `C:\Program Files\Mozilla Firefox\firefox.exe`（本机也有 Chrome） |
| OCR 环境 | `E:\Software\Anaconda3\envs\paddleocr\python.exe` | PaddleOCR 3.7.0 + Paddle 3.0.0 + PIL + numpy |
| 项目 skill 目录 | `E:\Code Program\LLM-Graph\skills\computer-control\` | 本文所在位置 |

**没有** playwright / selenium / pyautogui（都未安装），所以全走 Windows 原生 API。

---

## 2. 完整操作流程（从零进入游戏）

### 2.1 打开网站

```powershell
Start-Process -FilePath "C:\Program Files\Mozilla Firefox\firefox.exe" -ArgumentList "--new-window","https://h5mota.com"
```

- h5mota 首页是 `landing.php`，顶部导航有：首页 / 玩塔 / MOD专区 / 造塔 / 论坛 / 群聊。
- "玩塔"列表页的真实地址是 **`https://h5mota.com/play/`**（注意：`play.php` 会 404 显示 "File not found"）。
- 首页 HTML 里可以抓出魔塔链接，格式是 `/tower/?name=xxx`。

### 2.2 找到"玩塔"列表

用 PowerShell 抓 HTML 确认真实链接：

```powershell
$r = Invoke-WebRequest -Uri "https://h5mota.com/landing.php" -UseBasicParsing
[regex]::Matches($r.Content, 'href="([^"]*)"') | % { $_.Groups[1].Value } | ? { $_ -match 'play|tower' }
```

从玩塔列表页 /play/ 选一个塔。本例选了 **H5教程塔**，地址：
`https://h5mota.com/tower/?name=h5course`（它是官方教学塔，短小、专为教操作设计，最适合试玩）。

### 2.3 进入游戏需要"点击"而非"改 URL"

- 打开 `/tower/?name=h5course` 只是"作品主页"（评分、标签、开始游戏/下载/分享按钮）。
- **直接改 URL 加 `#play` 或 `&play=1` 不会进入游戏**，必须点击「开始游戏」按钮，由前端 JS 处理。
- 点击「开始游戏」后，页面才变成游戏界面（窗口标题从"作品主页"变成"HTML5魔塔"）。

### 2.4 点击按钮的坐标定位（关键经验）

按钮坐标用 PaddleOCR 识别文字框后取中心，但**直接点文字中心经常点不中**。

**核心坑（用户多次纠正）：OCR 框是文字框，不是按钮热区；按钮热区通常比文字大、中心偏下。**

- 点击时要在文字中心 y 基础上 **+8~+15px** 再点。
- 点偏上会点到按钮上沿甚至上方元素（本案例就误点过"载入游戏"而不是"开始游戏"）。
- 窗口位置变化会导致坐标全变：本案例窗口**未最大化**时「开始游戏」在 `(451,418)`，**最大化后**变成 `(807,291)`。所以每次操作前要么固定窗口状态，要么重新 OCR 定位。

### 2.5 游戏内的完整点击序列

| 步骤 | 界面 | 按钮/操作 | 屏幕坐标（窗口已缩小后） |
|------|------|-----------|--------------------------|
| 1 | 作品主页 | 点击「开始游戏」 | 第一次 `(451,418)` 失败（点偏上）；最大化后 `(807,291)`，再点 `(807,300)` 成功 |
| 2 | 游戏主菜单 | 点击「开始游戏」（菜单第一项） | `(849,692)`（文字中心 685 下移 7px）|
| 3 | 章节选择 | 点击「入门篇」 | `(855,785)` |
| 4 | 怪物手册 | 点击「返回游戏」 | `(1087,828)` |

> 注意：主菜单有「开始游戏 / 载入游戏 / 录像回放」三项，彼此纵向间距约 44px。点「开始游戏」时若 y 偏移过多就会点到下面的「载入游戏」（本次真实发生过，用户一眼看穿）。

### 2.6 键盘操作

魔塔用方向键移动、空格/回车推进对话、X 打开怪物手册。用 `keybd_event` 发送：

```powershell
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public class KeyAPI {
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
}
"@
# VK: UP=0x26 DOWN=0x28 LEFT=0x25 RIGHT=0x27 空格=0x20 回车=0x0D X=0x58
[KeyAPI]::keybd_event(0x26, 0, 0, [UIntPtr]::Zero)      # 按下
Start-Sleep -Milliseconds 80
[KeyAPI]::keybd_event(0x26, 0, 0x0002, [UIntPtr]::Zero) # 松开 (KEYEVENTF_KEYUP)
```

### 2.7 HTML5/Canvas 游戏必须先点画布获得焦点（极其关键）

**现象**：一开始狂按方向键，角色根本不动（图像差分几乎无变化）。

**原因**：焦点不在游戏 canvas 上，键盘事件没发给游戏。

**解法**：先 `SetCursorPos` 到画布中央并左键点击一次，再发方向键。
- 本案例画布中央约 `(1100,580)`。
- 点击后图像差分从 **1 个采样点** 跳到 **1936 个采样点**，证明角色开始移动了。

**验证移动是否生效的方法（图像差分）**：
1. 截屏 A
2. 按一下方向键
3. 截屏 B
4. 对比游戏区域像素，差异大=移动了；几乎无差异=没移动。

---

## 3. 魔塔游戏机制（从教学学到的知识）

H5教程塔的教学是"仙子"NPC 一步步引导的，按空格推进对话。学到的核心机制：

### 3.1 角色与状态栏
- 角色是**蓝色斗篷的勇士**（颜色特征见 §4）。
- 状态栏显示：金币、生命、攻击、防御、经验、状态、难度。本案例起始：生命60 / 攻10 / 防10 / 金币0 / Easy。

### 3.2 回合制战斗
- **你砍一刀，怪咬一口，直到一方死亡**。
- 实际游戏会**省略回合过程直接结算**（教学里特意演示了完整回合）。
- **击杀怪物的最后一刀不受反击伤害**（教学明确："因为你已经击败了它，所以它没有反击对你造成伤害"）。

### 3.3 伤害公式
**伤害 = 攻击方攻击力 - 防御方防御力**

- 主角(攻10) 打 史莱姆(防1)：每次造成 9 点伤害。
- 史莱姆(攻20) 打 主角(防10)：每次造成 10 点伤害。
- 主角生命 60→40→30→20：前两回合各被咬 10，第三回合击杀（未受反击），共掉 20 血。
- 打完后获得 1 金币，地图上史莱姆显示的数字"20" = **打它总共要掉的血**。

### 3.4 怪物手册（按 X 打开）
按 X 或点击打开手册，显示怪物详情。以绿色史莱姆为例：
- 生命 20 / 攻击 20 / 防御 1 / 金币 1 / 临界 1 / 减伤 10 / "1防2"
- "临界"和"防御减伤"是进阶机制（教学讲到但画面已推进，未截全）。

### 3.5 地图与移动
- 移动一格约 **12px**（y 方向，屏幕物理像素）。
- 地面灰色 `rgb(64,64,64)` / `rgb(96,96,96)`；墙棕色 `rgb(112,64,32)`；顶部有红色装饰带 `rgb(222,97,99)`。
- 主角周围左右可能是墙（棕色），上下是可走走廊（灰色）——本案例主角被卡在一个垂直走廊里，上下可走、左右是墙。

---

## 4. 视觉感知技术（OCR 读不到图形时怎么办）

**OCR 只能读文字，读不到角色/怪物/道具图形**。要"看到"它们，用以下方法：

### 4.1 定位主角：颜色特征 + 差分法

主角是蓝色系，颜色特征（先抽样获得）：
- `rgb(96,120,184)`、`rgb(24,72,144)`、`rgb(72,96,192)`、`rgb(136,168,208)`

严格蓝色掩码（可避免误抓到 UI/边框大色块）：
```python
blue = (b - r > 40) & (b > 90) & (b < 200) & (g > 50) & (g < 160) & (r < 120)
```

**关键坑**：
- 松一点的掩码（如 `b-r>30`）会把游戏 UI 蓝色面板、地图右边框（x≈1644 的竖条）一起抓进来，导致"主角大小 665x177"这种荒谬结果。
- **正确做法**：用严格掩码 + 聚类，主角是约 12x16 的**小块**（几十到一两百像素），而不是巨大的连通区域。
- 本案例用严格蓝 + 10px 网格聚类，成功锁定主角真实位置在 **x≈944** 的垂直走廊（y 随移动在 830→540→490→450 之间变化）。

### 4.2 判断"是否移动了"：图像差分

```python
diff = np.abs(a.astype(int) - b.astype(int)).sum(axis=-1)
ys, xs = np.where(diff > 50)   # 阈值按需调
```
- 排除干扰区（右侧 LLM-Graph 界面 x>1650、顶部浏览器标签 y<130、时间戳跳动点）。
- 差异集中在主角 bbox 附近 = 移动成功。

### 4.3 判断"周围有什么"：邻格颜色分析

以主角中心 `(cx,cy)` 为原点，步长 step=12px，采样上下左右相邻格子的主色：
- 灰色 = 可走的地面
- 棕色 = 墙（不可走）
- 蓝色 = 门/楼梯/特殊通道
- 红色/绿色/黄色 = 怪物/道具/门

### 4.4 判断"游戏事件"：状态栏数值变化

OCR 读状态栏数字，数值变化即事件：
- 生命 60→40→30→20 = 战斗受伤（验证了 10/回合）
- 金币 0→1→2 = 击杀怪物拾取金币
- 这些是"盲玩"时判断进展的最可靠信号。

---

## 5. 踩坑记录（按重要性排序）

1. **按钮点击偏上问题（本次最大坑）**：OCR 文字框中心 ≠ 按钮热区中心，热区偏下。点文字中心会点到上方元素。修正：y+8~15px。用户多次提醒"你点的位置实际上比那个按钮偏上"。
2. **窗口状态改变坐标**：未最大化 vs 最大化，按钮坐标完全不同。先 `SetForegroundWindow`，必要时 `ShowWindow(hwnd,3)`；但用户手动调过窗口时不要擅自最大化，重新 OCR 定位即可。
3. **Canvas 游戏键盘焦点**：不点击画布，方向键全部无效。必须先点画布。
4. **误点"载入游戏"**：主菜单「开始游戏」和「载入游戏」挨得近（间距约 44px），y 偏移过头就点错。
5. **坐标追踪漂移**：多轮盲走后绝对坐标不可靠，用差分/颜色重新定位。
6. **OCR 误抓大色块**：松掩码会把 UI 面板、边框当主角。用严格掩码 + 限制块大小。
7. **`#play` URL 方式无效**：改 URL 进不了游戏，必须点击按钮。
8. **DPI**：本机 100%（96 DPI）无需换算；其他机器若缩放≠100% 必须换算物理/逻辑坐标。
9. **前台锁定**：`SetForegroundWindow` 可能受 Windows 前台锁限制，可用 `(New-Object -ComObject WScript.Shell).AppActivate(PID)` 兜底。
10. **PaddleOCR 3.x 细节**：不能传 `log_level`；输入必须是 numpy 数组（PIL 对象会被忽略）；结果在 `r["rec_texts"]` / `r["rec_scores"]` / `r["rec_boxes"]`。

---

## 6. 局限性（诚实说明）

- **看不到完整地图**：OCR 只能读文字，颜色分析只能定位色块，无法像人眼一样看到整张地图布局。所以探索是"盲走 + 状态感知"，效率低、容易迷路。
- **教学文字可能被跳过**：连按空格会跳过教学（本案例早期按太多次跳过了一些内容）。正确做法是**每按一次空格就截屏读一次**。
- **纯色块分析在复杂场景会误判**（如把 UI、边框当目标）。需要视觉模型才能真正"看懂"画面。
- 若要完整自动通关，建议结合 vision 模型（如项目里的 `imageread`/`qwen3-vl`）做真正的"看图决策"，本 skill 提供的是"无视觉时的降级方案"。

---

## 7. 可复用脚本

本 skill 提供 `scripts/screen_ocr.py`：从 stdin 读截屏 PNG 的 base64，输出带坐标的 OCR 文本，支持 `--min-score` 和区域过滤。用法：

```powershell
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size); $g.Dispose()
$ms = New-Object System.IO.MemoryStream
$bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
$b64 = [Convert]::ToBase64String($ms.ToArray()); $ms.Dispose()
$b64 | & "E:\Software\Anaconda3\envs\paddleocr\python.exe" "E:\Code Program\LLM-Graph\skills\computer-control\scripts\screen_ocr.py" --min-score 0.5
```

---

## 8. 复现清单（给新会话）

如果新会话想重新走一遍：
1. 确认 conda 环境 `paddleocr` 可用、屏幕是 100% 缩放。
2. 用 Firefox 打开 `https://h5mota.com/tower/?name=h5course`。
3. OCR 定位「开始游戏」→ 文字中心 y+10 点击 → 进游戏主菜单。
4. 主菜单点「开始游戏」（注意别点成"载入游戏"）→ 章节选「入门篇」。
5. 进游戏后**先点击画布中央**获得焦点，再用方向键移动。
6. 每次按空格前先截图读教学，按一次推进一次。
7. 用严格蓝色掩码定位主角、用差分法验证移动、用状态栏数值变化判断事件。
