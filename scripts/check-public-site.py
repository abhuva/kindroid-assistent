"""Fail-closed publication boundary. Reports rule/path only, never matched values."""
from __future__ import annotations

import argparse
import re
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

SECRET = re.compile(r'\b(?:kn_[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
PRIVATE = re.compile(r'kindroid-backup-data|(?:^|[\s"\x27(=/])(?:var/|private-notes/|drafts/)|(?<![A-Za-z0-9])[A-Za-z]:[\\/]|(?:storage|api)\.kindroid\.ai|\b(?:entity_id|chunk_id|sender_type|attachment_urls)\s*["\x27]?\s*:', re.I)
INCLUDE = re.compile(r'--8<--|!include\b|@include\b|\{%\s*(?:include|import)|\bdraft:\s*true\b', re.I)
ASSET = re.compile(r'(?:src|href)=["\x27]([^"\x27]+)["\x27]', re.I)
LINK = re.compile(r'!?\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)')


class PublicationError(ValueError):
    pass


def inside(root, path):
    if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root.parent):
        raise PublicationError('Symlinks are not permitted in publication sources')
    if not path.resolve().is_relative_to(root.resolve()):
        raise PublicationError('Publication path escapes its source directory')


def public_files(root):
    root = Path(root).resolve()
    manifest = root/'public-site-files.txt'
    inside(root, manifest)
    names = [line.strip() for line in manifest.read_text('utf-8-sig').splitlines() if line.strip() and not line.startswith('#')]
    if len(names) != len(set(names)):
        raise PublicationError('Duplicate publication manifest entries')
    for name in names:
        if '\\' in name or Path(name).is_absolute() or '..' in Path(name).parts:
            raise PublicationError('Unsafe publication manifest entry')
        # The explicit tooling list cannot be used to include raw data or secrets.
        if name not in {'zensical.toml','requirements-docs.txt','public-site-files.txt','PUBLICATION.md','.gitignore',
                        'Preview Research Notes.bat','New Research Note.bat','.github/workflows/research-site.yml',
                        'scripts/research-site.ps1','scripts/check-public-site.py','scripts/test-public-site.py','scripts/serve-public-site.py'}:
            raise PublicationError('Unexpected file in publication manifest')
        inside(root, root/name)
        if not (root/name).is_file(): raise PublicationError('Missing publication source: '+name)
    docs = root/'public-docs'
    inside(root, docs)
    if not (docs/'index.md').is_file(): raise PublicationError('Public homepage is missing')
    for path in sorted(docs.rglob('*')):
        inside(docs, path)
        if path.is_dir(): continue
        relative = path.relative_to(docs)
        if any(part.startswith('.') or part.lower() in {'drafts','private','exports','backups'} for part in relative.parts):
            raise PublicationError('Hidden/private publication path is forbidden')
        if path.suffix not in {'.md','.css','.svg'}:
            raise PublicationError('Unsupported public asset type: '+path.suffix)
        if path.stat().st_size>1_000_000: raise PublicationError('Public source exceeds size limit')
        names.append(path.relative_to(root).as_posix())
    return names


def check_sources(root, publication_tree=False):
    root = Path(root).resolve()
    names = public_files(root)
    config = tomllib.loads((root/'zensical.toml').read_text('utf-8-sig'))['project']
    if config.get('docs_dir') != 'public-docs' or config.get('site_dir') != 'site':
        raise PublicationError('Build input/output directories must remain fixed')
    allowed = {'site_name','site_description','site_url','site_author','repo_url','repo_name','edit_uri','docs_dir','site_dir','dev_addr','nav','extra_css','theme'}
    if set(config) - allowed or config.get('theme', {}).get('font') is not False:
        raise PublicationError('Unreviewed build extensions or remote fonts are forbidden')
    for name in names:
        data = (root/name).read_text('utf-8-sig')
        if SECRET.search(data): raise PublicationError('Credential-like content rejected: '+name)
        if name.startswith('public-docs/'):
            if PRIVATE.search(data) or INCLUDE.search(data):
                raise PublicationError('Private data, local paths, includes or drafts rejected: '+name)
            if re.search(r'<(?:script|iframe|object|embed|foreignObject)\b|\bdata:',data,re.I):
                raise PublicationError('Embedded or encoded content requires separate review: '+name)
            for target in LINK.findall(data) + ASSET.findall(data):
                target = unquote(target.strip('<>'))
                parsed = urlsplit(target)
                if parsed.scheme and parsed.scheme != 'https':
                    raise PublicationError('Non-HTTPS link rejected: '+name)
                if not parsed.scheme and parsed.path:
                    path = (root/name).parent/parsed.path
                    inside(root/'public-docs', path)
                    if not path.exists(): raise PublicationError('Broken publication link: '+name)
    if publication_tree:
        tracked = subprocess.run(['git','ls-files','-z'],cwd=root,check=True,capture_output=True).stdout.decode().split('\0')
        if set(filter(None,tracked)) != set(names):
            raise PublicationError('Publication branch must contain exactly the reviewed file set')
    return names


def check_built(root):
    site = Path(root).resolve()/'site'
    if not (site/'index.html').is_file(): raise PublicationError('Built site is missing')
    count = 0
    for path in site.rglob('*'):
        inside(site,path)
        if path.is_dir(): continue
        if path.suffix.lower() in {'.jsonl','.db','.sqlite','.sqlite3','.env','.md','.zip','.gguf','.log'}:
            raise PublicationError('Raw data or source artifact in generated site')
        count += 1
        try: data = path.read_text('utf-8')
        except UnicodeError: continue
        if SECRET.search(data) or PRIVATE.search(data):
            raise PublicationError('Private-content rule rejected generated artifact')
        if path.suffix == '.html':
            for asset in re.findall(r'<(?:script|img)[^>]+src=["\x27]([^"\x27]+)',data,re.I):
                if urlsplit(asset).scheme or asset.startswith('//'):
                    raise PublicationError('Remote script/image in generated HTML')
    return count


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--built',action='store_true')
    parser.add_argument('--list',action='store_true')
    parser.add_argument('--publication-tree',action='store_true')
    args=parser.parse_args()
    try:
        names=check_sources(args.root,args.publication_tree)
        if args.built: print(f'Generated artifact check passed: {check_built(args.root)} files')
        if args.list: print('\n'.join(names))
        else: print(f'Public source check passed: {len(names)} reviewed files')
    except PublicationError as error:
        raise SystemExit('Public-site check failed: '+str(error)+'. No content was uploaded.') from None
    except (OSError,ValueError):
        # The detailed rules are available to tests; CLI never echoes input text.
        raise SystemExit('Public-site check failed. Review source paths, links, configuration and private-content rules; no content was uploaded.') from None


if __name__=='__main__': main()
