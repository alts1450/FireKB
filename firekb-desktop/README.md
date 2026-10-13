# FireKB Desktop 0.2-latest

桌面端将素材输入、处理控制、结果输出和模型配置放在左侧导航。支持四种强调色、浅色/深色/跟随系统与轻量动效。机器构建版本为 `0.2.0`。

## 开发启动

需要 Node.js 18+、可用的 Python 3.12+ 及项目解析依赖。

```powershell
cd firekb-desktop
npm ci
npm run build:app
$env:FIREKB_ROOT = (Resolve-Path ..).Path
$env:PYTHON_PATH = (Resolve-Path ../.venv/Scripts/python.exe).Path
npm start
```

`FIREKB_ROOT` 指向项目根，`PYTHON_PATH` 指向已配置的 Python 解释器。开发预览可运行 `npm run dev`。当前交付源码与直接构建应用；安装包仍需图标和干净机器验收。

## 模型配置

页面自动读取 CPU、GPU 专用显存、内存及可用量、系统架构和磁盘余量，检测信息只保留在本机。最低本地参考配置为 Windows x64、i7-13700F、RTX 4060 8GB、16GB RAM；未达到门槛或资源不足时推荐云端。

| 档位 | 文本候选 | 可用显存估算 | 可用内存估算 | 项目磁盘余量 |
|---|---|---:|---:|---:|
| 速度优先 | Qwen3.5 4B Q4_K_M | 4.5GB | 6GB | 18GB |
| 均衡 | Qwen3.5 4B Q8_0 | 6.5GB | 8GB | 20GB |
| 质量优先 | Qwen3.5 9B Q4_K_M | 7.5GB | 9GB | 24GB |

资源数据是保守估算，需要本机实测。未知等效型号、Laptop/Mobile GPU 和其他平台需独立验证，多个 GPU 不相加计算显存。

本地点击「一键配置本地环境」并确认下载：固定官方 Ollama 便携版、SHA-256 校验、模型拉取、限上下文方案、GPU 加载检查，成功后才切换接口；失败保留原配置。环境和权重位于项目 `.firekb-runtime/`，服务仅监听 `127.0.0.1:11435`，不修改系统 PATH 或驱动。

在项目根的 PowerShell 也可执行：

```powershell
python tools/device_setup.py --inspect
python tools/device_setup.py --install balanced
python tools/device_setup.py --start
$env:OLLAMA_HOST = '127.0.0.1:11435'
.\.firekb-runtime\ollama\v0.40.3\ollama.exe run firekb-balanced
```

云端选择百炼、DeepSeek、OpenRouter或自定义兼容 API，填写模型、地址和 Key，保存后测试连接。密钥只回传掩码；连接测试发送固定合成句，真实云服务可能收取测试费用。平台预设需按实际账户、区域与服务条款核对。

## 手动批准资料出站

配置云端接口不会批准资料上传。选择允许发送的类别、填写确认备注，并在原生对话框手动确认。批准默认不勾选，绑定接口地址、类别和知识处理用途，持续有效直到撤销；切换服务须重新批准。

「撤销全部批准」拦截之后的新请求，已发送数据须另按接收方规则处理。硬件推荐或安装模型不会扩大权限。课堂录音、转写、笔记、错题和复习记录也遵守该流程。请确认第三方素材和声音的处理权利。

密钥 `.env`、批准 `.egress-consent.json`、安装状态 `.deployment.json`、运行环境和日志均不随公开仓分发。CLI `--allow-egress` 只代表明确指定的该次引擎授权；LLM 使用按接收方绑定的批准范围。

## 当前边界

- 一键配置覆盖本地文本生成；嵌入、重排、云端 OCR/ASR 仍待独立适配，本地解析/OCR/faster-whisper 保持现有选择。
- 质量档限短文本试用；超出本地上下文时明确拒绝，不会静默截断引用。教材全量抽取可能需要调整分块。
- 素材列表不会自动复制到归档目录，处理读取项目既有归档；素材应先按项目结构归档。
- 启动所用 Python 与 NVIDIA 驱动需要事先配置；本版本不自动安装系统解释器或驱动。
- 已验收真实 ARM 低配推荐与隔离桌面流程；4060 页面的硬件快照和安装流程替身验证不代表真实 4060 性能测试，目标设备仍需实测。

更多变更见 [更新说明](../RELEASE_NOTES_0.2-latest.md)，许可与素材处理边界见 [第三方说明](../THIRD-PARTY-NOTICES.md)。
