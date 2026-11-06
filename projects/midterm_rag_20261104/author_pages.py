import ast,sys,html
from pathlib import Path
p=Path('projects/midterm_rag_20261104'); tree=ast.parse((p/'build_midterm.py').read_text(encoding='utf-8-sig')); slides=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='slides' for x in n.targets))
def text(x,y,v,size=28,c='#E8F4FA',weight='normal'):
 return f'<text x="{x}" y="{y}" font-size="{size}" fill="{c}" font-weight="{weight}">{html.escape(v)}</text>'
def rect(x,y,w,h,c,rx=12): return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{c}"/>'
for i in range(int(sys.argv[1]),int(sys.argv[2])+1):
 title,sub,items,note=slides[i-1]
 o=['<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720" font-family="Microsoft YaHei, Arial, sans-serif">',rect(0,0,1280,720,'#08172B',0),rect(64,52,8,47,'#21C9C3',0),text(90,91,title,40,'#F3F8FF','bold'),text(92,139,sub,24,'#75A9FF')]
 if i==1:
  o += [rect(92,210,1090,280,'#122D47'),text(140,300,'基于产品资料的可追溯问答',40,'#F3F8FF','bold'),text(142,365,'项目进展  /  实验结果  /  后续计划',28,'#21C9C3'),text(142,435,'课程项目 · 期中汇报',24,'#A9C0CF')]
 elif i in (7,8):
  for j,item in enumerate(items):
   y=187+j*102;o += [rect(92,y,1096,80,'#122D47'),rect(92,y,6,80,'#21C9C3',0),text(120,y+50,item,27 if j<3 else 23,'#F3F8FF')]
 elif i==11:
  for j,item in enumerate(items):
   y=190+j*82;o += [rect(92,y,1096,65,'#122D47'),text(118,y+42,item,25,'#F3F8FF')]
 else:
  for j,item in enumerate(items):
   y=190+j*105;o += [rect(92,y,1096,82,'#122D47'),text(116,y+52,f'{j+1:02}',24,'#21C9C3','bold'),text(170,y+52,item,26,'#F3F8FF')]
 o += [text(92,679,'产品说明书智能问答系统  |  期中汇报',18,'#A9C0CF'),text(1150,679,f'{i:02}',18,'#A9C0CF'),'</svg>']
 (p/'svg_output'/f'{i:02}.svg').write_text('\n'.join(o),encoding='utf-8')
