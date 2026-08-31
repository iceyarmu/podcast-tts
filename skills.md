---
name: podcast-tts
description: 使用火山引擎 Seed Audio API 生成单人或多人播客、配音和混合音轨。支持纯文本生成、文件或音色名参考、音频参数控制、字幕输出和文件验证。
---

# Podcast TTS Skill

通过 `src/generate_podcast.py` 调用火山引擎 Seed Audio API。脚本直接使用 `httpx` 发送 HTTP 请求，不依赖火山引擎 SDK。

## 适用场景

- 根据自然语言导演提示生成播客、对话、旁白、环境音或混合音轨。
- 使用一至三段本地音频复刻角色音色。
- 使用火山引擎音色名或 speaker ID 合成语音。
- 为生成音频同时保存句子级和词级字幕时间轴。

## 环境要求

安装依赖：

```bash
python3 -m pip install httpx
```

在仓库根目录 `.env` 中配置：

```dotenv
VOLCENGINE_APP_KEY=your_api_key
```

脚本会依次查找当前目录和仓库根目录的 `.env`。也可用 `--env-file` 指定其他文件。不要在命令、日志、提示词、字幕或提交中输出 API Key。

## 命令

```bash
python3 src/generate_podcast.py \
  --prompt "$(<test/podcast3.md)" \
  --filename test/podcast3.mp3 \
  --reference_audio docs/调皮男孩.mp3 docs/藕霸小童.mp3 \
  --subtitle-filename test/podcast3.subtitle.json
```

也可以直接传入短提示词：

```bash
python3 src/generate_podcast.py \
  --prompt "Generate a short, cheerful English greeting. No music." \
  --filename test/greeting.mp3
```

始终从用户的工作目录运行脚本，不要切换到 `src` 目录。

## 参数

| 参数 | 必选 | 默认值 | 说明 |
|---|---:|---|---|
| `--prompt` / `-p` | 是 | - | 直接传入导演提示词；可用 `"$(<file.md)"` 读取长提示词 |
| `--filename` / `-f` | 是 | - | 输出音频路径 |
| `--reference_audio` | 否 | - | 按顺序传入一至三个本地音频路径、音色名或 speaker ID；允许混排 |
| `--format` | 否 | 从扩展名推断 | `mp3`、`wav`、`pcm` 或 `ogg_opus` |
| `--sample-rate` | 否 | MP3/WAV/PCM 为 `24000` | 输出采样率；Opus 必须为 `48000` |
| `--speech-rate` | 否 | `0` | 语速，范围 `-50` 至 `100` |
| `--loudness-rate` | 否 | `0` | 音量，范围 `-50` 至 `100` |
| `--pitch-rate` | 否 | `0` | 音调，范围 `-12` 至 `12` |
| `--enable-subtitle` | 否 | 关闭 | 请求字幕并在终端输出字幕文本 |
| `--subtitle-filename` | 否 | - | 请求字幕并将完整时间轴保存为 JSON |
| `--env-file` | 否 | 自动查找 `.env` | 指定环境变量文件 |

`--reference_audio` 最多接收三个值。现有文件会编码为 `audio_data`；其他不带路径特征的值会原样作为 `speaker` 音色提交。文件和音色名可以混排，顺序不会改变。

```bash
python3 src/generate_podcast.py \
  --prompt "$(<test/podcast3.md)" \
  --filename test/mixed-reference.mp3 \
  --reference_audio docs/调皮男孩.mp3 zh_male_nezha
```

若值包含 `/`、`\`、`~`、`.` 开头或文件扩展名，脚本会把它视为文件路径；路径不存在时直接报错，不会静默当作音色名。

## API 硬限制

- `text_prompt` 最多 3000 个字符。
- 文件路径和音色名合计最多三条 reference。
- 每条参考音频不超过 30 秒且不超过 10 MB。
- 参考音频支持 WAV、MP3、PCM 和 OGG Opus。
- 单次模型原始输出最长 120 秒。
- `speaker`、`audio_data` 和 `audio_url` 在同一个 reference 对象中互斥。
- 本地参考音频必须在提示词中使用准确的 `@音频1`、`@音频2`、`@音频3` 标记。
- 引用标记必须与 `--reference_audio` 的传入顺序严格一致，不能翻译成 `@audio1`。

## 标准工作流

1. 明确用户要求的语言、角色、关系、观点、结论、音色文件和输出路径。
2. 用 `ffprobe` 检查每条参考音频的格式、时长和大小。
3. 确定 reference 顺序，并在提示词角色定义中绑定对应的 `@音频N`。
4. 完成整份提示词后检查字符数和预计口语长度，再调用 API。不要在调整草稿时反复调用付费接口。
5. 优先开启字幕并保存 JSON，用字幕核对关键台词、结论和结尾是否完整。
6. 用 FFmpeg 完整解码成品；字幕只能验证文本，角色音色和自然互动仍需实际听取确认。

参考音频预检：

```bash
ffprobe -v error \
  -show_entries format=filename,duration,size \
  -show_entries stream=codec_name,sample_rate,channels \
  -of json docs/reference.mp3
```

提示词长度检查：

```bash
wc -m test/podcast.md
```

成品验证：

```bash
ffprobe -v error \
  -show_entries format=format_name,duration,size,bit_rate \
  -show_entries stream=codec_name,sample_rate,channels \
  -of json test/podcast.mp3

ffmpeg -hide_banner -v error -i test/podcast.mp3 -f null -
```

## 提示词编写指南

### 推荐结构

一份稳定的多人播客提示词应按以下顺序编写：

1. **角色与音色绑定**：角色名、年龄感、性别感、音色、性格、吐字和 reference。
2. **事实与连续性约束**：人物关系、称谓、性别代词、必须保持的设定。
3. **全局声音导演要求**：语言、节目类型、节奏、是否有旁白/音乐、声场和角色稳定性。
4. **按时间顺序排列的台词**：稳定角色标签、情绪、动作、准确引号台词和穿插互动。
5. **结尾约束**：最终结论、结束台词以及“不要添加额外内容”等限制。

### 角色定义

每个角色只使用一个稳定名称，不要在同一提示词中交替使用“主播 1”“男孩”“萌萌”等不同标签。音色描述应有明显区分，但不需要堆叠同义形容词。

```text
Mengmeng has a mischievous, cute, childlike young boy's voice with clear diction. His performer is @音频1.
Bear Brother has an honest, gentle, slightly mature young boy's voice with clear diction. His performer is @音频2.
```

若存在容易被模型纠正的设定，应单独写成不可朗读的背景信息：

```text
Mama is a man and uses he and him pronouns. This is background information and must not be spoken or explained.
```

### 全局导演要求

写清楚“要什么”和“不要什么”。若只要人声，明确排除旁白、音乐和额外台词。若台词必须是英语，导演说明也优先全部使用英语；API 固定引用标记 `@音频N` 除外。

```text
This is a light and playful two-person children's podcast. Use only English for all spoken words. Keep both voices clear and close. Keep each character's voice consistent. There is no narrator and no background music. Do not add spoken words beyond the quoted dialogue. Do not speak the stage directions.
```

### 台词与舞台指示

- 所有必须准确说出的内容放在引号内。
- 引号外只写角色、语气、动作、停顿、笑声和交叠方式。
- 音效写在实际发生的位置，不要集中堆到提示词末尾。
- 使用具体指令，如 `gives a small laugh`、`interrupts quickly`，少用含糊的 `acts naturally`。
- 一次只安排一个主要动作；过密的笑声、停顿和重叠会拉长时长并降低可懂度。
- 重叠只用于很短的附和或打断，并补充 `while keeping every line easy to understand`。

```text
Mengmeng asks cheerfully, "Do you like the sun?"
Bear Brother answers at once, "Yes!"
Mengmeng quickly talks over him, "I like naps more!"
They both laugh briefly.
```

### 时长控制

模型计费和 120 秒限制以 `original_duration` 为准。口语词数不是精确时长，但可以作为提交前的风险指标：

- 对带停顿、笑声和打断的英文双人播客，可先从约 100 至 140 个口语词开始。
- 将台词写成多个短回合，而不是增加长独白。
- 舞台动作也会占用时间；越多笑声、停顿和环境音，留给台词的预算越少。
- 出现 `InvalidPayload:DurationOutOfRange` 时，不要原样重试；先删减约 20% 至 30% 的口语和动作，再重新提交。
- 超过一段音轨能稳定承载的内容时，应拆成多个片段，而不是依赖提高 `speech_rate` 强行压缩。

## 播客编写指南

### 先定义戏剧核心

动笔前用一句话明确：

```text
角色 A 认为 X，因为理由 1；角色 B 认为 Y，因为理由 2；最后通过 Z 达成结论。
```

这句话只用于规划，不必直接写进台词。观点必须与角色经验相关，避免两个角色只是重复同一事实。

### 推荐对话结构

1. **开场钩子**：用问题、惊讶或直接分歧在一至三句内进入主题。
2. **亮明观点**：两位角色分别给出清楚、不同的立场。
3. **来回举例**：用短句轮流给出理由、反驳、追问和玩笑。
4. **观点转折**：让角色承认对方至少有一点道理，而不是突然放弃原立场。
5. **共同结论**：用一至三句总结，并用一句容易记住的结束语收尾。

### 增加互动的方法

- **直接回应**：下一句复用或反驳上一句中的具体词，而不是开启新话题。
- **追问**：使用 `Why?`、`Really?`、`What about...?` 推动对话。
- **短附和**：适量加入 `Mm-hmm`、`Right`、`Oh!`，避免每句都附和。
- **轻微打断**：在观点最明确的位置用两至五个词插话。
- **回环笑点**：前面建立一个小玩笑，后面再次提到它。
- **共同说话**：只在结尾或明确共识处偶尔使用，避免角色音色难以区分。

高互动不等于所有句子重叠。目标是让每个回合都受上一个回合影响，同时保持台词可辨认。

### 儿童英文播客

- 使用主谓宾清楚的短句和常见词汇。
- 每句通常只表达一个意思。
- 通过拟声、夸张比喻和善意玩笑制造童趣。
- 避免复杂从句、抽象总结和成人式长篇说理。
- 分歧保持安全、温和；角色可以争论，但不要互相贬低。
- 结论应由前面的理由自然推导出来。

### 通用英文模板

```text
Host One has a [voice description] voice with clear diction. The performer is @音频1.
Host Two has a [contrasting voice description] voice with clear diction. The performer is @音频2.

[Important relationship, identity, pronunciation, or pronoun facts.] This is background information and must not be spoken.

This is a [mood] two-person podcast. Use only [language] for all spoken words. Keep the conversation highly interactive, with short replies, natural reactions, and a few brief interruptions. Keep pauses short and every line easy to understand. There is no narrator and no background music. Do not add spoken words beyond the quoted dialogue. Do not speak the stage directions.

Host One asks [emotion], "[opening question]"
Host Two answers quickly, "[position B]"
Host One interrupts playfully, "[position A]"
Host Two asks, "Why?"
Host One replies, "[reason A]"
Host Two responds, "[reason B]"
[Continue with short, connected exchanges.]
Host One admits, "[what is good about B]"
Host Two admits, "[what is good about A]"
Host One asks, "So, what do we think?"
Host Two answers warmly, "[shared conclusion]"
They end together, "[short closing line]"
```

## 生成后检查

至少确认以下内容：

- HTTP/API 状态成功，输出文件非空。
- `ffprobe` 识别出的容器、编码和采样率符合请求。
- FFmpeg 可以从头到尾完整解码。
- 字幕包含用户要求的关键事实、双方观点和最终结论。
- 最后一条字幕在音频结束前正常完成，没有被 120 秒限制截断。
- 实际听取后，角色音色没有互换，附和和重叠仍然清楚，音量没有明显削波。

字幕不标识说话人，因此“台词存在”不能证明“角色分配正确”。角色音色和说话顺序必须以实际听感为准。

## 常见问题

| 现象 | 处理方法 |
|---|---|
| `VOLCENGINE_APP_KEY is not set` | 在 `.env` 配置密钥，或用 `--env-file` 指定正确文件 |
| `DurationOutOfRange` | 缩短台词和舞台动作，不要原样重试 |
| 音色互换 | 检查 `--reference_audio` 顺序和 `@音频N` 绑定 |
| 输出多了旁白或额外台词 | 缩短提示词，并明确 `no narrator`、`do not add spoken words` |
| 英文对话夹杂其他语言 | 将所有导演说明和台词改成英语，仅保留固定 `@音频N` 标记 |
| 台词粘连或听不清 | 减少重叠，缩短单句，并要求短暂停顿和清晰可懂 |
| HTTP 502 或超时 | 脚本会使用同一个 request ID 自动重试；不要同时手动重复提交 |
| 字幕缺失 | 使用 `--enable-subtitle` 或 `--subtitle-filename` |

## 执行原则

- 用户指定的角色名、语言、观点、结论、音色文件、reference 顺序和输出路径必须保持不变。
- 在 API 调用前完成提示词和引用检查，避免无意义计费。
- 不在回复、日志、文档或提交中暴露 API Key 或 Base64 参考音频。
- 不用字幕替代实际听音；无法听取时，应明确说明验证边界。
- 只覆盖用户明确要求覆盖的输出文件，保留无关工作区改动。
