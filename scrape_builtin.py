#!/usr/bin/env python3
import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

BASE = "https://builtin.com"
DIRECTORY = "https://builtin.com/companies/location/colorado"
OUT = Path("builtin_colorado_companies.csv")
CHECKPOINT = Path("builtin_colorado_companies_checkpoint.csv")
MAX_PAGES = 252
TARGET_AREAS = [
    "Boulder", "Louisville", "Superior", "Broomfield", "Westminster", "Longmont",
    "Lafayette", "Denver", "Golden", "Lakewood", "Greenwood Village",
    "Denver Tech Center", "DTC", "Centennial", "Loveland", "Fort Collins",
]
ROOT_RE = re.compile(r"^https://builtin\.com/company/[^/?#]+/?$", re.I)


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def canonical_company_url(href):
    if not href:
        return ""
    u = urljoin(BASE, href)
    p = urlsplit(u)
    u = urlunsplit((p.scheme, p.netloc, p.path.rstrip("/"), "", ""))
    return u if ROOT_RE.match(u) else ""


def card_for_anchor(anchor, company_url):
    best = None
    for parent in anchor.parents:
        if getattr(parent, "name", None) in ("body", "html", None):
            break
        roots = set()
        for a in parent.find_all("a", href=True):
            u = canonical_company_url(a.get("href"))
            if u:
                roots.add(u)
        if company_url not in roots:
            continue
        if len(roots) > 1:
            break
        txt = clean(parent.get_text(" ", strip=True))
        if 90 <= len(txt) <= 7000:
            best = parent
    return best or anchor.parent


def extract_row(card, anchor, company_url, page_num, rank):
    company = clean(anchor.get_text(" ", strip=True))
    lines = [clean(x) for x in card.stripped_strings if clean(x)]

    # Industry/taxonomy text normally appears immediately after the company name
    # and before save/job-alert controls.
    industries = ""
    try:
        name_idx = next(i for i, x in enumerate(lines) if x == company)
    except StopIteration:
        name_idx = 0
    pre_meta = []
    for x in lines[name_idx + 1:]:
        xl = x.lower()
        if "save saved" in xl or "create job alert" in xl or re.search(r"\bemployees\b", xl) or re.search(r"\boffices?\b", xl):
            break
        if x not in {"Save", "Saved", "CREATE JOB ALERT"}:
            pre_meta.append(x)
    if pre_meta:
        industries = clean(" ".join(pre_meta[:3]))

    employees = ""
    emp_idx = None
    for i, x in enumerate(lines):
        m = re.fullmatch(r"([\d,]+)\s+(?:Total\s+)?Employees", x, re.I)
        if m:
            employees = m.group(1).replace(",", "")
            emp_idx = i
            break

    location_or_offices = ""
    if emp_idx is not None:
        for j in range(emp_idx - 1, max(-1, emp_idx - 7), -1):
            x = lines[j]
            xl = x.lower()
            if x in {company, "Save", "Saved", "CREATE JOB ALERT"}:
                continue
            if "benefit" in xl or "hiring now" in xl or "see our teams" in xl:
                continue
            if re.fullmatch(r"\d+\s+Offices?", x, re.I) or "remote" in xl or len(x) <= 80:
                location_or_offices = x
                break

    benefits = ""
    for x in lines:
        m = re.fullmatch(r"([\d,]+)\s+Benefits?", x, re.I)
        if m:
            benefits = m.group(1).replace(",", "")
            break

    hiring_now = any("hiring now" in x.lower() for x in lines)
    teams = any("see our teams" in x.lower() for x in lines)

    target_match = ""
    searchable = f"{location_or_offices} {' '.join(lines[:20])}".lower()
    matches = [city for city in TARGET_AREAS if re.search(rf"\b{re.escape(city.lower())}\b", searchable)]
    if matches:
        target_match = "; ".join(dict.fromkeys(matches))

    return {
        "company": company,
        "industries": industries,
        "listing_location_or_offices": location_or_offices,
        "target_area_match": target_match,
        "employees": employees,
        "benefits_count": benefits,
        "hiring_now": "yes" if hiring_now else "no",
        "teams_page_available": "yes" if teams else "no",
        "profile_url": company_url,
        "source_page": page_num,
        "page_rank": rank,
        "directory": "Built In Colorado",
        "collected_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def parse_page(html, page_num):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    seen = set()
    rank = 0
    for a in soup.find_all("a", href=True):
        u = canonical_company_url(a.get("href"))
        if not u or u in seen:
            continue
        name = clean(a.get_text(" ", strip=True))
        if not name or len(name) > 150:
            continue
        seen.add(u)
        rank += 1
        card = card_for_anchor(a, u)
        rows.append(extract_row(card, a, u, page_num, rank))
    return rows


def write_csv(path, rows):
    fields = [
        "company", "industries", "listing_location_or_offices", "target_area_match",
        "employees", "benefits_count", "hiring_now", "teams_page_available",
        "profile_url", "source_page", "page_rank", "directory", "collected_utc",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def make_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=en-US")
    options.add_experimental_option("prefs", {
        "profile.default_content_setting_values.notifications": 2,
        "profile.managed_default_content_settings.images": 2,
    })
    return webdriver.Chrome(options=options)


def wait_for_cards(driver, timeout=25):
    WebDriverWait(driver, timeout).until(
        lambda d: len(d.find_elements(By.CSS_SELECTOR, 'a[href^="/company/"]')) >= 5
                   or len(d.find_elements(By.CSS_SELECTOR, 'a[href*="builtin.com/company/"]')) >= 5
    )


def main():
    driver = make_driver()
    all_rows = []
    by_url = {}
    empty_streak = 0
    try:
        for page in range(1, MAX_PAGES + 1):
            url = f"{DIRECTORY}?country=USA&page={page}"
            ok = False
            for attempt in range(1, 4):
                try:
                    print(f"PAGE {page}/{MAX_PAGES} attempt={attempt} {url}", flush=True)
                    driver.get(url)
                    wait_for_cards(driver)
                    rows = parse_page(driver.page_source, page)
                    print(f"  parsed={len(rows)} current_url={driver.current_url} title={driver.title!r}", flush=True)
                    if rows:
                        ok = True
                        break
                except (TimeoutException, WebDriverException) as e:
                    print(f"  retryable error: {type(e).__name__}: {e}", flush=True)
                    time.sleep(2 * attempt)
            if not ok:
                empty_streak += 1
                print(f"  no rows; empty_streak={empty_streak}", flush=True)
                if empty_streak >= 3:
                    break
                continue
            empty_streak = 0
            for row in rows:
                by_url[row["profile_url"]] = row
            all_rows = list(by_url.values())
            if page % 10 == 0 or page == 1:
                write_csv(CHECKPOINT, all_rows)
                print(f"  checkpoint unique={len(all_rows)}", flush=True)
            # Moderate pace; this is browser automation, not a high-rate request flood.
            time.sleep(0.35)
    finally:
        driver.quit()

    all_rows = sorted(by_url.values(), key=lambda r: (int(r["source_page"]), int(r["page_rank"]), r["company"].lower()))
    write_csv(OUT, all_rows)
    print(f"FINAL unique_companies={len(all_rows)} output={OUT}", flush=True)


if __name__ == "__main__":
    main()
