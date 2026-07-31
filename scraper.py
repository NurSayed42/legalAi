import requests
from bs4 import BeautifulSoup
import os
import time
from tqdm import tqdm
from urllib.parse import urljoin, urlparse

# =============================
# CONFIG
# =============================
BASE_URL = "https://supremecourt.gov.bd"
JUDGEMENT_PAGE = "https://supremecourt.gov.bd/web/?page=judgments.php&menu=00&div_id=2"
SAVE_FOLDER = "downloaded_pdfs"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# =============================
# URL FIX — /../ সরাও
# =============================
def fix_url(href):
    if href.startswith("http"):
        # /../ থাকলে normalize করো
        parsed = urlparse(href)
        # path থেকে /../ resolve করো
        import posixpath
        clean_path = posixpath.normpath(parsed.path)
        fixed = parsed._replace(path=clean_path).geturl()
        return fixed
    else:
        # relative URL → absolute করো properly
        return urljoin(BASE_URL, href)

# =============================
# STEP 1: PDF LINKS COLLECT
# =============================
def get_all_pdf_links():
    print("🔍 Judgment list থেকে PDF links collect করছি...")
    
    pdf_links = []
    start = 0
    step = 10

    while True:
        url = f"{JUDGEMENT_PAGE}&start={start}"
        print(f"   Fetching: {url}")
        
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(response.text, "html.parser")
            
            links = soup.find_all("a", href=True)
            found_on_page = 0
            
            for link in links:
                href = link["href"]
                
                if ".pdf" in href.lower() and "process.php" not in href:
                    full_url = fix_url(href)
                    
                    if full_url not in pdf_links:
                        pdf_links.append(full_url)
                        found_on_page += 1
                        print(f"      ✅ Found: {full_url.split('/')[-1]}")
            
            print(f"   start={start}: {found_on_page} টা PDF পেলাম")
            
            if found_on_page == 0:
                print(f"\n✅ মোট {len(pdf_links)} টা PDF link পেলাম!")
                break
                
            start += step
            time.sleep(1.5)
            
        except Exception as e:
            print(f"❌ Error at start={start}: {e}")
            break
    
    return pdf_links

# =============================
# STEP 2: PDF DOWNLOAD (retry সহ)
# =============================
def download_pdfs(pdf_links):
    os.makedirs(SAVE_FOLDER, exist_ok=True)
    
    print(f"\n📥 {len(pdf_links)} টা PDF download শুরু করছি...")
    print(f"   (আগে downloaded গুলো skip হবে)\n")
    
    success = 0
    failed = 0
    skipped = 0

    for url in tqdm(pdf_links, desc="Downloading"):
        try:
            filename = url.split("/")[-1]
            filepath = os.path.join(SAVE_FOLDER, filename)
            
            # Already downloaded → skip
            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                skipped += 1
                success += 1
                continue
            
            # ৩ বার retry করো
            downloaded = False
            for attempt in range(3):
                try:
                    response = requests.get(url, headers=HEADERS, timeout=30)
                    
                    if response.status_code == 200:
                        with open(filepath, "wb") as f:
                            f.write(response.content)
                        success += 1
                        downloaded = True
                        break
                    else:
                        print(f"\n⚠️ HTTP {response.status_code}: {filename}")
                        break
                        
                except Exception as e:
                    if attempt < 2:
                        print(f"\n🔄 Retry {attempt+1}/3: {filename}")
                        time.sleep(3)  # retry এর আগে একটু wait
                    else:
                        raise e
            
            if not downloaded:
                failed += 1
                
            time.sleep(0.5)
            
        except Exception as e:
            print(f"\n❌ Failed: {url.split('/')[-1]} → {e}")
            failed += 1

    print(f"\n✅ Download complete!")
    print(f"   নতুন download: {success - skipped} টা")
    print(f"   আগে থেকে ছিল: {skipped} টা")
    print(f"   ব্যর্থ: {failed} টা")


# =============================
# MAIN
# =============================
if __name__ == "__main__":
    # আগের pdf_links.txt থাকলে সেটা use করো (re-scrape লাগবে না)
    if os.path.exists("pdf_links.txt"):
        print("📂 pdf_links.txt পেয়েছি — সেটা থেকে load করছি...")
        with open("pdf_links.txt", "r") as f:
            raw_links = [line.strip() for line in f if line.strip()]
        
        # URL fix করো পুরোনো links এ
        pdf_links = [fix_url(link) for link in raw_links]
        print(f"✅ {len(pdf_links)} টা link load হলো")
    else:
        pdf_links = get_all_pdf_links()
        with open("pdf_links.txt", "w") as f:
            for link in pdf_links:
                f.write(link + "\n")
        print(f"💾 {len(pdf_links)} টা link saved to pdf_links.txt")
    
    if pdf_links:
        download_pdfs(pdf_links)
    else:
        print("⚠️ কোনো PDF link পাওয়া যায়নি!")