from pathlib import Path
from datetime import datetime
import zipfile
root=Path(__file__).resolve().parent
out=root.parent/(root.name+"-backup-"+datetime.now().strftime("%Y%m%d-%H%M%S")+".zip")
with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
    for p in root.rglob("*"):
        if p.is_file(): z.write(p,p.relative_to(root))
print(out)
