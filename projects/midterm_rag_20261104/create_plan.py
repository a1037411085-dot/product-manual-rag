import ast
from pathlib import Path
p=Path('projects/midterm_rag_20261104'); tree=ast.parse((p/'build_midterm.py').read_text(encoding='utf-8-sig')); slides=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='slides' for x in n.targets))
s='''<!-- ppt-master-schema: design-spec/v1 -->
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
| Design Style | 深蓝科技风，信息清晰 |
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
- **Visual style**: dark-tech
- **Theme**: 学术技术汇报
- **Tone**: 清晰、克制

### Color Scheme

| Role | HEX | Purpose |
| --- | --- | --- |
| Background | #08172B | 背景 |
| Secondary background | #122D47 | 内容区域 |
| Primary | #F3F8FF | 主要文字 |
| Accent | #21C9C3 | 强调 |
| Secondary accent | #75A9FF | 次强调 |
| Body text | #E8F4FA | 正文 |
| Secondary text | #A9C0CF | 辅助文字 |
| Divider | #36536B | 分隔 |

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
- **Cross-page continuity**: 深蓝底、青色强调和页码
- **Spacing posture**: 开阔
- **Spacing anchors**: 72, 24, 32, 12, 40 px

## VI. Icon Usage Specification

- **Primary bundled library**: none

| Icon Path | Suitable Scenarios |
| --- | --- |

## VIII. Image Resource List

| Filename | Dimensions | Ratio | Purpose | Type | Image pattern | Crop Policy | Acquire Via | Status | Reference | text_policy | page_role |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## IX. Content Outline

### Part 1: 期中汇报
'''
for i,(title,sub,items,note) in enumerate(slides,1):
 s+=f'''\n#### Slide {i:02} - {title}\n\n- **Audience move**: 了解主题 → 理解本页要点\n- **Relationships**: {"；".join(items)}\n- **Title**: {title}\n- **Core message**: {sub}\n- **Content**: {"；".join(items)}\n'''
s+='''\n## X. Speaker Notes Requirements\n\n- **Generation**: enabled\n- **Filename**: match each SVG filename under `notes/`\n- **Content**: 中文口述，依据项目资料，解释数据口径\n- **Total duration**: 约10分钟\n- **Notes style**: 口语化、清晰\n- **Presentation purpose**: 介绍期中目标、实现、评估和改进\n'''
(p/'design_spec.md').write_text(s,encoding='utf-8')
lock='''<!-- ppt-master-schema: spec-lock/v1 -->
# Execution Lock

## canvas
- viewBox: 0 0 1280 720
- format: PPT 16:9

## communication
- primary_language: zh-CN
- audience: 课程教师和同学
- objective: 介绍目标、实现、评估和改进，使听众理解项目进展及局限
- core_message: 已实现可追溯问答，评估揭示证据和数据问题

## mode
- mode: instructional

## visual_style
- visual_style: dark-tech

## colors
- background: #08172B
- secondary_background: #122D47
- primary: #F3F8FF
- accent: #21C9C3
- secondary_accent: #75A9FF
- body_text: #E8F4FA
- secondary_text: #A9C0CF
- divider: #36536B

## typography
- font_family: Microsoft YaHei, Arial, sans-serif
- title_family: Microsoft YaHei, Arial, sans-serif
- body_family: Microsoft YaHei, Arial, sans-serif
- body: 28
- title: 40
- subtitle: 24
- annotation: 18

## icons
- library: none
- inventory: none

## page_rhythm
'''+''.join(f'- P{i:02}: '+('anchor' if i in (1,11) else 'dense' if i in (6,7,8) else 'breathing')+'\n' for i in range(1,12))+'''
## pptx_structure
- mode: flat

## forbidden
- `mask`, `<style>`, `class`, external CSS, `<foreignObject>`, `textPath`, `@font-face`, `<animate*>`, `<set>`, `<script>` / event attributes, `<iframe>`
- HTML named entities in text; write typography as raw Unicode and escape XML reserved characters
'''
(p/'spec_lock.md').write_text(lock,encoding='utf-8')
