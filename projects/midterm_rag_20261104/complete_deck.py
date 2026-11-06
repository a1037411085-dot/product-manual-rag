from pathlib import Path
from html import escape
import re
p=Path('projects/midterm_rag_20261104')
bg='#FAFAF8'; ink='#162B3B'; accent='#2F6675'; muted='#4B6C7A'; rule='#D8E0E2'; tint='#EEF2F4'
pages=[]
def t(x,y,s,size=28,c=ink,b=False):
 return f'<text x="{x}" y="{y}" font-size="{size}" fill="{c}" font-weight="{"bold" if b else "normal"}">{escape(s)}</text>'
def box(x,y,w,h,c):
 return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{c}"/>'
def lines(x,y,ss,size=28,step=43,c=ink):
 return ''.join(t(x,y+i*step,s,size,c) for i,s in enumerate(ss))
def add(title,sub,body,summary):
 pages.append((title,sub,body,summary))
def row(y,label,a,b):
 return box(82,y-42,1116,1,rule)+t(82,y,label,28,accent,True)+t(280,y,a,28)+t(280,y+38,b,24,muted)
def pic(n,y,w,h):
 return f'<image x="76" y="{y}" width="{w}" height="{h}" href="../images/image_{n:03}.png" preserveAspectRatio="xMidYMid meet"/>'
add('产品说明书智能问答系统','小组项目 · 期中汇报',
 box(82,228,80,4,accent)+t(82,323,'问一句话，找到说明书里的依据',42,ink,True)
 +t(82,410,'基于 RAG 的产品资料检索与问答',28,muted)
 +t(82,531,'汇报人  廖松涛',24,ink)+t(82,571,'2026 年 11 月 6 日',22,muted),'项目主题、汇报人和日期')
add('说明书有答案，但不好找','说明书里有答案，用户需要快速找到适用的那一条',
 box(82,212,1116,102,tint)+t(108,278,'“这款洗衣机的额定洗涤容量是多少？”',36,ink,True)
 +t(84,387,'01  确认型号',26,accent,True)+t(461,387,'02  找到依据',26,accent,True)+t(836,387,'03  给出结果',26,accent,True)
 +lines(84,444,['避免拿其他型号','的参数来回答'],27,40)
 +lines(461,444,['返回数值和来源','方便回查原资料'],27,40)
 +lines(836,444,['资料不足就拒答','退款、投诉转人工'],27,40),'目标：核对产品与版本；检索来源；回答、拒答或转人工')
add('从导入说明书到查到答案','已实现说明书入库、按产品和版本问答，以及来源展示',
 ''.join(t(82,y+46,label,24,accent,True)+''.join(box(236+j*238,y,194,72,tint)+t(254+j*238,y+46,a,25)+(t(437+j*238,y+46,'→',24,accent) if j<3 else '') for j,a in enumerate(seq)) for y,label,seq in [(218,'知识入库',['导入说明书','清洗和切片','向量化','建立索引']),(370,'用户问答',['选产品与版本','检索相关片段','组织答案','返回答案与来源'])])
 +t(82,568,'网页界面 · FastAPI · SQLite · 可选 FAISS 与模型服务',24,muted),'两条链路：资料入库与用户问答；项目技术组成')
add('说明书怎么进入知识库','切片决定了检索时能看到多少上下文',
 row(246,'清洗','规范化文本，去除页码和相邻重复行','标题保留下来，长文本按规则切片。')
 +row(385,'检索','离线用字符 n-gram 哈希与覆盖评分','语义路径用多语言 Embedding 和 FAISS。')
 +row(524,'限定范围','检索前先按产品和版本过滤','多型号表格被拍平时，字段和值仍可能失去对应关系。'),'清洗切片、字符检索与语义检索、产品版本隔离')
add('一次实际问答','v3 报告记录的在线模式样本',
 t(82,226,'用户问',24,accent,True)+t(250,226,'空调的建议制冷面积是多少？',32,ink,True)
 +box(82,266,1116,1,rule)+t(82,321,'系统答',24,accent,True)
 +lines(250,321,['32–48 m²，来自资料 [1]。','资料 [3] 给出 23–34 m²，系统提示人工确认。'],30,52)
 +box(82,438,1116,1,rule)+t(82,493,'怎么处理',24,accent,True)
 +lines(250,493,['给出参考值，同时把来源冲突告诉用户。','常规问答可走 FAQ、原文回答或模型生成。'],26,44)
 +t(82,622,'来源：v3 评估报告 §6.2。系统回答摘述。',18,muted),'报告实测案例：建议制冷面积及来源冲突；回答路径')
add('我们怎样测试系统','同一批问题，两套配置',
 t(82,245,'5 份说明书',42,ink,True)+t(467,245,'1,940 个片段',42,ink,True)+t(904,245,'158 道题',42,ink,True)
 +t(82,291,'洗衣机、吸尘器、扫拖机器人、冰箱、空调',24,muted)
 +t(82,337,'138 道应答题 · 15 道拒答题 · 5 道转人工题',24,muted)
 +row(422,'离线配置','字符检索 + 原文回退','检索阈值 0.16')
 +row(548,'在线配置','语义检索 + FAISS + glm-4-flash','检索阈值 0.35'),'实验规模与样本构成；两种配置及阈值')
add('判为已回答，内容也答对了吗','离线配置 · v3 的 158 题实验',
 pic(3,178,798,406)+t(903,234,'79.0% → 92.8%',30,accent,True)
 +lines(903,278,['证据 Hit@1 → Hit@3','前 3 条比首条更常','包含期望证据'],22,36,muted)
 +t(903,435,'12 / 15',34,ink,True)+t(903,478,'无答案题正确拒答',22,muted)
 +t(82,622,'来源：v3 评估报告图 3。关键词命中是内容核查的近似指标。',18,muted),'离线状态准确率97.5%，关键词命中79.0%；Hit@1/3、拒答样本')
add('离线配置在这批题上更稳','对比的是两套配置的整体表现',
 pic(6,177,802,432)+t(908,234,'23 ms',35,ink,True)+t(908,278,'离线问答 P50',22,muted)
 +t(908,367,'1,738 ms',35,ink,True)+t(908,411,'在线问答 P50',22,muted)
 +lines(908,502,['检索和回答方式','都发生了变化。'],24,38,accent)
 +t(82,629,'来源：v3 评估报告图 8、表 4。图中的在线关键词命中按数值等价计。',18,muted),'离线与在线的 Hit@K、状态、关键词和拒答对比；P50延迟及归因边界')
add('答错的 6 kg 是怎么来的','同一份说明书里，有多个不同含义的容量',
 t(82,227,'问题',24,accent,True)+t(250,227,'洗衣机额定洗涤容量是多少？',32,ink,True)
 +box(82,270,1116,1,rule)
 +t(82,330,'系统回答',24,accent,True)+t(250,330,'6 kg',40,ink,True)+t(421,330,'对应“智能清洗”程序',27,muted)
 +t(82,423,'说明书答案',24,accent,True)+t(250,423,'9 kg',40,ink,True)+t(421,423,'对应整机额定洗涤容量',27,muted)
 +lines(82,515,['表格拍平后，参数名脱离了原来的行和适用条件。','另一个问题：问“冰箱卖多少钱”，系统返回了食物冷却说明。'],25,45)
 +t(82,622,'来源：v3 评估报告 §6.2、表 3。',18,muted),'实际错答：6 kg 与 9 kg；表格结构丢失；资料不足误答')
add('先修表格和拒答','改完以后，用同一套题复测',
 row(246,'01  表格','保留型号、参数、数值和适用条件','优先复测空调多型号规格表与洗衣机容量。')
 +row(385,'02  拒答','判断证据是否真的包含所问字段','加测价格、费用和说明书未覆盖的问法。')
 +row(524,'03  评估','固定回答方式，单独比较检索器','补同义改写题和无答案题，用独立开发集校准阈值。'),'改表格结构、改拒答、补测并拆分变量')
add('小组分工','六位成员的实际贡献',
 ''.join(box(82+(i//3)*574,218+(i%3)*130,518,1,rule)
 +t(82+(i//3)*574,256+(i%3)*130,name,29,accent,True)
 +lines(82+(i//3)*574,298+(i%3)*130,desc,22,31)
 for i,(name,desc) in enumerate([
 ('黄健',['收集和清洗原始语料，编写测试集']),
 ('周欣宇',['切片、向量化、混合检索评分','组装 Prompt 上下文']),
 ('郑岳展',['模型与系统构建、架构设计','训练相关工作、代码实现']),
 ('徐明宇',['评估、基线对比、消融和误差分析','制作实验图表']),
 ('王军',['测试整合']),
 ('廖松涛',['汇总报告、案例与图表，梳理技术流程','负责最终演示与汇报'])]))
 +t(82,629,'谢谢老师和同学，欢迎提问。',23,muted),'六人实际分工与结束语')
sections={2:'01  为什么要做这个',3:'02  已经做了什么',10:'03  下一步打算',11:'04  每位同学做了什么'}
for i,(title,sub,body,_) in enumerate(pages,1):
 header=t(82,87,title,40,ink,True)+t(82,137,sub,24,muted)+box(82,162,1116,1,rule)
 footer=box(82,652,1116,1,rule)+t(1157,684,f'{i:02}',18,muted)
 svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720" font-family="Microsoft YaHei, Arial, sans-serif" data-pptx-page-role="{"cover" if i==1 else "content"}"><rect id="background" data-pptx-role="background" x="0" y="0" width="1280" height="720" fill="{bg}"/><g id="content" data-pptx-bounds="0 0 1280 720">{header}{body}{footer}</g></svg>'
 (p/'svg_output'/f'{i:02}.svg').write_text(svg,encoding='utf-8')
f=p/'design_spec.md'
spec=f.read_text(encoding='utf-8')
prefix='\n'.join(line for line in spec.split('## IX.')[0].splitlines() if not line.startswith('| image_00')).rstrip()
prefix+='\n| image_003.png | 1180 × 600 | 1.97 | 离线状态与关键词对比 | Figure | 左图右解读 | no-crop | user | Existing | v3 图 3 | preserve | content |'
prefix+='\n| image_006.png | 1280 × 690 | 1.86 | 离线在线配置对比 | Figure | 左图右解读 | no-crop | user | Existing | v3 图 8 | preserve | content |\n\n'
end=spec[spec.index('## X.'):]
roster='## IX. Content Outline\n\n### 封面\n\n'
for i,(title,sub,_,summary) in enumerate(pages,1):
 if i in sections:
  roster+=f'### {sections[i]}\n\n'
 roster+=f'#### Slide {i:02} - {title}\n\n- **Audience move**: 理解{summary}\n- **Relationships**: {summary}\n- **Title**: {title}\n- **Core message**: {sub}\n- **Content**: {summary}\n'
 if i in (7,8):
  roster+=f'- **Images**: image_{3 if i==7 else 6:03}.png，v3 报告原图\n'
 roster+='\n'
f.write_text(prefix+roster+end,encoding='utf-8')
f=p/'spec_lock.md';s=f.read_text(encoding='utf-8')
if '\n## images\n' not in s:
 s=s.replace('## forbidden','## images\n- p07: images/image_003.png | source=user | crop=no-crop\n- p08: images/image_006.png | source=user | crop=no-crop\n\n## forbidden')
f.write_text(s,encoding='utf-8')
