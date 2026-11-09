# GENERATED-BY: portable topic scaffold synchronization
# LAST-EDIT: 2026-11-09
# PURPOSE: Create three neutral research skeletons from versioned templates without overwriting research.
# ORIGIN: upstream topic_scaffold ensure/materials behavior
# CUMULATIVE-DELTA: Default TOPIC, dated EVIDENCE and STATUS; explicit skeleton and template hashes, safe backfill.
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re

TEMPLATES = {'TOPIC': 'topic.template.md', 'EVIDENCE': 'evidence.template.md',
             'STATUS': 'status_page.template.md'}


def ensure_topic_documents(directory, *, topic, objective=None, today=None, template_root=None):
    """Create missing document kinds only. Skeletons never certify a report or absence of evidence."""
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}', topic):
        raise ValueError('专题标识只能含字母、数字、下划线和短横线')
    day = today or datetime.now(timezone.utc).date().isoformat()
    datetime.strptime(day, '%Y-%m-%d')
    target = Path(directory).absolute()
    if target.is_symlink() or any(p.is_symlink() for p in target.parents):
        raise ValueError('专题文档目录不接受符号链接')
    templates = Path(template_root) if template_root is not None else Path(__file__).resolve().parents[1] / 'templates'
    existing = sorted(p.name for p in target.iterdir() if p.is_file()) if target.is_dir() else []
    rendered, skipped = [], []
    for kind, name in TEMPLATES.items():
        filename = f'EVIDENCE_{day}.md' if kind == 'EVIDENCE' else kind + '.md'
        covered = [n for n in existing if n.startswith('EVIDENCE_') and n.endswith('.md')] if kind == 'EVIDENCE' else [n for n in existing if n == filename]
        path = target / filename
        if path.is_symlink():
            raise ValueError('文档落点不接受符号链接：' + filename)
        if covered:
            skipped.extend(covered)
            continue
        raw = (templates / name).read_bytes()
        text = raw.decode('utf-8').replace('<专题名称>', topic).replace('<专题>', topic)
        end = text.find('-->')
        if end < 0:
            raise ValueError('模板缺文件头：' + name)
        stamp = f'\n> SCAFFOLD: 待填写骨架，尚未经过研究报告验收。生成日期：{day}。\n'
        if existing:
            stamp += '> 本页为补建骨架。空表仅表示尚未整理，不表示没有历史数据。已有文件：' + '、'.join(existing) + '。\n'
        text = text[:end+3] + stamp + text[end+3:]
        if kind == 'TOPIC' and objective is not None:
            text = text.replace('<一个由用户确定的研究目标。写明当前问题和希望得到的结果，不预设平台或分数。>', objective)
        rendered.append((path, text, name, hashlib.sha256(raw).hexdigest()))
    target.mkdir(parents=True, exist_ok=True)
    created, receipts = [], []
    for path, text, name, source_sha in rendered:
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
        created.append(path.name)
        receipts.append({'path': path.name, 'template': name, 'template_sha256': source_sha,
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'skeleton': True})
    return {'created': created, 'skipped': sorted(set(skipped)), 'documents': receipts,
            'report_accepted': False}
