import os
import json
import re
import time
import requests
import io
import tempfile
import fitz  # PyMuPDF for PDF extraction
from docx import Document
import google.generativeai as genai
import absl.logging

# --- Configuration ---
absl.logging.set_verbosity(absl.logging.ERROR)

# Configure Gemini API (insert your actual API key)
genai.configure(api_key="")

COMPANY_NAME = "Inditex"
# Sample URL for the unprocessed report PDF (from S3 or provided by colleagues)
SAMPLE_PDF_URL = "https://d126pdxfj5u0l1.cloudfront.net/87aec0a9-b061-4259-b81a-f9d55b382ced/Environment.pdf"
# Folder where output files will be stored (local temporary storage for processed output)
PROCESSED_FOLDER = r"C:\Users\Pranavi\PycharmProjects\PythonProject7\processed_reports"

# Generate dynamic file names using company name and timestamp
company_name_clean = COMPANY_NAME.replace(" ", "_")
timestamp = time.strftime("%Y%m%d_%H%M%S")
OUTPUT_JSON = os.path.join(PROCESSED_FOLDER, f"{company_name_clean}_esg_output_{timestamp}.json")
OUTPUT_DOCX = os.path.join(PROCESSED_FOLDER, f"{company_name_clean}_esg_report_{timestamp}.docx")

MIN_SCORE = 10
CHARS_PER_CHUNK = 4000
MAX_RETRIES = 3

# --- Step 1: Extract PDF Text from URL ---
def extract_text_from_pdf_url(pdf_url):
    """
    Downloads a PDF from the given URL, extracts its text using PyMuPDF,
    and cleans up whitespace and control characters.
    """
    try:
        response = requests.get(pdf_url)
        response.raise_for_status()  # Raise error on bad status
        pdf_stream = io.BytesIO(response.content)
        doc = fitz.open(stream=pdf_stream, filetype="pdf")
        text = "\n".join(page.get_text("text") for page in doc)
        text = re.sub(r"[\x00-\x08\x0B-\x1F\x7F]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()
    except Exception as e:
        print(f"⚠️ Error extracting text from PDF URL {pdf_url}: {e}")
        return ""

# --- Step 2: Chunk the Text ---
def chunk_text(full_text, chunk_size=CHARS_PER_CHUNK):
    """
    Splits the full text into chunks of up to 'chunk_size' characters.
    """
    chunks = []
    start = 0
    while start < len(full_text):
        end = start + chunk_size
        chunks.append(full_text[start:end])
        start = end
    return chunks

# --- Step 3: Analyze Each Chunk with Gemini (Exponential Backoff) ---
def analyze_chunk_with_gemini(chunk_text, max_retries=MAX_RETRIES):
    """
    Sends a text chunk to Gemini for analysis.
    Implements exponential backoff on 429 errors.
    The prompt instructs Gemini to output JSON with bullet lists.
    """
    model = genai.GenerativeModel("gemini-2.0-flash")
    prompt = f"""
You are an ESG analysis expert. Analyze the following chunk of a sustainability/ESG report.
Extract information for:
1) "Company Name" (if mentioned)
2) "Year" (if mentioned)
3) "Key ESG Metrics" (grouped by category, e.g. "Water Management", "Carbon Emissions", etc.)
4) "Performance Summary" (list key points as bullet points)
5) "Recommendations" (list actionable recommendations as bullet points)

Output ONLY valid JSON in this exact format:
{{
  "Company Name": "",
  "Year": "",
  "Key ESG Metrics": {{ }},
  "Performance Summary": [],
  "Recommendations": []
}}

Text chunk:
{chunk_text}
    """
    attempt = 1
    current_backoff = 5
    while attempt <= max_retries:
        try:
            response = model.generate_content(prompt, request_options={"timeout": 60})
            if not response or not hasattr(response, "text"):
                print("⚠️ AI Response empty for this chunk.")
                return ""
            return response.text.strip()
        except Exception as e:
            error_msg = str(e).lower()
            if "429" in error_msg or "quota" in error_msg:
                if attempt < max_retries:
                    print(f"⚠️ 429 Rate limit encountered. Retrying in {current_backoff} seconds... (Attempt {attempt} of {max_retries})")
                    time.sleep(current_backoff)
                    current_backoff *= 2
                    attempt += 1
                else:
                    print("⚠️ Max retries reached for this chunk. Skipping chunk.")
                    return ""
            else:
                print(f"⚠️ AI request failed for this chunk: {e}")
                return ""
    return ""

# --- Step 4: Parse JSON from AI Output ---
def parse_json_from_ai(ai_output):
    """
    Extracts valid JSON from the AI output.
    """
    try:
        start = ai_output.find("{")
        end = ai_output.rfind("}")
        if start == -1 or end == -1 or end <= start:
            print("⚠️ Could not find valid JSON braces in AI output.")
            return None
        json_str = ai_output[start:end+1].strip()
        return json.loads(json_str)
    except Exception as e:
        print(f"⚠️ Error parsing JSON: {e}")
        return None

# --- Step 5: Merge Partial JSON Results ---
def merge_partial_results(existing_data, new_data):
    """
    Merges new partial JSON (new_data) into existing_data.
    For list fields, results are extended.
    """
    if not existing_data:
        return new_data
    if not existing_data.get("Company Name") and new_data.get("Company Name"):
        existing_data["Company Name"] = new_data["Company Name"]
    if not existing_data.get("Year") and new_data.get("Year"):
        existing_data["Year"] = new_data["Year"]
    if "Key ESG Metrics" not in existing_data:
        existing_data["Key ESG Metrics"] = {}
    if "Key ESG Metrics" in new_data and isinstance(new_data["Key ESG Metrics"], dict):
        for category, metric_data in new_data["Key ESG Metrics"].items():
            if category not in existing_data["Key ESG Metrics"]:
                existing_data["Key ESG Metrics"][category] = metric_data
            else:
                if isinstance(metric_data, dict) and isinstance(existing_data["Key ESG Metrics"][category], dict):
                    existing_data["Key ESG Metrics"][category].update(metric_data)
                else:
                    existing_data["Key ESG Metrics"][category] = metric_data
    if "Performance Summary" not in existing_data:
        existing_data["Performance Summary"] = []
    if isinstance(new_data.get("Performance Summary"), list):
        existing_data["Performance Summary"].extend(new_data["Performance Summary"])
    if "Recommendations" not in existing_data:
        existing_data["Recommendations"] = []
    if isinstance(new_data.get("Recommendations"), list):
        existing_data["Recommendations"].extend(new_data["Recommendations"])
    return existing_data

# --- Step 6: Generate a Word Report ---
def generate_word_report_from_json(json_data, output_docx, source_pdf=None):
    """
    Creates a professional Word document from the merged JSON data.
    Uses headings and bullet points.
    """
    try:
        doc = Document()
        company = json_data.get("Company Name", "Unknown Company")
        year = json_data.get("Year", "N/A")
        doc.add_heading(f"Performance Report - {company} ({year})", level=1)
        if source_pdf:
            doc.add_paragraph(f"Source Document: {source_pdf}")
        doc.add_heading("Key ESG Metrics", level=2)
        esg_metrics = json_data.get("Key ESG Metrics", {})
        if isinstance(esg_metrics, dict) and esg_metrics:
            for category, metrics in esg_metrics.items():
                doc.add_heading(category, level=3)
                if isinstance(metrics, dict):
                    for metric, value in metrics.items():
                        doc.add_paragraph(f"{metric}: {value}", style="List Bullet")
                elif isinstance(metrics, list):
                    for item in metrics:
                        doc.add_paragraph(str(item), style="List Bullet")
                else:
                    doc.add_paragraph(str(metrics), style="List Bullet")
        else:
            doc.add_paragraph("No specific ESG Metrics found.", style="List Bullet")
        doc.add_heading("Performance Summary", level=2)
        perf_summary = json_data.get("Performance Summary", [])
        if isinstance(perf_summary, list) and perf_summary:
            for ps in perf_summary:
                doc.add_paragraph(ps, style="List Bullet")
        else:
            doc.add_paragraph("No Performance Summary provided.", style="List Bullet")
        doc.add_heading("Recommendations", level=2)
        recommendations = json_data.get("Recommendations", [])
        if isinstance(recommendations, list) and recommendations:
            for rec in recommendations:
                doc.add_paragraph(rec, style="List Bullet")
        else:
            doc.add_paragraph("No Recommendations provided.", style="List Bullet")
        doc.save(output_docx)
        print(f"✅ Word report saved to {output_docx}")
    except Exception as e:
        print(f"⚠️ Error generating Word report: {e}")

# --- Step 7: Upload Processed Report via API ---
def upload_processed_report_via_api(company_name, local_file_path, docx_filename):
    """
    Uploads the processed Word document to the backend via the API endpoint.
    The API expects a multipart POST with this payload:
      {
         Report_Name: 'processed',
         Company_Name: <company_name>,
         documentURL: '',
         FileName: <docx_filename>,
         DocumentType: 'CompanyReports',
         file: (binary)
      }
    """
    api_url = "https://api.reputdev.in/upload"  # Replace with the actual endpoint
    payload = {
        "Report_Name": "processed",
        "Company_Name": company_name,
        "DocumentURL": "",
        "FileName": docx_filename,
        "DocumentType": "CompanyReports"
    }
    try:
        with open(local_file_path, "rb") as f:
            files = {
                "file": (docx_filename, f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            }
            response = requests.post(api_url, data=payload, files=files, timeout=30)
            if response.status_code == 200:
                print("✅ Processed report uploaded via API.")
            else:
                print(f"⚠️ Processed report upload failed. Status: {response.status_code}, Response: {response.text}")
    except Exception as e:
        print(f"⚠️ Exception during processed report upload: {e}")

# --- Step 8: Main Pipeline Execution ---
if __name__ == "__main__":
    # 1. Extract text from the report PDF via its URL
    raw_text = extract_text_from_pdf_url(SAMPLE_PDF_URL)
    if not raw_text:
        print("⚠️ No text extracted. Exiting pipeline.")
        exit()
    print(f"DEBUG: Extracted text length: {len(raw_text)} characters.")

    # 2. Split text into token-friendly chunks
    chunks = chunk_text(raw_text, chunk_size=CHARS_PER_CHUNK)
    print(f"DEBUG: Split into {len(chunks)} chunks.")

    # 3. Process each chunk with Gemini and merge partial results
    merged_result = {}
    for i, chunk in enumerate(chunks, start=1):
        print(f"DEBUG: Processing chunk {i}/{len(chunks)}...")
        ai_output = analyze_chunk_with_gemini(chunk)
        partial_json = parse_json_from_ai(ai_output)
        if partial_json:
            merged_result = merge_partial_results(merged_result, partial_json)

    if not merged_result:
        print("⚠️ No valid ESG data extracted from any chunks.")
        exit()

    # 4. Save the final JSON output locally
    with open(OUTPUT_JSON, "w", encoding="utf-8") as jf:
        json.dump(merged_result, jf, indent=4)
    print(f"✅ JSON ESG output saved to {OUTPUT_JSON}")

    # 5. Generate the Word report from the merged JSON
    generate_word_report_from_json(merged_result, OUTPUT_DOCX, source_pdf=SAMPLE_PDF_URL)

    # 6. Upload the processed Word report via API
    upload_processed_report_via_api(COMPANY_NAME, OUTPUT_DOCX, os.path.basename(OUTPUT_DOCX))

    print("✅ Pipeline execution completed.")
