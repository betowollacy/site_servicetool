import json
import logging
import mimetypes
import os
import re
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from django.conf import settings
from django.db.models import Q

from . import provider_api
from .models import ServiceList, SystemSetting

logger = logging.getLogger(__name__)

CATALOG_PATHS = ('/remote/service', '/imei/service', '/server/service', '/file/service')
DEFAULT_CATALOG_SITES = (
    'https://kfsoftwaremobile.com',
    'https://kenncellserver.com',
    'https://mdmbr-unlocker.com',
)
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
FETCH_TIMEOUT = 20
MAX_IMAGE_BYTES = 2 * 1024 * 1024
ALLOWED_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.gif')


def configured_sites():
    """Sites GSM Theme a varrer. Padrão fixo, sobrescrevível pelo ajuste
    `imageCatalogSites` (lista JSON de URLs)."""
    raw = SystemSetting.get('imageCatalogSites', '')
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list) and parsed:
            sites = [str(x).strip().rstrip('/') for x in parsed if str(x).strip()]
            if sites:
                return sites
    return list(DEFAULT_CATALOG_SITES)


def fetch_text(url):
    req = urllib.request.Request(url, headers={
        'User-Agent': USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Encoding': 'identity',
    })
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read().decode('utf-8', 'replace')


def extract_services(html):
    """Extrai o array `var services = [...]` embutido no HTML das páginas GSM Theme.
    Varre colchetes respeitando aspas (títulos podem ter '[' e ']').
    Retorna lista de dicts {title, thumbnail, slug, url}."""
    out = []
    for m in re.finditer(r'var\s+services\s*=\s*\[', html):
        raw = _slice_array(html, m.end() - 1)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            title = (item.get('title') or '').strip()
            thumb = (item.get('thumbnail') or '').strip()
            if title and thumb:
                out.append({
                    'title': ' '.join(title.split()),
                    'thumbnail': thumb,
                    'slug': (item.get('slug') or '').strip(),
                    'url': (item.get('url') or '').strip(),
                })
    return out


def _slice_array(text, start):
    """Devolve o fragmento JSON do array que começa na posição `start`."""
    depth = 0
    in_str = False
    escaped = False
    quote = ''
    i = start
    while i < len(text):
        c = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == quote:
                in_str = False
        else:
            if c in ('"', "'"):
                in_str = True
                quote = c
            elif c == '[':
                depth += 1
            elif c == ']':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return None


def fetch_site_catalog(origin):
    """Baixa as páginas de catálogo de um site e devolve {titulo: url_da_imagem}."""
    origin = origin.rstrip('/')
    result = {}
    for path in CATALOG_PATHS:
        url = origin + path
        try:
            html = fetch_text(url)
        except Exception as exc:
            logger.warning('catalog_images: falha ao ler %s: %s', url, exc)
            continue
        for entry in extract_services(html):
            thumb = entry['thumbnail']
            if thumb.startswith('http://') or thumb.startswith('https://'):
                full = thumb
            elif thumb.startswith('//'):
                full = 'https:' + thumb
            elif thumb.startswith('/'):
                full = origin + thumb
            else:
                full = origin + '/' + thumb
            result.setdefault(entry['title'], full)
    return result


def _catalog_index(catalog):
    """Pré-computa tokens, duração e tipo de cada item do catálogo para casar rápido."""
    index = []
    for title, url in catalog.items():
        index.append({
            'title': title,
            'url': url,
            'tokens': provider_api._brand_tokens(title),
            'dur': provider_api._detect_duration(title),
            'kind': provider_api._detect_kind(title),
        })
    return index


def find_image_match(title, catalog, index=None):
    """Encontra a imagem do catálogo para o serviço local, usando o mesmo critério
    de correspondência da vinculação automática. Retorna (url, score) ou (None, 0.0)."""
    if index is None:
        index = _catalog_index(catalog)
    brand = provider_api._brand_tokens(title)
    if not brand:
        return None, 0.0
    dur = provider_api._detect_duration(title)
    kind = provider_api._detect_kind(title)
    best = None
    best_score = 0.0
    best_overlap = 0.0
    best_cand = None
    for cand in index:
        overlap = provider_api._brand_overlap(brand, cand['tokens'])
        if overlap < 0.34:
            continue
        rdur = cand['dur']
        rkind = cand['kind']
        score = overlap * 2.0
        if dur and rdur:
            score += 1.0 if dur == rdur else -0.8
        if kind and rkind:
            score += 0.8 if kind == rkind else -0.6
        if score > best_score:
            best_score = score
            best_overlap = overlap
            best = cand['url']
            best_cand = cand
    if best is None:
        return None, 0.0
    if not provider_api._match_acceptable(
            best_score, best_overlap, dur, best_cand['dur'], kind,
            best_cand['kind'], title, best_cand['title']):
        return None, 0.0
    return best, best_score


def download_thumbnail(service, url):
    """Baixa a imagem e salva em MEDIA_ROOT/thumbnails. Retorna True/False."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': USER_AGENT,
            'Accept-Encoding': 'identity',
        })
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            raw = resp.read(MAX_IMAGE_BYTES + 1)
            ctype = (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
    except Exception as exc:
        logger.warning('catalog_images: falha ao baixar %s: %s', url, exc)
        return False
    if len(raw) > MAX_IMAGE_BYTES or not ctype.startswith('image/'):
        logger.warning('catalog_images: imagem invalida em %s (tipo=%s tamanho=%s)', url, ctype, len(raw))
        return False
    ext = mimetypes.guess_extension(ctype) or ''
    if ext in ('.jpe',) or ext not in ALLOWED_EXTS:
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()[:5]
        if ext not in ALLOWED_EXTS:
            ext = '.jpg'
    folder = Path(settings.MEDIA_ROOT) / 'thumbnails'
    folder.mkdir(parents=True, exist_ok=True)
    name = '{}-{}'.format(service.slug or 'service', uuid.uuid4().hex[:8]) + ext
    with open(str(folder / name), 'wb') as dest:
        dest.write(raw)
    service.thumbnail = '{}thumbnails/{}'.format(settings.MEDIA_URL, name)
    service.save(update_fields=['thumbnail'])
    return True


def scan_and_fill(overwrite=False):
    """Varre os sites GSM Theme e preenche a thumbnail dos serviços locais sem imagem.
    Retorna um dict com as estatísticas da varredura."""
    stats = {
        'sites_ok': [], 'sites_fail': [], 'catalog_items': 0,
        'matched': 0, 'downloaded': 0, 'failed': 0, 'total': 0,
    }
    catalog = {}
    for origin in configured_sites():
        found = fetch_site_catalog(origin)
        if found:
            stats['sites_ok'].append(origin)
            catalog.update(found)
        else:
            stats['sites_fail'].append(origin)
    stats['catalog_items'] = len(catalog)
    if not catalog:
        return stats
    qs = ServiceList.objects.filter(Q(thumbnail__isnull=True) | Q(thumbnail=''))
    if overwrite:
        qs = ServiceList.objects.all()
    services = list(qs.only('id', 'title', 'slug', 'thumbnail'))
    stats['total'] = len(services)
    index = _catalog_index(catalog)
    for service in services:
        url, _score = find_image_match(service.title, catalog, index=index)
        if not url:
            continue
        stats['matched'] += 1
        if download_thumbnail(service, url):
            stats['downloaded'] += 1
        else:
            stats['failed'] += 1
    return stats