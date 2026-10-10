"""Decimal MB consent boundary and verified HTTPS download metadata."""
import json
import shutil
import urllib.request
from urllib.parse import urlsplit

LIMIT = 100_000_000  # 100 MB, not 100 MiB.
TRUSTED = {'pypi.org', 'files.pythonhosted.org', 'registry.ollama.ai'}

class TrustedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        verify_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def verify_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in TRUSTED or parsed.username or parsed.password:
        raise ValueError('Download source is not an approved HTTPS source.')

def remote_json(url):
    verify_url(url)
    opener = urllib.request.build_opener(TrustedRedirect())
    with opener.open(url, timeout=15) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('Download metadata exceeded its size limit.')
    return json.loads(raw)

def approve_download(label, size, required, *, optional, confirm, location, privileged=False):
    if size is not None and (type(size) is not int or size < 0):
        raise ValueError('Invalid download size.')
    free = shutil.disk_usage(location).free
    size_text = f'{size / 1_000_000:.2f} MB ({size} bytes)' if size is not None else 'unknown'
    disk_text = f'{required / 1_000_000:.2f} MB' if required is not None else 'unknown; cannot verify before download'
    print(f'{label}: download {size_text}; required free disk space {disk_text}; available {free / 1_000_000:.2f} MB.')
    if required is not None and free < required:
        print('Insufficient free disk space. Download skipped.')
        return False
    if optional or privileged or size is None or size >= LIMIT:
        return confirm(f'Allow {label} download' + (' and installation?' if privileged else '?'))
    return True

def model_download_size(model):
    # Official Ollama manifest; all blobs are counted even if some are cached.
    tag = model.split(':', 1)[1]
    value = remote_json('https://registry.ollama.ai/v2/library/qwen2.5/manifests/' + tag)
    blobs = [value['config'], *value['layers']]
    if not blobs or any(type(b.get('size')) is not int or b['size'] < 0 for b in blobs):
        raise ValueError('Unknown model size.')
    return sum(b['size'] for b in blobs)

def dependency_plan(requirements):
    # Select only compatible, pinned wheels. pip verifies each exact SHA-256.
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.tags import sys_tags
    from pip._vendor.packaging.utils import parse_wheel_filename
    tags = list(sys_tags())
    ranks = {tag: rank for rank, tag in enumerate(tags)}
    lines, total = [], 0
    for line in requirements.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        req = Requirement(line)
        if req.marker and not req.marker.evaluate():
            continue
        specs = list(req.specifier)
        if len(specs) != 1 or specs[0].operator != '==':
            raise ValueError('Dependencies must be exactly pinned.')
        data = remote_json(f'https://pypi.org/pypi/{req.name}/{specs[0].version}/json')
        candidates = []
        for wheel in data['urls']:
            if wheel['packagetype'] != 'bdist_wheel' or wheel.get('yanked'):
                continue
            _, _, _, supported = parse_wheel_filename(wheel['filename'])
            matching = supported & ranks.keys()
            if matching:
                candidates.append((min(ranks[t] for t in matching), wheel))
        if not candidates:
            raise ValueError('No compatible pinned wheel for ' + req.name)
        wheel = min(candidates, key=lambda item: item[0])[1]
        verify_url(wheel['url'])
        digest = wheel['digests']['sha256']
        import re
        if not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('Unverified wheel metadata.')
        size = wheel.get('size')
        if type(size) is not int or size < 0:
            total = None
        elif total is not None:
            total += size
        lines.append(f"{req.name} @ {wheel['url']} --hash=sha256:{digest}")
    return total, '\n'.join(lines) + '\n'
