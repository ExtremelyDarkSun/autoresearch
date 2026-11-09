# GENERATED-BY: portable extraction / repository bootstrap, 2026-11-09 +08:00
# LAST-EDIT: portable framework synchronization, 2026-11-09
# PURPOSE: Portable project entrypoint or verification.
# ORIGIN: none
# CUMULATIVE-DELTA: 2026-11-09 同步研究判据、三态审计、小修、完整读取及研究报告规则。
"""Complete instruction bundles: measure first, then verify captured host input."""
from __future__ import annotations
import hashlib
import re
from pathlib import Path
from .profiles import load_profile, validate_profile, required_instructions


def instruction_manifest(repo_root, profile_path):
    root = Path(repo_root).resolve()
    profile = load_profile(profile_path)
    errors = validate_profile(profile)
    if errors:
        raise ValueError('; '.join(errors))
    paths = [root / 'AGENTS.md']
    paths += sorted((root / 'kernel' / 'instructions').glob('*.md'))
    paths += sorted((root / 'kernel' / 'contracts').glob('*.md'))
    import json
    reading_path = root / 'kernel' / 'contracts' / 'reading_required.json'
    reading = json.loads(reading_path.read_text(encoding='utf-8-sig'))
    paths.append(reading_path)
    for entry in reading.get('entries', []):
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) or not entry['path'].strip():
            raise ValueError('reading_required entry must contain a nonempty path')
        if entry.get('scope', 'full_document') != 'full_document':
            raise ValueError('current required documents must use full_document scope')
        candidate = (root / entry['path']).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError('kernel reading path escapes the repository')
        content = candidate.read_text(encoding='utf-8-sig')
        if entry.get('anchor') and entry['anchor'] not in content:
            raise ValueError('missing reading anchor: ' + entry['path'] + ' ' + entry['anchor'])
        paths.append(candidate)
    for relative in profile.get('instructions', {}).get('required', []):
        paths.append(Path(profile['_profile_dir']) / relative)
    unique, documents = set(), []
    for candidate in paths:
        path = candidate.resolve()
        if path in unique:
            continue
        unique.add(path)
        raw = path.read_bytes()
        text = raw.decode('utf-8-sig')
        label = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
        documents.append({'source': label, 'sha256': hashlib.sha256(raw).hexdigest(),
                          'bytes': len(raw), 'utf16_chars': len(text.encode('utf-16-le')) // 2,
                          'content': text})
    total = sum(d['bytes'] for d in documents)
    result = {'format': 1, 'profile': profile['id'], 'documents': documents,
              'source_bytes': total, 'largest_source_bytes': max((d['bytes'] for d in documents), default=0)}
    bundle = render_bundle(result)
    result['bundle_bytes'] = len(bundle.encode('utf-8'))
    result['suggested_max_bytes'] = result['bundle_bytes'] + max(16384, result['bundle_bytes'] // 4)
    result['note'] = 'This is a measured bundle plus headroom; verify actual host input after installing it.'
    return result


def render_bundle(manifest):
    return '\n\n'.join(f"<!-- REQUIRED-SOURCE: {d['source']} SHA256: {d['sha256']} -->\n{d['content']}" for d in manifest['documents']) + '\n'


def public_manifest(manifest):
    return {k: ([{a: b for a, b in d.items() if a != 'content'} for d in v] if k == 'documents' else v)
            for k, v in manifest.items()}


def verify_injection(manifest, captured):
    text = captured.decode('utf-8-sig') if isinstance(captured, bytes) else captured
    text = text.replace('\r\n', '\n')
    missing = [d['source'] for d in manifest['documents'] if d['content'].replace('\r\n', '\n').strip() not in text]
    omission_lines = re.findall(r'(?im)^Workspace instruction budget[^\n]*\bomitted\b[^\n]*', text)
    return {'complete': not missing and not omission_lines, 'missing': missing,
            'omission_lines': omission_lines, 'checked_sources': len(manifest['documents']),
            'scope': 'Checks the supplied captured text only; does not authenticate its host provenance.'}
