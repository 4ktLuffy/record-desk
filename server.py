import argparse, base64, json, mimetypes, subprocess, urllib.parse, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import core
import datasets
from table_quality import inspect_csv
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def respond(self,code,data,kind='application/json'):
        body=json.dumps(data).encode() if kind=='application/json' else data
        self.send_response(code)
        for k,v in {'Content-Type':kind,'Content-Length':str(len(body)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-src 'self'; object-src 'none'; frame-ancestors 'self'"}.items(): self.send_header(k,v)
        self.end_headers();self.wfile.write(body)
    def allowed(self): return self.headers.get('Host','') in {'127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)}
    def do_GET(self):
        if not self.allowed():return self.respond(403,{'error':'Local access only.'})
        route=urllib.parse.urlparse(self.path).path
        try:
            if route.startswith('/api/dataset-csv/'):
                return self.respond(200,datasets.export_csv(route.rsplit('/',1)[1]).encode(),'text/csv; charset=utf-8')
            if route=='/api/datasets':return self.respond(200,datasets.listing())
            if route.startswith('/api/dataset/'):return self.respond(200,datasets.detail(route.rsplit('/',1)[1]))
            if route=='/api/documents':return self.respond(200,core.documents())
            if route.startswith('/api/document/'):return self.respond(200,core.public(core.get(route.rsplit('/',1)[1]),True))
            if route.startswith('/source/'):
                row=core.get(route.rsplit('/',1)[1]);p=Path(row['path'])
                if p.suffix.lower()=='.heic':
                    preview=core.STATE/'previews'/(row['id']+'.jpg')
                    if not preview.exists():
                        r=subprocess.run(['sips','-s','format','jpeg',str(p),'--out',str(preview)],capture_output=True,timeout=30)
                        if r.returncode:raise ValueError('Could not preview image.')
                    p=preview
                return self.respond(200,p.read_bytes(),mimetypes.guess_type(p.name)[0] or 'application/octet-stream')
            if route=='/api/export':
                import csv,io
                stream=io.StringIO();writer=csv.writer(stream);writer.writerow(['source_id','filename']+core.FIELDS)
                for r in core.documents():
                    if r['status']=='approved' and not core.validate(r['fields'],r['id']):
                        values=[r['id'],r['name']]+[r['fields'].get(k,'') for k in core.FIELDS]
                        writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for v in values])
                return self.respond(200,stream.getvalue().encode(),'text/csv; charset=utf-8')
            static={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/favicon.svg':'favicon.svg'}
            if route in static:
                p=core.ROOT/'dist'/static[route];return self.respond(200,p.read_bytes(),mimetypes.guess_type(p.name)[0] or 'text/plain')
            self.respond(404,{'error':'Not found.'})
        except (ValueError,OSError,subprocess.TimeoutExpired):self.respond(400,{'error':'This document or page could not be opened.'})
    def do_POST(self):
        origin=self.headers.get('Origin')
        if not self.allowed() or (origin and origin not in {'http://127.0.0.1:'+str(self.server.server_port),'http://localhost:'+str(self.server.server_port)}):return self.respond(403,{'error':'Local same-origin access required.'})
        if self.headers.get('Content-Type')!='application/json':return self.respond(415,{'error':'JSON required.'})
        try:
            size=int(self.headers.get('Content-Length',0))
            if size<=0 or size>35*1024*1024:raise ValueError('Request exceeds upload limit.')
            data=json.loads(self.rfile.read(size));route=urllib.parse.urlparse(self.path).path
            if route=='/api/demo':result=datasets.demo()
            elif route=='/api/compare':result=datasets.compare(**data)
            elif route=='/api/trim':result=datasets.trim_preview(data['id'],data.get('apply') is True)
            elif route=='/api/dataset-save':result=datasets.save(data['name'],data['content'])
            elif route=='/api/dataset-map':result=datasets.map_columns(data['id'],data['mapping'])
            elif route=='/api/dataset-reconcile':result=datasets.reconcile(data['id'])
            elif route=='/api/table-inspect':result=inspect_csv(data['content'])
            elif route=='/api/import':result=core.import_folder()
            elif route=='/api/extract':result=core.extract(data['id'])
            elif route=='/api/split':result=core.split_pdf(data['id'])
            elif route=='/api/review':result=core.review(data['id'],data['fields'],data.get('approve') is True,data.get('exclude') is True)
            elif route=='/api/ask':result=core.query(data['question'])
            elif route=='/api/upload':
                name=Path(data['name']).name;ext=Path(name).suffix.lower()
                if ext not in core.EXTENSIONS:raise ValueError('Unsupported file type.')
                raw=base64.b64decode(data['content'],validate=True)
                if len(raw)>25*1024*1024:raise ValueError('25 MB file limit.')
                p=core.STATE/'uploads'/(uuid.uuid4().hex+ext);p.write_bytes(raw);ident,_=core.add(p,name);result={'id':ident}
            else:return self.respond(404,{'error':'Not found.'})
            self.respond(200,result)
        except (ValueError,KeyError,TypeError) as e:self.respond(400,{'error':str(e)[:600]})
        except subprocess.TimeoutExpired:self.respond(400,{'error':'Text reading timed out. Try a smaller or clearer document.'})
        except Exception:self.respond(500,{'error':'Processing failed. The original document is unchanged; please try again.'})
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);args=parser.parse_args();core.init()
    datasets.init()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print('Record Desk: http://127.0.0.1:%s'%args.port,flush=True);server.serve_forever()
