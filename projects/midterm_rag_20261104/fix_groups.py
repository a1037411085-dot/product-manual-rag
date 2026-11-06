from pathlib import Path
import xml.etree.ElementTree as ET
ns='http://www.w3.org/2000/svg'; ET.register_namespace('',ns)
for f in Path('projects/midterm_rag_20261104/svg_output').glob('*.svg'):
 t=ET.parse(f);r=t.getroot();r.set('data-pptx-page-role','cover' if f.stem=='01' else 'content');items=list(r);items[0].set('id','background')
 for g in items[1:]:r.remove(g)
 group=ET.SubElement(r,f'{{{ns}}}g',{'id':'content','data-pptx-bounds':'0 0 1280 720'})
 for g in items[1:]:
  for child in list(g):group.append(child)
 t.write(f,encoding='utf-8')
