import pdfplumber
import csv
import json
import re
from pathlib import Path


#run python3 projects/scraping/pdf-extraction/pdf_extractor.py projects/scraping/pdf-extraction/i1040gi.pdf

OUTPUT_DIR = Path(__file__).parent

def extract_text_from_pdf(pdf_path):
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        print(f"Opened PDF with {len(pdf.pages)} pages")
        for i, page in enumerate(pdf.pages):
            text += page.extract_text() + "\n"
            if (i + 1) % 10 == 0:
                print(f"  Processed {i + 1} pages...")
    return text


def extract_tables_from_pdf(pdf_path):
    all_tables = []
    
    with pdfplumber.open(pdf_path) as pdf:
        print(f"Extracting tables from {len(pdf.pages)} pages...")
        
        for i, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            
            for table_idx, table in enumerate(tables):
                if table and len(table) > 1:
                    headers = table[0]
                    data = table[1:]
                    
                    records = []
                    for row in data:
                        if any(cell and cell.strip() for cell in row):
                            record = {}
                            for col_idx, cell in enumerate(row):
                                if col_idx < len(headers):
                                    col_name = headers[col_idx] or f"col_{col_idx}"
                                    record[col_name] = cell.strip() if cell else ""
                            records.append(record)
                    
                    if records:
                        all_tables.extend(records)
            
            if (i + 1) % 10 == 0:
                print(f"  Processed {i + 1} pages...")
    
    return all_tables


def extract_with_regex(pdf_path, patterns):
    text = extract_text_from_pdf(pdf_path)
    results = []
    
    for pattern_name, pattern in patterns.items():
        matches = re.findall(pattern, text, re.MULTILINE | re.IGNORECASE)
        print(f"Pattern '{pattern_name}': found {len(matches)} matches")
        results.extend(matches)
    
    return results


def save_csv(data, filepath):
    if not data:
        print("No data to save")
        return
    
    fieldnames = []
    for record in data:
        for key in record.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)
    print(f"Saved {len(data)} records to {filepath}")


def save_json(data, filepath):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(data)} records to {filepath}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python pdf_extractor.py <pdf_file> [mode]")
        print("\nModes:")
        print("  text (default) - Extract all text in reading order")
        print("  tables - Extract structured tables to CSV/JSON")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "text"
    
    if not Path(pdf_path).exists():
        print(f"Error: File not found: {pdf_path}")
        sys.exit(1)
    
    print(f"Extracting data from: {pdf_path}\n")
    
    if mode == "tables":
        print("=== Extracting tables ===")
        tables = extract_tables_from_pdf(pdf_path)
        
        if tables:
            print(f"\nExtracted {len(tables)} records from tables")
            save_csv(tables, OUTPUT_DIR / "pdf_data.csv")
            save_json(tables, OUTPUT_DIR / "pdf_data.json")
        else:
            print("\nNo tables found.")
    else:
        print("=== Extracting text in reading order ===")
        text = extract_text_from_pdf(pdf_path)
        
        output_file = OUTPUT_DIR / "pdf_full_text.txt"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(text)
        
        print(f"\nSaved full text to {output_file}")
        print(f"Total length: {len(text)} characters")
