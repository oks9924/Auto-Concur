"""Materialize checksum-verified UI sources for CI; removed before delivery."""
from pathlib import Path
import base64, zlib, json, hashlib, subprocess
p = Path('.ux-payload')
parts = [(p / f'{i}.txt').read_text().strip() for i in range(3)]
parts[1] = parts[1].replace('P+tOfivD9IR8', 'P+t9uafp/ULsOmaMUEQX/9ITwtzg5ikdLqHjElYdj69Tc2q9GEDOIe7oEBpE00MVZMvEFV+/t2FWyDD3ki5l/vbZraHYKkcWz/f7HMiEaZSLY+yNIUs8kpO/ub/c//soLq0D+yx1h9M8f9gGi0ImgZqdnd8pSPWFuLPsyBpfuDLa+Vhu2u2esFtjT8UU19gwn67M9RQYuzVyVaRf7Vx4cPNkSwZQYDULZ39BIPdi7pk/bm8pNoSvgVPDMquzepA7O5T90w40A5yadEGl0va45JZh2qR8a3R58exu9KmEZKHly/jFcIHHgbXBan/ltR8MMS5+JLQL5Mb2sv0wLSpAoTUZPs+xqVjQn9KGzz9zhDVaTWrq21ZxnCohEDfi52T5Thu/i3mqUBMsNPOvjW201G2yLRgc9WzAqebicuC+nC2K56hRHEgjq/kRJ+pU5j+r9Hj8zIhYAO1HutuBBrCcKG8bLRibc7kKCLXAyYGyEt1vEKS1rmSG3YYIGNA4uFqCBRTFMc5nUUGbm6z7oOfBZzu6ah9nOInS4JHKgj7Cb00Mvu/IWl70YE2/zQluF3muMpVO+u62KDsIiHFFIoJPvIK6iKwHeUOGaAP9Wge7zw/pOfivD9IR8')
encoded = ''.join(parts)
assert hashlib.sha256(encoded.encode()).hexdigest() == '6524f8c8f30e3a1e487a76f3ccdd282349e7512f01f586c403305ceb7858bcaf', 'Source transfer checksum mismatch'
data = json.loads(zlib.decompress(base64.b64decode(encoded)))
for name, content in data['new'].items():
    target = Path(name)
    assert not target.is_absolute() and '..' not in target.parts
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
subprocess.run(['git', 'apply', '--whitespace=nowarn'], input=data['patch'].encode(), check=True)
for name, digest in data['hashes'].items():
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest, name
print('Verified UI sources:', ', '.join(data['hashes']))
