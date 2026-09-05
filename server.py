#!/usr/bin/env python3
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, unquote
import html as html_lib
import json, re, base64, mimetypes, shutil, zipfile
from datetime import datetime

ROOT=Path(__file__).resolve().parent
PORT=8000

def safe_name(s):
    s=re.sub(r"[^A-Za-z0-9._-]+","-",str(s)).strip("-")
    return s[:100] or "item"

def visible(t):
    m=re.search(r"<body[^>]*>([\s\S]*?)</body>",t,re.I)
    x=m.group(1) if m else t
    x=re.sub(r"<style\b[^>]*>[\s\S]*?</style\s*>"," ",x,flags=re.I)
    x=re.sub(r"<script\b[^>]*>[\s\S]*?</script\s*>"," ",x,flags=re.I)
    # Remove legacy CSS that older vault versions accidentally stored as body text.
    x=re.sub(r"body\s*\{[^{}]*\}\s*h1\s*\{[^{}]*\}\s*h2\s*\{[^{}]*\}\s*pre\s*\{[^{}]*\}[\s\S]*?\.tag\s*\{[^{}]*\}"," ",x,flags=re.I)
    x=re.sub(r"(?:body|html|h[1-6]|pre|\.callout|\.blue|\.green|\.yellow|\.red|\.purple|\.orange|\.tag|\.diagram)\s*\{[^{}]*\}"," ",x,flags=re.I)
    # Also remove a literal CSS prefix if it was concatenated directly before content.
    x=re.sub(r"^\s*body\s*\{.*?\}\s*","",x,flags=re.I|re.S)
    x=re.sub(r"<[^>]+>"," ",x)
    return html_lib.unescape(re.sub(r"\s+"," ",x).strip())


def clean_editor_body(t):
    """Remove only known legacy wrapper artifacts; preserve real documentation."""
    m=re.search(r"<body[^>]*>([\s\S]*?)</body>",t,re.I)
    x=m.group(1) if m else t
    x=re.sub(r"<style\b[^>]*>[\s\S]*?</style\s*>","",x,flags=re.I)
    x=re.sub(r"<script\b[^>]*>[\s\S]*?</script\s*>","",x,flags=re.I)
    # Remove old vault title/tag header from the body because the editor has its own metadata fields.
    x=re.sub(r"^\s*<h1[^>]*>.*?</h1>\s*<div[^>]*>.*?</div>\s*","",x,count=1,flags=re.I|re.S)
    # Remove known accidental literal CSS prefix.
    x=re.sub(r"^\s*body\s*\{.*?\}\s*","",x,count=1,flags=re.I|re.S)
    x=re.sub(r"\s*body\s*\{font-family:system-ui;.*?\.tag\s*\{[^{}]*\}\s*","",x,count=1,flags=re.I|re.S)
    return x.strip()


def doc(title,tags,body):
    tags_html="".join('<span class="tag">'+html_lib.escape(t)+'</span>' for t in tags)
    return '<!doctype html><html><head><meta charset="utf-8"><title>'+html_lib.escape(title)+'</title><style>body{font-family:system-ui;max-width:1000px;margin:36px auto;padding:0 24px;line-height:1.62;color:#17202a}h1{font-size:30px}h2{margin-top:28px}pre{background:#111827;color:#f8fafc;padding:14px;border-radius:9px;overflow:auto}.callout{padding:12px 14px;border-left:5px solid #64748b;background:#f8fafc;margin:12px 0;border-radius:6px}.blue{border-color:#2563eb}.green{border-color:#16a34a}.yellow{border-color:#ca8a04}.red{border-color:#dc2626}.purple{border-color:#9333ea}.orange{border-color:#ea580c}.tag{display:inline-block;background:#e8eef7;border-radius:14px;padding:3px 9px;margin:2px;font-size:12px}.diagram{background:#f8fafc;border:1px dashed #94a3b8;padding:14px;white-space:pre-wrap;font-family:monospace}img{max-width:100%;height:auto;border-radius:8px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #cbd5e1;padding:7px}</style></head><body><h1>'+html_lib.escape(title)+'</h1><div>'+tags_html+'</div>'+body+'</body></html>'

def md_html(md):
    out=[]; code=[]; inc=False
    for line in md.splitlines():
        if line.strip().startswith("```"):
            if inc: out.append("<pre><code>"+html_lib.escape("\n".join(code))+"</code></pre>"); code=[]; inc=False
            else: inc=True
        elif inc: code.append(line)
        elif line.startswith("### "): out.append("<h3>"+html_lib.escape(line[4:])+"</h3>")
        elif line.startswith("## "): out.append("<h2>"+html_lib.escape(line[3:])+"</h2>")
        elif line.startswith("# "): out.append("<h1>"+html_lib.escape(line[2:])+"</h1>")
        elif line.startswith("- "): out.append("<li>"+html_lib.escape(line[2:])+"</li>")
        elif line.strip(): out.append("<p>"+html_lib.escape(line)+"</p>")
    return "\n".join(out)

def rebuild():
    a=[]
    for p in ROOT.glob("[0-9][0-9][0-9]-*/topics/*/entry.html"):
        t=p.read_text(encoding="utf-8",errors="replace")
        m=re.search(r"<h1[^>]*>(.*?)</h1>",t,re.I|re.S)
        title=html_lib.unescape(re.sub(r"<[^>]+>","",m.group(1)).strip()) if m else p.parent.name
        tags=[]
        try: tags=json.loads((p.parent/"metadata.json").read_text(encoding="utf-8")).get("tags",[])
        except: pass
        rel=p.relative_to(ROOT).as_posix()
        updated_at=""
        try:
            md=json.loads((p.parent/"metadata.json").read_text(encoding="utf-8"))
            updated_at=str(md.get("updated_at","") or "")
        except Exception:
            pass
        if not updated_at:
            try:
                updated_at=datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
            except Exception:
                updated_at=""
        a.append({"id":rel,"module":rel.split("/")[0],"title":title,"type":"Knowledge","tags":tags,"path":rel,"text":visible(t)[:20000],"updated_at":updated_at})
    a.sort(key=lambda e:(e["module"],e["title"].lower()))
    (ROOT/"000-Index"/"search-index.json").write_text(json.dumps(a,indent=2,ensure_ascii=False),encoding="utf-8")
    return len(a)


def backup_vault():
    """Create a timestamped full Vault ZIP, excluding backups/caches/venvs."""
    backup_dir=ROOT/"_Vault-Backups"
    backup_dir.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now().strftime("%Y%m%d-%H%M%S")
    out=backup_dir/f"Tarun-Knowledge-Vault-{stamp}.zip"
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
        for f in ROOT.rglob("*"):
            if not f.is_file(): continue
            rel=f.relative_to(ROOT)
            parts=set(rel.parts)
            if "_Vault-Backups" in parts or ".git" in parts or "__pycache__" in parts:
                continue
            if "venv" in parts or ".venv" in parts:
                continue
            z.write(f,rel.as_posix())
    return out

def repair_image_urls(body,module,folder):
    """Repair legacy image URLs using the actual attachment in this topic."""
    if not body:
        return body

    module_safe=safe_name(module)
    folder_safe=safe_name(folder)
    attachment_dir=(ROOT/module_safe/"topics"/folder_safe/"attachments").resolve()

    def actual_url(filename):
        filename=Path(unquote(filename)).name
        if not filename or not attachment_dir.exists():
            return None
        exact=attachment_dir/filename
        if exact.is_file():
            return "/files/"+Path(module_safe,"topics",folder_safe,"attachments",exact.name).as_posix()
        low=filename.lower()
        for f in attachment_dir.iterdir():
            if f.is_file() and f.name.lower()==low:
                return "/files/"+Path(module_safe,"topics",folder_safe,"attachments",f.name).as_posix()
        return None

    def repl(m):
        before,src,after=m.group(1),m.group(2),m.group(3)
        s=src.strip()
        if s.startswith("data:") or s.startswith("http://") or s.startswith("https://"):
            return m.group(0)

        if s.startswith("/files/"):
            candidate=actual_url(s[7:])
            return before+(candidate or s)+after

        clean=s.replace("\\","/").lstrip("./")
        name=clean[len("attachments/"):] if clean.startswith("attachments/") else clean
        candidate=actual_url(name)
        return before+(candidate or s)+after

    return re.sub(r'(<img\b[^>]*\bsrc=["\'])([^"\']+)(["\'])',repl,body,flags=re.I)

class H(BaseHTTPRequestHandler):
    def sendj(self,o,status=200):
        b=json.dumps(o,ensure_ascii=False).encode(); self.send_response(status); self.send_header("Cache-Control","no-store, no-cache, must-revalidate, max-age=0"); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def sendf(self,p,ct=None):
        b=p.read_bytes(); self.send_response(200); self.send_header("Cache-Control","no-store, no-cache, must-revalidate, max-age=0"); self.send_header("Content-Type",ct or mimetypes.guess_type(str(p))[0] or "application/octet-stream"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u=urlparse(self.path).path
        if u=="/": return self.sendf(ROOT/"editor.html","text/html; charset=utf-8")
        if u=="/editor.html": return self.sendf(ROOT/"editor.html","text/html; charset=utf-8")
        if u=="/api/backup":
            try:
                out=backup_vault()
                return self.sendj({"ok":True,"file":str(out.relative_to(ROOT)).replace("\\","/")})
            except Exception as ex:
                return self.sendj({"error":"Backup failed: "+str(ex)},500)

        if u=="/api/index": return self.sendj({"entries":json.loads((ROOT/"000-Index/search-index.json").read_text(encoding="utf-8"))})
        if u=="/api/topic":
            from urllib.parse import parse_qs
            q=parse_qs(urlparse(self.path).query)
            rel=unquote(q.get("path",[""])[0]).replace("\\","/")
            p=(ROOT/rel).resolve()
            if ROOT not in p.parents or not p.is_file():
                # Legacy/stale index recovery: locate the same topic folder
                # under any module. This is especially useful after a module
                # rename or an older saved index.
                folder=Path(rel).parent.name if rel else ""
                candidates=[]
                if folder:
                    for cand in ROOT.glob("[0-9][0-9][0-9]-*/topics/"+folder+"/entry.html"):
                        if cand.is_file(): candidates.append(cand.resolve())
                if len(candidates)==1:
                    p=candidates[0]
                else:
                    return self.sendj({"error":"Topic not found"},404)
            body=clean_editor_body(p.read_text(encoding="utf-8",errors="replace"))
            parts=p.relative_to(ROOT).parts
            module=parts[0] if len(parts)>0 else ""
            folder=p.parent.name
            body=repair_image_urls(body,module,folder)
            return self.sendj({"html":body})
        if u.startswith("/files/"):
            rel=unquote(u[7:]).replace("\\","/").lstrip("/")
            fp=(ROOT/rel).resolve()

            if ROOT in fp.parents and fp.is_file():
                return self.sendf(fp)

            # Legacy compatibility: old HTML may contain
            # /files/attachments/name.png or an old module/folder.
            filename=Path(rel).name
            if filename:
                matches=[]
                for d in ROOT.glob("[0-9][0-9][0-9]-*/topics/*/attachments"):
                    if d.is_dir():
                        for f in d.iterdir():
                            if f.is_file() and f.name.lower()==filename.lower():
                                matches.append(f)
                if len(matches)==1:
                    return self.sendf(matches[0])

            return self.send_error(404, "File not found")
        if u=="/api/attachments":
            from urllib.parse import parse_qs
            q=parse_qs(urlparse(self.path).query)
            module=safe_name(q.get("module",[""])[0]); folder=safe_name(q.get("folder",[""])[0])
            d=(ROOT/module/"topics"/folder/"attachments").resolve()
            if ROOT not in d.parents or not d.exists(): return self.sendj({"files":[]})
            files=[]
            for p in sorted(d.iterdir(),key=lambda x:x.name.lower()):
                if p.is_file():
                    rel=(Path(module)/"topics"/folder/"attachments"/p.name).as_posix()
                    files.append({"name":p.name,"path":rel,"url":"/files/"+rel})
            return self.sendj({"files":files})
        return self.send_error(404)
    def do_DELETE(self):
        u=urlparse(self.path).path
        if u=="/api/delete-topic":
            from urllib.parse import parse_qs
            q=parse_qs(urlparse(self.path).query)
            requested=unquote(q.get("path",[""])[0]).replace("\\","/").lstrip("/")
            requested_id=unquote(q.get("id",[""])[0]).replace("\\","/").lstrip("/")
            rebuild()
            index_path=ROOT/"000-Index"/"search-index.json"
            entries=json.loads(index_path.read_text(encoding="utf-8"))
            match=next((x for x in entries if requested_id and x.get("id")==requested_id),None)
            if not match: match=next((x for x in entries if requested and x.get("path")==requested),None)
            rel=((match or {}).get("path") or requested).replace("\\","/").lstrip("/")
            target=(ROOT/rel).resolve()
            if ROOT not in target.parents or target.name!="entry.html":
                return self.sendj({"error":"Invalid topic path."},400)
            topic_dir=target.parent
            topics_dir=topic_dir.parent
            module_dir=topics_dir.parent
            if topics_dir.name!="topics" or module_dir.parent!=ROOT:
                return self.sendj({"error":"Topic delete not allowed."},400)
            if not topic_dir.is_dir():
                return self.sendj({"error":"Topic folder not found: "+str(topic_dir)},404)
            shutil.rmtree(topic_dir)
            count=rebuild()
            entries=json.loads(index_path.read_text(encoding="utf-8"))
            return self.sendj({"ok":True,"deleted":rel,"topics":count,"entries":entries})

        if u=="/api/rename-module":
            old_module=safe_name(d.get("module",""))
            new_module=safe_name(d.get("new_module",""))
            if not old_module or not new_module:
                return self.sendj({"error":"Current module and new module name are required."},400)
            if old_module==new_module:
                return self.sendj({"error":"New module name is the same as the current name."},400)

            old_dir=(ROOT/old_module).resolve()
            new_dir=(ROOT/new_module).resolve()

            if old_dir.parent!=ROOT or not old_dir.is_dir() or not (old_dir/"topics").is_dir():
                return self.sendj({"error":"Module not found: "+old_module},404)
            if new_dir.exists():
                return self.sendj({"error":"A module with that name already exists: "+new_module},409)

            try:
                shutil.move(str(old_dir),str(new_dir))

                # Update every topic's metadata after the folder move.
                topics=new_dir/"topics"
                for topic in topics.iterdir():
                    if not topic.is_dir(): continue
                    meta=topic/"metadata.json"
                    if meta.exists():
                        try:
                            md=json.loads(meta.read_text(encoding="utf-8"))
                        except Exception:
                            md={}
                        md["module"]=new_module
                        md["updated_at"]=datetime.now().isoformat(timespec="seconds")
                        md["id"]=Path(new_module,"topics",topic.name).as_posix()
                        meta.write_text(json.dumps(md,indent=2,ensure_ascii=False),encoding="utf-8")

                count=rebuild()
                entries=json.loads((ROOT/"000-Index"/"search-index.json").read_text(encoding="utf-8"))
                return self.sendj({"ok":True,"old_module":old_module,"module":new_module,"topics":count,"entries":entries})
            except Exception as ex:
                # If a move partially fails, report the real error.
                return self.sendj({"error":"Module rename failed: "+str(ex)},500)

        if u=="/api/delete-module":
            from urllib.parse import parse_qs
            q=parse_qs(urlparse(self.path).query)
            module=safe_name(unquote(q.get("module",[""])[0]))
            if not module: return self.sendj({"error":"Module is required."},400)
            module_dir=(ROOT/module).resolve()
            if module_dir.parent!=ROOT or not module_dir.is_dir() or not (module_dir/"topics").is_dir():
                return self.sendj({"error":"Module not found: "+module},404)
            shutil.rmtree(module_dir)
            count=rebuild()
            index_path=ROOT/"000-Index"/"search-index.json"
            entries=json.loads(index_path.read_text(encoding="utf-8"))
            return self.sendj({"ok":True,"deleted":module,"topics":count,"entries":entries})

        if u=="/api/delete-file":
            from urllib.parse import parse_qs
            q=parse_qs(urlparse(self.path).query)
            rel=unquote(q.get("path",[""])[0]).replace("\\","/")
            p=(ROOT/rel).resolve()
            # Only allow deleting files inside a topic's attachments directory.
            if ROOT not in p.parents or not p.is_file() or "attachments" not in p.parts:
                return self.sendj({"error":"Attachment not found or delete not allowed."},400)
            p.unlink()
            return self.sendj({"ok":True})
        if u=="/api/delete-module":
            from urllib.parse import parse_qs
            q=parse_qs(urlparse(self.path).query)
            module=safe_name(unquote(q.get("module",[""])[0]))
            if not module:
                return self.sendj({"error":"Module is required."},400)

            # Only allow a top-level module folder directly under ROOT.
            module_dir=(ROOT/module).resolve()
            if module_dir.parent != ROOT or module_dir.name != module:
                return self.sendj({"error":"Invalid module path."},400)

            topics_dir=module_dir/"topics"
            if not module_dir.is_dir() or not topics_dir.is_dir():
                return self.sendj({"error":"Module not found: "+module},404)

            # Safety: module deletion is limited to module folders containing
            # the expected topics structure.
            if not any(topics_dir.iterdir()):
                shutil.rmtree(module_dir)
            else:
                # The module folder itself and everything below it are owned
                # by the vault module structure.
                shutil.rmtree(module_dir)

            count=rebuild()
            entries=json.loads((ROOT/"000-Index"/"search-index.json").read_text(encoding="utf-8"))
            return self.sendj({"ok":True,"deleted":module,"topics":count,"entries":entries})

        if u=="/api/backup":
            try:
                out=backup_vault()
                return self.sendj({"ok":True,"file":str(out.relative_to(ROOT)).replace("\\","/")})
            except Exception as ex:
                return self.sendj({"error":"Backup failed: "+str(ex)},500)

        if u=="/api/delete-topic":
            from urllib.parse import parse_qs
            q=parse_qs(urlparse(self.path).query)
            requested=unquote(q.get("path",[""])[0]).replace("\\","/").lstrip("/")
            requested_id=unquote(q.get("id",[""])[0]).replace("\\","/").lstrip("/")

            rebuild()
            index_path=ROOT/"000-Index"/"search-index.json"
            try:
                entries=json.loads(index_path.read_text(encoding="utf-8"))
            except Exception as ex:
                return self.sendj({"error":"Could not read search index: "+str(ex)},500)

            match=next((x for x in entries if requested_id and x.get("id")==requested_id),None)
            if not match:
                match=next((x for x in entries if requested and x.get("path")==requested),None)

            rel=((match or {}).get("path") or requested).replace("\\","/").lstrip("/")
            p=(ROOT/rel).resolve()

            if ROOT not in p.parents or p.name!="entry.html":
                return self.sendj({"error":"Invalid topic path: "+rel},400)

            topic_dir=p.parent
            topics_dir=topic_dir.parent
            module_dir=topics_dir.parent
            if topics_dir.name!="topics" or module_dir.parent!=ROOT:
                return self.sendj({"error":"Topic delete not allowed for: "+rel},400)

            if not topic_dir.is_dir():
                return self.sendj({"error":"Topic folder not found:\n"+str(topic_dir)},404)

            if not p.is_file() and not (topic_dir/"metadata.json").is_file():
                return self.sendj({"error":"Topic entry not found:\n"+str(p)},404)

            shutil.rmtree(topic_dir)
            count=rebuild()
            entries=json.loads(index_path.read_text(encoding="utf-8"))
            return self.sendj({"ok":True,"deleted":rel,"topics":count,"entries":entries})

        return self.send_error(404)

    def do_POST(self):
        u=urlparse(self.path).path
        try:
            n=int(self.headers.get("Content-Length","0"))
            raw=self.rfile.read(n).decode() if n else ""
            d=json.loads(raw) if raw.strip() else {}
        except Exception:
            return self.sendj({"error":"Invalid JSON"},400)
        if u=="/api/backup":
            try:
                out=backup_vault()
                return self.sendj({"ok":True,"file":str(out.relative_to(ROOT)).replace("\\","/")})
            except Exception as ex:
                return self.sendj({"error":"Backup failed: "+str(ex)},500)

        if u=="/api/rename-topic":
            title=str(d.get("title","")).strip()
            topic_id=str(d.get("id","")).replace("\\","/").strip("/")
            if not title or not topic_id:
                return self.sendj({"error":"Topic id and new title are required."},400)

            parts=Path(topic_id).parts
            if len(parts)!=4 or parts[1]!="topics" or parts[3]!="entry.html":
                return self.sendj({"error":"Invalid topic id."},400)

            module,_,folder,_=parts
            target=(ROOT/module/"topics"/folder/"entry.html").resolve()
            if ROOT not in target.parents or not target.is_file():
                return self.sendj({"error":"Topic not found."},404)

            # Keep the existing topic folder and attachments intact.
            # Only update the HTML title and metadata title.
            content=target.read_text(encoding="utf-8",errors="replace")
            content=re.sub(
                r"<title>.*?</title>",
                lambda m:"<title>"+html_lib.escape(title)+"</title>",
                content,count=1,flags=re.I|re.S
            )
            content=re.sub(
                r"<h1[^>]*>.*?</h1>",
                lambda m:"<h1>"+html_lib.escape(title)+"</h1>",
                content,count=1,flags=re.I|re.S
            )
            target.write_text(content,encoding="utf-8")

            meta=target.parent/"metadata.json"
            if meta.exists():
                try:
                    md=json.loads(meta.read_text(encoding="utf-8"))
                except Exception:
                    md={}
                md["title"]=title
                md["updated_at"]=datetime.now().isoformat(timespec="seconds")
                meta.write_text(json.dumps(md,indent=2,ensure_ascii=False),encoding="utf-8")

            count=rebuild()
            entries=json.loads((ROOT/"000-Index"/"search-index.json").read_text(encoding="utf-8"))
            return self.sendj({"ok":True,"title":title,"topics":count,"entries":entries})

        if u=="/api/upload":
            try:
                module=safe_name(d.get("module","002-Python-VSCode"))
                folder=safe_name(d.get("folder",""))
                name=safe_name(d.get("name","attachment.bin"))
                if not folder: return self.sendj({"error":"Save the topic once before uploading files."},400)
                target=ROOT/module/"topics"/folder/"attachments"
                target.mkdir(parents=True,exist_ok=True)
                raw=base64.b64decode(d.get("data","").split(",",1)[-1])
                dest=target/name; i=2
                while dest.exists():
                    dest=target/f"{Path(name).stem}-{i}{Path(name).suffix}"; i+=1
                dest.write_bytes(raw)
                file_mime=d.get("mime") or mimetypes.guess_type(dest.name)[0] or "application/octet-stream"
                return self.sendj({"ok":True,"relative":"attachments/"+dest.name,"url":"/files/"+module+"/topics/"+folder+"/attachments/"+dest.name,"name":dest.name,"mime":file_mime})
            except Exception as ex:
                return self.sendj({"error":"Upload failed on server: "+str(ex)},500)
        if u=="/api/save":
            # Keep the whole operation inside a JSON-returning guard so a
            # filesystem/OneDrive hiccup can never turn into browser
            # "Failed to fetch" caused by a dropped HTTP response.
            try:
                title=str(d.get("title","")).strip()
                requested_module=safe_name(d.get("module","002-Python-VSCode"))
                original_module=safe_name(d.get("original_module","")) if d.get("original_module") else None
                tags=[str(x).strip() for x in d.get("tags",[]) if str(x).strip()]
                if not title: return self.sendj({"error":"Title is required"},400)
                if not requested_module: return self.sendj({"error":"Module is required"},400)

                folder=d.get("folder")
                is_edit=bool(folder and original_module)

                if not folder:
                    topics=ROOT/requested_module/"topics"
                    topics.mkdir(parents=True,exist_ok=True)
                    nums=[int(m.group(1)) for p in topics.iterdir() if (m:=re.match(r"^(\d{3})-",p.name))]
                    folder=f"{max(nums,default=0)+1:03d}-{safe_name(title).lower()[:70]}"

                folder=safe_name(folder)

                if is_edit:
                    source=ROOT/original_module/"topics"/folder
                    destination=ROOT/requested_module/"topics"/folder

                    # Recovery for a stale module name after module rename.
                    if not source.is_dir():
                        candidates=list(ROOT.glob("[0-9][0-9][0-9]-*/topics/"+folder))
                        candidates=[x for x in candidates if x.is_dir()]
                        if len(candidates)==1:
                            source=candidates[0]
                            original_module=source.parent.parent.name
                        else:
                            return self.sendj({"error":"Original topic folder not found: "+str(source)},404)

                    if original_module != requested_module:
                        destination.parent.mkdir(parents=True,exist_ok=True)
                        if destination.exists():
                            return self.sendj({
                                "error":"A topic with the same folder name already exists in module '"+requested_module+"'."
                            },409)
                        shutil.move(str(source),str(destination))
                    topic=destination
                else:
                    topic=ROOT/requested_module/"topics"/folder
                    topic.mkdir(parents=True,exist_ok=True)

                (topic/"attachments").mkdir(exist_ok=True)
                (topic/"history").mkdir(exist_ok=True)

                entry=topic/"entry.html"

                # History is valuable, but it must NEVER prevent the actual
                # Save. If OneDrive/AV/locking causes a snapshot error, keep
                # going and report it as a warning.
                history_warning=None
                if entry.exists():
                    stamp=datetime.now().strftime("%Y%m%d-%H%M%S")
                    try:
                        shutil.copy2(entry,topic/"history"/(stamp+".html"))
                    except Exception as ex:
                        history_warning="History snapshot could not be created: "+str(ex)

                    try:
                        backup_root=ROOT/"_Vault-Backups"/"topic-snapshots"/requested_module
                        backup_root.mkdir(parents=True,exist_ok=True)
                        snapshot=backup_root/f"{folder}-{stamp}"
                        if snapshot.exists(): shutil.rmtree(snapshot)
                        shutil.copytree(topic,snapshot)
                    except Exception as ex:
                        # Snapshot is an additional safety layer; don't block
                        # the primary document save.
                        extra="Snapshot could not be created: "+str(ex)
                        history_warning=(history_warning+" | " if history_warning else "")+extra

                content=str(d.get("content",""))
                content=clean_editor_body(content)
                content=repair_image_urls(content,requested_module,folder)

                fmt=d.get("format","html")
                if fmt=="markdown":
                    (topic/"entry.md").write_text(content,encoding="utf-8")
                    content=md_html(content)

                # Atomic-ish write: write the new document beside entry.html,
                # then replace it. This avoids leaving a half-written HTML file.
                new_entry=topic/"entry.html.tmp"
                new_entry.write_text(doc(title,tags,content),encoding="utf-8")
                try:
                    new_entry.replace(entry)
                except Exception:
                    # Windows/OneDrive fallback.
                    entry.write_text(new_entry.read_text(encoding="utf-8"),encoding="utf-8")
                    try: new_entry.unlink()
                    except Exception: pass

                now=datetime.now().isoformat(timespec="seconds")
                (topic/"metadata.json").write_text(json.dumps({
                    "id":topic.relative_to(ROOT).as_posix(),
                    "module":requested_module,
                    "title":title,
                    "type":"Knowledge",
                    "tags":tags,
                    "updated_at":now
                },indent=2,ensure_ascii=False),encoding="utf-8")

                count=rebuild()
                result={
                    "ok":True,
                    "updated":is_edit,
                    "topics":count,
                    "folder":folder,
                    "module":requested_module,
                    "updated_at":now
                }
                if history_warning:
                    result["warning"]=history_warning
                return self.sendj(result)

            except Exception as ex:
                # Never let a save exception terminate the HTTP connection.
                return self.sendj({
                    "error":"Save failed on server: "+type(ex).__name__+": "+str(ex)
                },500)

        return self.send_error(404)

if __name__=="__main__":
    rebuild(); print("Tarun Technical Knowledge Vault V12 FINAL"); print("Open: http://127.0.0.1:8000/"); ThreadingHTTPServer(("127.0.0.1",PORT),H).serve_forever()
