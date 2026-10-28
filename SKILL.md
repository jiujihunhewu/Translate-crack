---
name: vn-reverse-engineering
description: 逆向老式日文视觉小说 / galgame 的二进制数据，做简体中文汉化补丁，并从脚本 VM 反推完整攻略。当用户要求「提取游戏文本」「汉化这个游戏」「做个汉化补丁」「反推剧情/事件表」「做攻略」「查事件触发条件」，或给出 .id/.dat/.pak/.arc 之类的脚本数据文件、提到 SJIS 乱码、字库、等长回填、VM 反编译时使用。Also use for reverse-engineering dword-oriented script VMs, Shift-JIS text extraction, equal-length patch injection, and walkthrough generation from compiled scenario data.
agent_created: true
---

# 视觉小说逆向 / 汉化 / 攻略生成

处理 2000 年代日式视觉小说引擎（KAAS、Adv32、NScripter 变体、自研 VM）的完整工具链：
**文本提取 → 翻译 → 等长注入 → 补丁打包**，以及 **脚本 VM 反汇编 → 条件还原 → 攻略生成**。

两条主线共用同一套底层（编码探测 + 字节结构 + VM 解码），不要分开做。

## 何时使用

- 用户给出游戏数据文件，要求提取/汉化/做补丁
- 游戏文本是日文，显示成乱码，或需要改成中文
- 要求「攻略」「事件表」「触发条件」「为什么剧情不出来」
- 需要反汇编自研字节码 / 分析 dword 数组构成的指令流

## 铁律（先看这条，能省几小时）

1. **原始文件只读**。所有工作基于 `orig/` 里的纯净副本。玩家装过补丁的文件已不是原版，拿它当数据源会提取出乱码。开工第一步先算 md5 存档。
2. **绝不用 `0x00` 填充空位**。多数脚本格式里 `0x00` 是换行控制码，补 0x00 会插入换行把后面的行顶出文本框（症状：每屏只显示第一行、句子半截断掉）。用全角空格：GBK 是 `A0 A1`，Big5 是 `A1 40`。
3. **半角 ASCII 必须全角化**。引擎按固定 2 字节步进解码时，1 字节的半角字符会打乱后续所有字符（症状：`A` 显示成 `@`，`1` 显示成 `0`）。
4. **等长注入不能静默截断**。译文超预算时回退原文并报告，让译者去缩短，不要砍字。
5. **比较运算符的极性要反汇编确认**，不要猜。`0=< 1=<= 2=> 3=>= 4=== 5=!=` 是常见排布，但必须对着 handler 验证一次，读反会让所有条件结论颠倒。

## 主线 A：汉化补丁

```bash
# 0) 建工作目录
python scripts/init.py work/mygame --title "游戏名" --script game.id

# 1) 探测编码 / 区域 / 页分隔符（盲猜，30 秒出结果）
python scripts/probe.py work/mygame/orig/game.id

# 2) 按探测结果填 work/mygame/game.json，然后提取
python scripts/extract.py work/mygame/game.json work/mygame/orig/game.id -o work/mygame/data

# 3) 翻译 data/strings.json -> trans/script_001.json（键用 '041D04' 偏移或原文）
# 4) 注入
python scripts/inject.py work/mygame/game.json work/mygame/orig/game.id \
       work/mygame/trans/script_001.json -o work/mygame/build/game.id

# 5) 校验（必须全绿才继续）
python scripts/verify.py work/mygame/game.json work/mygame/orig/game.id \
       work/mygame/build/game.id --trans work/mygame/trans/script_001.json

# 6) 打包
python scripts/package.py work/mygame/game.json --orig work/mygame/orig \
       --build work/mygame/build --out work/mygame/patch
```

`verify.py` 的 `non-text drift` 必须为 `none`，`odd length` 必须为 0。任一项不满足说明槽位表算错了，此时打包出去会损坏游戏。

### 字体 / 代码页

文本走 GDI `TextOutA` 时，显示哪国文字由**字体字符集**决定，不是由字符串编码决定。原版字符集 `SHIFTJIS(0x80)`，要显示 GBK 中文必须改成 `GB2312(0x86)` + SimSun/微软雅黑。改法见 `references/encoding-traps.md` 的「字体 patch」一节。

### 位图文字改不了

标题菜单、CG 画廊、设置界面的文字常是预渲染贴图，不在文本层。补丁覆盖不到，如实告知用户，不要承诺。

## 主线 B：VM 逆向与攻略

```bash
# 1) 反汇编（配置驱动，opcode 表在 vm.json）
python scripts/vmdis.py work/mygame/vm.json work/mygame/orig/game.id \
       --pages work/mygame/data/pages.json --list 12000 12100

# 2) 建全量指令表
python scripts/vmdis.py work/mygame/vm.json work/mygame/orig/game.id --dump data/ins.json

# 3) 已知 opcode 长度不够时，用覆盖率最大化反解
python scripts/oplen.py work/mygame/vm.json work/mygame/orig/game.id \
       --unknown 0x15,0x17,0x1F -o vm_solved.json

# 4) 还原每个文本块的显示条件（攻略的核心数据）
python scripts/guards.py work/mygame/vm.json work/mygame/orig/game.id \
       --pages work/mygame/data/pages.json -o data/guards.json
```

常用查询：

```bash
--funcs            # 列所有函数入口
--callers 8315     # 谁调用了这个函数
--xref 531         # 变量 v531 的所有读写点（找剧情耦合最快的手段）
--body 15443       # 打印一个函数体
--texts            # 列所有文本块
```

`guards.py` 的输出直接就是攻略的骨架：每个文本块带着 `{变量, 比较符, 常数}` 的条件。把它按函数分组、按变量取值分档，就是「动摇度 → 事件」对照表。

## 关键技巧

**先读游戏自己给的提示文本，再追调度链。** 脚本里的提示语、教程、旁白往往直接说明了机制。反汇编一个 3000 行的调度器之前，先用 `--texts` 搜一遍关键词，通常能省掉大半工作。

**用说话人归属判断变量归属。** 当多个角色共用一套数据结构、变量编号相邻时，靠代码顺序猜容易错。正确做法：统计每个变量控制范围里「独占文本块」的说话人，谁出现多就是谁。这个判据在本项目里纠正了一次角色互换的错误。

**跨角色耦合藏在 `if_any` 复合条件里。** 用 `--xref <动摇度变量>` 扫一遍，如果 A 角色的代码里读了 B 角色的进度变量，两者就是联动的，只练一个会卡死。

**进度计数器往往比数值门槛更硬。** 用户报「数值够了但剧情不出」时，先找章节/天数计数器，而不是继续调数值。

## 脚本清单

| 脚本 | 作用 |
|---|---|
| `scripts/init.py` | 建工作目录 + 配置模板 |
| `scripts/probe.py` | 盲猜编码 / xor / 文本区 / 页分隔符 |
| `scripts/extract.py` | 提取文本 + 重建分页 |
| `scripts/inject.py` | 等长回填（含全角化、奇偶对齐、超预算回退） |
| `scripts/verify.py` | 校验补丁（尺寸 / 非文本区漂移 / 可解码率） |
| `scripts/package.py` | 打包补丁（含安装/卸载脚本 + md5 清单） |
| `scripts/vmdis.py` | VM 反汇编 + 查询（list/funcs/callers/xref/body/texts） |
| `scripts/oplen.py` | 覆盖率最大化反解 opcode 长度 |
| `scripts/guards.py` | 文本块条件还原（守卫传播） |
| `scripts/common.py` | 编码 / 字节 / 全角 / 换行公共库 |

纯标准库，Python 3.9+，Windows / Linux / macOS 通用。

## 参考文档

- `references/workflow.md` — 完整流程手册，含每一步的判据与失败处理
- `references/encoding-traps.md` — 编码陷阱全集（lead xor、全角、奇偶、pad、字体 patch）
- `references/vm-reverse.md` — VM 逆向方法论（找 ip 变量、读长度表、运算符极性、守卫传播）
- `references/case-kaas.md` — KAAS 引擎完整实战案例（含 ww4r 的全部结论）

## 交付纪律

- 报告数字要真实：`translated 22143 / kept 0 / over 0 / odd 0` 这种能对上账的统计才写。
- 未跑的步骤标 `NOT RUN`，附下一条可复现命令，不要含糊过去。
- 有推测成分的结论要标注（例：「部位 4 = 臀部」是按 7 条台词推断的）。
- 补丁必须带 `backup/` 和卸载脚本，没有回滚路径的补丁是陷阱。
