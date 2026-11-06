<!-- ppt-master-schema: design-spec/v1 -->
# RAG Midterm - Design Spec

## I. Project Information

| Item | Value |
| --- | --- |
| Project Name | RAG Midterm |
| Canvas Format | PPT 16:9 1280 × 720 |
| Page Count | 11 |
| Primary Language | zh-CN |
| Target Audience | 课程教师和同学 |
| Communication Intent | 介绍期中目标、实现、评估和改进 |
| Desired Audience Outcome | 理解项目进度、实验边界和后续工作 |
| Core Message / Ask / Action | 项目已实现可追溯问答，评估揭示证据和数据问题 |
| Delivery Context | 课堂现场汇报，约10分钟 |
| Artifact Afterlife | 可编辑课件和逐页讲稿 |
| Reading Mode | presentation |
| Content Strategy | 保留确认的11页顺序，依据v3与代码准确呈现 |
| Design Style | 浅色简洁课程汇报，信息清晰 |
| AI Image Acquisition Path | not applicable |
| Generation Mode | continuous |
| Spec Refinement | disabled |
| Speaker Notes | enabled — explicit user instruction |
| Custom Animations | disabled — final Stage-2 proactive policy |
| Narration Audio | disabled — final Stage-2 proactive policy |
| Created Date | 2026-11-04 |

## II. Canvas Specification

| Property | Value |
| --- | --- |
| Format | PPT 16:9 |
| Dimensions | 1280 × 720 |
| viewBox | `0 0 1280 720` |
| Margins | 72 px |
| Content Area | 1136 × 576 px |

## III. Visual Theme

### Theme Style

- **Mode**: instructional
- **Visual style**: swiss-minimal
- **Theme**: 简洁课堂汇报
- **Tone**: 清晰、克制

### Color Scheme

| Role | HEX | Purpose |
| --- | --- | --- |
| Background | #FAFAF8 | 背景 |
| Secondary background | #EEF2F4 | 内容区域 |
| Primary | #162B3B | 主要文字 |
| Accent | #2F6675 | 强调 |
| Secondary accent | #4B6C7A | 次强调 |
| Body text | #162B3B | 正文 |
| Secondary text | #607884 | 辅助文字 |
| Divider | #D8E0E2 | 分隔 |

## IV. Typography System

### Font Plan

| Role | Character (Reference) | Primary | English if non-English | Fallback tail |
| --- | --- | --- | --- | --- |
| Title | 清晰无衬线 | Microsoft YaHei | Arial | sans-serif |
| Body | 清晰无衬线 | Microsoft YaHei | Arial | sans-serif |

- **Title stack**: Microsoft YaHei, Arial, sans-serif
- **Body stack**: Microsoft YaHei, Arial, sans-serif

### Font Size Hierarchy

| Purpose | Anchor Size (px) |
| --- | ---: |
| Body | 28 |
| Title | 40 |
| Subtitle | 24 |
| Annotation | 18 |

## V. Layout Principles

### Deck-wide Direction

- **Hierarchy direction**: 标题、主视觉、解释、来源
- **Composition tendency**: 流程和对比优先，交替布局
- **Cross-page continuity**: 浅色背景、墨蓝文字和页码
- **Spacing posture**: 开阔
- **Spacing anchors**: 72, 24, 32, 12, 40 px

## VI. Icon Usage Specification

- **Primary bundled library**: none

| Icon Path | Suitable Scenarios |
| --- | --- |

## VIII. Image Resource List

| Filename | Dimensions | Ratio | Purpose | Type | Image pattern | Crop Policy | Acquire Via | Status | Reference | text_policy | page_role |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| image_003.png | 1180 × 600 | 1.97 | 离线状态与关键词对比 | Figure | 左图右解读 | no-crop | user | Existing | v3 图 3 | preserve | content |
| image_006.png | 1280 × 690 | 1.86 | 离线在线配置对比 | Figure | 左图右解读 | no-crop | user | Existing | v3 图 8 | preserve | content |

## IX. Content Outline

### 封面

#### Slide 01 - 产品说明书智能问答系统

- **Audience move**: 理解项目主题、汇报人和日期
- **Relationships**: 项目主题、汇报人和日期
- **Title**: 产品说明书智能问答系统
- **Core message**: 小组项目 · 期中汇报
- **Content**: 项目主题、汇报人和日期

### 01  为什么要做这个

#### Slide 02 - 说明书有答案，但不好找

- **Audience move**: 理解目标：核对产品与版本；检索来源；回答、拒答或转人工
- **Relationships**: 目标：核对产品与版本；检索来源；回答、拒答或转人工
- **Title**: 说明书有答案，但不好找
- **Core message**: 说明书里有答案，用户需要快速找到适用的那一条
- **Content**: 目标：核对产品与版本；检索来源；回答、拒答或转人工

### 02  已经做了什么

#### Slide 03 - 从导入说明书到查到答案

- **Audience move**: 理解两条链路：资料入库与用户问答；项目技术组成
- **Relationships**: 两条链路：资料入库与用户问答；项目技术组成
- **Title**: 从导入说明书到查到答案
- **Core message**: 已实现说明书入库、按产品和版本问答，以及来源展示
- **Content**: 两条链路：资料入库与用户问答；项目技术组成

#### Slide 04 - 说明书怎么进入知识库

- **Audience move**: 理解清洗切片、字符检索与语义检索、产品版本隔离
- **Relationships**: 清洗切片、字符检索与语义检索、产品版本隔离
- **Title**: 说明书怎么进入知识库
- **Core message**: 切片决定了检索时能看到多少上下文
- **Content**: 清洗切片、字符检索与语义检索、产品版本隔离

#### Slide 05 - 一次实际问答

- **Audience move**: 理解报告实测案例：建议制冷面积及来源冲突；回答路径
- **Relationships**: 报告实测案例：建议制冷面积及来源冲突；回答路径
- **Title**: 一次实际问答
- **Core message**: v3 报告记录的在线模式样本
- **Content**: 报告实测案例：建议制冷面积及来源冲突；回答路径

#### Slide 06 - 我们怎样测试系统

- **Audience move**: 理解实验规模与样本构成；两种配置及阈值
- **Relationships**: 实验规模与样本构成；两种配置及阈值
- **Title**: 我们怎样测试系统
- **Core message**: 同一批问题，两套配置
- **Content**: 实验规模与样本构成；两种配置及阈值

#### Slide 07 - 判为已回答，内容也答对了吗

- **Audience move**: 理解离线状态准确率97.5%，关键词命中79.0%；Hit@1/3、拒答样本
- **Relationships**: 离线状态准确率97.5%，关键词命中79.0%；Hit@1/3、拒答样本
- **Title**: 判为已回答，内容也答对了吗
- **Core message**: 离线配置 · v3 的 158 题实验
- **Content**: 离线状态准确率97.5%，关键词命中79.0%；Hit@1/3、拒答样本
- **Images**: image_003.png，v3 报告原图

#### Slide 08 - 离线配置在这批题上更稳

- **Audience move**: 理解离线与在线的 Hit@K、状态、关键词和拒答对比；P50延迟及归因边界
- **Relationships**: 离线与在线的 Hit@K、状态、关键词和拒答对比；P50延迟及归因边界
- **Title**: 离线配置在这批题上更稳
- **Core message**: 对比的是两套配置的整体表现
- **Content**: 离线与在线的 Hit@K、状态、关键词和拒答对比；P50延迟及归因边界
- **Images**: image_006.png，v3 报告原图

#### Slide 09 - 答错的 6 kg 是怎么来的

- **Audience move**: 理解实际错答：6 kg 与 9 kg；表格结构丢失；资料不足误答
- **Relationships**: 实际错答：6 kg 与 9 kg；表格结构丢失；资料不足误答
- **Title**: 答错的 6 kg 是怎么来的
- **Core message**: 同一份说明书里，有多个不同含义的容量
- **Content**: 实际错答：6 kg 与 9 kg；表格结构丢失；资料不足误答

### 03  下一步打算

#### Slide 10 - 先修表格和拒答

- **Audience move**: 理解改表格结构、改拒答、补测并拆分变量
- **Relationships**: 改表格结构、改拒答、补测并拆分变量
- **Title**: 先修表格和拒答
- **Core message**: 改完以后，用同一套题复测
- **Content**: 改表格结构、改拒答、补测并拆分变量

### 04  每位同学做了什么

#### Slide 11 - 小组分工

- **Audience move**: 理解六人实际分工与结束语
- **Relationships**: 六人实际分工与结束语
- **Title**: 小组分工
- **Core message**: 六位成员的实际贡献
- **Content**: 六人实际分工与结束语

## X. Speaker Notes Requirements

- **Generation**: enabled
- **Filename**: match each SVG filename under `notes/`
- **Content**: 中文口述，依据项目资料，解释数据口径
- **Total duration**: 约10分钟
- **Notes style**: 口语化、清晰
- **Presentation purpose**: 介绍期中目标、实现、评估和改进
