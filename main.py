from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests, io, re, zipfile
from pdfminer.high_level import extract_text
from pptx import Presentation
from docx import Document
from collections import Counter

app = FastAPI(title="StudyCoach Backend", version="1.2")

class PDFRequest(BaseModel):
    url: str  # قد يكون PDF أو PPTX أو DOCX

def clean_text(t: str) -> str:
    return re.sub(r'\s+', ' ', (t or '')).strip()

def extract_from_pdf(data: bytes) -> str:
    return extract_text(io.BytesIO(data)) or ""

def extract_from_pptx(data: bytes) -> str:
    text = []
    prs = Presentation(io.BytesIO(data))
    for slide in prs.slides:
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text:
                text.append(shape.text)
    return "\n".join(text)

def extract_from_docx(data: bytes) -> str:
    # python-docx يدعم التحميل من file-like
    doc = Document(io.BytesIO(data))
    text = []
    for p in doc.paragraphs:
        if p.text:
            text.append(p.text)
    return "\n".join(text)

def naive_sentences(text: str):
    parts = re.split(r'(?<=[.!?\u061F])\s+', text)
    return [s.strip() for s in parts if len(s.strip()) > 30]

def top_terms(text: str, k=20):
    words = re.findall(r"[A-Za-z\u0600-\u06FF][A-Za-z0-9_\u0600-\u06FF'-]*", text)
    words = [w.lower() for w in words if len(w) > 3]
    stop = {"this","that","with","from","your","about","these","those","which","their",
            "have","will","there","here","where","when","then","into","over","under",
            "also","such","than","only","very","more","most","much","many","some",
            "been","were","what","shall","should","could","would","might","must"}
    common = [w for w in words if w not in stop]
    return [w for w,_ in Counter(common).most_common(k)]

def summarize(text: str, max_sents=6):
    sents = naive_sentences(text)
    if not sents:
        return "No clear sentences extracted."
    terms = set(top_terms(text, k=25))
    scored = []
    for s in sents:
        score = sum(1 for w in re.findall(r"[A-Za-z\u0600-\u06FF][A-Za-z0-9_\u0600-\u06FF'-]*", s.lower()) if w in terms)
        scored.append((score, s))
    pick = [sents[0]] + [s for _, s in sorted(scored, key=lambda x: x[0], reverse=True)[:max_sents-1]]
    guidance = "\n\nReflection:\n- ما المفهوم الرئيسي؟\n- مثال يوضّحه؟\n- الفرق مع مفهوم قريب؟\n- أين نوظّفه في MIS؟"
    return "\n".join(pick) + guidance

def cloze_quiz(text: str, n=5):
    terms = top_terms(text, k=40)
    sents = naive_sentences(text)
    qs, used = [], set()
    for s in sents:
        for t in terms:
            if t in used: continue
            if re.search(rf"\b{re.escape(t)}\b", s, flags=re.IGNORECASE):
                masked = re.sub(rf"\b{re.escape(t)}\b", "____", s, flags=re.IGNORECASE, count=1)
                qs.append({"stem": masked, "hint": f"length: {len(t)}"})
                used.add(t)
                break
        if len(qs) >= n: break
    if not qs and sents:
        qs = [{"stem": re.sub(r"[A-Za-z0-9\u0600-\u06FF]{4,}", "____", sents[0], count=1), "hint":"fill the missing word"}]
    return qs

@app.post("/process_pdf")
def process_pdf(req: PDFRequest):
    # نزّل الملف من مودل (fileurl + token=... يخلّيه قابل للتحميل المباشر)
    try:
        r = requests.get(req.url, timeout=40)
        r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Download error: {e}")

    content_type = (r.headers.get("Content-Type") or "").lower()
    url_lower = req.url.lower()

    # حدّد النوع
    text = ""
    try:
        if "pdf" in content_type or url_lower.endswith(".pdf"):
            text = extract_from_pdf(r.content)
        elif "presentation" in content_type or url_lower.endswith(".pptx"):
            text = extract_from_pptx(r.content)
        elif "word" in content_type or url_lower.endswith(".docx"):
            text = extract_from_docx(r.content)
        else:
            # fallback: جرّب PDF أولاً
            try:
                text = extract_from_pdf(r.content)
            except Exception:
                raise HTTPException(status_code=415, detail=f"Unsupported file type: {content_type or 'unknown'}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Extract error: {e}")

    text = clean_text(text)
    if len(text) < 120:
        raise HTTPException(status_code=422, detail="Too little text extracted.")

    return {"ok": True, "summary": summarize(text), "quiz": cloze_quiz(text)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
