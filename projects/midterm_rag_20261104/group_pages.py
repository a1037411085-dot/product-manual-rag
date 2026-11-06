from pathlib import Path
import xml.etree.ElementTree as ET
p=Path('projects/midterm_rag_20261104/svg_output'); ns='http://www.w3.org/2000/svg';ET.register_namespace('',ns)
for f in p.glob('*.svg'):
 t=ET.parse(f); root=t.getroot(); root.set('data-pptx-page-role','cover' if f.stem=='01' else 'closing' if f.stem=='11' else 'content'); children=list(root); children[0].set('data-pptx-role','background')
 for i,child in enumerate(children[1:],1):
  root.remove(child); g=ET.SubElement(root,f'{{{ns}}}g',{'id':f'unit-{i:02}'});g.append(child)
 t.write(f,encoding='utf-8',xml_declaration=False)
