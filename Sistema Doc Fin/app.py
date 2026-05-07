import base64
import html
import json
import time
import os
import threading
import re
import shutil
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote

from pypdf import PdfReader, PdfWriter


HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8123"))
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "2048"))
JOB_TTL_HOURS = int(os.environ.get("JOB_TTL_HOURS", "24"))
BASE_DIR = Path(__file__).resolve().parent
JOBS_DIR = Path(os.environ.get("JOBS_DIR", str(BASE_DIR / "web_jobs")))
JOBS_DIR.mkdir(parents=True, exist_ok=True)


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


END_DATE_RE = re.compile(r"(?:[_\-\s])(\d{2}[A-Za-z]{3}\d{2})$", re.IGNORECASE)
RE_RH_TEXT = re.compile(r"\bOFICIO\b.*\b\d+/\d{4}-RH\b", re.IGNORECASE)
RE_RH_NAME = re.compile(r"(^|[_\-\s])RH([_\-\s]|$)", re.IGNORECASE)
RE_FIN_TEXT = re.compile(r"OFICIO[- ]FIN(?:\b|/)", re.IGNORECASE)
RE_FIN_NAME = re.compile(r"\bOFICIO\b.*\bFIN\b|\bOFICIO[-_ ]FIN\b", re.IGNORECASE)
RE_TYPE_TRANSFER = re.compile(
    r"\bTRANSFERENCIAS?\b.*\bTITULARIDADE\b|\bMESMA\b.*\bTITULARIDADE\b",
    re.IGNORECASE,
)
RE_TYPE_IMPOSTOS = re.compile(r"\bPAGAMENTOS?\b.*\bIMPOSTOS\b|\bIMPOSTOS\b", re.IGNORECASE)
RE_TYPE_ELETRONICO = re.compile(
    r"PAGAMENTOS?\s+DIVERSOS?.*PROCESSAMENTO\s+ELETRONICO|PROCESSAMENTO\s+ELETRONICO",
    re.IGNORECASE,
)

FIN_TYPE_LABELS = {
    "TRANSFER": "transferencias de mesma titularidade",
    "IMPOSTOS": "pagamentos diversos - impostos",
    "ELETRONICO": "pagamentos diversos via processamento eletronico",
    "OTHER": "outros pagamentos FIN",
}
RE_REL_CONF = re.compile(r"\bRELATORIO\b.*\bBANCARIO\b.*\bCONFERENCIA\b", re.IGNORECASE)
RE_REL_CONF_NAME = re.compile(r"\bCONFERENCIA\b|\bREL\b.*\bCONFER\b", re.IGNORECASE)
TRANSFER_PHRASE = "TRANSFERENCIA ENTRE CONTAS DA MESMA TITULARIDADE"
RE_CONTA_ORIGEM_ROBUSTA = re.compile(r"CONTA\s*ORIGEM\s*:.*?(\d{3,12})\s*-\s*C/C", re.IGNORECASE)
RE_ACCOUNTS_IN_FIN = re.compile(r"(\d{3,12})\s*-\s*C/C|\b(\d{3,12})\b", re.IGNORECASE)
RE_ACCOUNT_WITH_CC = re.compile(r"(\d{3,12})\s*-\s*C/C", re.IGNORECASE)
RE_FIN_ACCOUNT_LINE = re.compile(r"^\s*(\d{3,12})\s+(?:\d{1,3}(?:\.\d{3})*,\d{2})\s*$")
TRANSFER_PHRASE_COMPACT = re.sub(r"\s+", "", TRANSFER_PHRASE)


INDEX_HTML = """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Combinador de PDFs</title>
  <style>
    :root { --bg:#f4efe7; --ink:#14213d; --muted:#5f6b7a; --line:rgba(20,33,61,.18); --accent:#bd4f28; --ok:#177245; --bad:#a32020; --warn:#9a6500; }
    *{box-sizing:border-box} body{margin:0;font-family:Arial,Helvetica,sans-serif;background:var(--bg);color:var(--ink)}
    .shell{width:min(100% - 28px,1120px);margin:32px auto}.hero,.panel{background:#fff;border:1px solid var(--line);border-radius:22px;box-shadow:0 16px 40px rgba(20,33,61,.08);padding:24px}.hero{margin-bottom:18px}
    h1{margin:.2rem 0;font-size:2.1rem}h2{margin:0 0 12px}.lede{color:var(--muted);font-size:1.05rem}.grid{display:grid;grid-template-columns:420px 1fr;gap:18px}.stack{display:flex;flex-direction:column;gap:14px}
    label{font-weight:700}.field{display:flex;flex-direction:column;gap:6px}input,select{border:1px solid var(--line);border-radius:12px;padding:10px;font-size:1rem}.picker{border:1px dashed var(--line);border-radius:16px;padding:14px;background:#fffaf6}.picker span{display:block;color:var(--muted);font-size:.92rem;margin:6px 0 10px}
    button{border:0;border-radius:14px;background:var(--accent);color:white;padding:12px 16px;font-weight:800;cursor:pointer}.secondary{background:#e7ded3;color:var(--ink)}.actions{display:flex;gap:10px;flex-wrap:wrap}.chips{display:flex;gap:8px;flex-wrap:wrap}.chip{background:#f7eadf;border-radius:999px;padding:8px 10px}.status{min-height:24px;font-weight:700}.summary{display:flex;flex-direction:column;gap:10px}.item{border-top:1px solid var(--line);padding:9px 0}.item small{display:block;color:var(--muted)}.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}progress{width:100%;height:20px}.note{font-size:.9rem;color:var(--muted)}a{color:var(--accent);font-weight:700}@media(max-width:900px){.grid{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero"><h1>Combinador de PDFs</h1><p class="lede">Versão para lotes grandes: envia os arquivos em pedaços e processa em segundo plano, evitando erro 502 em uploads acima de 500 MB.</p></section>
    <section class="grid">
      <form class="panel stack" id="uploadForm">
        <h2>Processar arquivos</h2>
        <div class="field"><label for="mergeMode">Modo de junção</label><select id="mergeMode"><option value="filtered">Filtrado: RH + FIN + relatórios compatíveis</option><option value="all">Tudo: junta todos os PDFs na ordem recebida</option></select></div>
        <div class="field"><label for="outputName">Nome do PDF final</label><input id="outputName" type="text" placeholder="Deixe em branco para usar o nome sugerido"></div>
        <div class="picker"><strong>Escolher uma pasta inteira</strong><span>Ideal quando os PDFs já estão em um diretório.</span><input id="folderInput" type="file" webkitdirectory directory multiple accept=".pdf,application/pdf"></div>
        <div class="picker"><strong>Ou selecionar muitos PDFs</strong><span>Use para enviar mais de 20 arquivos de uma vez.</span><input id="filesInput" type="file" multiple accept=".pdf,application/pdf"></div>
        <div class="chips"><div class="chip" id="folderCount">Pasta: 0 arquivos</div><div class="chip" id="filesCount">Seleção manual: 0 arquivos</div></div>
        <progress id="progress" value="0" max="100"></progress>
        <div class="actions"><button type="submit" id="submitBtn">Enviar e gerar PDF</button><button type="button" class="secondary" id="clearBtn">Limpar seleção</button></div>
        <div class="status" id="status"></div><div class="note">Limite configurado: __MAX_UPLOAD_MB__ MB. O envio é feito em blocos de 8 MB.</div>
      </form>
      <section class="panel stack"><h2>Resultado</h2><div class="summary" id="summary"><div>Nenhum processamento iniciado ainda.</div></div></section>
    </section>
  </main>
<script>
const folderInput=document.getElementById('folderInput'),filesInput=document.getElementById('filesInput'),folderCount=document.getElementById('folderCount'),filesCount=document.getElementById('filesCount'),statusEl=document.getElementById('status'),summary=document.getElementById('summary'),progress=document.getElementById('progress'),clearBtn=document.getElementById('clearBtn'),form=document.getElementById('uploadForm'),submitBtn=document.getElementById('submitBtn');
const MAX_UPLOAD_MB=__MAX_UPLOAD_MB__; const CHUNK=8*1024*1024;
function esc(v){return String(v||'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}
function mb(b){return (b/1048576).toLocaleString('pt-BR',{maximumFractionDigits:1})}
function counts(){folderCount.textContent=`Pasta: ${folderInput.files.length} arquivos`;filesCount.textContent=`Seleção manual: ${filesInput.files.length} arquivos`;}
folderInput.addEventListener('change',counts);filesInput.addEventListener('change',counts);clearBtn.onclick=()=>{folderInput.value='';filesInput.value='';counts();progress.value=0;statusEl.textContent='';summary.innerHTML='<div>Nenhum processamento iniciado ainda.</div>'}
async function jsonFetch(url,opts){const r=await fetch(url,opts);const t=await r.text();let p={};try{p=t?JSON.parse(t):{}}catch(e){throw new Error(`Resposta inválida do servidor. HTTP ${r.status}: ${t.slice(0,400)||'vazia'}`)}if(!r.ok)throw new Error(p.error||`Falha HTTP ${r.status}`);return p}
async function uploadChunk(jobId,file,fileIndex,rel,chunkIndex,totalChunks,blob){const qs=new URLSearchParams({job:jobId,file_index:fileIndex,name:file.name,relative_path:rel,chunk_index:chunkIndex,total_chunks:totalChunks});return jsonFetch('/api/upload_chunk?'+qs.toString(),{method:'POST',body:blob,headers:{'Content-Type':'application/octet-stream'}})}
function renderList(title,items,tone){if(!items||!items.length)return `<div class="item"><strong>${esc(title)}</strong><small>nenhum</small></div>`;return `<div><strong>${esc(title)}</strong>${items.map(i=>`<div class="item"><strong class="${tone}">${esc(i.name)}</strong><small>${esc(i.reason||i.classification||'')}</small></div>`).join('')}</div>`}
async function poll(jobId){while(true){const p=await jsonFetch('/api/status?job='+encodeURIComponent(jobId));progress.value=p.progress||0;statusEl.textContent=p.message||p.status;if(p.status==='done'){const c=p.counts||{};summary.innerHTML=`<div><strong>Saída:</strong> ${esc(p.output_name)}</div><div><strong>Arquivos recebidos:</strong> ${c.input_total||0}</div><div><strong>Incluídos:</strong> ${c.included_total||0}</div><div><strong>Ignorados:</strong> ${c.ignored_total||0}</div><div class="chips"><div class="chip"><a href="${p.download_pdf_url}" target="_blank">Baixar PDF final</a></div><div class="chip"><a href="${p.download_report_url}" target="_blank">Baixar relatório</a></div></div>${renderList('Ordem usada',p.included,'ok')}${renderList('Ficaram de fora',p.ignored,'warn')}`;return}
if(p.status==='error')throw new Error(p.error||'Erro no processamento'); await new Promise(res=>setTimeout(res,1500));}
}
form.addEventListener('submit',async ev=>{ev.preventDefault();submitBtn.disabled=true;try{const folder=Array.from(folderInput.files).filter(f=>f.name.toLowerCase().endsWith('.pdf'));const manual=Array.from(filesInput.files).filter(f=>f.name.toLowerCase().endsWith('.pdf'));const chosen=folder.length?folder:manual;if(!chosen.length)throw new Error('Selecione uma pasta ou PDFs.');const total=chosen.reduce((s,f)=>s+f.size,0);if(total>MAX_UPLOAD_MB*1048576)throw new Error(`O lote tem ${mb(total)} MB, acima do limite de ${MAX_UPLOAD_MB} MB.`);summary.innerHTML='<div>Preparando envio...</div>';progress.value=0;const sourceMode=folder.length?'folder':'files';const job=await jsonFetch('/api/create_job',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({merge_mode:document.getElementById('mergeMode').value,output_name:document.getElementById('outputName').value,source_mode:sourceMode,total_files:chosen.length,total_bytes:total})});let sent=0;for(let i=0;i<chosen.length;i++){const f=chosen[i];const rel=sourceMode==='folder'?(f.webkitRelativePath||f.name):f.name;const totalChunks=Math.max(1,Math.ceil(f.size/CHUNK));for(let c=0;c<totalChunks;c++){const start=c*CHUNK,end=Math.min(f.size,start+CHUNK);await uploadChunk(job.job_id,f,String(i),rel,String(c),String(totalChunks),f.slice(start,end));sent += end-start;progress.value=Math.min(60,Math.round((sent/total)*60));statusEl.textContent=`Enviando ${i+1}/${chosen.length}: ${esc(f.name)} (${mb(sent)} de ${mb(total)} MB)`;}}
await jsonFetch('/api/start_process',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:job.job_id})});summary.innerHTML='<div>Arquivos enviados. Processando em segundo plano...</div>';await poll(job.job_id);}catch(e){summary.innerHTML=`<div class="bad">${esc(e.message)}</div>`;statusEl.textContent='Não foi possível concluir este lote.'}finally{submitBtn.disabled=false}});counts();
</script>
</body>
</html>
"""


@dataclass
class UploadedPDF:
    input_index: int
    original_name: str
    relative_path: str
    saved_path: Path
    classification: str = ""
    fin_type: str | None = None
    why: str = ""
    text: str = ""
    origin: str | None = None
    pages_to_merge: list[int] | None = None


def infer_output_name_from_last_file(pdfs: list[UploadedPDF]) -> str:
    if not pdfs:
        return "PDF_FINAL.pdf"
    last = Path(pdfs[-1].relative_path or pdfs[-1].original_name).stem
    match = END_DATE_RE.search(last)
    if match:
        return f"{match.group(1).lower()}_mb.pdf"
    return "PDF_FINAL.pdf"


def normalize_text(s: str) -> str:
    s = s.upper()
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def compact(s: str) -> str:
    return re.sub(r"\s+", "", s)


def is_transfer_report(text_norm: str) -> bool:
    if not text_norm:
        return False
    return compact(text_norm).find(TRANSFER_PHRASE_COMPACT) >= 0


def extract_pdf_text_normalized(pdf_path: Path, max_pages: int = 3) -> str:
    try:
        reader = PdfReader(str(pdf_path))
        parts = []
        for page in reader.pages[:max_pages]:
            text = page.extract_text() or ""
            if text:
                parts.append(text)
        return normalize_text("\n".join(parts))
    except Exception:
        return ""


def digits_only(value: str | None) -> str:
    return re.sub(r"\D+", "", value or "")


def account_keys(value: str | None, *, source: str = "auto") -> set[str]:
    """Return comparison keys for bank account numbers.

    Rules confirmed by real samples:
    - Santander/BB oficio lines may include an extra final check digit.
    - BTG may use exactly the same number in oficio and report.
    - Report lines usually show the base account followed by -C/C.

    Therefore we do not force a fixed length. We compare exact digits and,
    for oficio/auto values, also accept the value without the last digit.
    """
    digits = digits_only(value)
    if not digits:
        return set()
    keys = {digits}
    if source in {"oficio", "auto"} and len(digits) > 3:
        keys.add(digits[:-1])
    return keys


def accounts_compatible(oficio_value: str | None, report_value: str | None) -> bool:
    return bool(account_keys(oficio_value, source="oficio") & account_keys(report_value, source="report"))


def extract_transfer_origin_account(text_norm: str) -> str | None:
    if not text_norm:
        return None
    match = RE_CONTA_ORIGEM_ROBUSTA.search(text_norm)
    return digits_only(match.group(1)) if match else None


def extract_accounts_from_fin(text_norm: str) -> set[str]:
    """Extract account keys from the first oficio page without fixed length.

    The parser prioritizes the account table on the oficio page. It avoids
    collecting random CNPJ, dates or amounts from attached report pages.
    """
    accounts: set[str] = set()
    if not text_norm:
        return accounts

    first_page_like = text_norm.split("RELATORIO BANCARIO DE CONFERENCIA", 1)[0]

    # Accounts printed as 12345-C/C in report-like text.
    for match in RE_ACCOUNT_WITH_CC.finditer(first_page_like):
        accounts.update(account_keys(match.group(1), source="report"))

    # Accounts listed in the oficio table before the TOTAL line.
    for raw_line in first_page_like.replace(" TOTAL ", "\nTOTAL ").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("TOTAL"):
            continue
        match = RE_FIN_ACCOUNT_LINE.match(line)
        if match:
            accounts.update(account_keys(match.group(1), source="oficio"))

    # Fallback for normalized single-line extraction: only use the section
    # between the authorization text and TOTAL, then read account + currency pairs.
    if not accounts:
        m = re.search(r"PROCESSAMENTO ELETRONICO\.\s*(.*?)\s*TOTAL\s+", first_page_like, re.IGNORECASE)
        if m:
            table = m.group(1)
            for acct in re.findall(r"\b(\d{3,12})\b\s+\d{1,3}(?:\.\d{3})*,\d{2}", table):
                accounts.update(account_keys(acct, source="oficio"))

    return accounts


def infer_fin_type(text_norm: str) -> str:
    """Classify the oficio purpose before choosing the matching report type."""
    if not text_norm:
        return "OTHER"
    if RE_TYPE_TRANSFER.search(text_norm):
        return "TRANSFER"
    if RE_TYPE_IMPOSTOS.search(text_norm):
        return "IMPOSTOS"
    if RE_TYPE_ELETRONICO.search(text_norm):
        return "ELETRONICO"
    return "OTHER"


def expected_report_class_for_fin(fin_type: str | None) -> str:
    if fin_type == "TRANSFER":
        return "REL_TRANS"
    # Processamento Eletronico and Impostos both use Relatorio Bancario de Conferencia.
    return "REL_CONF"


def fin_type_label(fin_type: str | None) -> str:
    return FIN_TYPE_LABELS.get(fin_type or "OTHER", "outros pagamentos FIN")


def should_merge_only_first_page(pdf_path: Path, text_norm: str) -> bool:
    """True when an oficio PDF also contains attached report pages.

    In this workflow the oficio serves as the index, while the individual
    report PDFs should be merged separately. Keeping only page 1 prevents
    duplicate report pages in the final PDF.
    """
    if not text_norm or not RE_FIN_TEXT.search(text_norm) or not RE_REL_CONF.search(text_norm):
        return False
    try:
        return len(PdfReader(str(pdf_path)).pages) > 1
    except Exception:
        return False


def classify_doc(pdf: UploadedPDF) -> UploadedPDF:
    text = extract_pdf_text_normalized(pdf.saved_path, max_pages=3)
    name_norm = normalize_text(Path(pdf.original_name).stem)

    pdf.text = text

    if text and RE_RH_TEXT.search(text):
        pdf.classification = "RH"
        pdf.why = "RH(texto)"
        return pdf
    if RE_RH_NAME.search(pdf.original_name):
        pdf.classification = "RH"
        pdf.why = "RH(nome)"
        return pdf

    is_fin = False
    fin_why = ""
    if text and RE_FIN_TEXT.search(text):
        is_fin = True
        fin_why = "FIN(texto)"
    elif RE_FIN_NAME.search(name_norm):
        is_fin = True
        fin_why = "FIN(nome)"

    if is_fin:
        pdf.classification = "FIN"
        pdf.fin_type = infer_fin_type(text)
        label = fin_type_label(pdf.fin_type)
        if should_merge_only_first_page(pdf.saved_path, text):
            pdf.pages_to_merge = [0]
            pdf.why = f"{fin_why} finalidade={label}; somente pagina 1 para evitar duplicidade"
        else:
            pdf.why = f"{fin_why} finalidade={label}"
        return pdf

    if text and is_transfer_report(text):
        pdf.classification = "REL_TRANS"
        pdf.origin = extract_transfer_origin_account(text)
        pdf.why = f"REL_TRANS(texto, origem={pdf.origin})"
        return pdf

    if text and RE_REL_CONF.search(text):
        pdf.classification = "REL_CONF"
        pdf.why = "REL_CONF(texto)"
        return pdf
    if RE_REL_CONF_NAME.search(name_norm):
        pdf.classification = "REL_CONF"
        pdf.why = "REL_CONF(nome)"
        return pdf

    pdf.classification = "OTHER"
    pdf.why = "OTHER"
    return pdf


def prepare_uploaded_files(file_parts, relative_paths, source_mode, originals_dir: Path) -> list[UploadedPDF]:
    uploads: list[UploadedPDF] = []
    for index, part in enumerate(file_parts):
        filename = part.get_filename() or f"arquivo_{index + 1}.pdf"
        if not filename.lower().endswith(".pdf"):
            continue

        payload = part.get_payload(decode=True) or b""
        if not payload:
            continue

        relative_path = relative_paths[index] if index < len(relative_paths) else filename
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name)
        saved_path = originals_dir / f"{index:03d}_{safe_name}"
        saved_path.write_bytes(payload)

        uploads.append(
            UploadedPDF(
                input_index=index,
                original_name=Path(filename).name,
                relative_path=relative_path,
                saved_path=saved_path,
            )
        )

    if source_mode == "folder":
        uploads.sort(key=lambda item: natural_key(item.relative_path))
    else:
        uploads.sort(key=lambda item: item.input_index)

    return uploads


def build_filtered_sequence(pdfs: list[UploadedPDF]) -> tuple[list[UploadedPDF], list[dict], dict]:
    classified = [classify_doc(pdf) for pdf in pdfs]
    included_ids: set[int] = set()
    ignored_ids: set[int] = set()
    included: list[UploadedPDF] = []
    ignored: list[dict] = []
    counts = {
        "rh": 0,
        "fin": 0,
        "rel_conf": 0,
        "rel_trans": 0,
        "input_total": len(classified),
        "included_total": 0,
        "ignored_total": 0,
    }

    def add_included(pdf: UploadedPDF, reason: str):
        if pdf.input_index in included_ids:
            return
        included_ids.add(pdf.input_index)
        included.append(pdf)
        if pdf.classification == "RH":
            counts["rh"] += 1
        elif pdf.classification == "FIN":
            counts["fin"] += 1
        elif pdf.classification == "REL_CONF":
            counts["rel_conf"] += 1
        elif pdf.classification == "REL_TRANS":
            counts["rel_trans"] += 1
        pdf.why = f"{pdf.why} | {reason}"

    def add_ignored(pdf: UploadedPDF, reason: str):
        if pdf.input_index in ignored_ids:
            return
        ignored_ids.add(pdf.input_index)
        ignored.append({
            "name": pdf.relative_path,
            "classification": pdf.classification,
            "reason": reason,
        })

    for pdf in classified:
        if pdf.classification == "RH":
            add_included(pdf, "incluido no topo")

    index = 0
    while index < len(classified):
        current = classified[index]
        if current.classification != "FIN":
            index += 1
            continue

        add_included(current, "oficio FIN base")

        block: list[UploadedPDF] = []
        cursor = index + 1
        while cursor < len(classified) and classified[cursor].classification not in {"RH", "FIN"}:
            block.append(classified[cursor])
            cursor += 1

        expected_class = expected_report_class_for_fin(current.fin_type)
        if current.fin_type == "TRANSFER":
            fin_accounts = extract_accounts_from_fin(current.text)
            if not fin_accounts:
                for pdf in block:
                    if pdf.classification == "REL_TRANS":
                        add_ignored(pdf, "relatorio de transferencia sem conta compativel porque o oficio FIN nao trouxe contas validas")
            else:
                for pdf in block:
                    if pdf.classification != "REL_TRANS":
                        if pdf.classification == "REL_CONF":
                            add_ignored(pdf, "relatorio bancario de conferencia ignorado: oficio pede transferencia de mesma titularidade")
                        continue
                    if not pdf.origin:
                        add_ignored(pdf, "relatorio de transferencia sem conta de origem legivel")
                        continue
                    if any(accounts_compatible(acc, pdf.origin) for acc in fin_accounts):
                        add_included(pdf, "transferencia compativel com oficio FIN")
                    else:
                        add_ignored(pdf, f"conta de origem {pdf.origin} nao compativel com o oficio FIN")
        else:
            for pdf in block:
                if pdf.classification == expected_class:
                    add_included(pdf, f"relatorio bancario de conferencia compativel com oficio FIN de {fin_type_label(current.fin_type)}")
                elif pdf.classification == "REL_TRANS":
                    add_ignored(pdf, f"relatorio de transferencia ignorado: oficio pede {fin_type_label(current.fin_type)}")

        index = cursor

    for pdf in classified:
        if pdf.input_index in included_ids or pdf.input_index in ignored_ids:
            continue
        if pdf.classification == "REL_CONF":
            reason = "relatorio de conferencia sem oficio FIN comum logo acima"
        elif pdf.classification == "REL_TRANS":
            reason = "relatorio de transferencia sem oficio FIN de transferencia compativel"
        elif pdf.classification == "OTHER":
            reason = "arquivo fora das classes reconhecidas"
        else:
            reason = "arquivo nao entrou na sequencia final"
        add_ignored(pdf, reason)

    counts["included_total"] = len(included)
    counts["ignored_total"] = len(ignored)
    return included, ignored, counts


def build_all_sequence(pdfs: list[UploadedPDF]) -> tuple[list[UploadedPDF], list[dict], dict]:
    classified = [classify_doc(pdf) for pdf in pdfs]
    counts = {
        "rh": sum(1 for pdf in classified if pdf.classification == "RH"),
        "fin": sum(1 for pdf in classified if pdf.classification == "FIN"),
        "rel_conf": sum(1 for pdf in classified if pdf.classification == "REL_CONF"),
        "rel_trans": sum(1 for pdf in classified if pdf.classification == "REL_TRANS"),
        "input_total": len(classified),
        "included_total": len(classified),
        "ignored_total": 0,
    }
    for pdf in classified:
        pdf.why = f"{pdf.why} | incluido pela opcao juntar tudo"
    return classified, [], counts


def merge_pdfs(pdfs: list[UploadedPDF], output_path: Path):
    writer = PdfWriter()
    for pdf in pdfs:
        if pdf.pages_to_merge is None:
            writer.append(str(pdf.saved_path))
            continue
        reader = PdfReader(str(pdf.saved_path))
        for page_index in pdf.pages_to_merge:
            if 0 <= page_index < len(reader.pages):
                writer.add_page(reader.pages[page_index])
    with output_path.open("wb") as handle:
        writer.write(handle)


def write_report(output_path: Path, output_name: str, merge_mode: str, included: list[UploadedPDF], ignored: list[dict], counts: dict):
    lines = [
        f"Arquivo final: {output_name}",
        f"Modo: {merge_mode}",
        "",
        "Ordem usada:",
    ]
    for index, pdf in enumerate(included, start=1):
        lines.append(f"{index:02d}. {pdf.relative_path} [{pdf.classification}] - {pdf.why}")
    lines.append("")
    lines.append("Arquivos incluidos:")
    for pdf in included:
        lines.append(f"- {pdf.relative_path}")
    lines.append("")
    lines.append("Arquivos ignorados:")
    if ignored:
        for item in ignored:
            lines.append(f"- {item['name']} [{item['classification']}] - {item['reason']}")
    else:
        lines.append("- nenhum")
    lines.append("")
    lines.append("Resumo:")
    for key, value in counts.items():
        lines.append(f"- {key}: {value}")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def parse_multipart(content_type: str, body: bytes):
    header_block = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    message = BytesParser(policy=default).parsebytes(header_block + body)
    if not message.is_multipart():
        raise ValueError("Corpo multipart inválido.")
    return list(message.iter_parts())


def build_json_response(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)



def cleanup_old_jobs():
    """Remove jobs antigos para nao acumular arquivos no servidor."""
    if JOB_TTL_HOURS <= 0:
        return
    cutoff = time.time() - (JOB_TTL_HOURS * 3600)
    for path in JOBS_DIR.iterdir():
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def check_basic_auth(header: str | None) -> bool:
    """Senha opcional via APP_PASSWORD. Se nao configurar, acesso fica aberto."""
    if not APP_PASSWORD:
        return True
    if not header or not header.startswith("Basic "):
        return False
    try:
        raw = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
    except Exception:
        return False
    user, sep, password = raw.partition(":")
    return bool(sep) and password == APP_PASSWORD


def require_auth(handler: BaseHTTPRequestHandler) -> bool:
    if check_basic_auth(handler.headers.get("Authorization")):
        return True
    handler.send_response(HTTPStatus.UNAUTHORIZED)
    handler.send_header("WWW-Authenticate", 'Basic realm="PDF Combiner"')
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.end_headers()
    handler.wfile.write("Autenticacao necessaria.".encode("utf-8"))
    return False


JOB_LOCK = threading.Lock()


def job_status_path(job_dir: Path) -> Path:
    return job_dir / "status.json"


def read_json_file(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def write_json_file(path: Path, payload: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def update_status(job_dir: Path, **kwargs):
    with JOB_LOCK:
        current = read_json_file(job_status_path(job_dir), {})
        current.update(kwargs)
        current["updated_at"] = time.time()
        write_json_file(job_status_path(job_dir), current)


def process_job_background(job_id: str):
    job_dir = JOBS_DIR / job_id
    try:
        update_status(job_dir, status="processing", progress=65, message="Classificando documentos...")
        manifest = read_json_file(job_dir / "manifest.json", {})
        files = manifest.get("files", [])
        source_mode = manifest.get("source_mode", "files")
        merge_mode = manifest.get("merge_mode", "filtered")
        output_name = manifest.get("output_name", "")
        originals_dir = job_dir / "originais"
        uploads: list[UploadedPDF] = []
        for item in files:
            path = originals_dir / item["saved_name"]
            if not path.exists():
                raise ValueError(f"Arquivo ausente no servidor: {item.get('original_name')}")
            uploads.append(UploadedPDF(
                input_index=int(item["input_index"]),
                original_name=item["original_name"],
                relative_path=item.get("relative_path") or item["original_name"],
                saved_path=path,
            ))
        if source_mode == "folder":
            uploads.sort(key=lambda item: natural_key(item.relative_path))
        else:
            uploads.sort(key=lambda item: item.input_index)
        if not uploads:
            raise ValueError("Nenhum PDF foi enviado.")
        update_status(job_dir, progress=75, message="Montando sequência final...")
        if merge_mode == "all":
            included, ignored, counts = build_all_sequence(uploads)
        else:
            included, ignored, counts = build_filtered_sequence(uploads)
        if not included:
            raise ValueError("Nenhum PDF entrou na sequência final. Revise a classificação.")
        update_status(job_dir, progress=88, message="Gerando PDF final...")
        final_name = output_name or infer_output_name_from_last_file(included)
        if not final_name.lower().endswith(".pdf"):
            final_name += ".pdf"
        safe_final = re.sub(r"[^A-Za-z0-9._-]+", "_", final_name)
        output_pdf = job_dir / safe_final
        merge_pdfs(included, output_pdf)
        report_path = job_dir / (Path(safe_final).stem + "_relatorio.txt")
        write_report(report_path, safe_final, merge_mode, included, ignored, counts)
        update_status(job_dir,
            status="done", progress=100, message="Processamento concluído.", output_name=safe_final,
            download_pdf_url=f"/download?job={job_id}&kind=pdf", download_report_url=f"/download?job={job_id}&kind=report",
            counts=counts,
            included=[{"name": pdf.relative_path, "classification": pdf.classification, "reason": pdf.why} for pdf in included],
            ignored=ignored,
        )
    except Exception as exc:
        update_status(job_dir, status="error", progress=100, message="Erro no processamento.", error=str(exc))


def load_manifest(job_dir: Path) -> dict:
    return read_json_file(job_dir / "manifest.json", {"files": []})


def save_manifest(job_dir: Path, manifest: dict):
    write_json_file(job_dir / "manifest.json", manifest)


class PDFCombinerHandler(BaseHTTPRequestHandler):
    server_version = "PDFCombinerWeb/1.0"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            body = b"ok"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not require_auth(self):
            return
        cleanup_old_jobs()
        if parsed.path == "/":
            body = INDEX_HTML.replace("__MAX_UPLOAD_MB__", str(MAX_UPLOAD_MB)).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/status":
            params = parse_qs(parsed.query)
            job_id = params.get("job", [""])[0]
            job_dir = JOBS_DIR / job_id
            if not job_id or not job_dir.exists():
                build_json_response(self, {"error":"Job inválido ou expirado."}, status=404)
                return
            build_json_response(self, read_json_file(job_status_path(job_dir), {"status":"unknown"}))
            return

        if parsed.path == "/download":
            params = parse_qs(parsed.query)
            job_id = params.get("job", [""])[0]
            kind = params.get("kind", [""])[0]
            if not job_id or kind not in {"pdf", "report"}:
                self.send_error(HTTPStatus.BAD_REQUEST, "Download inválido.")
                return

            ext = ".pdf" if kind == "pdf" else ".txt"
            job_dir = JOBS_DIR / job_id
            files = list(job_dir.glob(f"*{ext}"))
            if not files:
                self.send_error(HTTPStatus.NOT_FOUND, "Arquivo não encontrado.")
                return
            target = files[0]
            data = target.read_bytes()
            content_type = "application/pdf" if kind == "pdf" else "text/plain; charset=utf-8"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Rota não encontrada.")

    def do_POST(self):
        parsed = urlparse(self.path)
        if not require_auth(self):
            return
        cleanup_old_jobs()
        try:
            if parsed.path == "/api/create_job":
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                data = json.loads(body or "{}")
                total_bytes = int(data.get("total_bytes") or 0)
                if total_bytes > MAX_UPLOAD_MB * 1024 * 1024:
                    raise ValueError(f"Upload maior que o limite configurado de {MAX_UPLOAD_MB} MB.")
                merge_mode = (data.get("merge_mode") or "filtered").strip()
                if merge_mode not in {"all", "filtered"}:
                    raise ValueError("Modo de junção inválido.")
                source_mode = (data.get("source_mode") or "files").strip()
                if source_mode not in {"folder", "files"}:
                    source_mode = "files"
                job_id = uuid.uuid4().hex[:10]
                job_dir = JOBS_DIR / job_id
                (job_dir / "originais").mkdir(parents=True, exist_ok=True)
                manifest = {
                    "job_id": job_id,
                    "merge_mode": merge_mode,
                    "source_mode": source_mode,
                    "output_name": (data.get("output_name") or "").strip(),
                    "total_files": int(data.get("total_files") or 0),
                    "total_bytes": total_bytes,
                    "files": [],
                    "created_at": time.time(),
                }
                save_manifest(job_dir, manifest)
                write_json_file(job_status_path(job_dir), {"status":"uploading", "progress":0, "message":"Aguardando envio dos arquivos.", "created_at": time.time()})
                build_json_response(self, {"job_id": job_id})
                return

            if parsed.path == "/api/upload_chunk":
                params = parse_qs(parsed.query)
                job_id = params.get("job", [""])[0]
                job_dir = JOBS_DIR / job_id
                if not job_id or not job_dir.exists():
                    raise ValueError("Job inválido ou expirado.")
                file_index = int(params.get("file_index", ["0"])[0])
                chunk_index = int(params.get("chunk_index", ["0"])[0])
                total_chunks = int(params.get("total_chunks", ["1"])[0])
                original_name = unquote(params.get("name", [f"arquivo_{file_index}.pdf"])[0])
                relative_path = unquote(params.get("relative_path", [original_name])[0])
                if not original_name.lower().endswith(".pdf"):
                    raise ValueError("Somente arquivos PDF são aceitos.")
                length = int(self.headers.get("Content-Length", "0"))
                chunk = self.rfile.read(length)
                safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(original_name).name)
                saved_name = f"{file_index:04d}_{safe_name}"
                target = job_dir / "originais" / saved_name
                mode = "wb" if chunk_index == 0 else "ab"
                with target.open(mode) as fh:
                    fh.write(chunk)
                if chunk_index == total_chunks - 1:
                    manifest = load_manifest(job_dir)
                    files = [f for f in manifest.get("files", []) if int(f.get("input_index", -1)) != file_index]
                    files.append({"input_index": file_index, "original_name": Path(original_name).name, "relative_path": relative_path, "saved_name": saved_name, "size": target.stat().st_size})
                    files.sort(key=lambda x: int(x["input_index"]))
                    manifest["files"] = files
                    save_manifest(job_dir, manifest)
                build_json_response(self, {"ok": True})
                return

            if parsed.path == "/api/start_process":
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                data = json.loads(body or "{}")
                job_id = data.get("job_id", "")
                job_dir = JOBS_DIR / job_id
                if not job_id or not job_dir.exists():
                    raise ValueError("Job inválido ou expirado.")
                manifest = load_manifest(job_dir)
                expected = int(manifest.get("total_files") or 0)
                received = len(manifest.get("files", []))
                if expected and received < expected:
                    raise ValueError(f"Ainda faltam arquivos: recebidos {received} de {expected}.")
                update_status(job_dir, status="queued", progress=62, message="Processamento iniciado.")
                thread = threading.Thread(target=process_job_background, args=(job_id,), daemon=True)
                thread.start()
                build_json_response(self, {"ok": True, "job_id": job_id})
                return

            build_json_response(self, {"error":"Rota não encontrada."}, status=404)
        except Exception as exc:
            build_json_response(self, {"error": str(exc)}, status=400)

    def log_message(self, format, *args):
        return


def main():
    with ThreadingHTTPServer((HOST, PORT), PDFCombinerHandler) as server:
        print(f"Servidor pronto em http://{HOST}:{PORT}")
        server.serve_forever()


if __name__ == "__main__":
    main()
