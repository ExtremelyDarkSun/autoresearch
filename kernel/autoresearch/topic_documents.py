# GENERATED-BY: portable topic scaffold synchronization
# LAST-EDIT: 2026-11-13 加只读专题文档检查：专题目录直接层的 DESIGN_* 判红，archive/ 历史件不追溯
# PURPOSE: Create three neutral research skeletons from versioned templates without overwriting research,
#          and expose a read-only check for the waiting-book rule (no DESIGN_* beside research documents).
# ORIGIN: upstream topic_scaffold ensure/materials behavior; upstream check_one DESIGN_* rejection
# CUMULATIVE-DELTA: Default TOPIC, dated EVIDENCE and STATUS; explicit skeleton and template hashes, safe backfill.
#   2026-11-13 新增 design_documents() / archived_design_documents() / topic_document_problems()：
#   等待期设计只登记 AutoDL实验/实验分析/WAITING.md；专题直接层的 DESIGN_* 判红，子目录（含 archive/）
#   里的历史件只回执不判红；检查只读（不建目录、不写文件、不改字节）。
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re

TEMPLATES = {'TOPIC': 'topic.template.md', 'EVIDENCE': 'evidence.template.md',
             'STATUS': 'status_page.template.md'}

#: 等待簿：全项目挂起事项的唯一落点（等审批 / 等读数 / 等机器）。与项目模板同路径。
WAITING_BOOK = 'AutoDL实验/实验分析/WAITING.md'
#: 专题目录**直接层**不许出现的等待期设计前缀；`archive/` 里的历史件不追溯。
DESIGN_PREFIX = 'DESIGN_'
#: 专题目录里的就地归档子目录（归档跟着原件走，不集中到一个全项目大归档堆）。
ARCHIVE_DIRNAME = 'archive'


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


def design_documents(directory):
    """专题目录**直接层**的 `DESIGN_*` 条目；子目录（含 `archive/`）内部的件不算。

    按**名字**判，不跟随符号链接、也不看类型：平铺的设计文件与同名目录都算第二落点
    （只查文件会留下「把设计塞进 DESIGN_x/ 目录」这条绕过）。判定只读目录项，不读正文。
    """
    target = Path(directory)
    if not target.is_dir():
        return []
    return sorted(path.name for path in target.iterdir()
                  if path.name.startswith(DESIGN_PREFIX))


def archived_design_documents(directory):
    """专题 `archive/` 里的 `DESIGN_*` 历史件（相对 archive 的 POSIX 路径）。

    ⚠️ 只用于回执与查历史，**不判红**：退役件就该躺在这里，不追溯、不要求改名。
    """
    archive = Path(directory) / ARCHIVE_DIRNAME
    if not archive.is_dir():
        return []
    return sorted(path.relative_to(archive).as_posix() for path in archive.rglob(DESIGN_PREFIX + '*')
                  if path.is_file() and not path.is_symlink())


def topic_document_problems(directory):
    """只读检查专题目录层的文档纪律：直接层不许有 `DESIGN_*` 等待期设计。

    规则来源（`project/DOC_DESIGN.md` §5）：等审批 / 等读数 / 等机器的事只登记
    `AutoDL实验/实验分析/WAITING.md`；专题直接层出现 `DESIGN_*` ⇒ 判红。
    `archive/` 与其它子目录里的历史件不追溯（只在 `archived_design` 里回执）。

    ⛔ 只读：不建目录、不写文件、不加锁；不存在 `directory` 时返回 `ok=False`，
    但同样不创建它。判定只看文件名字与「是不是直接层」，不读正文。
    """
    target = Path(directory)
    result = {'directory': str(target), 'read_only': True, 'waiting_book': WAITING_BOOK,
              'design': [], 'archived_design': [], 'problems': [], 'ok': False,
              'scope': '只判专题目录直接层的 DESIGN_*；子目录与 archive/ 里的历史件不追溯'}
    if not target.is_dir():
        result['problems'].append('专题目录不存在：' + str(target))
        return result
    design = design_documents(target)
    result['design'] = design
    result['archived_design'] = archived_design_documents(target)
    if design:
        result['problems'].append(
            '专题目录直接层不许出现 `DESIGN_*` 等待期设计：挂起的事只登记 '
            + WAITING_BOOK + '；设计正文写进 TOPIC.md 或 plans/<实验号>.json，'
            '退役件进 ' + ARCHIVE_DIRNAME + '/。现存：' + '、'.join(design))
    result['ok'] = not result['problems']
    return result
