from pathlib import Path
p=Path('projects/midterm_rag_20261104/svg_output/07.svg');s=p.read_text(encoding='utf-8');s=s.replace('79.0%     Hit@3','79.0%  /  Hit@3').replace('97.5%     答案','97.5%  /  答案').replace('12 / 15     转人工','12 / 15  /  转人工');p.write_text(s,encoding='utf-8')
