from pathlib import Path
p=Path('projects/midterm_rag_20261104/svg_output/03.svg')
s='''<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720" font-family="Microsoft YaHei, Arial, sans-serif" data-pptx-page-role="content"><rect id="background" data-pptx-role="background" x="0" y="0" width="1280" height="720" fill="#08172B"/><g id="content" data-pptx-bounds="0 0 1280 720"><rect x="64" y="52" width="8" height="47" fill="#21C9C3"/><text x="90" y="91" font-size="40" fill="#F3F8FF" font-weight="bold">系统架构：知识入库与问答两条链路</text><text x="92" y="139" font-size="24" fill="#75A9FF">资料与问题在检索阶段汇合</text>'''
def txt(x,y,t,size=25,color='#F3F8FF'):return f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}">{t}</text>'
def rect(x,y,w,h,fill):return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}"/>'
for y,label,nodes in [(205,'知识入库',['文档导入','清洗分块','向量化','建立索引']),(355,'用户问答',['选择范围','检索证据','组织回答','答案与来源'])]:
 s+=txt(92,y+47,label,25,'#21C9C3')
 for j,n in enumerate(nodes):
  x=260+j*230;s+=rect(x,y,190,72,'#122D47')+txt(x+30,y+47,n,25)
  if j<3:s+=txt(x+198,y+46,'→',24,'#75A9FF')
s+=txt(92,567,'网页界面  ·  FastAPI  ·  SQLite  ·  可选 FAISS 与模型服务',25,'#A9C0CF')+txt(92,679,'产品说明书智能问答系统  |  期中汇报',18,'#A9C0CF')+txt(1150,679,'03',18,'#A9C0CF')+'</g></svg>'
p.write_text(s,encoding='utf-8')
