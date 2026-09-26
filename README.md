# FireKB · 面向强引用学科的可溯源 AI 学习库

**教材、规范、课件与课堂录音进；结论可核验的复习材料出。**
每条结论都能落到「文件 + 页码」—— 落不到的，不进材料。

> ## ⚠️ 0.1α · 测试版
>
> 本项目**仍在测试**，功能可用但**接口未冻结**：
> **后续版本可能包含破坏兼容性的改动** —— 配置格式、产物契约（JSONL 字段）、
> CLI 参数与目录结构都可能变，alpha 阶段不做渐进弃用。
>
> · 要稳定请固定 tag：`git checkout v0.1.0-alpha`
> · 升级后先跑 `python -m firekb doctor` 看契约是否仍相符
> · 升级前备份 `20_文本库/` 与 `30_知识点/`（那是你的成果，不是代码）
> · 可能变动的清单与升级建议见 [`CHANGELOG.md`](CHANGELOG.md)
>
> 版本：`0.1.0-alpha`（机器可读）／ **0.1α**（显示）　·　`python -m firekb --version`

> **适用学科**：工科（规范与标准繁多）· 法学（法条与判例必须精确引用）· 医学（指南与剂量不容错）。
> 它们的共同点是：**记错一个数字、引错一句话，代价远大于"没复习到"。**
>
> **项目名称** 来源于实际测试案例消防工程—— 它已跑通完整流程，是本项目最完整的实例；
> 引擎本身与学科无关。

> English: A traceable, citation-first study library — every claim is verifiable against a
> specific file and page; quotes that fail verification are excluded, not silently kept.
> Built for citation-heavy majors: engineering, law, medicine.

> 🔒 **你的资料不出本机**：课堂录音、转写、笔记与复习产物被写死为「绝不出境」
> （同意与 `--allow-egress` 都不放行）；只有教材正文可选择性送往你自备密钥的模型。

---

## 它解决什么问题

用大模型读教材、生成复习资料，最容易出的问题不是"写得不好"，而是**你不知道哪句是真的**：

- 模型把原文改了一个数字（`500mm` → `1500mm`），看起来毫无破绽；
- 模型"总结"出一段原文里根本没有的话；
- 引用的页码是编的，或者指向另一本书；
- 教材里明明有附录数值表，却没被抽到 —— 而且**没人发现**。

这些错误的共同点是**静默**。所以本项目的核心不是"让模型写得好"，而是
**让每一条产出都可核验、让每一次遗漏都看得见**。

### 三条硬规矩

| 规矩 | 做法 |
|---|---|
| **结论必须落到「文件 + 页码」** | 每条知识点携带 `source_file` + 页码范围；引用要能在原文里**逐字**找到 |
| **"找不到"必须报出来，不能猜** | 引用核验分四档（`exact` / `partial` / `fabricated` / `not_found`），只有 `exact` 算通过 |
| **口径只允许一种说法** | 同一件事（课表 / 骨架 / 知识点 / 页码…）摊成一张台账，不一致就报警 |

---

## 核心机制

### 1. 引用四档核验（`kb.verify_quote`）

把模型给出的引用与原文逐字比对，判成四档：

| 档位 | 含义 | 去向 |
|---|---|---|
| `exact` | 逐字命中原文 | 可用 |
| `partial` | 头尾命中、中段有落差 | 进人工复核队列，**不计入通过率** |
| `fabricated` | 只有前缀或尾部命中（"头真尾假"） | **拦下** |
| `not_found` | 连前缀都找不到 | 进人工复核队列 |

两条容易被忽略但很关键的设计：

- **数字走严格通道**：`1.5` 与 `15` 必须区分。宁可牺牲通过率，也不让数字蒙过去。
- **归一化要处理 PDF 的坑**：英文教材 PDF 的文本层大量使用**合字**（`ﬁ` `ﬂ` `ﬃ`），
  而模型引用时写成普通 `fi`/`fl` —— 归一化若不折叠合字，正常引用会被判成「编造」，
  而且集中发生在同一本英文书上（折叠合字能把全库引用精确率从 92.46% 提到 95.09%）。

### 2. 多口径一致性台账（`firekb consistency`）

把"同一门课在不同环节叫什么、有哪些东西"摊成一张表，一次看清**口径裂缝**：

```
课程                          课表     磁盘素材      已入库素材     文本页     骨架    知识点     证据         节覆盖      章覆盖
enclosure fire dynamics     ✓       ✓          ✓       1265    ✓     3205   5109      99.6%   100.0%
消防给水工程                   ✓       ✓          ✓       420     ✓      635   1044      98.6%    95.1%
...
```

出现下面这类情况会直接报警，而不是"谁也没发现"：

- 课表里有这门课，但**没有任何文本页**（某个环节漏跑了）；
- 有骨架但**不在课表**（身份不明的课程）；
- 骨干的章节页码范围只覆盖了 63% 的页（其余页会被塞进退化块）；
- 附录/附表被当成"非正文材料"**整章跳过**（一整章的数值表就此不进抽取范围）。

### 3. 每个环节可追溯（`run_id` + `runs.jsonl`）

每次运行产生一个批次标识（形如 `EXTyyyymmdd-hhmmss-nnnn`，字典序即时间序），写进产出的每一行；
`90_日志/runs.jsonl` 记录每个环节的起止、耗时、退出码、成本、模型与引擎指纹。

**为什么必须这样**：批次标识必须是运行开始时一次性分配、与内容无关的值。
如果拿"逐段生成的时间戳"这类随内容漂移的字段当批次标识，按批次清理数据时
就会误删不属于该批次的行。批次标识不能靠猜。

### 4. 引擎层可替换（`tools/engines/`）

解析 / OCR / 转写三类能力各自接口化，实现由 `00_配置/engines.json` 选择
（本地实现 / 云端实现）。好处：换实现不改业务代码；也让"随包分发第三方二进制"变成
"用户自行配置"，许可义务随之转移。

### 5. 数据出境把关（`tools/engines/egress.py`）

两类数据，**处理方式刻意不同**：

| 类别 | 处理 |
|---|---|
| **个性化数据**（课堂录音、转写、复习产物、笔记、作答记录） | **绝不出境**：写死为硬规则，`--allow-egress` 与同意文件**都不放行** |
| **第三方版权书目**（教材/规范/课件） | 书面禁令 + **不做技术限制**：同意流程照常，责任由使用人承担 |

理由：前者记录的是**使用人自己的学习痕迹**，一旦出境无法收回，不该由一次按钮决定；
后者是"有没有权利上传"的问题，那可以由人授权 —— 但判断"这本书有没有版权"需要现实信息，
代码判不了，所以**诚实写一条禁令**，而不是做一个会判错的硬拦（那会给人"已经合规了"的错觉）。

---

## 快速开始

### 环境

- Python 3.10+（Windows / Linux 均可；本项目在 Windows 上开发与验证）
- **建议用虚拟环境**（下面命令按 venv 写）—— 直接 `python -m pip install` 会装进全局环境，
  多个项目互相影响：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # Linux/macOS：source .venv/bin/activate
```

依赖分三层，**缺哪一层只影响哪一层的环节**，不会挡住整条流水线：

| 层 | 包 | 谁需要它 |
|---|---|---|
| **必需** | `pypdf` + `pypdfium2` | M1 解析（默认引擎：pypdf 取文字、pypdfium2 渲染页面） |
| **必需** | `python-pptx` | 解析 PPTX（只放 PDF 教材也建议装上） |
| 可选 | `rapidocr-onnxruntime` + `numpy` | **只有 M2 OCR**（影印版教材才需要） |
| 可选 | `faster-whisper` + `ffmpeg` | **只有 M3 转写**；`ffmpeg` 必须是**命令行可用**（装完让 PATH 生效） |
| 可选 | `reportlab` | **只有 `firekb testdata`**（生成合成样例） |
| 可选 | LLM API Key（默认 DeepSeek） | M4b 抽取 / M5 校验，走你自己的密钥 |

> 各环节开工前**只校验自己用到的引擎**：没装 OCR 依赖照样能跑 M1 解析。
> `doctor` 会把未就绪的可选引擎列为"软门槛"（退出码 `6`），那不是失败。

### 安装与配置

```powershell
git clone <this-repo> FireKB
cd FireKB
python -m venv .venv ; .\.venv\Scripts\Activate.ps1

python -m pip install pypdf pypdfium2 python-pptx   # 必需
python -m pip install reportlab                     # 想先跑合成样例就装它
python -m pip install rapidocr-onnxruntime numpy    # 扫描版 PDF 的 OCR（可选）

python -m firekb init     # 首次初始化：建目录 + 把 *.example.* 模板复制成真名（幂等，不覆盖）
python -m firekb doctor   # 体检
```

`init` 会告诉你还差什么（通常是填 API Key、放素材、改课表）。
项目根通过**脚本自身位置**自动确定，放在哪个目录都能跑；
多机共用一份数据时可用环境变量 `FIREKB_ROOT` 覆盖。

### 零素材先跑通（推荐第一步）

手上还没有教材也能先确认整条流水线是通的 —— 仓库自带合成样例生成器：

```powershell
python -m firekb testdata      # 生成 PDF/PPTX/EPUB 三个样例 → 10_原始归档\课件\_样例验证\
python -m firekb parse         # 解析它们（应得到若干页文本）
python -m firekb skeleton      # 从样例构建章/节
```

样例只写进 `10_原始归档\课件\_样例验证\`，**不碰你的真实素材**；不想留就整个目录删掉，
再 `python -m firekb parse --force` 重新解析。

> 依赖：`reportlab`（生成 PDF 用）。缺它时 `testdata` 会以**退出码 4** 失败并提示安装命令。

### 第一次运行

```powershell
# 把教材/课件放进 10_原始归档\规范教材\<课程名>\ 与 10_原始归档\课件\<课程名>\
python -m firekb --list        # 看有哪些环节
python -m firekb parse         # M1 解析 → 20_文本库\pages.jsonl
python -m firekb ocr           # M2 扫描页 OCR（影印版才需要）
python -m firekb skeleton      # M4a 知识骨架（章/节结构）
python -m firekb extract       # M4b 知识点抽取（花钱最多的一步，建议先 --limit 3 试跑）
python -m firekb verify        # M5 校验 + 生成人工复核队列
python -m firekb review        # M6 复习产物（背诵卡 / 数字对照表 / Anki 导入…）
```

也可以直接 `python tools\<脚本>.py`，两者等价（统一入口只是转发）。

### 自检与验收

```powershell
python -m firekb doctor          # 退出码见下表
python -m firekb selftest        # 单元自测（原语 + 引擎层 + 出境纪律）
python -m firekb consistency     # 多口径台账
python -m firekb schema          # 产物契约校验
```

`doctor` 退出码**分四档**，刻意把"还没配"和"坏了"分开：

| 退出码 | 含义 | 该怎么办 |
|---|---|---|
| `0` | 全部通过 | — |
| `7` | **尚未初始化**（刚 clone 下来的正常状态） | `python -m firekb init` |
| `6` | 硬门槛全过，仅**可选引擎**未就绪（如没装 whisper） | 不承担转写的机器上属正常 |
| `5` | **硬门槛失败**（环境/契约/一致性/单测） | 必须处理 |

各**环节脚本**另有自己的退出码（与 `doctor` 的四档是两回事）：

| 退出码 | 含义 |
|---|---|
| `0` | 成功 |
| `2` | 前置条件不满足（参数错误、文本库或配置尚未生成） |
| `3` | 守卫拒跑（契约不符、检出静默破坏、缺 API Key 等**明确拒绝开工**的情形） |
| `4` | **跑完了，但有失败项**（部分文件解析失败 / 有失败块 / 样例没生成成功）—— 输出里会列出失败清单 |
| `5` | 该环节自身的硬门槛失败 |

> `4` 的存在是刻意的：早先的做法是"打印了失败清单、退出码却仍是 `0`"，
> 脚本与 CI 无法据此判断这次到底成没成，只能靠人读输出。

---

## 流水线环节

| 环节 | 命令 | 产物 |
|---|---|---|
| 初始化 | `firekb init` | 建目录 + 从模板生成配置（幂等） |
| M1 解析 | `firekb parse` | `20_文本库/pages.jsonl`（页级文本，核心契约文件） |
| M2 OCR | `firekb ocr` | `20_文本库/ocr_results.jsonl`（影印页补文字层） |
| M3 转写 | `firekb transcribe` | `20_文本库/transcript.jsonl`（**权重最低，仅供参考，不作引用来源**） |
| M4a 骨架 | `firekb skeleton` | `30_知识点/skeleton.json`（课 → 章 → 节 + 页码范围） |
| M4a′ 骨架复核 | `firekb review-skel` | AI 复核章节层级（`--apply` 才回写） |
| M4b 抽取 | `firekb extract` | `kp.jsonl` / `evidence.jsonl` |
| M5 校验 | `firekb verify` | `audit.jsonl` + 知识点状态 |
| M5′ 人工复核 | `firekb queue` | 复核工作表（引用与原文**同屏对照**） |
| M6 复习产物 | `firekb review` | 背诵卡 / 数字对照表 / 脉络展开 / Anki 导入 |
| 页码双轨 | `firekb offsets` | 印刷页 ↔ PDF 物理页 的偏移（探测 + 人工登记） |
| 运维 | `firekb doctor` / `consistency` / `schema` / `status` / `runs` | 自检与台账 |

---

## 项目结构

```
firekb.py                 统一入口（python -m firekb <环节>）
tools/
  kb.py                   共享核心：路径常量、契约读写、切块、引用核验、状态
  init_project.py         首次初始化（建目录 + 从模板生成配置）
  parse_docs.py           M1 解析（PDF/PPTX/EPUB）
  ocr_pages.py            M2 OCR
  build_skeleton.py       M4a 骨架（含附录归类判据 classify_kind）
  review_skeleton.py      M4a′ AI 复核骨架
  extract_kp.py           M4b 抽取
  verify_kp.py            M5 校验
  review_queue.py         M5′ 人工复核工作表
  make_review.py          M6 产物
  page_offsets.py         页码双轨
  consistency.py          多口径台账
  schema_check.py         产物契约校验
  fingerprint.py          产物/代码指纹（跨机对账）
  engines/                引擎层（parser / ocr / asr + 出境把关）
  selftest/               单元自测
00_配置/                   配置（带 .example 的是模板）
10_原始归档/               原始素材（**不入版本库**）
20_文本库/ 30_知识点/ 40_复习产物/   派生数据（**不入版本库**）
90_日志/                   运行日志与备份（**不入版本库**）
```

---

## 仓库里**没有**什么（重要）

本仓库**只包含代码、配置模板与文档**。以下内容**被有意排除**（见 `.gitignore`）：

- **原始素材**：教材、规范、课件、课堂录音（第三方版权物与讲课人权益）；
- **派生数据**：`pages.jsonl` / `kp.jsonl` 等 —— 它们**包含教材原文片段**
  （引用核验需要逐字比对），因此同样不对外分发；
- **密钥**：`.env`。

⇒ 也就是说：**clone 下来是一套可以跑的流水线，不是一份现成的复习资料**。
你需要自己准备素材，并确认你有权处理它们。

---

## 已知限制（如实写在前面）

- **输出质量依赖素材的文本层**：影印版需要先 OCR；公式、表格的抽取质量明显低于正文。
- **页码偏移需要按书校准**：同一门课挂多本书时，各书偏移不同，**不能用一个常数修**。
- **面向中文教材**：章节识别、标点归一化按中文习惯调过；英文教材可用但需自行验证。
- 转写（M3）只是课堂线索，**权重最低，永远不作为引用来源**。

---

## 许可与第三方

- 本项目代码：**MIT**（见 `LICENSE`）。
- **默认依赖全部是宽松许可**（pypdf / pypdfium2 / rapidocr / python-pptx）——
  所以**把它打包成应用分发出去是自由的**。
- 唯一带 copyleft 的是可选的 `PyMuPDF`（AGPL-3.0）引擎：默认**不启用**；
  若你显式启用它再打包分发，整个组合就须按 AGPL 履行义务。
  完整清单与边界见 `THIRD-PARTY-NOTICES.md`。
- 数据出境纪律：见 `THIRD-PARTY-NOTICES.md`「出境纪律」一节。

---

## 开发方式

本项目由 **DeepSeek V4.1 Flash** 辅助开发。
