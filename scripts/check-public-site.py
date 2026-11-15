"""Fail-closed publication boundary. Reports rule/path only, never matched values."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
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


DEPLOY_TOOLS = {'.github/workflows/research-site.yml', 'scripts/check-public-site.py'}


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
                        'scripts/research-site.ps1','scripts/check-public-site.py','scripts/test-public-site.py','scripts/serve-public-site.py','scripts/check-documentation.py'}:
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


def check_sources(root):
    root = Path(root).resolve()
    names = public_files(root)
    config = tomllib.loads((root/'zensical.toml').read_text('utf-8-sig'))['project']
    if config.get('docs_dir') != 'public-docs' or config.get('site_dir') != 'site':
        raise PublicationError('Build input/output directories must remain fixed')
    allowed = {'site_name','site_description','site_url','site_author','repo_url','repo_name','edit_uri','docs_dir','site_dir','dev_addr','nav','extra_css','theme'}
    if set(config) - allowed or config.get('theme', {}).get('font') is not False:
        raise PublicationError('Unreviewed build extensions or remote fonts are forbidden')
    if config.get('edit_uri') or 'content.action.edit' in config.get('theme', {}).get('features', []):
        raise PublicationError('Author pages locally; deployment must not expose a second editing location')
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


def source_digest(root):
    root = Path(root).resolve()
    digest = hashlib.sha256()
    for name in sorted(check_sources(root)):
        digest.update(name.encode() + b'\0' + (root/name).read_bytes() + b'\0')
    return digest.hexdigest()


def stage_deployment(root, destination):
    """Export generated output only, into a checked isolated publication checkout."""
    root, destination = Path(root).resolve(), Path(destination).resolve()
    allowed = root/'var/research-site'
    if not destination.is_relative_to(allowed) or not destination.name.startswith('publish-'):
        raise PublicationError('Deployment staging must stay in an isolated publication checkout')
    digest = source_digest(root)
    marker = allowed/'build-source.sha256'
    if not marker.is_file() or marker.read_text('ascii').strip() != digest:
        raise PublicationError('Authoring sources changed since the checked build; build again')
    check_built(root)
    destination.mkdir(parents=True, exist_ok=True)
    names = sorted(p.relative_to(root).as_posix() for p in (root/'site').rglob('*') if p.is_file())
    names += sorted(DEPLOY_TOOLS)
    files = {}
    for name in names:
        source, target = root/name, destination/name
        inside(root, source); inside(destination, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        files[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    revision = subprocess.run(['git','rev-parse','HEAD'],cwd=root,capture_output=True,text=True)
    source_revision = revision.stdout.strip() if revision.returncode == 0 else 'working-tree'
    manifest = {'version':1,'authoring':'public-docs/ in the authoring repository',
                'source_revision':source_revision,'source_sha256':digest,'files':files}
    (destination/'deployment.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    check_deployment(destination)
    return [*names, 'deployment.json']


def check_deployment(root, tracked=False):
    root = Path(root).resolve()
    manifest_path = root/'deployment.json'; inside(root, manifest_path)
    data = json.loads(manifest_path.read_text('utf-8'))
    if not isinstance(data,dict):
        raise PublicationError('Invalid generated deployment manifest')
    files = data.get('files')
    if data.get('version') != 1 or not isinstance(files,dict) or not DEPLOY_TOOLS <= files.keys():
        raise PublicationError('Invalid generated deployment manifest')
    if not re.fullmatch('[a-f0-9]{64}',str(data.get('source_sha256',''))):
        raise PublicationError('Missing authoring source fingerprint')
    expected = set(files) | {'deployment.json'}
    if len(expected) > 30000:
        raise PublicationError('Generated deployment exceeds its file limit')
    for name, digest in files.items():
        if not isinstance(name,str) or '\\' in name or Path(name).is_absolute() or '..' in Path(name).parts:
            raise PublicationError('Unsafe generated deployment path')
        if name not in DEPLOY_TOOLS and not name.startswith('site/'):
            raise PublicationError('Authoring sources are forbidden in deployment output')
        path = root/name; inside(root,path)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise PublicationError('Generated output changed; rebuild from the authoring repository')
    if tracked:
        actual = set(filter(None,subprocess.run(['git','ls-files','-z'],cwd=root,check=True,capture_output=True).stdout.decode().split('\0')))
    else:
        actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and '.git' not in p.relative_to(root).parts}
    if actual != expected:
        raise PublicationError('Deployment must contain exactly the checked output and deployment tooling')
    check_built(root)
    return len(files)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--built',action='store_true')
    parser.add_argument('--list',action='store_true')
    parser.add_argument('--source-digest',action='store_true')
    parser.add_argument('--stage-deployment',type=Path)
    parser.add_argument('--deployment-tree',action='store_true')
    args=parser.parse_args()
    try:
        if args.deployment_tree:
            print(f'Generated deployment check passed: {check_deployment(args.root,tracked=True)} files')
            return
        if args.stage_deployment:
            print('\n'.join(stage_deployment(args.root,args.stage_deployment)))
            return
        if args.source_digest:
            print(source_digest(args.root))
            return
        names=check_sources(args.root)
        if args.built: print(f'Generated artifact check passed: {check_built(args.root)} files')
        if args.list: print('\n'.join(names))
        else: print(f'Public source check passed: {len(names)} reviewed files')
    except PublicationError as error:
        raise SystemExit('Public-site check failed: '+str(error)+'. No content was uploaded.') from None
    except (OSError,ValueError):
        # The detailed rules are available to tests; CLI never echoes input text.
        raise SystemExit('Public-site check failed. Review source paths, links, configuration and private-content rules; no content was uploaded.') from None


if __name__=='__main__': main()
