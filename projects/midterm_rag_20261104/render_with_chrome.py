import sys
from playwright.sync_api import BrowserType
orig=BrowserType.launch
def launch(self,*args,**kwargs):
 kwargs.setdefault('executable_path',r'C:\Program Files\Google\Chrome\Application\chrome.exe')
 return orig(self,*args,**kwargs)
BrowserType.launch=launch
sys.path.insert(0,r'C:\Users\T9\.agents\skills\ppt-master\scripts')
import visual_review
sys.argv=['visual_review.py',r'C:\Users\T9\Desktop\珠海学院人工智能\RAG\projects\midterm_rag_20261104']
raise SystemExit(visual_review.main())
