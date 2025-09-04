from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests, io, re
from pdfminer.high_level import extract_text
from collections import Counter

app = FastAPI(title="StudyCoach Backend", version="1.0")

class PDFRequest(BaseModel):
    url: str

def clean_text(t: str) -> str:
    return re.sub(r'\s+', ' ', t).strip()

def naive_sentences(text: str):
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in parts if len(s.strip()) > 30]

def top_terms(text: str, k=12):
    words = re.findall(r"[A-Za-z\u0600-\u06FF][A-Za-z0-9_\u0600-\u06FF'-]*", text)
    words = [w.lower() for w in words if len(w) > 3]
    stop = {"this","that","with","from","your","about","these","those","which","their",
            "have","will","there","here","where","when","then","into","over","under",
            "also","such","than","only","very","more","most","much","many","some",
            "been","were","what","shall","should","could","would","might","must"}
    common = [w for w in words if w not in stop]
    return [w for w,_ in Counter(common).most_common(k)]

def summarize(text: str, max_sents=5):
    sents = naive_sentences(text)
    if not sents:
        return "No clear sentences extracted."
    terms = set(top_terms(text, k=20))
    scored = []
    for s in sents:
        score = sum(1 for w in re.findall(r"[A-Za-z\u0600-\u06FF][A-Za-z0-9_\u0600-\u06FF'-]*", s.lower()) if w in terms)
        scored.append((score, s))
    pick = [sents[0]] + [s for _, s in sorted(scored, key=lambda x: x[0], reverse=True)[:max_sents-1]]
    guidance = "\n\nReflection:\n- What is the main concept?\n- Which example illustrates it?\n- How can you apply it?"
    return "\n".join(pick) + guidance

def cloze_quiz(text: str, n=5):
    terms = top_terms(text, k=25)
    sents = naive_sentences(text)
    qs = []
    used = set()
    for s in sents:
        for t in terms:
            if t in used:
                continue
            if t in s.lower():
                masked = re.sub(rf"\b{t}\b", "____", s, flags=re.IGNORECASE, count=1)
                qs.append({"stem": masked, "hint": f"length: {len(t)}"})
                used.add(t)
                break
        if len(qs) >= n:
            break
    return qs

@app.post("/process_pdf")
def process_pdf(req: PDFRequest):
    try:
        r = requests.get(req.url, timeout=20)
        r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to download PDF: {e}")

    try:
        data = io.BytesIO(r.content)
        text = extract_text(data) or ""
        text = clean_text(text)
        if len(text) < 100:
            raise ValueError("Too little text extracted.")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to extract text: {e}")

    return {
        "ok": True,
        "summary": summarize(text),
        "quiz": cloze_quiz(text)
    }

# لتشغيله على Render أو Replit
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
