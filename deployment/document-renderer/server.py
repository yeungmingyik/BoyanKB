import hmac
import json
import os
import hashlib
import pathlib
import signal
import shutil
from http.server import BaseHTTPRequestHandler, HTTPServer

from render import ROOT, RenderError, generate


def readiness():
    manifest=json.loads((ROOT/"manifest.json").read_text(encoding="utf-8"))
    expected={scene+"-"+kind for scene in ("teaching","business","operations") for kind in ("docx","pptx","xlsx","md")}
    if manifest.get("version")!="boyan-v1" or manifest.get("brand_id")!="boyan-culture" or len(manifest.get("templates",[]))!=12 or {entry.get("id") for entry in manifest["templates"]}!=expected:
        raise ValueError("invalid_template_manifest")
    for entry in manifest["templates"]:
        file=ROOT/entry["file"]
        if file.resolve().parent!=ROOT.resolve() or hashlib.sha256(file.read_bytes()).hexdigest()!=entry["sha256"]:
            raise ValueError("invalid_template_hash")
    if not shutil.which("libreoffice") and not shutil.which("soffice"):
        raise ValueError("spreadsheet_recalculation_unavailable")
    return {"status":"ok","template_version":"boyan-v1","formats":["docx","pptx","xlsx","md"],"template_integrity":True,"spreadsheet_recalculation":True}


def render_timeout(signum, frame):
    raise RenderError("render_timeout")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def send(self, status, payload):
        data=json.dumps(payload,ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(data)))
        self.send_header("Cache-Control","no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path!="/health":
            self.send(404,{"error":"not_found"})
            return
        try:
            self.send(200,readiness())
        except Exception:
            self.send(503,{"error":"templates_unavailable"})

    def do_POST(self):
        token=os.environ.get("BOYAN_DOCUMENT_RENDERER_TOKEN","")
        if self.path!="/render":
            self.send(404,{"error":"not_found"})
            return
        if len(token)<32 or not hmac.compare_digest(self.headers.get("Authorization",""),"Bearer "+token):
            self.send(401,{"error":"unauthorized"})
            return
        try:
            if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Type", "").split(";",1)[0].strip()!="application/json":
                raise ValueError()
            size=int(self.headers.get("Content-Length","0"))
            if size<=0 or size>768*1024:
                raise ValueError()
            self.connection.settimeout(10)
            raw=self.rfile.read(size)
            if len(raw)!=size:
                raise ValueError()
            request=json.loads(raw)
            signal.alarm(75)
            result=generate(request)
            signal.alarm(0)
            self.send(200,result)
        except RenderError as error:
            signal.alarm(0)
            self.send(422,{"error":str(error)})
        except Exception:
            signal.alarm(0)
            self.send(422,{"error":"render_failed"})


if __name__=="__main__":
    if len(os.environ.get("BOYAN_DOCUMENT_RENDERER_TOKEN",""))<32:
        raise SystemExit("renderer_token_missing_or_too_short")
    readiness()
    signal.signal(signal.SIGALRM,render_timeout)
    HTTPServer(("0.0.0.0",8091),Handler).serve_forever()
