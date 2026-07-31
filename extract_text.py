import os
import PyPDF2
import json
from tqdm import tqdm

PDF_FOLDER = "downloaded_pdfs"
OUTPUT_FILE = "judgements.json"

def extract_text_from_pdf(pdf_path):
    text = ""
    try:
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                text += page.extract_text() or ""
    except Exception as e:
        print(f"❌ Error reading {pdf_path}: {e}")
    return text.strip()


def extract_all():
    pdf_files = [f for f in os.listdir(PDF_FOLDER) if f.endswith(".pdf")]
    print(f"📄 {len(pdf_files)} টা PDF থেকে text extract করছি...")
    
    judgements = []

    for filename in tqdm(pdf_files, desc="Extracting"):
        filepath = os.path.join(PDF_FOLDER, filename)
        text = extract_text_from_pdf(filepath)
        
        if text:
            judgements.append({
                "filename": filename,
                "text": text,
                "word_count": len(text.split())
            })

    # JSON এ save করো
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(judgements, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Done! {len(judgements)} টা judgement extracted.")
    print(f"💾 Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    extract_all()