import requests
from bs4 import BeautifulSoup
import re
import json
import time

BASE_URL = "http://bdlaws.minlaw.gov.bd/act-details-{}.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def clean_text(text):
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def remove_footnote_markers(tag):
    for span in tag.find_all('span', class_='footnote'):
        span.decompose()
    for sup in tag.find_all('sup'):
        sup.decompose()
    return tag


def scrape_act(act_id, session):
    url = BASE_URL.format(act_id)
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')

        # Title
        h3 = soup.find('h3')
        if h3:
            remove_footnote_markers(h3)
            title = clean_text(h3.get_text(' '))
        else:
            title = "Act {}".format(act_id)

        # Publication date
        date_tag = soup.find('p', class_='publish-date')
        pub_date = clean_text(date_tag.get_text()).strip('[] ') if date_tag else ""

        # ── Sections ──
        # Use direct children search to avoid nested row duplicates.
        # txt-head and txt-details always live inside bg-striped sections.
        sections = []
        seen_texts = set()

        # Find all txt-head divs directly (not nested ones)
        all_heads = soup.find_all('div', class_='txt-head')

        for head_div in all_heads:
            # txt-details is always the sibling div inside the same parent row
            parent_row = head_div.find_parent('div', class_='row')
            if not parent_row:
                continue
            body_div = parent_row.find('div', class_='txt-details')
            if not body_div:
                continue

            section_title = clean_text(head_div.get_text(' '))
            if not section_title:
                continue

            # Clone body so we don't destroy original soup
            body_copy = BeautifulSoup(str(body_div), 'html.parser')
            remove_footnote_markers(body_copy)
            for tag in body_copy.find_all('div', class_=['clbr', 'na']):
                tag.decompose()

            section_body = clean_text(body_copy.get_text(' '))
            if not section_body:
                continue

            # Deduplicate by body text
            if section_body in seen_texts:
                continue
            seen_texts.add(section_body)

            sections.append({'section': section_title, 'text': section_body})

        # Footnotes
        footnotes = []
        for li in soup.find_all('li', class_='footnoteList'):
            for h6 in li.find_all('h6'):
                h6.decompose()
            fn_text = clean_text(li.get_text(' '))
            if fn_text:
                footnotes.append(fn_text)

        # Fallback
        if not sections:
            main = soup.find('div', class_='col-md-11')
            fallback = clean_text(main.get_text(' ')) if main else ""
            sections = [{'section': 'Full Text', 'text': fallback}]

        return {
            'id': act_id,
            'title': title,
            'url': url,
            'pub_date': pub_date,
            'sections': sections,
            'footnotes': footnotes,
            'section_count': len(sections),
            'type': 'legislation',
        }

    except requests.exceptions.HTTPError as e:
        print("  [HTTP Error] Act {}: {}".format(act_id, e))
    except requests.exceptions.ConnectionError as e:
        print("  [Connection Error] Act {}: {}".format(act_id, e))
    except requests.exceptions.Timeout:
        print("  [Timeout] Act {}".format(act_id))
    except Exception as e:
        print("  [Error] Act {}: {}".format(act_id, e))
    return None


def scrape_all(start=1, end=10, delay=1.5, output_file='bangladesh_laws.json'):
    all_acts = []

    with requests.Session() as session:
        session.headers.update(HEADERS)

        for act_id in range(start, end + 1):
            print("\n" + "-" * 55)
            print("  Scraping Act {} ...".format(act_id))

            act_data = scrape_act(act_id, session)

            if act_data:
                all_acts.append(act_data)
                print("  OK  Title   : {}".format(act_data['title']))
                print("  OK  Date    : {}".format(act_data['pub_date']))
                print("  OK  Sections: {}".format(act_data['section_count']))
                for sec in act_data['sections']:
                    preview = sec['text'][:100]
                    print("     [{}] {} ...".format(sec['section'], preview))
            else:
                print("  FAIL  Act {}".format(act_id))

            time.sleep(delay)

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_acts, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 55)
    print("  Saved {} acts to {}".format(len(all_acts), output_file))
    return all_acts


if __name__ == "__main__":
    scrape_all(start=1, end=1400, delay=1.5, output_file='bangladesh_laws.json')