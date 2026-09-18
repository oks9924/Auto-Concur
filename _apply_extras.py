from pathlib import Path
import json, base64, zlib, hashlib, subprocess
parts=[Path(f'.extras-payload/{i}.txt').read_text().strip() for i in range(3)]
# Restore transport transcription; whole payload must match the locally tested bytes.
for old,new in [('93lododb/','93odb/'),('olYL5c1qtTpOu','olYLtTpOu'),('2sy9vfc77zercv','2sy9v3ercv'),('DU3MSWWowl','DU3MSWowl')]:
    parts[0]=parts[0].replace(old,new)
s=''.join(parts)
assert hashlib.sha256(s.encode()).hexdigest()=='7acb4db7137145f6868631a207223a5e6ba0149eb6f745e134451a92c68f7300', 'Source transfer checksum mismatch'
data=json.loads(zlib.decompress(base64.b64decode(s)))
for name,content in data['new'].items():
    p=Path(name)
    assert not p.is_absolute() and '..' not in p.parts
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(content,encoding='utf-8')
subprocess.run(['git','apply','--whitespace=nowarn'],input=data['patch'].encode(),check=True)
print('Local tested source checksum matched.')
