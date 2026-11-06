from pathlib import Path
p=Path('projects/midterm_rag_20261104'); replacements={'项目目标：让说明书中的信息更容易查找':'为什么做产品说明书问答','系统架构：知识入库与问答两条链路':'系统是怎么工作的','核心实现：从原始文本到可检索证据':'说明书怎么进入知识库','回答流程：证据、来源与兜底':'从提问到回答','评估设计：真实说明书与 158 个问题':'我们怎样测试系统','离线结果：检索命中与回答正确需要分开看':'离线模式测试结果','配置对比：在线方案在本次测试中表现较弱':'两种配置的测试对比','失败案例：相似资料不等于有效答案':'目前发现的几个问题','后续计划：围绕数据、检索与评估持续迭代':'下一步准备怎么做','多种回答路径服务不同场景':'回答依据来自产品资料','形成可复现的改进闭环':'围绕本次测试暴露的问题改进','各环节协作完成项目':'小组成员与分工'}
for f in (p/'svg_output').glob('*.svg'):
 s=f.read_text(encoding='utf-8')
 for a,b in replacements.items():s=s.replace(a,b)
 f.write_text(s,encoding='utf-8')
f=p/'design_spec.md';s=f.read_text(encoding='utf-8')
for a,b in replacements.items():s=s.replace(a,b)
f.write_text(s,encoding='utf-8')
