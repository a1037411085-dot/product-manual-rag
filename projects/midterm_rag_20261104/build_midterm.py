from pptx import Presentation
from pptx.util import Inches,Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE
from pathlib import Path
p=Path(r'C:\Users\T9\Desktop\珠海学院人工智能\RAG\projects\midterm_rag_20261104'); p.mkdir(exist_ok=True,parents=True)
slides=[
('产品说明书智能问答系统','期中汇报｜基于 RAG 的知识检索与问答',['面向产品资料的可追溯问答','项目进展 · 实验结果 · 后续计划'],'各位老师好，我们小组的项目是产品说明书智能问答系统。本次汇报介绍项目目标、系统实现、阶段评估结果和后续工作。'),
('项目目标：让说明书中的信息更容易查找','从检索资料到回答问题',['问题：说明书篇幅长，型号、参数与操作步骤查找费时','目标：按产品与版本查找资料，回答问题并展示来源','边界：资料不足时拒答；敏感业务转人工'],'传统说明书的信息分散，用户查找产品参数和操作方法需要花费时间。我们的目标是让系统依据指定产品和版本的资料回答，同时展示来源。对于资料缺失或敏感业务，系统提供拒答或转人工路径。'),
('系统架构：知识入库与问答两条链路','资料与问题在检索阶段汇合',['知识入库：文档导入 → 清洗分块 → 向量化 → 索引','用户问答：选择产品版本 → 检索证据 → 组织回答 → 返回来源','应用组成：网页界面 · FastAPI · SQLite · 可选 FAISS 与模型服务'],'系统分为两条链路。知识入库阶段解析文档、清洗分块并建立索引。问答阶段，用户选择产品和版本，系统在对应范围检索证据，再通过原文或模型组织回答，并返回来源。'),
('核心实现：从原始文本到可检索证据','数据处理与特征工程',['清洗：规范化文本、去除页码及相邻重复行','切片：保留标题，处理长文本；表格结构仍需改善','检索：字符 n-gram 哈希与覆盖评分；可选语义向量和 FAISS','约束：按产品与版本过滤，减少不同资料之间串用'],'在数据处理方面，我们先清洗文本，再切分为可检索片段。系统保留字符检索基线，也提供语义向量和 FAISS 路径。检索会先限定产品和版本，但表格中字段和值分离仍是当前需要解决的问题。'),
('回答流程：证据、来源与兜底','多种回答路径服务不同场景',['FAQ 匹配充分时直接返回已保存答案与来源','检索后可返回原文，或将证据组装进 Prompt 调用模型','缺少依据时拒答；退款、投诉等敏感问题转人工','知识缺口与用户反馈用于后续补充'],'回答阶段会优先处理 FAQ。其他问题先检索相关片段，再按配置返回原文或调用模型。系统展示来源，也提供知识缺口和反馈入口。但现有结果表明，拒答和证据充分性仍需加强。'),
('评估设计：真实说明书与 158 个问题','同一批问题比较两种系统配置',['语料：5 份产品说明书，约 7.1 万字、1,940 个片段','问题：138 条应答、15 条拒答、5 条转人工','离线：字符检索 + 原文回退；在线：语义检索 + 模型生成','指标：片段 Hit@K、状态与关键词命中、拒答和延迟'],'本次主要展示 v3 报告中的评估。语料包括五份真实产品说明书和 158 个问题。离线与在线配置使用同一批问题，但两者同时改变检索与生成环节，因此对比反映整体配置差异。'),
('离线结果：检索命中与回答正确需要分开看','158 题实验｜字符检索 + 原文回退',['Hit@1  79.0%     Hit@3  92.8%','状态准确率  97.5%     答案关键词命中率  79.0%','拒答正确  12 / 15     转人工正确  5 / 5','关键词命中是自动近似指标，不等同于完整答案质量'],'离线配置的片段 Hit at 1 为百分之七十九，前三条命中率为百分之九十二点八。状态准确率较高，但关键词命中率为百分之七十九，说明系统判断已回答，并不意味着回答内容正确。拒答和转人工样本分别只有十五条和五条，结论需要结合样本量理解。'),
('配置对比：在线方案在本次测试中表现较弱','同时更换了检索与回答方式',['片段 Hit@1：离线 79.0%  /  在线 48.6%','拒答正确率：离线 80.0%  /  在线 40.0%','端到端延迟 P50：离线 23 ms  /  在线 1,738 ms','结论限于本次语料、模型、阈值和测试集'],'在这组实验中，离线配置的检索命中和拒答指标高于在线配置，延迟也更低。但这是两套完整配置的比较，不能将差异全部归因于语义检索或模型本身。阈值、切片粒度和模型生成都可能影响结果。'),
('失败案例：相似资料不等于有效答案','错误揭示数据与证据判断的瓶颈',['多型号表格拍平后，字段与数值分离，可能匹配错误型号','无价格信息时，系统可能返回产品相关但答非所问的片段','繁简不一致影响字符匹配；小样本统一后 3/8 → 8/8','模型可能用常识解释替代资料中缺失的参数'],'失败案例集中在数据结构和证据充分性。多型号参数表被拍平后，字段与数值可能失去对应关系。无答案问题可能匹配到产品相关的其他内容。报告中繁简统一的小样本改善也说明预处理很重要，但八题不足以推广成整体效果。'),
('后续计划：围绕数据、检索与评估持续迭代','形成可复现的改进闭环',['数据：保留表格行列与型号关系，优化分块和繁简规范化','检索：比较段落粒度、重排和证据充分性判断','评估：扩充真实问法与无答案题，在独立开发集校准阈值','演示：固定环境、准备示例及异常情况下的备用方案'],'下一阶段先改进表格解析和切片，保留型号、字段与数值之间的关系。随后增加重排和证据充分性判断，并扩大真实问法和无答案问题，使用独立开发集校准阈值。汇报演示也会准备固定样例和备用方案。'),
('团队分工','各环节协作完成项目',['黄健｜数据收集与清洗：原始语料、测试集','周欣宇｜特征与上下文：切片、向量化、混合评分、Prompt','郑岳展｜模型与系统：架构设计、训练相关工作、代码实现','徐明宇｜评估与可视化；王军｜测试整合','廖松涛｜报告整合、案例展示与汇报'],'项目由六位成员共同完成。黄健负责数据收集与测试集，周欣宇负责特征和上下文工程，郑岳展负责模型与系统构建，徐明宇负责评估与可视化，王军负责测试整合，我负责报告整合和最终演示汇报。谢谢各位老师。')]
r=Presentation(); r.slide_width=Inches(13.333); r.slide_height=Inches(7.5)
def box(s,x,y,w,h,text,size,color,bold=False):
 t=s.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h)).text_frame;t.word_wrap=True
 t.margin_left=Inches(.02);t.margin_right=Inches(.02)
 for i,line in enumerate(text.split('\n')):
  q=t.paragraphs[0] if i==0 else t.add_paragraph();q.text=line;q.font.name='Microsoft YaHei';q.font.size=Pt(size);q.font.bold=bold;q.font.color.rgb=RGBColor.from_string(color)
 return t
for i,(title,sub,items,note) in enumerate(slides):
 s=r.slides.add_slide(r.slide_layouts[6]);bg=s.background.fill;bg.solid();bg.fore_color.rgb=RGBColor.from_string('08172B')
 shape=s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(.55), Inches(.48), Inches(.09), Inches(.55));shape.fill.solid();shape.fill.fore_color.rgb=RGBColor.from_string('21C9C3');shape.line.fill.background()
 box(s,.85,.42,11.8,.65,title,27,'F3F8FF',True);box(s,.87,1.16,11,.42,sub,15,'6EDDD6')
 if i in (6,7):
  for j,item in enumerate(items):
   y=1.85+j*.95; sh=s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,Inches(.9),Inches(y),Inches(11.5),Inches(.72));sh.fill.solid();sh.fill.fore_color.rgb=RGBColor.from_string('122D47');sh.line.fill.background();box(s,1.2,y+.13,10.9,.5,item,20 if j<3 else 16,'E8F4FA',j<2)
 else:
  for j,item in enumerate(items):
   y=1.85+j*(.83 if len(items)>4 else 1.02);box(s,1.05,y,.35,.4,'●',14,'21C9C3');box(s,1.48,y-.03,10.8,.75,item,20 if len(items)>4 else 22,'E8F4FA')
 box(s,.86,7.04,10,.24,'产品说明书智能问答系统  |  期中汇报',10,'8CA4B8');box(s,12,7.04,.45,.25,f'{i+1:02}',10,'8CA4B8')
 s.notes_slide.notes_text_frame.text=note
r.save(p/'期中汇报_RAG.pptx')
(p/'逐页讲稿.md').write_text('# 期中汇报逐页讲稿\n\n'+ '\n\n'.join(f'## 第{i+1}页 {x[0]}\n\n{x[3]}' for i,x in enumerate(slides)),encoding='utf-8')
print(p/'期中汇报_RAG.pptx');print(p/'逐页讲稿.md')
