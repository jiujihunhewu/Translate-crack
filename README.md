# vn-reverse-engineering

[![CI](https://github.com/jiujihunhewu/Translate-crack/actions/workflows/ci.yml/badge.svg)](https://github.com/jiujihunhewu/Translate-crack/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#)

逆向老式日文视觉小说（galgame）的二进制数据 —— **汉化补丁** + **攻略生成**，一套工具链通吃。

A toolchain for reverse-engineering old Japanese visual novels: extract script text,
build equal-length translation patches, and recover event conditions from the compiled
script VM to generate walkthroughs.

纯 Python 标准库，无第三方依赖，Windows / Linux / macOS 通用。

---

## 能做什么

| 任务 | 命令 |
|---|---|
| 盲猜数据文件的编码 / 混淆 / 文本区 / 页分隔符 | `probe.py` |
| 提取全部剧本文本并重建分页 | `extract.py` |
| 等长注入译文（含四个编码陷阱的自动处理） | `inject.py` |
| 校验补丁没破坏任何非文本数据 | `verify.py` |
| 打包成可分发、可回滚的补丁 | `package.py` |
| 反汇编 dword 型脚本 VM | `vmdis.py` |
| 覆盖率最大化反解未知 opcode 长度 | `oplen.py` |
| 还原每个文本块的显示条件（攻略核心） | `guards.py` |

---

## 快速开始

```bash
git clone <this-repo>
cd vn-reverse-engineering

# 0) 建工作目录
python scripts/init.py work/mygame --title "游戏名" --script game.id

# 1) 把纯净原件放进 work/mygame/orig/，然后探测
python scripts/probe.py work/mygame/orig/game.id

# 2) 按探测结果填 work/mygame/game.json，提取
python scripts/extract.py work/mygame/game.json work/mygame/orig/game.id -o work/mygame/data

# 3) 翻译 data/strings.json -> trans/script_001.json
#    格式: {"041D04": "译文", ...}

# 4) 注入
python scripts/inject.py work/mygame/game.json work/mygame/orig/game.id \
       work/mygame/trans/script_001.json -o work/mygame/build/game.id

# 5) 校验（必须全绿）
python scripts/verify.py work/mygame/game.json work/mygame/orig/game.id \
       work/mygame/build/game.id --trans work/mygame/trans/script_001.json

# 6) 打包
python scripts/package.py work/mygame/game.json --orig work/mygame/orig \
       --build work/mygame/build --out work/mygame/patch
```

攻略部分：

```bash
python scripts/vmdis.py work/mygame/vm.json work/mygame/orig/game.id \
       --pages work/mygame/data/pages.json --xref 531

python scripts/guards.py work/mygame/vm.json work/mygame/orig/game.id \
       --pages work/mygame/data/pages.json -o work/mygame/data/guards.json
```

---

## 五个最容易踩的坑

1. **绝不用 `0x00` 填充空位** —— 多数脚本格式里 `0x00` 是换行控制码，补它会插入换行，
   把后面的行顶出文本框（症状：每屏只显示第一行）。
   用全角空格：GBK 是 `A0 A1`，Big5 是 `A1 40`。
2. **半角 ASCII 必须全角化** —— 引擎按固定 2 字节步进解码时，1 字节字符会打乱后续所有字符的对齐。
3. **lead 字节范围要包含 `0x80` 和 `0xFD`** —— 做 bit0 翻转的引擎，`0x81→0x80` 是极常见的 lead，
   漏掉会把每个长句切断。
4. **比较运算符的极性必须反汇编确认** —— 常见排布是 `0=< 1=<= 2=> 3=>= 4=== 5=!=`，
   但读反会让所有条件结论颠倒。
5. **原始文件只读** —— 拿已打补丁的文件当数据源会提取出乱码。开工先算 md5 存档。

详细说明见 [`references/encoding-traps.md`](references/encoding-traps.md)。

---

## 目录结构

```
vn-reverse-engineering/
├── SKILL.md                  # 技能定义（WorkBuddy / Claude 可直接加载）
├── README.md
├── LICENSE
├── scripts/
│   ├── common.py             # 编码 / 字节 / 全角 / 换行
│   ├── init.py               # 建工作目录
│   ├── probe.py              # 编码探测
│   ├── extract.py            # 文本提取 + 分页
│   ├── inject.py             # 等长注入
│   ├── verify.py             # 校验
│   ├── package.py            # 打包
│   ├── vmdis.py              # VM 反汇编
│   ├── oplen.py              # opcode 长度反解
│   └── guards.py             # 条件还原
├── references/
│   ├── workflow.md           # 完整流程手册
│   ├── encoding-traps.md     # 编码陷阱全集
│   ├── vm-reverse.md         # VM 逆向方法论
│   └── case-kaas.md          # KAAS 引擎实战案例
└── examples/
    └── ww4r-kaas/            # 示例配置（不含游戏数据）
        ├── game.json
        └── vm.json
```

---

## 作为 AI 技能使用

`SKILL.md` 遵循通用 skill 格式（YAML frontmatter + Markdown 正文）。
放进技能目录即可被自动加载：

```bash
# WorkBuddy
cp -r vn-reverse-engineering ~/.workbuddy-ai/skills/

# Claude Code
cp -r vn-reverse-engineering ~/.claude/skills/
```

触发词：提取游戏文本、汉化、做补丁、攻略、事件表、反推剧情、VM 反编译、SJIS 乱码。

---

## 支持范围

**已验证**：KAAS 引擎（DISCOVERY 2004）、dword 型脚本 VM、SJIS + lead-bit0 混淆、
GDI `TextOutA` 文本渲染。

**结构上适用**：其它 dword VM（Adv32 / 自研引擎）、Big5 / EUC-KR 目标编码、
任何「等长回填」的补丁方式。

**不适用**：加壳/加密的数据文件（需先脱壳）、图片文字（需改贴图）、
Unity / Ren'Py 等现代引擎（它们有自己的工具链）。

---

## 免责声明

本工具仅供**技术研究**与**个人已购正版游戏的本地化**使用。

- 不包含、不分发任何游戏本体或游戏数据
- 使用前请确认你拥有该游戏的合法授权
- 请勿将汉化补丁用于商业用途或二次分发游戏本体
- 因使用本工具产生的任何法律问题由使用者自行承担

---

## License

MIT
