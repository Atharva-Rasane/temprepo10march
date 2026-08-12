#!/usr/bin/env python3
import csv, os, re, time
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

BASE="https://builtin.com"; DIRECTORY="https://builtin.com/companies/location/colorado"
START_PAGE=int(os.getenv("START_PAGE","1")); END_PAGE=int(os.getenv("END_PAGE","252"))
OUT=Path(os.getenv("OUT_FILE",f"builtin_colorado_{START_PAGE}_{END_PAGE}.csv"))
TARGET_AREAS=["Boulder","Louisville","Superior","Broomfield","Westminster","Longmont","Lafayette","Denver","Golden","Lakewood","Greenwood Village","Denver Tech Center","DTC","Centennial","Loveland","Fort Collins"]
ROOT_RE=re.compile(r"^https://builtin\.com/company/[^/?#]+/?$",re.I)

def clean(s): return re.sub(r"\s+"," ",s or "").strip()
def canonical(h):
    if not h:return ""
    u=urljoin(BASE,h); p=urlsplit(u); u=urlunsplit((p.scheme,p.netloc,p.path.rstrip('/'),'', ''))
    return u if ROOT_RE.match(u) else ""
def card_for(a,u):
    best=None
    for p in a.parents:
        if getattr(p,'name',None) in ('body','html',None): break
        roots={canonical(x.get('href')) for x in p.find_all('a',href=True)}; roots.discard('')
        if u not in roots: continue
        if len(roots)>1: break
        t=clean(p.get_text(' ',strip=True))
        if 90<=len(t)<=7000: best=p
    return best or a.parent

def extract(card,a,u,page,rank):
    company=clean(a.get_text(' ',strip=True)); lines=[clean(x) for x in card.stripped_strings if clean(x)]
    industries=''
    try:i=next(i for i,x in enumerate(lines) if x==company)
    except StopIteration:i=0
    tmp=[]
    for x in lines[i+1:]:
        xl=x.lower()
        if 'save saved' in xl or 'create job alert' in xl or re.search(r'\bemployees\b',xl) or re.search(r'\boffices?\b',xl): break
        if x not in {'Save','Saved','CREATE JOB ALERT'}: tmp.append(x)
    if tmp: industries=clean(' '.join(tmp[:3]))
    employees=''; emp_idx=None
    for i,x in enumerate(lines):
        m=re.fullmatch(r'([\d,]+)\s+(?:Total\s+)?Employees',x,re.I)
        if m: employees=m.group(1).replace(',',''); emp_idx=i; break
    loc=''
    if emp_idx is not None:
        for j in range(emp_idx-1,max(-1,emp_idx-7),-1):
            x=lines[j]; xl=x.lower()
            if x in {company,'Save','Saved','CREATE JOB ALERT'} or 'benefit' in xl or 'hiring now' in xl or 'see our teams' in xl: continue
            if re.fullmatch(r'\d+\s+Offices?',x,re.I) or 'remote' in xl or len(x)<=80: loc=x; break
    benefits=''
    for x in lines:
        m=re.fullmatch(r'([\d,]+)\s+Benefits?',x,re.I)
        if m: benefits=m.group(1).replace(',',''); break
    searchable=f"{loc} {' '.join(lines[:20])}".lower(); matches=[c for c in TARGET_AREAS if re.search(rf'\b{re.escape(c.lower())}\b',searchable)]
    return {'company':company,'industries':industries,'listing_location_or_offices':loc,'target_area_match':'; '.join(dict.fromkeys(matches)),'employees':employees,'benefits_count':benefits,'hiring_now':'yes' if any('hiring now' in x.lower() for x in lines) else 'no','teams_page_available':'yes' if any('see our teams' in x.lower() for x in lines) else 'no','profile_url':u,'source_page':page,'page_rank':rank,'directory':'Built In Colorado','collected_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}

def parse(html,page):
    soup=BeautifulSoup(html,'html.parser'); out=[]; seen=set(); rank=0
    for a in soup.find_all('a',href=True):
        u=canonical(a.get('href'))
        if not u or u in seen: continue
        name=clean(a.get_text(' ',strip=True))
        if not name or len(name)>150: continue
        seen.add(u); rank+=1; out.append(extract(card_for(a,u),a,u,page,rank))
    return out

def write(rows):
    fields=['company','industries','listing_location_or_offices','target_area_match','employees','benefits_count','hiring_now','teams_page_available','profile_url','source_page','page_rank','directory','collected_utc']
    with OUT.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)

def driver():
    o=webdriver.ChromeOptions()
    for a in ['--headless=new','--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--window-size=1920,1080','--lang=en-US']: o.add_argument(a)
    o.add_experimental_option('prefs',{'profile.default_content_setting_values.notifications':2,'profile.managed_default_content_settings.images':2})
    return webdriver.Chrome(options=o)

def main():
    d=driver(); by={}; failures=0
    try:
        for page in range(START_PAGE,END_PAGE+1):
            url=f'{DIRECTORY}?country=USA&page={page}'; rows=[]
            for attempt in range(1,3):
                try:
                    print(f'PAGE {page} attempt={attempt}',flush=True); d.get(url)
                    WebDriverWait(d,18).until(lambda x: len(x.find_elements(By.CSS_SELECTOR,'a[href^="/company/"]'))>=5 or len(x.find_elements(By.CSS_SELECTOR,'a[href*="builtin.com/company/"]'))>=5)
                    rows=parse(d.page_source,page); print(f' parsed={len(rows)} title={d.title!r}',flush=True)
                    if rows: break
                except (TimeoutException,WebDriverException) as e: print(type(e).__name__,e,flush=True)
            if not rows:
                failures+=1
                if failures>=3: break
            else:
                failures=0
                for r in rows: by[r['profile_url']]=r
            time.sleep(.15)
    finally:d.quit()
    rows=sorted(by.values(),key=lambda r:(int(r['source_page']),int(r['page_rank']),r['company'].lower())); write(rows); print('ROWS',len(rows),'OUT',OUT,flush=True)
if __name__=='__main__':main()
