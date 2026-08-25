<img width="475" height="467" alt="Alexandria Logo" src="https://github.com/user-attachments/assets/fa2c36d3-a5f3-49ab-9dfe-30933359dfbd" />

# Alexandria 有声书生成器（Alexandria Audiobook Generator）

[English](README.md) | 中文

> **给新用户的说明：** Alexandria 最近突然受到大量关注并涌入许多新用户。作为一个小型项目，我可能无法及时回复每一个问题。在提交 issue 之前，请先通读本 README 和 [Wiki](https://github.com/Finrandojin/alexandria-audiobook/wiki) —— 大多数常见问题都已经在那里给出了答案。感谢您的耐心！

使用 AI 驱动的脚本标注（script annotation）和文本转语音（TTS），把任何一本书或小说变成一本完整配音的有声书。内置 Qwen3-TTS 引擎，支持批处理，并提供基于浏览器的编辑器，让你在最终导出前逐行精调。

## 示例：[sample.mp3](https://github.com/user-attachments/files/25276110/sample.mp3)


## 截图（Screenshots）

<img src="https://github.com/user-attachments/assets/874b5e30-56d2-4292-b754-4408fc53f5d6" width="30%"></img> <img src="https://github.com/user-attachments/assets/488cde02-6b93-47fa-874b-97a618ae482c" width="30%"></img> <img src="https://github.com/user-attachments/assets/4c0805a6-bb9d-42c1-a9ff-79bb29d0613c" width="30%"></img> <img src="https://github.com/user-attachments/assets/8e58a5bf-ed8f-4864-8545-1e3d9681b0cf" width="30%"></img> <img src="https://github.com/user-attachments/assets/531830da-8668-4189-a0dc-020e6661bfb6" width="30%"></img>

## 功能特性（Features）

### AI 驱动的流水线（AI-Powered Pipeline）
- **本地与云端 LLM 支持** —— 可使用任何 OpenAI 兼容 API（LM Studio、Ollama、OpenAI 等）
- **自动脚本标注** —— 一个串行的 9-walk LLM 流水线（2a→2i）把你书中的内容转换为带说话人、对白和 TTS 指令方向的结构化 spans
- **置信度审核（Confidence Review）** —— 低置信度的标注会被标记出来供人工审核（接受 / 拒绝 / 覆盖），而不是静默地传播错误
- **语音试听与分配（Voice Audition & Assignment）** —— 流水线为每个角色生成语音描述，对照你的语音库试听，并自动分配语音 —— 从一本书到完整配音阵容只需一次点击
- **说话人别名（Speaker Aliases）** —— 说话人名称的变体会在标注过程中自动解析为某个单一角色的别名（例如 "YOUNG ELENA" → "ELENA"），因此各变体会共享同一个语音配置

### 语音生成（Voice Generation）
- **内置 TTS 引擎** —— Qwen3-TTS 在本地运行，无需外部服务器
- **外部服务器模式** —— 可选地连接到远程 Qwen3-TTS Gradio 服务器
- **多语言支持** —— 英语、中文、法语、德语、意大利语、日语、韩语、葡萄牙语、俄语、西班牙语，或自动检测
- **自定义语音** —— 9 个预训练语音，支持基于指令的情绪/语调控制
- **语音克隆（Voice Cloning）** —— 用 5–15 秒的参考音频样本克隆任意语音
- **语音设计师（Voice Designer）** —— 根据文本描述创建新语音（例如 "A warm, deep male voice with a calm and steady tone"）
- **LoRA 语音训练** —— 在自定义语音数据集上对 Base 模型进行微调，创建具有指令跟随能力的持久语音身份
- **内置 LoRA 预设** —— 开箱即用地内置预训练语音适配器，即可分配给角色
- **数据集构建器（Dataset Builder）** —— 用于创建 LoRA 训练数据集的交互式工具，支持逐样本的文本、情绪和音频预览
- **批处理** —— 同时生成几十个 spans，吞吐量为实时速度的 3–6 倍
- **编解码器编译（Codec Compilation）** —— 可选的 `torch.compile` 优化，批量解码速度提升 3–4 倍
- **非语言声音（Non-verbal Sounds）** —— LLM 会写出自然的发声（"Ahh!"、"Mmm..."、"Haha!"），并附上上下文感知的指令方向
- **自然停顿（Natural Pauses）** —— 可在不同说话人之间（默认 500 ms）和同一说话人的相邻片段之间（默认 250 ms）配置停顿，支持按书覆盖以及按 span 编辑停顿。解析后的停顿值会在 M4B 导出时被插入到合并后的音频中

### Web UI 编辑器（Web UI Editor）
- **精简的界面** —— 核心流水线标签页（Setup、Script、Voices、Editor、Projects、Workbench）以及高级工具（Designer、Preparer、Dataset Builder、Training）和工作流工具（Persona、Prompt Config）
- **Span 编辑器** —— 编辑任意一行的说话人、文本和指令
- **结构操作（Structural Operations）** —— 直接在编辑器中拆分、合并、移动和删除 spans
- **批处理** —— 经过优化的批量渲染，支持子批次划分，以实现高效的 GPU 利用
- **实时进度** —— 实时的 walk 状态和渲染进度：单个渲染显示每个分块的已完成/总数计数并带有失败徽标，批量渲染显示任务级进度
- **音频预览** —— 预览语音、试听单个已渲染的 spans，并在最终下载前播放整本渲染完成的书

### 导出选项（Export Options）
- **M4B 有声书** —— 带章节标记的分章 M4B（AAC），从 Editor 标签页通过元数据表单（title、author、narrator、year、description + 可选封面）导出。可在 Audiobookshelf、Apple Books、VLC 等中播放
- **MP3** —— 当后端的 ffmpeg 支持 MP3（libmp3lame）时生成 MP3 导出；否则导出将降级为仅 M4B 并给出明确提示。下载链接会根据能力标记（capability flag）出现，并指向同源服务路由 `GET /api/pipeline/export/mp3/{job_id}`
- **Audacity 包（Audacity Bundle）** —— 用于 DAW 编辑的原始 WAV 分块的 ZIP_STORED 打包，随 M4B 一同生成。下载链接会根据能力标记出现，并指向同源服务路由 `GET /api/pipeline/export/audacity/{job_id}`
- **原始分块（Raw Chunks）** —— 以 ZIP 形式下载已渲染的音频分块，供 DAW 编辑或手动拼装

## 系统要求（Requirements）

- Docker（含 Compose），或本地 Python 环境
- LLM 服务器（以下之一）：
  - [LM Studio](https://lmstudio.ai/)（本地）—— 推荐：Qwen3 或类似模型
  - [Ollama](https://ollama.ai/)（本地）
  - [OpenAI API](https://platform.openai.com/)（云端）
  - 任何 OpenAI 兼容 API
- **GPU：** 最低 8 GB 显存，推荐 16 GB+ —— 参见下方兼容性表格
  - 每个 TTS 模型占用约 3.4 GB；剩余显存决定批大小
  - 所有平台都支持 CPU 模式，但会显著更慢
- **内存（RAM）：** 推荐 16 GB（最低 8 GB）
- **磁盘：** 约 20 GB（8 GB venv/PyTorch、约 7 GB 模型权重、音频工作空间）

### GPU 兼容性（GPU Compatibility）

| GPU | 操作系统 | 状态 | 驱动要求 | 备注 |
|-----|-----|--------|-------------------|-------|
| **NVIDIA** | Windows | 全面支持 | Driver 550+（CUDA 12.8） | 内置 Flash attention，编码更快 |
| **NVIDIA** | Linux | 全面支持 | Driver 550+（CUDA 12.8） | 内置 Flash attention + triton |
| **AMD** | Linux | 全面支持 | ROCm 6.3+ | 自动应用 ROCm 优化 |
| **AMD** | Windows | 仅 CPU | N/A | 不支持 GPU 加速 —— 应用以 CPU 模式运行。如需 AMD 的 GPU 加速，请使用 Linux |
| **Apple Silicon** | macOS | 仅 CPU | N/A | 目前不支持 MPS 加速。可用但较慢 |
| **Intel** | macOS | 仅 CPU | N/A | |

> **注：** 无需外部 TTS 服务器。Alexandria 内置 Qwen3-TTS 引擎，可直接加载模型。模型权重会在首次使用时自动下载（每个模型变体约 3.5 GB）。

> **文档：** 有关语音类型、LoRA 训练、批量生成等的深入指南，请参阅 [Wiki](https://github.com/Finrandojin/alexandria-audiobook/wiki)。

## 运行 Alexandria（Running Alexandria）

Alexandria 可以在本地、Docker、Pinokio 或 Google Colab 中运行。
对于可复现的部署，推荐使用 Docker；Pinokio 和 Colab 是为喜欢引导式安装的用户提供的便捷安装方式。

### Pinokio

使用其 GitHub URL 从 Pinokio 安装本仓库，然后点击
**Install（安装）** 和 **Start（启动）**。Pinokio 会创建应用环境，
安装依赖和 Qwen3-TTS，为主机选择合适的 PyTorch 构建，
并在其报告的本地 URL 打开 Web UI。

### Google Colab

在 Colab 中打开 [`alexandria_colab.ipynb`](alexandria_colab.ipynb)，在运行单元格之前选择一个
GPU 运行时。该 notebook 会克隆当前仓库，将文件和 Hugging Face 模型权重持久化到 Google Drive，并
通过 Colab 端口转发暴露 Web UI。脚本标注仍然需要一个 LLM API；
可选的 Ollama 单元格可以提供本地 LLM，但在 TTS 生成之前应停止 Ollama 以释放 GPU 显存。

### Docker Compose（推荐）

针对 NVIDIA GPU 部署：

```bash
git clone https://github.com/xiaden/alexandria-audiobook.git
cd alexandria-audiobook
docker compose up --build
```

这需要已安装 [Docker](https://docs.docker.com/get-docker/) 以及
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)。
Web UI 可从 `http://localhost:4200` 访问。TTS 模型在
首次使用时下载，并缓存在一个 Docker 卷中。用户数据（上传、语音
配置、训练好的 LoRA 适配器以及音频输出）通过挂载的
项目目录持久化。

### 本地开发（Local development）

```bash
git clone https://github.com/xiaden/alexandria-audiobook.git
cd alexandria-audiobook
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r app/requirements.txt
pip install qwen-tts==0.1.1
python app/app.py
```

Web UI 可从 `http://localhost:4200` 访问。

## 首次启动 —— 会发生什么（First Launch — What to Expect）

如果你是第一次运行 Alexandria，请先阅读本节。

### 1. 首先你需要一个正在运行的 LLM 服务器

Alexandria **不** 内置 LLM —— 它通过 API 连接到一个 LLM。在生成脚本之前，你必须启动以下之一：

| 服务器 | 默认 URL | 安装 |
|--------|-------------|---------|
| [LM Studio](https://lmstudio.ai/) | `http://localhost:1234/v1` | 下载并加载一个模型，然后启动服务器 |
| [Ollama](https://ollama.ai/) | `http://localhost:11434/v1` | `ollama run qwen3` |
| [OpenAI API](https://platform.openai.com/) | `https://api.openai.com/v1` | 获取 API key |

如果在启动标注 walks 时 LLM 服务器没有运行，生成将会失败。请查看应用终端中的错误详情。

### 2. 首次 TTS 生成会下载约 3.5 GB

TTS 模型**不**包含在安装包中。它们会在你首次生成音频时自动从 Hugging Face 下载。这是正常现象：

- **每个模型变体约 3.5 GB**（CustomVoice、Base/Clone、VoiceDesign）
- 只会下载你使用的变体（大多数用户从 CustomVoice 开始）
- 下载在后台进行 —— 请查看应用终端了解进度
- 在此期间 Web UI 可能看起来像卡住了。其实没有 —— 它只是在等待下载完成
- 首次下载之后，模型会被缓存在本地，几秒内就能加载完成

> **小贴士：** 如果下载看起来卡住了，请检查你的网络连接。如果失败，请重启应用再试一次 —— 它会从上次中断的地方继续。

### 3. 首次批处理会有额外的预热时间

会话中第一次批量生成会比后续的耗时更长：

- **MIOpen 自动调优**（AMD GPU）：GPU 内核优化器每个会话运行一次，会增加 30–60 秒
- **编解码器编译**（如果启用）：一次性约 30–60 秒预热，之后所有剩余批次快 3–4 倍
- **这是正常现象。** 首次批处理之后，生成速度会稳定下来

### 4. 显存决定你能做什么

| 可用显存 | 能做什么 |
|---------------|------------|
| 8 GB | 一次一个模型，小批量（2–5 个分块），可能需要进行 CPU 卸载 |
| 16 GB | 大多数用例都很舒适，10–20 个分块的批次 |
| 24 GB+ | 全速，配合编解码器编译可实现 40–60 个分块的批次 |

- 如果显存不足，请在 Setup 标签页中减少**并行工作数（Parallel Workers）**
- 生成前关闭其他占用 GPU 的应用（游戏、其他 AI 工具）
- 在语音类型之间切换（Custom → Clone → LoRA）会卸载并重新加载模型，这会暂时释放显存

### 5. 出问题时该去哪里看

Web UI 显示的是高层次的运行状态，但详细的日志在应用
终端中：

- 检查正在运行 `python app/app.py` 或 `docker compose up` 的终端
- 模型加载、下载进度、显存估算和错误都会出现在这里
- 如果生成在 UI 中静默失败，终端会显示原因

常见问题及解决方案，请参阅 [故障排查（Troubleshooting）](https://github.com/Finrandojin/alexandria-audiobook/wiki/Troubleshooting)。

---

## 快速上手（Quick Start）

界面分为**核心流水线**（绿色标签页，带编号）和**高级工具**（蓝色标签页，无编号）。要制作一本有声书，你只需要核心流水线。

### 核心流水线（Core Pipeline）

**第 1 步 —— Setup**
配置你的 LLM 连接和 TTS 引擎。你至少需要：
- **LLM Base URL**：`http://localhost:1234/v1`（LM Studio）或 `http://localhost:11434/v1`（Ollama）
- **LLM API Key**：你的 API key（本地服务器使用 `local`）
- **LLM Model Name**：要使用的模型（例如 `qwen2.5-14b`）
- **TTS Mode**：`local`（内置，推荐）—— 直接加载模型，无需外部服务器
- 完成后点击 **Save Configuration（保存配置）**

**第 2 步 —— Script**
- 使用文件选择器选择你的书文件（仅 EPUB）—— 它会自动上传并接入流水线（在服务器端转换为纯文本）。每次 **Onboard / Import-as-new** 上传都会创建一本不同的书（拥有独立的 book ID 和 series）
- 点击 **Run All Walks** —— 这会运行 9-walk LLM 标注流水线（场景切分 → 角色发现 → 别名解析 → 场景出现 → span 归属 → 角色描述 → 语音试听 → 语音分配 → 交付），以构建标注后的脚本
- 实时观察 walk 进度；每个 walk 完成时都会显示其状态
- 对于已有书籍，点击 **Replace（替换）** 可以用一本新的 EPUB 替换其文档文本。Replace 会保留该书的 ID 和 series/position，重置该书已生成的输出，并在继续之前等待（取消）任何仍在活动中的 walk。它绝不会重新排列系列顺序或切换导出可见性
- *（可选）* 如果需要从头重新加载这本书，点击 **Re-onboard（重新接入）**（重置已生成的输出，但保留运行历史 —— 与 Replace 不同）

**第 3 步 —— Voices**
流水线的语音分配 walk（2h）会自动从你的语音库中为每个角色分配一个语音：
- 打开 **Voices** 标签页查看角色列表以及每个角色被分配的语音
- 通过下拉框更改任何分配 —— 这会立即保存到角色台账（character ledger）中
- 每种语音类型：Custom Voice（最简单）、Clone Voice、LoRA Voice 或 Voice Design
- 对于 Custom Voice，从 9 个预设中选择一个（Ryan、Serena、Aiden 等），并可选择设置角色风格（例如 "Heavy Scottish accent"）
- **说话人别名（Speaker Aliases）** —— 说话人名称变体会在标注过程中自动解析为某个单一角色的别名（例如 "YOUNG ELENA" → "ELENA"），因此各变体会共享同一个角色记录和语音配置
- 关于每种类型的指导，请参阅 [语音类型（Voice Types）](https://github.com/Finrandojin/alexandria-audiobook/wiki/Voice-Types)

**第 4 步 —— Editor**
- 点击 **Render（渲染）** 生成音频 —— 单个渲染实时显示每分块进度（已完成/总数并带失败徽标），批量渲染显示任务级进度
- 内联编辑任意 span 的文本、说话人或指令并重新生成
- 使用 **Split（拆分）/ Merge（合并）/ Move（移动）/ Delete（删除）** 操作重构脚本
- 解决置信度审核标记出的低置信度标注
- 满意后，使用 **Export M4B** 表单（元数据 + 可选封面 → 分章 M4B），或点击 **Merge（合并）** 再 **Download（下载）** 以获得普通有声书

### 高级工具（Advanced Tools，可选）

这些标签页面向希望更精细控制语音创建的高级用户：

- **Designer** —— 根据文本描述创建新语音（例如 "A warm elderly woman with a gentle raspy voice"）。将其保存下来，用作 Voices 标签页中的克隆参考
- **Preparer** —— 从上传的音频批量准备语音数据集（用作 LoRA 训练数据）
- **Dataset** —— 交互式构建 LoRA 训练数据集，一次一个样本，并带音频预览
- **Training** —— 在语音数据集上训练 LoRA 适配器，创建遵循指令方向的持久语音身份
- **Persona / Prompt Config / Workbench** —— 优化角色人设、校验提示配置，并检查中间流水线产物。

### Projects 标签页

Projects 是管理多本书的入口。用它来打开现有项目、通过接入 EPUB 创建新项目、替换书籍的源 EPUB 同时保留其身份，或从头重新接入一本书。

## Web 界面（Web Interface）

### Setup 标签页
配置你的 LLM 和 TTS 引擎连接。

**LLM 设置（LLM Settings）：**
- **Base URL** —— LLM 服务器 URL（LM Studio / Ollama / OpenAI / 任何 OpenAI 兼容 API）
- **API Key** —— 你的 API key（本地服务器用 `local`）
- **Model Name** —— 要使用的模型
- **Reasoning Effort** —— 推理模型的可选思考力度设置
- **Temperature** —— 用于标注 walks 的 LLM 采样温度

**TTS 设置（TTS Settings）：**
- **Mode** —— `local`（内置引擎）或 `external`（连接 Gradio 服务器）
- **Device** —— `auto`（推荐）、`cuda`、`cpu` 或 `mps`
- **Language** —— TTS 合成语言：英语（默认）、中文、法语、德语、意大利语、日语、韩语、葡萄牙语、俄语、西班牙语或 Auto（让模型自行检测）
- **Parallel Workers** —— 快速批量渲染的批大小（更高 = 更多显存占用）
- **Batch Seed** —— 用于可复现批量输出的固定种子（留空则为随机）
- **Compile Codec** —— 启用 `torch.compile`，批量解码快 3–4 倍（首次生成会增加约 30–60 秒预热）
- **Batch Group by Type** —— 按语音类型拆分批次，以避免混合类型分发
- **Sub-batching** —— 按文本长度拆分批次，以减少填充造成的 GPU 计算浪费（默认启用）
- **Min Sub-batch Size** —— 允许拆分前每个子批次的最小分块数（默认：4）
- **Length Ratio** —— 强制拆分子批次前的最长/最短文本长度比（默认：5）
- **Max Sub-batch Items** —— 每个子批次分块数的上限
- **Speaker Change Pause** —— 不同说话人之间的全局默认停顿，会被插入到合并后的音频中（默认：500 ms；0–10000 ms）
- **Same Speaker Pause** —— 同一说话人继续时的全局默认停顿，会被插入到合并后的音频中（默认：250 ms；0–10000 ms）
- **Book Pause Overrides** —— 按书的停顿覆盖，优先于配置默认值（留空则继承）。解析后的值会在 Setup 标签页上以预览形式显示
- **Per-Span Pause** —— Editor 中的每个 span 都可以设置一个 pause-after 覆盖（留空 = 解析后的默认值，0 = 无间隙）。M4B 导出会把解析后的说话人停顿插入到合并后的音频中；导出表面会报告装配三态（`applied` / `failed` / pending），并给出解析后的值以及 span 覆盖计数

### 停顿插入（Pause Insertion）

流水线会把可配置的、确定性的说话人停顿插入到合并后的
整书音频中。停顿是写入规范暂停产物
（`audiobook-paused.wav`）的真实可听静音，该文件成为
M4B、MP3 和 Audacity 导出的唯一来源。

**优先级（解析顺序）：** 对于两项停顿设置中的每一项 ——
不同说话人和同一说话人 —— 解析后的值取自第一个
提供非空值的层级：

1. 渲染请求覆盖（在受支持时）
2. 书/项目覆盖（`GET`/`PUT /api/pipeline/book/{book_id}/pause_settings`）
3. 已持久化的配置默认值（`tts.pause_between_speakers_ms` /
   `tts.pause_same_speaker_ms`，通过 `POST /api/config` 保存）
4. 内置回退 —— 不同说话人之间 **500 ms**，同一说话人
   继续时 **250 ms**

**边界：** 每个停顿值都是一个**非负整数毫秒计数**，
范围在 **0–10000 ms**（最大 10 s）。配置保存、书/span 覆盖以及
逐 span 编辑都会以稳定的 422（配置或 API）错误拒绝负数、小数、布尔值、NaN/inf 以及
超出范围的值。没有无界的停顿值。

**可空 vs. 零：** 缺失/`NULL` 值表示*解析适用的默认值*；显式的 `0` 是*有意的无间隙*覆盖（绝不会被
强制转换为默认值）。这种区别在保存/加载以及项目快照
往返中都能保留。

**逐 span 停顿：** 每个 span 都可以通过
`GET`/`PUT /api/pipeline/span/{span_id}/pause_after` 设置一个 `pause_after_ms` 覆盖（留空/NULL = 解析后的
默认值，`0` = 无间隙，正整数 = 显式毫秒）。span 覆盖总是
优先于解析后的书/配置默认值。

**格式与清理保证：** 暂停产物会以显式保留的
源 WAV 格式拼装（帧率、通道、采样位宽 ——
否则 pydub 会把低采样率源上提）。它会被导出到运行目录中的一个临时
文件，进行 fsync，原子地重命名到目标位置，并
对目录执行 fsync。临时/中间文件会在每一条
成功或失败路径上被移除。

**说话人来源：** 说话人顺序和停顿元数据取自
**渲染时的内存脚本/分块** —— 绝不会从文件名或
manifest 推断。后处理器在 `render_audiobook` 期间运行，此时该元数据
仍在内存中。

**产物状态字段：** 渲染状态和 M4B 导出响应会报告
`resolved_pause_between_speakers_ms`、`resolved_pause_same_speaker_ms`、
`pause_override_count`（具有非空 `pause_after_ms` 的 spans），以及
三态 `pauses_state`：`pending`（尚未观察到装配）、`applied`
（暂停产物存在并用作导出来源，带有简洁的
`pauses_message`）或 `failed`（装配不可用；`pauses_error` 携带
有界详情且不含文件系统路径）。`pauses_applied` 是派生出的
布尔值（当且仅当 `applied` 时为 `true`）。失败永远不会声称成功。渲染状态
保守地报告 `pending`（装配在渲染第 6 步运行，但
后台任务在被轮询时可能尚未到达该步骤）；导出
响应在提交的暂停产物被消费后报告真实的 `applied` / `failed` 三态。

**迁移与向后兼容：** 现有项目会幂等地迁移 ——
它们的 `book` 行会获得 `NULL` 的停顿覆盖列，每个 `span` 会获得
一个可空的 `pause_after_ms`（解析默认值，绝不强制转换为 0）。无声暂停键的
项目快照（无 pause 键）在恢复时不会触及当前的 pause
状态。当没有可用的暂停产物时（例如在未装配
情况下完成的渲染），导出会透明地回退到现有的逐 span
分块拼接，并报告 `failed` 三态。

### Script 标签页
上传 EPUB 文件并运行标注 walks。接入仅支持 EPUB —— 文件在上传时会在服务器端转换为纯文本。流水线运行 9 个串行 LLM walks，把你的书转换为一个结构化 span 图，包含：
- 说话人识别（NARRATOR 与角色名）
- 带自然发声的对白文本（写成可发音的文本，而不是标签）
- 用于 TTS 交付的风格方向
- 角色描述、语音试听和语音分配

- **Onboard** —— 上传并把书加载到流水线中
- **Run All Walks** —— 按顺序执行 9-walk 标注 DAG
- **Walk status** —— 实时显示每个 walk 的进度；每个 walk 也可以单独重新运行
- **Walk runs** —— walk 徽标下方有一个运行历史列表（`GET /api/pipeline/walks/{book_id}/runs`），显示创建/完成时间以及每个运行的徽标状态
- **Cancel Walks** —— 停止正在运行的 walk 周期（在 503 竞争时请求会自动重试一次）
- **Re-onboard** —— 重新加载书并重置流水线状态（清除已生成的输出、推进进度；运行/walk 历史会保留 —— 与 Replace 不同）
- **Replace** —— 用一本新的 EPUB 替换当前书的文档，同时保留其 book ID、系列号和位置。该书所有已生成的输出（walks、renders、workbench、persist）都会被清除并重新运行；任何仍在活动中的 walk 会先被取消/等待（在竞争时 API 返回 HTTP 503 + `Retry-After`）。Replace 绝不会重新排序或重新编号系列，并且按设计不暴露导出可见性控制 —— Import-as-new（Onboard）才是引入不同系列书籍的唯一方式

### Persona、Prompt Config 和 Workbench 标签页

这些工作流标签页无需直接访问数据库即可暴露中间流水线状态：
- **Persona** —— 查看并重新运行已生成的角色人设数据。
- **Prompt Config** —— 在重新运行受影响的 walks 之前，校验并修改 walk 提示配置。
- **Workbench** —— 查看项目级工作配置和中间输出。

### Voices 标签页
流水线会在 walk 2h 期间为每个角色分配一个语音。Voices 标签页让你查看和调整这些分配：

- **角色列表（Character list）** —— 来自角色台账的每个角色及其被分配的语音
- **分配下拉框（Assignment dropdown）** —— 从语音库更改角色的语音；通过 `PUT /api/pipeline/characters/{id}/voice` 立即保存
- **语音库（Voice catalog）** —— 浏览和试听可用的语音配置（Custom、Clone、LoRA、Voice Design），并分配给角色

**说话人别名（Speaker Aliases）：**
说话人名称变体会在标注（walk 2c）过程中自动解析为某个单一角色的别名，因此各变体会共享同一个角色记录和语音配置 —— 例如 "DR. SMITH" → "SMITH"，"YOUNG ELENA" → "ELENA"。Voices 标签页会把每个角色解析后的别名显示为徽标。

**Custom Voice 模式：**
- 从 9 个预训练语音中选择：Aiden、Dylan、Eric、Ono_anna、Ryan、Serena、Sohee、Uncle_fu、Vivian
- 设置一个角色风格，把持久的特质附加到每条 TTS 指令上（例如 "Heavy Scottish accent"、"Refined aristocratic tone"）

**Clone Voice 模式：**
- 选择一个设计好的语音，或输入自定义参考音频路径
- 提供参考音频的准确转写
- 注：克隆语音会忽略指令方向

**LoRA Voice 模式：**
- 从 Training 标签页选择一个训练好的 LoRA 适配器
- 设置一个角色风格（与 Custom 相同 —— 附加到每条指令上）
- 将训练带来的语音身份与 Base 模型的指令跟随能力结合起来

**Voice Design 模式：**
- 设置一个基础语音描述（例如 "Young strong soldier"）
- 每行的指令会被作为交付/情绪方向附加
- 使用 VoiceDesign 模型即时生成语音 —— 非常适合次要角色

### Voice Designer 标签页
无需参考音频，从文本描述创建新语音。

- 用自然语言**描述一个语音**（例如 "A warm elderly woman with a gentle, raspy voice and a slight Southern drawl"）
- 保存前先用示例文本**预览（Preview）**该语音
- **保存到库（Save to library）**，用作 Voices 标签页中的克隆语音参考
- 使用 Qwen3-TTS VoiceDesign 模型根据描述合成语音特征

### Training 标签页
在 Base 模型上训练 LoRA 适配器，创建自定义语音身份。若干内置 LoRA 预设开箱即用，并会与你的训练适配器一起显示。

**数据集（Dataset）：**
- **上传 ZIP（Upload ZIP）** —— WAV 文件（24kHz 单声道）+ `metadata.jsonl`，包含 `audio_filepath` 和 `text` 字段
- **生成数据集（Generate Dataset）** —— 根据 Voice Designer 描述自动生成带自定义示例文本的训练样本
- **数据集构建器（Dataset Builder）** —— 自己的标签页中的交互式工具（见下文），用于逐个样本地构建数据集并带预览

**训练配置（Training Configuration）：**
- **Adapter Name** —— 训练模型的标识符
- **Epochs** —— 对数据集的完整遍历轮数（20+ 样本推荐 15–30）
- **Learning Rate** —— 默认 5e-6（保守）。更高训练更快，但有失去稳定性的风险
- **LoRA Rank** —— 适配器容量。高（64+）能强锁定语音身份，但可能使交付变平。低（8–16）保留表现力
- **LoRA Alpha** —— 缩放因子。有效强度 = alpha / rank。常见起点：alpha = 2 倍 rank
- **Language** —— codec 前缀 token 的语言。请与训练数据的语言匹配（英语、中文、韩语、日语等）。语言不匹配可能导致适配器丢失说话人身份
- **Batch Size / Grad Accum** —— 对于 24GB 显卡，批大小 1 配合梯度累积 8 是典型配置

**训练提示（Training tips）：**
- 包含情绪多样的样本（快乐、悲伤、愤怒、平静），以获得有表现力的语音
- 仅中性训练数据会产生平的语音，且抗拒指令提示
- 界面中的设置信息面板会解释每个参数对语音质量的影响

### Dataset Builder 标签页
以交互方式逐个构建 LoRA 训练数据集。

- 用语音描述和可选全局种子**创建一个项目**
- **定义样本（Define samples）** —— 逐行设置文本和情绪/风格
- **预览音频（Preview audio）** —— 生成并试听单个样本，或一次性批量生成全部
- **取消批处理（Cancel batch）** —— 停止正在运行的批量生成而不会丢失已完成的样本
- **保存为数据集（Save as dataset）** —— 把项目导出为一个训练就绪的数据集，会出现在 Training 标签页中
- 设计好的语音和 Voice Designer 描述通过 Qwen3-TTS VoiceDesign 模型驱动音频生成

### Editor 标签页
在导出前精调你的有声书：
- 在带状态指示器和置信度分数的表格中**查看所有 spans**
- **内联编辑（Edit inline）** —— 点击修改说话人、文本或指令
- **结构操作（Structural operations）** —— 拆分、合并、移动或删除一个 span
- **Render** —— 为待处理的 spans 生成音频，并带实时进度：单个渲染显示每分块的已完成/总数计数和失败徽标，批量渲染显示任务级进度
- **Cancel** —— 停止正在运行的渲染任务（在 503 竞争时请求会自动重试一次）
- **置信度审核（Confidence review）** —— 接受、拒绝或覆盖低置信度标注
- **播放 / 预览（Play / Preview）** —— 在浏览器中播放整本渲染完成的书，或预览单个 spans
- **Export M4B** —— 元数据表单（title、author、narrator、year、description + 可选封面）生成分章 M4B；当后端报告支持时会出现 MP3 下载，同时 Audacity 包与 M4B 一同生成。下载链接会根据能力标记出现，并指向同源服务路由 `GET /api/pipeline/export/mp3/{job_id}` 和 `GET /api/pipeline/export/audacity/{job_id}`
- **Merge / Download** —— 把渲染好的分块合并成最终的 M4B 有声书并下载（或将原始分块作为 ZIP 下载）

## 性能（Performance）

### 批量生成的推荐设置

| 设置 | 推荐值 | 备注 |
|---------|-------------|-------|
| TTS Mode | `local` | 内置引擎，无需外部服务器 |
| Compile Codec | `true` | 一次性预热后解码快 3–4 倍 |
| Parallel Workers | 20–60 | 越高 = 吞吐量越大、显存占用越多 |

### 基准测试（Benchmarks）

在 AMD RX 7900 XTX（24 GB 显存，ROCm 6.3/7.2）上测试：

| 配置 | 吞吐量 |
|--------------|------------|
| 标准模式（串行） | 约 1x 实时 |
| 批处理模式，无 codec 编译 | 约 2x 实时 |
| 批处理模式 + compile_codec | **3–6x 实时** |

一本 273 分块的有声书（约 54 分钟音频）在启用批处理模式和编解码器编译的情况下，大约 16 分钟即可生成。

### ROCm（AMD GPU）备注

> **仅限 Linux。** AMD GPU 加速需要在 Linux 上使用 ROCm 6.3+。Windows 上的 AMD GPU 以 CPU 模式运行 —— 参见 [GPU 兼容性](#gpu-兼容性gpu-compatibility)。

在 AMD GPU 上运行时，Alexandria 会自动应用 ROCm 特定的优化：
- **MIOpen fast-find 模式** —— 防止导致慢速 GEMM 回退的工作空间分配失败
- **Triton AMD flash attention** —— 为 whisper 编码器启用原生 flash attention
- **triton_key 兼容 shim** —— 修复 pytorch-triton-rocm 上的 `torch.compile`

这些优化是透明应用的，无需任何配置。

> **ROCm 7.x GPU 降频修复：** ROCm 7.x 存在一个回归，即 GPU 的 DPM 控制器会在自回归生成步骤之间积极降低着色器引擎时钟，导致批量生成慢到爬行甚至看起来像挂起。解决方法是把 GPU 电源配置设置为 COMPUTE，这会强制执行最低时钟频率下限：
>
> ```bash
> echo 5 | sudo tee /sys/class/drm/card1/device/pp_power_profile_mode
> ```
>
> 这需要在每次开机时运行一次（它不会跨重启保留）。你可以把它加入系统启动项，或在启动 Alexandria 前手动运行。要验证它是否生效，请检查以下命令输出中的 `COMPUTE*`：
>
> ```bash
> cat /sys/class/drm/card1/device/pp_power_profile_mode
> ```
>
> ROCm 6.x 用户和 NVIDIA 用户不受影响。

## 脚本格式（Script Format）

标注后的脚本是一个 JSON 数组，包含 `speaker`、`text` 和 `instruct` 字段（每个 span 一条）：

```json
[
  {"speaker": "NARRATOR", "text": "The door creaked open slowly.", "instruct": "Calm, even narration."},
  {"speaker": "ELENA", "text": "Ah! Who's there?", "instruct": "Startled and fearful, sharp whispered question, voice cracking with panic."},
  {"speaker": "MARCUS", "text": "Haha... did you miss me?", "instruct": "Menacing confidence, low smug drawl with a dark chuckle, savoring the moment."}
]
```

- **`instruct`** —— 直接发送给引擎的 2–3 句 TTS 语音方向。设定基调、描述交付方式，然后给出具体参考。示例："Devastated by grief, Sniffing between words and pausing to collect herself, end with a wracking sob."

### 非语言声音（Non-verbal Sounds）
发声会被写成 TTS 可以直接说出来的真实可发音文本 —— 不使用括号标签或特殊 token。LLM 会生成带简短指令方向的自然拟声词：
- 倒吸一口气："Ah!"、"Oh!"，指令如 "Fearful, sharp gasp."
- 叹息："Haah..."、"Hff..."
- 笑声："Haha!"、"Ahaha..."
- 哭泣："Hic... sniff..."
- 感叹："Mmm..."、"Hmm..."、"Ugh..."

## 输出文件（Output Files）

渲染会为每个任务生成一个特定任务的输出目录，里面是逐 span 的音频分块。Editor 标签页的 Export M4B 表单会把一次完成的渲染变成最终的有声书：

**最终有声书（Final Audiobook）：**
- `audiobook.m4b` —— 带内嵌章节标记和 Export M4B 表单中输入的元数据（title、author、narrator、year、description、可选封面）的 AAC 有声书。可在 Audiobookshelf、Apple Books、VLC、Haruna 以及大多数有声书播放器中播放。当规范暂停产物可用时，它会从暂停源 WAV 拼装（参见 *停顿插入 Pause Insertion*），因此解析后的说话人停顿在最终书中是可听见的
- `audiobook.mp3` —— 当后端 ffmpeg 包含 MP3 支持（libmp3lame）时生成的 MP3 导出；否则导出降级为仅 M4B 并给出明确提示。下载链接会根据能力标记出现，并通过 `GET /api/pipeline/export/mp3/{job_id}` 提供。与 M4B 一样，它在可用时也源自暂停源 WAV
- `audiobook-audacity.zip` —— 与 M4B 一同生成的 ZIP_STORED 打包，供 Audacity 或其他 DAW 编辑。当暂停产物可用时，它包含 `audiobook-paused.wav`（暂停的整书源）以及 MP3；否则回退到逐 span 的 WAV 分块。下载链接会根据能力标记出现，并通过 `GET /api/pipeline/export/audacity/{job_id}` 提供

**渲染任务输出（Render job output，每次渲染）：**
- `chunk_0000.wav`、`chunk_0001.wav`、... —— 每个 span 一个 WAV，按时间线顺序编号
- `audiobook-paused.wav` —— 规范的、确定性的暂停整书产物（装配解析了说话人停顿；参见 *停顿插入 Pause Insertion*）。它成为该任务的 `output_artifact_path`，并作为 M4B、MP3 和 Audacity 导出的唯一来源
- `GET /api/pipeline/download/{job_id}` 端点在可用时提供合并后的 `audiobook.m4b`，否则会把分块打包成 `audiobook.zip` 供手动拼装

## API 参考（API Reference）

Alexandria 提供了一个用于程序化访问的 REST API：

### 配置（Configuration）
```bash
# Get current config（获取当前配置）
curl http://127.0.0.1:4200/api/config

# Save config（保存配置）
curl -X POST http://127.0.0.1:4200/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "llm": {"base_url": "...", "api_key": "...", "model_name": "..."},
    "tts": {
      "mode": "local",
      "device": "auto",
      "language": "English",
      "parallel_workers": 25,
      "batch_seed": 12345,
      "compile_codec": true,
      "sub_batch_enabled": true,
      "sub_batch_min_size": 4,
      "sub_batch_ratio": 5,
      "pause_between_speakers_ms": 500,
      "pause_same_speaker_ms": 250
    }
  }'
```

### 流水线（脚本生成）（Pipeline (Script Generation)）
```bash
# Onboard an EPUB — extracts text, populates spine, returns book_id（接入一个 EPUB —— 提取文本、填充 spine、返回 book_id）
curl -X POST http://127.0.0.1:4200/api/pipeline/onboard \
  -F "file=@mybook.epub"

# Run all annotation walks (replace <book_id> with the ID returned by onboard)（运行所有标注 walks，用 onboard 返回的 ID 替换 <book_id>）
curl -X POST http://127.0.0.1:4200/api/pipeline/run_all_walks \
  -H "Content-Type: application/json" \
  -d '{"book_id": "<book_id>"}'

# Check walk status（检查 walk 状态）
curl http://127.0.0.1:4200/api/pipeline/walk_status/<book_id>

# Review items (low-confidence annotations needing human review)（审核项 —— 需要人工审核的低置信度标注）
curl http://127.0.0.1:4200/api/pipeline/review/<book_id>

# Accept a review item（接受一个审核项）
curl -X POST http://127.0.0.1:4200/api/pipeline/review/accept \
  -H "Content-Type: application/json" \
  -d '{"item_id": "<item_id>"}'

# Cancel running walks (the UI retries once automatically on 503 contention)（取消正在运行的 walks，503 竞争时 UI 会自动重试一次）
curl -X POST http://127.0.0.1:4200/api/pipeline/cancel_walks \
  -H "Content-Type: application/json" \
  -d '{"book_id": "<book_id>"}'

# Walk run history for a book (created/finished times + status per run)（一本书的 walk 运行历史 —— 创建/完成时间 + 每次运行的状态）
curl http://127.0.0.1:4200/api/pipeline/walks/<book_id>/runs

# Read walk prompt configuration（读取 walk 提示配置）
curl http://127.0.0.1:4200/api/pipeline/walks/<book_id>/config

# Read or rerun a character persona（读取或重新运行角色人设）
curl http://127.0.0.1:4200/api/pipeline/characters/<character_id>/persona
curl -X POST http://127.0.0.1:4200/api/pipeline/characters/<character_id>/persona/rerun
```

### 语音库（Voice Catalog）
```bash
# List all voice configs（列出所有语音配置）
curl http://127.0.0.1:4200/api/pipeline/voices

# Create a voice config（创建一个语音配置）
curl -X POST http://127.0.0.1:4200/api/pipeline/voices \
  -H "Content-Type: application/json" \
  -d '{"name": "NARRATOR", "type": "custom", "voice": "Ryan", "character_style": "calm"}'

# Assign a voice to a character（把一个语音分配给一个角色）
curl -X PUT http://127.0.0.1:4200/api/pipeline/characters/<character_id>/voice \
  -H "Content-Type: application/json" \
  -d '{"voice_assignment_id": "<voice_id>"}'
```

### 角色台账（Character Ledger）
```bash
# View characters extracted during pipeline walks (voices, roles, descriptions)（查看流水线 walks 期间提取的角色 —— 语音、角色、描述）
curl http://127.0.0.1:4200/api/pipeline/characters/<book_id>
```

### 语音设计师（Voice Designer）
```bash
# Preview a voice from text description（根据文本描述预览一个语音）
curl -X POST http://127.0.0.1:4200/api/voice_design/preview \
  -H "Content-Type: application/json" \
  -d '{"description": "A warm, deep male voice", "text": "Hello world."}'

# Save a designed voice（保存一个设计好的语音）
curl -X POST http://127.0.0.1:4200/api/voice_design/save \
  -H "Content-Type: application/json" \
  -d '{"name": "warm_narrator", "description": "A warm, deep male voice", "text": "Hello world."}'

# List saved designed voices（列出已保存的设计语音）
curl http://127.0.0.1:4200/api/voice_design/list

# Delete a designed voice（删除一个设计好的语音）
curl -X DELETE http://127.0.0.1:4200/api/voice_design/voice_id_here
```

### LoRA 训练（LoRA Training）
```bash
# Upload a training dataset (ZIP with WAV + metadata.jsonl)（上传训练数据集 —— 含 WAV 的 ZIP + metadata.jsonl）
curl -X POST http://127.0.0.1:4200/api/lora/upload_dataset \
  -F "file=@dataset.zip" -F "name=my_voice"

# Generate a dataset from Voice Designer description（根据 Voice Designer 描述生成数据集）
curl -X POST http://127.0.0.1:4200/api/lora/generate_dataset \
  -H "Content-Type: application/json" \
  -d '{"name": "warm_voice", "description": "A warm male voice", "texts": ["Hello.", "Goodbye."]}'

# List uploaded datasets（列出已上传的数据集）
curl http://127.0.0.1:4200/api/lora/datasets

# Delete a dataset（删除一个数据集）
curl -X DELETE http://127.0.0.1:4200/api/lora/datasets/dataset_id_here

# Start LoRA training（开始 LoRA 训练）
curl -X POST http://127.0.0.1:4200/api/lora/train \
  -H "Content-Type: application/json" \
  -d '{"name": "narrator_warm", "dataset_id": "my_voice", "epochs": 25, "lr": 5e-6, "lora_r": 32, "lora_alpha": 64}'

# List trained adapters（列出训练好的适配器）
curl http://127.0.0.1:4200/api/lora/models

# Test a trained adapter（测试一个训练好的适配器）
curl -X POST http://127.0.0.1:4200/api/lora/test \
  -H "Content-Type: application/json" \
  -d '{"adapter_id": "narrator_warm_1234567890", "text": "Test line.", "instruct": "Calm narration."}'

# Delete an adapter（删除一个适配器）
curl -X DELETE http://127.0.0.1:4200/api/lora/models/adapter_id_here

# Check LoRA training status（检查 LoRA 训练状态）
curl http://127.0.0.1:4200/api/lora/status
```

### 语音数据集准备器（Voice Dataset Preparer）
```bash
# Check a preparer job's status (task_name: preparer | batch_preparer)（检查 preparer 任务的状态 —— task_name: preparer | batch_preparer）
curl http://127.0.0.1:4200/api/preparer/status/preparer
curl http://127.0.0.1:4200/api/preparer/status/batch_preparer
```

### 数据集构建器（Dataset Builder）
```bash
# List all dataset builder projects（列出所有数据集构建器项目）
curl http://127.0.0.1:4200/api/dataset_builder/list

# Create a new project（创建一个新项目）
curl -X POST http://127.0.0.1:4200/api/dataset_builder/create \
  -H "Content-Type: application/json" \
  -d '{"name": "my_voice_dataset"}'

# Update project metadata (description and global seed)（更新项目元数据 —— 描述和全局种子）
curl -X POST http://127.0.0.1:4200/api/dataset_builder/update_meta \
  -H "Content-Type: application/json" \
  -d '{"name": "my_voice_dataset", "description": "A warm male narrator", "global_seed": "42"}'

# Update sample rows（更新样本行）
curl -X POST http://127.0.0.1:4200/api/dataset_builder/update_rows \
  -H "Content-Type: application/json" \
  -d '{"name": "my_voice_dataset", "rows": [{"text": "Hello world.", "emotion": "cheerful"}]}'

# Generate a single sample preview（生成单个样本预览）
curl -X POST http://127.0.0.1:4200/api/dataset_builder/generate_sample \
  -H "Content-Type: application/json" \
  -d '{"dataset_name": "my_voice_dataset", "description": "A warm male voice", "text": "Hello.", "sample_index": 0, "seed": 42}'

# Batch generate all samples（批量生成所有样本）
curl -X POST http://127.0.0.1:4200/api/dataset_builder/generate_batch \
  -H "Content-Type: application/json" \
  -d '{"name": "my_voice_dataset", "description": "A warm male voice", "samples": [{"text": "Hello.", "emotion": "cheerful"}]}'

# Check batch generation status（检查批量生成状态）
curl http://127.0.0.1:4200/api/dataset_builder/status/my_voice_dataset

# Cancel a running batch generation（取消正在运行的批量生成）
curl -X POST http://127.0.0.1:4200/api/dataset_builder/cancel \
  -H "Content-Type: application/json" \
  -d '{"name": "my_voice_dataset"}'

# Save project as a training dataset（把项目保存为训练数据集）
curl -X POST http://127.0.0.1:4200/api/dataset_builder/save \
  -H "Content-Type: application/json" \
  -d '{"name": "my_voice_dataset", "ref_index": 0}'

# Delete a project（删除一个项目）
curl -X DELETE http://127.0.0.1:4200/api/dataset_builder/my_voice_dataset
```

### 渲染与下载（Rendering & Download）
```bash
# Start a render job (replace <book_id> with your book)（启动一个渲染任务，把你的书替换到 <book_id>）
curl -X POST http://127.0.0.1:4200/api/pipeline/render \
  -H "Content-Type: application/json" \
  -d '{"book_id": "<book_id>", "use_batch": true}'
# → {"job_id": "...", "status": "started"}

# Poll render status until completed.（轮询渲染状态直到完成）
# Individual renders include `mode` plus per-chunk counts（单个渲染包含 `mode` 以及每个分块的计数）
# (completed_chunks / total_chunks / failed_chunks); batch renders are job-level.（completed_chunks / total_chunks / failed_chunks）；批量渲染是任务级的）
curl http://127.0.0.1:4200/api/pipeline/render_status/<job_id>

# Cancel a running render job (the UI retries once automatically on 503 contention)（取消正在运行的渲染任务，503 竞争时 UI 会自动重试一次）
curl -X POST http://127.0.0.1:4200/api/pipeline/cancel_render \
  -H "Content-Type: application/json" \
  -d '{"job_id": "<job_id>"}'

# Export a chaptered M4B with metadata + optional cover (multipart)（导出带元数据 + 可选封面的分章 M4B，multipart）
curl -X POST http://127.0.0.1:4200/api/pipeline/export/m4b \
  -F "job_id=<job_id>" \
  -F "title=My Book" -F "author=Jane Doe" -F "narrator=John Roe" \
  -F "year=2026" -F "description=An audiobook." \
  -F "cover=@cover.jpg"

# Merge rendered chunks into audiobook.m4b（把渲染好的分块合并为 audiobook.m4b）
curl -X POST http://127.0.0.1:4200/api/pipeline/merge \
  -H "Content-Type: application/json" \
  -d '{"job_id": "<job_id>"}'

# Download the merged M4B (or audiobook.zip of raw chunks)（下载合并后的 M4B，或原始分块的 audiobook.zip）
curl http://127.0.0.1:4200/api/pipeline/download/<job_id> --output audiobook.m4b
```

## Python 集成（Python Integration）

```python
import requests
import time

BASE = "http://127.0.0.1:4200"

# Onboard EPUB — extracts text, populates spine, returns book_id（接入 EPUB —— 提取文本、填充 spine、返回 book_id）
with open("mybook.epub", "rb") as f:
    onboard = requests.post(f"{BASE}/api/pipeline/onboard", files={"file": f}).json()
book_id = onboard["book_id"]

# Run all annotation walks（运行所有标注 walks）
requests.post(f"{BASE}/api/pipeline/run_all_walks", json={"book_id": book_id})

# Poll until all walks complete（轮询直到所有 walks 完成）
while True:
    statuses = requests.get(f"{BASE}/api/pipeline/walk_status/{book_id}").json()
    if all(s in ("completed", "failed", "cancelled") for s in statuses.values()):
        break
    time.sleep(2)

# Assign a voice to a character (optional; walks 2g/2h already assign voices)（为角色分配语音 —— 可选；walks 2g/2h 已分配语音）
characters = requests.get(f"{BASE}/api/pipeline/characters/{book_id}").json()
narrator = next(c for c in characters if c["name"] == "NARRATOR")
requests.put(
    f"{BASE}/api/pipeline/characters/{narrator['id']}/voice",
    json={"voice_assignment_id": "NARRATOR"},
)

# Render the audiobook（渲染有声书）
job = requests.post(f"{BASE}/api/pipeline/render", json={"book_id": book_id, "use_batch": True}).json()
job_id = job["job_id"]

# Poll until render completes（轮询直到渲染完成）
while True:
    status = requests.get(f"{BASE}/api/pipeline/render_status/{job_id}").json()
    if status["status"] == "completed":
        break
    time.sleep(2)

# Merge into audiobook.m4b and download（合并为 audiobook.m4b 并下载）
requests.post(f"{BASE}/api/pipeline/merge", json={"job_id": job_id})
with open("audiobook.m4b", "wb") as f:
    f.write(requests.get(f"{BASE}/api/pipeline/download/{job_id}").content)
```

## JavaScript 集成（JavaScript Integration）

```javascript
const BASE = "http://127.0.0.1:4200";

// Onboard EPUB — extracts text, populates spine, returns book_id（接入 EPUB —— 提取文本、填充 spine、返回 book_id）
const formData = new FormData();
formData.append("file", fileInput.files[0]);
const onboard = await fetch(`${BASE}/api/pipeline/onboard`, { method: "POST", body: formData });
const { book_id: bookId } = await onboard.json();

// Run all annotation walks（运行所有标注 walks）
await fetch(`${BASE}/api/pipeline/run_all_walks`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ book_id: bookId }),
});

// Poll until all walks complete（轮询直到所有 walks 完成）
async function waitForWalks(bookId) {
  while (true) {
    const res = await fetch(`${BASE}/api/pipeline/walk_status/${bookId}`);
    const statuses = await res.json();
    if (Object.values(statuses).every(s => ["completed", "failed", "cancelled"].includes(s))) return statuses;
    await new Promise(r => setTimeout(r, 2000));
  }
}
await waitForWalks(bookId);

// Render the audiobook（渲染有声书）
const render = await fetch(`${BASE}/api/pipeline/render`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ book_id: bookId, use_batch: true }),
});
const { job_id: jobId } = await render.json();

// Poll until render completes（轮询直到渲染完成）
while (true) {
  const res = await fetch(`${BASE}/api/pipeline/render_status/${jobId}`);
  const status = await res.json();
  if (status.status === "completed") break;
  await new Promise(r => setTimeout(r, 2000));
}

// Merge into audiobook.m4b and download（合并为 audiobook.m4b 并下载）
await fetch(`${BASE}/api/pipeline/merge`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ job_id: jobId }),
});
window.location.href = `${BASE}/api/pipeline/download/${jobId}`;
```

## 推荐的 LLM 模型（Recommended LLM Models）

对于脚本生成，非思考（non-thinking）模型效果最好：
- **Qwen3-next**（80B-A3B-instruct）—— 出色的 JSON 输出和指令方向
- **Gemma3**（推荐 27B）—— 强 JSON 输出和指令方向
- **Qwen2.5**（任意大小）—— 可靠的 JSON 输出
- **Qwen3**（非思考变体）
- **Llama 3.1/3.2** —— 角色区分度好
- **Mistral/Mixtral** —— 快速且可靠

**思考模型（Thinking models）**（DeepSeek-R1、GLM4-air 等）可能会干扰 JSON 输出。如果必须使用，请优先使用非思考变体，或为标注 walks 单独提供一个端点。

## 故障排查（Troubleshooting）

### 脚本生成失败（Script generation fails）
- 检查 LLM 服务器是否正在运行且可访问
- 验证模型名与已加载的一致
- 尝试不同的模型 —— 有些模型难以输出 JSON

### 模型下载失败或非常慢（Model download fails or is very slow）
- TTS 模型（每个约 3.5 GB）在首次使用时从 Hugging Face 下载
- 如果因网络限制（在中国大陆很常见）导致下载缓慢或失败，请在启动前设置 Hugging Face 镜像：
  - 在启动应用前设置环境变量 `HF_ENDPOINT=https://hf-mirror.com`
- 对于其他启动方式，在启动 Alexandria 前在其环境中设置 `HF_ENDPOINT=https://hf-mirror.com`。
- 如果遇到速率限制，请创建一个免费的 [Hugging Face 账号](https://huggingface.co/join)，并把 `HF_TOKEN` 设置为你的访问令牌
- 下载中断时会自动续传 —— 只需重启应用

### TTS 生成失败（TTS generation fails）
- 检查运行 Alexandria 的终端，看是否有模型加载错误
- 确保有足够的显存（bfloat16 推荐 16+ GB）
- 对于外部模式，确保 Gradio TTS 服务器在配置的 URL 上运行
- 在 Voices 标签页中验证每个角色都分配了有效的语音（语音配置存储在流水线的 voice_config 表中）
- 对于克隆语音，验证参考音频存在且转写准确

### 批量生成缓慢（Slow batch generation）
- 在 Setup 中启用 **Compile Codec**（增加预热时间，但之后快 3–4 倍）
- 如果显存允许，增加 **Parallel Workers**（批大小）
- 如果在 AMD 上看到 MIOpen 警告，这些会被自动处理

### 内存不足错误（Out of memory errors）
- 减少 **Parallel Workers**（批大小）
- 关闭其他占用 GPU 的应用
- 尝试 `device: cpu` 作为回退（会慢得多）

### 损坏或极小的 MP3 文件（428 字节）（Broken or tiny MP3 files (428 bytes)）
Windows 上 Conda 自带的 ffmpeg 通常缺少 MP3 编码器（libmp3lame）。Alexandria 现在会检测这一点并自动回退到 WAV，但如果你想要 MP3 输出：
- 安装带 MP3 支持的 ffmpeg：`conda install -c conda-forge ffmpeg`
- 或移除 conda 的 ffmpeg 以使用系统自带的：`conda remove ffmpeg`
- 验证：`ffmpeg -encoders 2>/dev/null | grep mp3`

### 音频质量问题（Audio quality issues）
- 克隆时使用 5–15 秒清晰无噪声的参考音频
- 避免参考样本中的背景噪声
- 为自定义语音尝试不同的种子

### 输出中的乱码字符（Mojibake characters in output）
- 系统会自动修复常见的编码问题
- 如果问题仍然存在，请确保你的输入文本是 UTF-8 编码的

## 开发与测试（Development & Testing）

后端测试位于 `tests/pipeline/`（pytest）：

```bash
# Full backend suite (pipeline tests + guard suite)（完整后端套件 —— 流水线测试 + guard 套件）
pytest tests/pipeline -q

# With the app-level API tests (requires a live LLM server; environmental（连同应用级 API 测试 —— 需要一个正在运行的 LLM 服务器；因缺少 TTS/LLM 依赖）
# failures for missing TTS/LLM dependencies are expected)（而导致的失败是预期内的）
pytest --import-mode=importlib app/test_api.py tests/pipeline -q
```

前端（在 `frontend/` 中）：

```bash
npm run build    # production build (outputs to ../app/static/dist)（生产构建，输出到 ../app/static/dist）
npx tsc --noEmit # TypeScript type check（TypeScript 类型检查）

# Frontend unit tests（前端单元测试）
npm test
```

## 项目结构（Project Structure）

```
Alexandria/
├── app/
│   ├── app.py                 # FastAPI server (config, voice_design, lora, dataset_builder, preparer)
│   ├── engine.py              # TTS engine factory (get_tts_engine / reset_tts_engine, module cache)
│   ├── tts.py                 # TTS engine (local + external backends)
│   ├── train_lora.py          # LoRA training subprocess script
│   ├── hf_utils.py            # Hugging Face model download utilities
│   ├── utils.py               # Shared utilities (LLM client, config resolution)
│   ├── pipeline/              # v3 Annotation Pipeline (SQLite-WAL, two-graph model)
│   │   ├── api.py             # Pipeline router entry point (includes sub-routers)
│   │   ├── api_onboard.py     # Onboard / re-onboard endpoints
│   │   ├── api_walks.py       # Walk execution + status endpoints
│   │   ├── api_operations.py  # Structural operations + span text editing
│   │   ├── api_review.py      # Confidence review endpoints
│   │   ├── api_export.py      # Export / render / merge / download endpoints
│   │   ├── api_characters.py  # Character ledger + voice assignment
│   │   ├── api_voices.py      # Voice catalog CRUD + preview
│   │   ├── adapter.py         # Pipeline adapter + schema init
│   │   ├── extract.py         # EPUB text extraction
│   │   ├── populate.py        # Spine population
│   │   ├── ledger.py          # Character ledger
│   │   ├── operations.py      # Operation executor (split/merge/move/delete)
│   │   ├── review.py          # Review manager (low-confidence items)
│   │   ├── assembly.py        # Script assembly + export
│   │   ├── tts_integration.py # TTS integration (render_audiobook)
│   │   ├── schema.py          # SQL schema + span_presentation VIEW
│   │   └── walks/             # 9-walk serial DAG (2a→2b→…→2i)
│   │       ├── walk_2a_scene_segmentation.py
│   │       ├── walk_2b_character_discovery.py
│   │       ├── walk_2c_alias_resolution.py
│   │       ├── walk_2d_scene_presence.py
│   │       ├── walk_2e_span_attribution.py
│   │       ├── walk_2f_character_description.py
│   │       ├── walk_2g_voice_audition.py
│   │       ├── walk_2h_voice_assignment.py
│   │       └── walk_2i_delivery.py
│   ├── config.json            # Runtime configuration (gitignored)
│   ├── static/index.html      # Web UI
│   ├── static/dist/           # Built frontend bundle (generated, never hand-edited)
│   └── requirements.txt       # Python dependencies
├── frontend/                  # Frontend source (TypeScript, bundled to app/static/dist)
│   └── src/tabs/              # setup, script, voices, editor, projects, workbench, persona, prompt-config, designer, preparer, dataset-builder, training
├── builtin_lora/              # Pre-trained LoRA voice presets
├── dataset_builder/           # Dataset builder project workspace (gitignored)
├── designed_voices/           # Saved Voice Designer outputs (gitignored)
├── lora_datasets/             # Uploaded/generated training datasets (gitignored)
├── lora_models/               # Trained LoRA adapters (gitignored)
├── data/                      # SQLite pipeline database (pipeline.db, gitignored)
├── install.js                 # Legacy Pinokio installer
├── start.js                   # Legacy Pinokio launcher
├── reset.js                   # Reset script
├── pinokio.js                 # Legacy Pinokio UI config
├── pinokio.json               # Legacy Pinokio metadata
└── README.md
```

## 致谢（Acknowledgements）

- [Ayush Naphade](https://github.com/aayushnaphade) —— 人设（Persona）生成、说话人别名解析和上下文脚本审核（[PR #42](https://github.com/Finrandojin/alexandria-audiobook/pull/42)）。去看看他的项目 [Lily](https://lily.rayoneai.in/) —— 期待它的发展！
- [Michii](https://github.com/on22s) —— 带实时 GPU/磁盘监控的系统健康仪表盘（[PR #45](https://github.com/Finrandojin/alexandria-audiobook/pull/45)）、跨平台子进程运行器（[PR #46](https://github.com/Finrandojin/alexandria-audiobook/pull/46)）、语音训练数据集准备器标签页（[PR #47](https://github.com/Finrandojin/alexandria-audiobook/pull/47)）

## 许可证（License）

MIT

### 第三方许可证（Third-Party Licenses）

- [qwen_tts](https://github.com/Qwen/Qwen3-TTS) —— Apache License 2.0，Copyright Alibaba Qwen Team
