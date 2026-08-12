from flask import Flask, jsonify, request
from pathlib import Path
import csv
import io

HOST = "127.0.0.1"
PORT = 5000
ROOT = Path(__file__).resolve().parent
INSTANCE_DIR = ROOT / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)

# IMPORTANT:
# This is a deliberately isolated local-only P&L shell.
# It does NOT import app.py, so governed marketplace runtime, Neon,
# workers, schedulers and marketplace write paths cannot block localhost startup.
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BT P&L Local</title>
<style>
:root{font-family:Arial,sans-serif;color:#172033;background:#f5f7fb}
body{margin:0}.top{background:#153b64;color:#fff;padding:22px 28px}.top h1{margin:0;font-size:25px}.top p{margin:6px 0 0;opacity:.9}
.wrap{max-width:1100px;margin:28px auto;padding:0 22px}.card{background:#fff;border:1px solid #dfe5ee;border-radius:12px;padding:24px;box-shadow:0 3px 14px rgba(0,0,0,.05)}
.status{display:inline-block;background:#e6f5eb;color:#146c35;border-radius:999px;padding:7px 12px;font-weight:700;margin-bottom:18px}
.drop{border:2px dashed #9fb4ca;border-radius:12px;padding:42px;text-align:center;background:#fafcff}.drop h2{margin:0 0 8px}.drop p{color:#637083}
button{background:#1f5d99;color:#fff;border:0;border-radius:8px;padding:11px 18px;font-weight:700;cursor:pointer}.meta{margin-top:18px;color:#657184;font-size:14px}
#result{margin-top:22px;display:none}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.kpi{background:#f7f9fc;border:1px solid #e1e6ee;padding:16px;border-radius:9px}.kpi b{display:block;font-size:24px;margin-top:6px}.tablewrap{overflow:auto;margin-top:18px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:8px;border-bottom:1px solid #e5e9ef;text-align:left}th{background:#eef3f8;position:sticky;top:0}
@media(max-width:800px){.grid{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
<div class="top"><h1>BT P&amp;L Local</h1><p>Local marketplace file analyser</p></div>
<div class="wrap">
  <div class="card">
    <div class="status">● LOCAL SERVER RUNNING — 127.0.0.1:5000</div>
    <div class="drop">
      <h2>Drop a CSV report here</h2>
      <p>This first local shell proves the system can run without Bash or the governed BT38 runtime.</p>
      <input id="file" type="file" accept=".csv,.txt" hidden>
      <button onclick="document.getElementById('file').click()">Choose report</button>
    </div>
    <div class="meta">No marketplace API connection. No Neon connection. No marketplace writes. File processing stays on this PC.</div>
    <div id="result">
      <div class="grid">
        <div class="kpi">File<b id="filename">—</b></div>
        <div class="kpi">Rows<b id="rows">0</b></div>
        <div class="kpi">Columns<b id="cols">0</b></div>
        <div class="kpi">Status<b>Loaded</b></div>
      </div>
      <div class="tablewrap"><table><thead id="head"></thead><tbody id="body"></tbody></table></div>
    </div>
  </div>
</div>
<script>
document.getElementById('file').addEventListener('change', async (e)=>{
 const f=e.target.files[0]; if(!f)return;
 const fd=new FormData(); fd.append('file',f);
 const r=await fetch('/preview',{method:'POST',body:fd});
 const d=await r.json();
 if(!r.ok){alert(d.error||'Could not read file');return;}
 document.getElementById('filename').textContent=d.filename;
 document.getElementById('rows').textContent=d.row_count;
 document.getElementById('cols').textContent=d.headers.length;
 document.getElementById('head').innerHTML='<tr>'+d.headers.map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr>';
 document.getElementById('body').innerHTML=d.preview.map(row=>'<tr>'+d.headers.map(h=>'<td>'+esc(row[h]??'')+'</td>').join('')+'</tr>').join('');
 document.getElementById('result').style.display='block';
});
function esc(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
</script>
</body></html>'''

@app.get("/")
def home():
    return PAGE

@app.get("/health")
def health():
    return jsonify(ok=True, host=HOST, port=PORT, mode="local-pnl-isolated")

@app.post("/preview")
def preview():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify(error="Choose a CSV or text report first."), 400
    raw = upload.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = reader.fieldnames or []
    if not headers:
        return jsonify(error="No header row was detected in this report."), 400
    preview_rows = []
    count = 0
    for row in reader:
        count += 1
        if len(preview_rows) < 20:
            preview_rows.append({h: row.get(h, "") for h in headers})
    return jsonify(filename=upload.filename, headers=headers, row_count=count, preview=preview_rows)

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)
