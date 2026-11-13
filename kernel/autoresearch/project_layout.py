# GENERATED-BY: project layout installation, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-13 +08:00
# PURPOSE: 从仓库内 project 模板确定性地安装项目目录、重写 Markdown 链接、返回回执并只读校验结果。
# ORIGIN: 用户要求（模板安装、@FRAMEWORK@ 替换、manifest 回执、只读 validate_layout）
# CUMULATIVE-DELTA: 2026-11-13 安装时将project跨专题必读资料改写为工作区内相对路径，仅映射安装清单已有文件。 2026-11-09 新分层工作区写入固定中文布局标记，旧无标记工作区保持旧路径；安装templates文档骨架并纳入来源回执；机器可读清单契约是模板的 context_routes.json。
"""项目目录模板的确定性安装与只读校验。

四条边界（改本模块前先读）：

1. **只安装到全新目录。** 目标必须不存在；本模块不覆盖、不合并、不删除任何现有文件，
   也不把目标放在框架仓库内部（仓库内演示只允许 ``.local`` 下的新目录）。所有校验与
   内容渲染都在建目录之前完成，校验失败时目标不会被创建。
2. **模板、kernel、profile 只读。** ``@FRAMEWORK@`` 只在目标目录内的副本里替换，从不
   写回模板；本模块不联网、不启动评测、不碰宿主，也不创建任何监控启用文件。生成的
   SQLite 是真正的空 Store/Governance/Topics/Runtime，与 ``init`` 一致。
3. **回执用相对标签。** 清单里的 ``path`` 相对目标根，``source`` 相对框架仓库根；
   私有绝对源路径不进公共源码。能映射进工作区的链接一律写成工作区相对路径；只有
   无法映射的框架资料（例如 ``kernel`` 源码或 ``docs``）才用框架仓库绝对路径，且校验时
   单独记为“外部引用”，不计入工作区本地链接。``framework_root`` 是运行时测量值，只落在
   私有工作区的 ``.autoresearch/layout-manifest.json`` 里，不写进模块常量或测试。
4. **``validate_layout`` 只读。** 只读文件，不写、不改、不建 sidecar；只检查 Markdown
   本地链接、绝对路径、组织路由清单与阅读登记。跨到框架仓库（含 ``kernel`` 目录的祖先）
   的链接允许存在，但必须真的能打开且只记外部引用；工作区内部的链接必须落在工作区里。

机器可读清单的契约就是模板里的 ``context_routes.json``，字段见 ``validate_layout``。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from .profiles import load_profile, validate_profile

#: 目标内运行时状态目录（与 ``init`` 一致）。
STATE_DIRNAME = '.autoresearch'
#: 安装回执文件名（相对 ``STATE_DIRNAME``）。
MANIFEST_NAME = 'layout-manifest.json'
#: 模板目录候选（相对框架仓库根）；第一个存在的胜出。
TEMPLATE_CANDIDATES = ('project', 'repo/project')
#: 框架文档落点（AutoDL 实验目录下的框架子目录）。
FRAMEWORK_TOP = 'AutoDL实验'
FRAMEWORK_DOC_DIR = '框架'
#: 从 ``kernel/`` 复制到框架落点的子树。
FRAMEWORK_TREES = ('contracts', 'instructions', 'roles', 'templates')
#: 平台配置落点。
PLATFORM_TOP = 'Kaggle平台'
PLATFORM_PROFILE_DIR = 'config/agent-harness'
#: 模板里的框架路径占位符。
FRAMEWORK_PLACEHOLDER = '@FRAMEWORK@'
#: 组织路由清单：``routes[].entry`` / ``routes[].required`` 相对所在项目根，安装后改写为
#: 工作区相对路径；``full_document`` 必须为 true，``history_is_current_instruction`` 必须为 false。
CONTEXT_ROUTES_NAME = 'context_routes.json'
#: 阅读登记清单：``entries[].path`` 原为 ``kernel/...``，安装后改写为框架目录内的实际相对路径。
READING_PLAN_NAME = 'reading_required.json'
#: 模板没有自带 .gitignore 时使用的默认内容（与 ``init`` 一致）。
DEFAULT_GITIGNORE = '.autoresearch/\nprivate/\ndata/\nresults/\n.env\n'
#: 不复制：版本库、缓存与运行时状态目录。
EXCLUDED_DIR_NAMES = frozenset({
    '.git', '.hg', '.svn', '__pycache__', '.pytest_cache', '.mypy_cache',
    '.ruff_cache', '.ipynb_checkpoints', 'node_modules', '.venv', 'venv',
    STATE_DIRNAME,
})
#: 不复制：系统垃圾文件。
EXCLUDED_FILE_NAMES = frozenset({'.DS_Store', 'Thumbs.db'})
#: 不复制：字节码、数据库与 SQLite sidecar。
EXCLUDED_FILE_SUFFIXES = ('.pyc', '.pyo', '.sqlite', '.sqlite3', '.db', '.sqlite-wal', '.sqlite-shm')
#: 按 Markdown 处理链接的扩展名。
MARKDOWN_SUFFIXES = ('.md', '.markdown')
#: 生成项目必须有这些根入口文件；空目录或残缺项目不能算通过。
GENERATED_ROOT_DOCUMENTS = ('AGENTS.md', 'RULES.md', 'DOC_MAINTENANCE.md')
#: 错误条数上限：报告要能被人读完，同时明确标出被截断。
MAX_LINK_ERRORS = 100

#: 行内 Markdown 链接；``!`` 前缀（图片）同样按本地链接处理。
_LINK = re.compile(
    r'(?P<prefix>!?\[)(?P<label>[^\[\]\n]*)(?P<middle>\]\(\s*)'
    r'(?P<target><[^<>\n]*>|[^()\s]*)'
    r'(?P<tail>\s*(?:"[^"\n]*")?\s*\))'
)
#: 带 scheme 的目标不是本地链接（``https:`` / ``mailto:`` / Windows ``C:``）。
_SCHEME = re.compile(r'^[A-Za-z][A-Za-z0-9+.\-]*:')


# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------


def initialize_layout(repo_root, destination, profile_path=None, *, template=None):
    """把 ``project`` 模板安装到全新目录 ``destination``，返回回执清单。

    参数
    ----
    repo_root : 框架仓库根（提供 ``kernel/``、``profiles/`` 与模板目录）。
    destination : 必须不存在的目标目录；仓库内只允许 ``.local`` 下的新目录。
    profile_path : ``profile.toml`` 或其目录；缺省用 ``profiles/agent-harness``。
    template : 可选，模板目录（绝对路径，或相对框架仓库根）；缺省按
        ``TEMPLATE_CANDIDATES`` 探测。

    复制与改写：

    * 复制模板里的全部常规文件，保留模板自带的 ``.gitignore``；跳过符号链接、缓存与状态
      （``.git``、``__pycache__``、SQLite 等）。模板没有 ``.gitignore`` 时才写默认内容。
    * ``kernel/contracts``、``kernel/instructions``、``kernel/roles``、``kernel/templates`` 复制到
      ``AutoDL实验/框架/`` 下的同名目录；所选 profile 复制到
      ``Kaggle平台/config/agent-harness``；profile 里的符号链接必须留在 profile 目录内。
    * Markdown 本地链接按目标位置重写：模板指向 ``../kernel/<tree>/...`` 的链接落到框架副本，
      指向 ``../profiles/<所选 profile>/...`` 的落到本地 config profile，跨 ``kernel`` 子目录的
      也落到对应本地目录；其余仍在框架仓库里的资料改用框架仓库绝对路径（工作区不自称拥有）。
    * ``context_routes.json`` 的 ``entry``/``required`` 改写为工作区相对路径；
      ``reading_required.json`` 的 ``kernel/<tree>/...`` 改写为框架目录内的实际相对路径。
    * ``@FRAMEWORK@`` 只在副本里替换为框架仓库绝对路径（POSIX 形式，避免混用分隔符），
      从不写回模板。
    * 建立与 ``init`` 一致的空 Store/Governance/Topics/Runtime；不创建监控启用文件。

    返回的 dict 同时写入 ``<destination>/.autoresearch/layout-manifest.json``：
    ``files`` 是逐文件回执（目标相对路径 + sha256 + bytes），``root_measurement`` 与
    ``entry_measurement`` 是根入口与各 route entry/required 的实测字节，``host_activation``
    明确说明宿主加载未被检查。实测字节不等于首轮注入，也不构成加载证明。
    """
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise ValueError('框架仓库根目录不存在：' + str(root))
    dest = _free_destination(root, destination)
    template_dir = template_directory(root, template)
    profile = load_profile(profile_path if profile_path is not None else root / 'profiles' / 'agent-harness')
    problems = validate_profile(profile)
    if problems:
        raise ValueError('profile 不可用：' + '；'.join(problems))
    profile_dir = Path(profile['_profile_dir']).resolve()
    profile_rel = _relative_posix(profile_dir, root)
    names = {'framework': _pick_top_level(template_dir, 'AutoDL', FRAMEWORK_TOP),
             'platform': _pick_top_level(template_dir, 'Kaggle', PLATFORM_TOP)}

    plan = _plan(root, template_dir, profile_dir, profile_rel, names, dest)
    # 先读完、渲染完所有内容，再建目录：源侧任何问题都不会留下半个工作区。
    rendered = [(item, _render(item, dest, plan['planned'])) for item in plan['files']]
    dest.mkdir(parents=True, exist_ok=False)
    state = dest / STATE_DIRNAME
    state.mkdir()
    files = []
    for item, (data, source_sha, info) in rendered:
        _write_new(dest / Path(item['target']), data)
        digest = hashlib.sha256(data).hexdigest()
        entry = {'path': item['target'], 'origin': item['origin'], 'source': item['source_label'],
                 'sha256': digest, 'bytes': len(data), 'source_sha256': source_sha,
                 'rewritten': source_sha is not None and digest != source_sha,
                 'links_rewritten': info['links'], 'framework_references': info['framework_references'],
                 'json_paths_rewritten': info['json_paths']}
        if info['placeholder']:
            entry['framework_placeholder_replaced'] = info['placeholder']
        files.append(entry)
    from .workspace_layout import write_layout_marker
    workspace_layout = write_layout_marker(dest)
    store_state = _initialize_state(state)
    measurement = _measure_root(dest)
    entries = _measure_entries(dest)
    manifest = {
        'format': 1,
        'profile': profile['id'],
        'template': _relative_posix(template_dir, root) or str(template_dir),
        'framework_root': str(root),
        'files': files,
        'skipped': plan['skipped'],
        'state': store_state,
        'workspace_layout': workspace_layout,
        'root_measurement': measurement,
        'entry_measurement': entries,
        'measurement': {
            'files': len(files),
            'bytes': sum(entry['bytes'] for entry in files),
            'rewritten': sum(1 for entry in files if entry['rewritten']),
            'links_rewritten': sum(entry['links_rewritten'] for entry in files),
            'framework_references': sum(entry['framework_references'] for entry in files),
            'json_paths_rewritten': sum(entry['json_paths_rewritten'] for entry in files),
            'entry_bytes': entries['entry_bytes'],
            'required_bytes': entries['required_bytes'],
            'note': '逐文件回执与入口实测字节；不把整包材料称首轮注入。',
        },
        'host_activation': '未检查当前宿主；生成文件存在不等于宿主已加载，也不表示规则已注入。',
        'external_actions': False,
        'note': '回执只描述本模块实际写下的字节；模板、kernel 与 profile 未被修改。',
    }
    _write_new(state / MANIFEST_NAME,
               (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    return manifest


def template_directory(repo_root, template=None):
    """返回模板目录；``template`` 为空时按 ``TEMPLATE_CANDIDATES`` 探测。"""
    root = Path(repo_root).resolve()
    if template is not None:
        path = Path(template)
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        if not path.is_dir():
            raise ValueError('模板目录不存在：' + str(path))
        return path
    for candidate in TEMPLATE_CANDIDATES:
        path = root / candidate
        if path.is_dir():
            return path.resolve()
    raise ValueError('缺少项目模板目录；需要以下之一：' + '、'.join(TEMPLATE_CANDIDATES))


def _free_destination(root, destination):
    """校验目标：全新、不覆盖、不在源码内部（``.local`` 例外）。"""
    if destination is None or not str(destination).strip():
        raise ValueError('必须明确给出目标目录')
    requested = Path(destination).absolute()
    if requested.exists() or requested.is_symlink():
        raise ValueError('目标已存在；本模块只建立全新目录，不覆盖、不合并：' + str(requested))
    dest = requested.resolve()
    if dest.exists() or dest.is_symlink():
        raise ValueError('目标已存在；本模块只建立全新目录，不覆盖、不合并：' + str(dest))
    if dest == root:
        raise ValueError('目标不能是框架仓库根目录本身')
    if (dest / 'kernel').is_dir() and (dest / 'profiles').is_dir():
        raise ValueError('目标看起来是框架仓库；请换一个全新目录')
    if dest.is_relative_to(root) and not dest.is_relative_to(root / '.local'):
        raise ValueError('目标不能位于框架仓库内部；仓库内演示请使用 .local 下的新目录')
    return dest


def _pick_top_level(template_dir, keyword, default):
    """模板里存在同名关键字目录时沿用它的名字，避免生成第二套同级目录。"""
    if template_dir is not None and template_dir.is_dir():
        for entry in sorted(template_dir.iterdir(), key=lambda path: path.name):
            if entry.is_dir() and not entry.is_symlink() and keyword.lower() in entry.name.lower():
                return entry.name
    return default


def _plan(root, template_dir, profile_dir, profile_rel, names, dest):
    """列出全部待写文件；重复目标直接报错，而不是后写覆盖先写。"""
    context = {'repo_root': root, 'profile_rel': profile_rel, 'layout': names,
               'framework_root': root.as_posix()}
    template_prefix = _relative_posix(template_dir, root) or ''
    files, skipped, labels, planned = [], [], {}, set()

    def add(item):
        target = item['target']
        if target in labels:
            raise ValueError('目标路径冲突：' + target + '（来源：' + labels[target]
                             + ' 与 ' + item['source_label'] + '）；本模块不覆盖')
        labels[target] = item['source_label']
        planned.add(target)
        files.append(item)

    def make(target, source, source_label, origin, origin_root, target_root, placeholder=False):
        return {'target': target, 'source': source, 'source_label': source_label, 'origin': origin,
                'origin_root': origin_root, 'target_root': target_root, 'placeholder': placeholder,
                'context': context}

    template_files, template_skips = _walk(template_dir)
    for relative, path in template_files:
        add(make(relative, path, _join(template_prefix, relative), 'template',
                 template_dir, dest, placeholder=True))
    for relative, reason in template_skips:
        skipped.append({'source': _join(template_prefix, relative), 'reason': reason})

    for tree in FRAMEWORK_TREES:
        source_root = root / 'kernel' / tree
        if not source_root.is_dir():
            raise ValueError('缺少框架目录：kernel/' + tree)
        target_root = dest / names['framework'] / FRAMEWORK_DOC_DIR / tree
        tree_files, tree_skips = _walk(source_root)
        for relative, path in tree_files:
            add(make(f'{names["framework"]}/{FRAMEWORK_DOC_DIR}/{tree}/{relative}',
                     path, f'kernel/{tree}/{relative}', 'framework', source_root, target_root))
        for relative, reason in tree_skips:
            skipped.append({'source': f'kernel/{tree}/{relative}', 'reason': reason})

    profile_target = f'{names["platform"]}/{PLATFORM_PROFILE_DIR}'
    profile_files, profile_skips = _profile_files(profile_dir)
    profile_prefix = profile_rel or ('external-profile/' + profile_dir.name)
    for relative, path in profile_files:
        add(make(f'{profile_target}/{relative}', path, _join(profile_prefix, relative),
                 'profile', profile_dir, dest / profile_target))
    for relative, reason in profile_skips:
        skipped.append({'source': _join(profile_prefix, relative), 'reason': reason})

    if '.gitignore' not in planned:
        add({'target': '.gitignore', 'source': None, 'source_label': 'generated',
             'origin': 'generated', 'origin_root': None, 'target_root': None,
             'placeholder': False, 'context': context})
    return {'files': files, 'skipped': skipped, 'planned': planned}


def _walk(source_root):
    """列出常规文件（跳过符号链接、缓存与状态），返回 ``(files, skipped)``。"""
    if source_root is None:
        return [], []
    source_root = Path(source_root)
    found, skipped = [], []
    for current, dirnames, filenames in os.walk(source_root, topdown=True, followlinks=False):
        base = Path(current)
        keep = []
        for name in sorted(dirnames):
            path = base / name
            relative = path.relative_to(source_root).as_posix()
            if path.is_symlink():
                skipped.append((relative, 'symlink-directory'))
            elif name in EXCLUDED_DIR_NAMES:
                skipped.append((relative, 'excluded-directory'))
            else:
                keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames):
            path = base / name
            relative = path.relative_to(source_root).as_posix()
            if path.is_symlink():
                skipped.append((relative, 'symlink'))
            elif name in EXCLUDED_FILE_NAMES or name.lower().endswith(EXCLUDED_FILE_SUFFIXES):
                skipped.append((relative, 'cache-or-state'))
            elif not path.is_file():
                skipped.append((relative, 'not-a-regular-file'))
            else:
                found.append((relative, path))
    found.sort(key=lambda item: item[0])
    skipped.sort(key=lambda item: item[0])
    return found, skipped


def _profile_files(profile_dir):
    """列举 profile 文件；符号链接必须留在 profile 目录内，否则报错。"""
    found, skipped = _walk(profile_dir)
    for relative, _ in skipped:
        path = profile_dir / relative
        if not path.is_symlink():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(profile_dir):
            raise ValueError('profile 符号链接越出目录：' + relative + ' -> ' + str(resolved))
        if resolved.is_dir():
            raise ValueError('profile 符号链接指向目录，未展开：' + relative)
    # 保留在目录内的符号链接按解引用后的正文复制。
    return [(relative, path) for relative, path in found], skipped


def _render(item, dest, planned):
    """把一个源文件渲染成目标字节；返回 ``(bytes, source_sha256, info)``。"""
    origin = item['origin']
    info = {'links': 0, 'framework_references': 0, 'placeholder': 0, 'json_paths': 0}
    if origin == 'generated':
        data = DEFAULT_GITIGNORE.encode('utf-8')
        return data, None, info
    source = Path(item['source'])
    raw = source.read_bytes()
    source_sha = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw, source_sha, info
    changed = False
    if item['placeholder']:
        count = text.count(FRAMEWORK_PLACEHOLDER)
        if count:
            text = text.replace(FRAMEWORK_PLACEHOLDER, item['context']['framework_root'])
            info['placeholder'] = count
            changed = True
    if source.name == CONTEXT_ROUTES_NAME:
        text, count = _rewrite_routes(text, item, dest, planned)
        info['json_paths'] = count
        changed = changed or bool(count)
    elif source.name == READING_PLAN_NAME:
        text, count = _rewrite_reading_plan(text, item, dest, planned)
        info['json_paths'] = count
        changed = changed or bool(count)
    if source.suffix.lower() in MARKDOWN_SUFFIXES:
        text, links, references = _rewrite_links(text, item, dest, planned)
        info['links'], info['framework_references'] = links, references
        changed = changed or bool(links)
    return (text.encode('utf-8') if changed else raw), source_sha, info


def _rewrite_routes(text, item, dest, planned):
    """把 ``routes[].entry`` / ``routes[].required`` 改写成工作区相对本地路径。"""
    data = _load_json(text)
    if data is None:
        return text, 0
    count = 0
    for route in data.get('routes') or []:
        if not isinstance(route, dict):
            continue
        entry = _route_value(route.get('entry'), item, dest, planned)
        if entry is not None and entry != route.get('entry'):
            route['entry'] = entry
            count += 1
        required = route.get('required')
        if isinstance(required, list):
            rewritten = []
            for value in required:
                mapped = _route_value(value, item, dest, planned)
                if mapped is not None and mapped != value:
                    count += 1
                rewritten.append(mapped if mapped is not None else value)
            route['required'] = rewritten
    if not count:
        return text, 0
    return json.dumps(data, ensure_ascii=False, indent=2) + '\n', count


def _route_value(value, item, dest, planned):
    """单个路由路径（相对项目根）-> 工作区相对路径；无法映射返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    if _SCHEME.match(value) or value.startswith('/') or value.startswith('#'):
        return None
    path_part, separator, fragment = value.partition('#')
    path_part = path_part.split('?', 1)[0]
    if not path_part:
        return None
    origin_root = item['origin_root']
    try:
        resolved = (origin_root / path_part).resolve()
    except (OSError, ValueError):
        return None
    if resolved.is_relative_to(origin_root):
        return resolved.relative_to(origin_root).as_posix() + separator + fragment
    context = item['context']
    repo_rel = _relative_posix(resolved, context['repo_root'])
    if repo_rel is None:
        return None
    mapped = _map_repo_relative(repo_rel, context['profile_rel'], context['layout'])
    if mapped is not None:
        return mapped + separator + fragment
    if '/' not in repo_rel and repo_rel in planned:
        return repo_rel + separator + fragment
    return None


def _rewrite_reading_plan(text, item, dest, planned):
    """把内核及 project 模板必读来源改写为相对安装后框架目录的路径。"""
    data = _load_json(text)
    if data is None:
        return text, 0
    entries = data.get('entries')
    if not isinstance(entries, list):
        return text, 0
    count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        value = entry.get('path')
        mapped = _reading_local_path(value)
        if mapped is None and isinstance(value, str) and value.startswith('project/'):
            parts = value.split('/')[1:]
            if parts and '..' not in parts and '.' not in parts and all(parts):
                names = item['context']['layout']
                parts[0] = {FRAMEWORK_TOP: names['framework'], PLATFORM_TOP: names['platform']}.get(parts[0], parts[0])
                target = '/'.join(parts)
                if target in planned:
                    framework = (dest / item['target']).parent.parent
                    mapped = _relative_link(dest / target, framework)
        if mapped is not None and mapped != value:
            entry['path'] = mapped
            count += 1
    if not count:
        return text, 0
    return json.dumps(data, ensure_ascii=False, indent=2) + '\n', count


def _reading_local_path(value):
    """``kernel/<tree>/rest`` -> ``<tree>/rest``（相对框架目录）；其余原样不动。"""
    if not isinstance(value, str):
        return None
    parts = value.replace('\\', '/').split('/')
    if len(parts) >= 3 and parts[0] == 'kernel' and parts[1] in FRAMEWORK_TREES:
        return '/'.join(parts[1:])
    return None


def _load_json(text):
    """解析 JSON；不合法返回 None（不猜、不半改写）。"""
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _rewrite_links(text, item, dest, planned):
    """把 Markdown 本地链接改写到目标位置；返回 ``(text, rewrites, references)``。"""
    source_file = Path(item['source'])
    target_dir = (dest / Path(item['target'])).parent
    counter = {'rewritten': 0, 'references': 0}

    def replace(match):
        raw_target = match.group('target')
        wrapped = len(raw_target) > 1 and raw_target.startswith('<') and raw_target.endswith('>')
        inner = raw_target[1:-1] if wrapped else raw_target
        if not _is_local_target(inner):
            return match.group(0)
        path_part, separator, fragment = inner.partition('#')
        path_part = path_part.split('?', 1)[0]
        if not path_part:
            return match.group(0)
        try:
            resolved = (source_file.parent / path_part).resolve()
        except (OSError, ValueError):
            return match.group(0)
        counterpart = _counterpart(resolved, item, dest, planned)
        if counterpart is None:
            return match.group(0)
        path, reference = counterpart
        if reference:
            # 无法映射进工作区的框架资料用框架仓库绝对路径，工作区不自称拥有它。
            relative = path.as_posix()
        else:
            relative = _relative_link(path, target_dir)
        if relative is None:
            return match.group(0)
        new_inner = relative + separator + fragment
        if new_inner == inner:
            return match.group(0)
        counter['rewritten'] += 1
        counter['references'] += int(reference)
        rendered = '<' + new_inner + '>' if _needs_brackets(new_inner) else new_inner
        return match.group('prefix') + match.group('label') + match.group('middle') + rendered + match.group('tail')

    return _LINK.sub(replace, text), counter['rewritten'], counter['references']


def _counterpart(resolved, item, dest, planned):
    """把源侧解析结果映射成目标侧绝对路径；返回 ``(path, is_framework_reference)``。"""
    origin_root, target_root = item['origin_root'], item['target_root']
    if target_root is not None and resolved.is_relative_to(origin_root):
        return target_root / resolved.relative_to(origin_root), False
    context = item['context']
    repo_rel = _relative_posix(resolved, context['repo_root'])
    if repo_rel is None:
        return None
    mapped = _map_repo_relative(repo_rel, context['profile_rel'], context['layout'])
    if mapped is not None:
        return dest / mapped, False
    if '/' not in repo_rel and repo_rel in planned:
        # 模板自带同名根文件时，指向框架根入口文件的链接落到目标入口文件。
        return dest / repo_rel, False
    # 其余仍留在框架仓库内的引用（例如 kernel 源码、docs）改用框架仓库绝对路径：
    # 链接能打开，但工作区不自称拥有该文件，校验时单独记为外部引用。
    return resolved, True


def _map_repo_relative(repo_rel, profile_rel, layout):
    """框架仓库相对路径 -> 目标相对路径；无法映射返回 None。"""
    parts = repo_rel.split('/')
    if len(parts) >= 3 and parts[0] == 'kernel' and parts[1] in FRAMEWORK_TREES:
        return '/'.join([layout['framework'], FRAMEWORK_DOC_DIR, *parts[1:]])
    if profile_rel and (repo_rel == profile_rel or repo_rel.startswith(profile_rel + '/')):
        rest = repo_rel[len(profile_rel):].lstrip('/')
        base = layout['platform'] + '/' + PLATFORM_PROFILE_DIR
        return base + ('/' + rest if rest else '')
    return None


def _is_local_target(target):
    """只接受本地相对链接：锚点、外部 scheme、协议相对与绝对路径都不改写。"""
    return bool(target) and not target.startswith('#') and not target.startswith('//') \
        and not _is_absolute_target(target) and not _SCHEME.match(target)


def _needs_brackets(target):
    """目标含空格时用尖括号包起来；含尖括号的路径原样保留，避免嵌套。"""
    return ' ' in target and '<' not in target and '>' not in target


def _relative_link(path, base):
    """目标侧相对链接（``/`` 分隔）；跨盘符等无法表达时返回 None。"""
    try:
        relative = os.path.relpath(str(path), str(base))
    except ValueError:
        return None
    relative = relative.replace(os.sep, '/')
    return None if Path(relative).is_absolute() else relative


def _relative_posix(path, base):
    """``path`` 相对 ``base`` 的 posix 形式；不在 ``base`` 内返回 None。"""
    try:
        relative = Path(path).relative_to(Path(base))
    except ValueError:
        return None
    return relative.as_posix()


def _join(prefix, relative):
    """拼接回执标签，避免空段与重复斜杠。"""
    if not prefix:
        return relative
    if not relative:
        return prefix
    return prefix + '/' + relative


def _write_new(path, data):
    """新建文件（``xb``）：目标已存在就失败，绝不覆盖。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(data)


def _initialize_state(state):
    """建立与 ``init`` 一致的空 Store/Governance/Topics/Runtime。"""
    from .governance import Governance
    from .runtime.service import Runtime
    from .runtime.store import Store
    from .runtime.topics import Topics
    database = state / 'runtime.sqlite3'
    store = Store(database)
    Governance(store)
    Topics(store)
    Runtime(store)
    return {'database': STATE_DIRNAME + '/runtime.sqlite3',
            'initialized': ['governance', 'topics', 'runtime'],
            'empty': True,
            'note': '新建空服务；没有任何专题、审批或运行记录。'}


def _measure_root(dest):
    """测量目标根入口材料：逐文件 sha256/bytes，Markdown 另给 UTF-16 字符数。"""
    files = []
    for entry in sorted(dest.iterdir(), key=lambda path: path.name):
        if entry.is_symlink() or not entry.is_file():
            continue
        raw = entry.read_bytes()
        markdown = entry.suffix.lower() in MARKDOWN_SUFFIXES
        record = {'path': entry.name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                  'markdown': markdown}
        if markdown:
            record['utf16_chars'] = len(raw.decode('utf-8', errors='replace').encode('utf-16-le')) // 2
        files.append(record)
    entry_documents = [record for record in files if record['markdown']]
    return {'path': '.', 'files': files, 'file_count': len(files),
            'bytes': sum(record['bytes'] for record in files),
            'entry_documents': [record['path'] for record in entry_documents],
            'entry_document_bytes': sum(record['bytes'] for record in entry_documents),
            'entry_document_utf16_chars': sum(record['utf16_chars'] for record in entry_documents),
            'scope': '只测量目标根的直接文件；子目录内容见 files 回执。'}


def _measure_entries(dest):
    """测量根路由的每个 entry 与 required 的真实字节。

    这些是生成文件的实测大小，不是宿主首轮注入，也不能据此声称规则已加载。
    """
    routes, entry_bytes, required_bytes = [], 0, 0
    for document in _named_documents(dest, CONTEXT_ROUTES_NAME):
        data = _load_json(document.read_text(encoding='utf-8', errors='replace'))
        if not isinstance(data, dict) or not isinstance(data.get('routes'), list):
            continue
        for route in data['routes']:
            if not isinstance(route, dict):
                continue
            entry = _measure_file(dest, route.get('entry'))
            required = [_measure_file(dest, value)
                        for value in route.get('required') or [] if isinstance(value, str)]
            routes.append({'manifest': document.relative_to(dest).as_posix(),
                           'id': route.get('id'), 'entry': entry,
                           'required': [item for item in required if item]})
            entry_bytes += (entry['bytes'] or 0) if entry else 0
            required_bytes += sum(item['bytes'] or 0 for item in required if item)
    return {'routes': routes, 'entry_bytes': entry_bytes, 'required_bytes': required_bytes,
            'note': '实测生成文件的字节；不是宿主首轮注入，也不证明宿主已加载。'}


def _measure_file(dest, value):
    """测量清单里的一个路径；绝对路径与工作区外文件标出 ``workspace_local``。"""
    if not isinstance(value, str) or not value.strip():
        return None
    path_part = value.partition('#')[0].split('?', 1)[0]
    if not path_part:
        return None
    path = Path(path_part) if _is_absolute_target(path_part) else (dest / path_part)
    record = {'path': value, 'present': path.is_file() and not path.is_symlink(),
              'workspace_local': None, 'bytes': None, 'sha256': None}
    try:
        resolved = path.resolve()
    except (OSError, ValueError):
        return record
    record['workspace_local'] = resolved.is_relative_to(dest)
    if not record['present']:
        return record
    raw = resolved.read_bytes()
    record['bytes'] = len(raw)
    record['sha256'] = hashlib.sha256(raw).hexdigest()
    if resolved.suffix.lower() in MARKDOWN_SUFFIXES:
        record['utf16_chars'] = len(raw.decode('utf-8', errors='replace').encode('utf-16-le')) // 2
    return record


# ---------------------------------------------------------------------------
# 只读校验
# ---------------------------------------------------------------------------


def validate_layout(root):
    """只读校验一个模板目录或已生成工作区的 Markdown 本地链接与机器可读清单。

    规则（精确边界）：

    * 只看 ``*.md`` 里的行内链接；``#锚点``、带 scheme 的目标与协议相对链接跳过。
    * 相对链接解析后落在 ``root`` 内：必须存在，否则报错。
    * 相对链接词法上在 ``root`` 内、解析后越界：符号链接逃逸，报错。
    * 相对链接解析后越出 ``root``：目标必须存在且位于某个含 ``kernel`` 目录的祖先仓库内
      （跨模板到框架仓库的引用），否则报错；能打开也只记为“外部引用”。
    * 绝对路径（含框架仓库绝对路径）：必须存在，且只能在框架仓库祖先内；即使打开也
      记为外部引用，**不计入工作区本地链接**——不把“指向框架仓库”当成工作区自足。
    * ``context_routes.json``：模板与生成项目都必须有且只有一份；``full_document`` 必须为
      true，``history_is_current_instruction`` 必须为 false；每条 route 的 ``entry`` 与
      ``required`` 都相对 ``root`` 解析并必须能打开（模板里指向框架 ``kernel``/``profiles``
      的路径按上面的跨仓库规则放行）。
    * 生成项目（``.autoresearch/layout-manifest.json`` 存在）：根必须还有 ``AGENTS.md``、
      ``RULES.md``、``DOC_MAINTENANCE.md``，至少一份 ``reading_required.json``，且路由里的
      路径必须是工作区本地路径——不能用框架外部路径充当本项目的入口。
    * ``reading_required.json``：``entries[].path`` 相对其所在框架目录解析并必须能打开。
    * 不写文件、不改文件、不建 sidecar；不检查宿主是否加载。

    返回 ``{'ok', 'errors', 'errors_truncated', 'documents', 'links_checked', 'external_links',
    'framework_references', 'unresolved', 'patterns', 'routes_manifests', 'route_paths_checked',
    'reading_manifests', 'reading_paths_checked', 'read_only', 'scope'}``。
    """
    base = Path(root)
    if not base.is_dir():
        return {'ok': False, 'errors': ['根目录不存在：' + str(base)], 'errors_truncated': False,
                'documents': 0, 'links_checked': 0, 'external_links': [], 'framework_references': 0,
                'unresolved': [], 'patterns': [], 'routes_manifests': 0, 'route_paths_checked': 0,
                'reading_manifests': 0, 'reading_paths_checked': 0, 'read_only': True}
    root_path = base.resolve()
    generated = (root_path / STATE_DIRNAME / MANIFEST_NAME).is_file()
    errors, unresolved, external, patterns = [], [], [], []
    from .workspace_layout import resolve_layout
    try:
        resolve_layout(root_path)
    except (ValueError, OSError) as exc:
        errors.append('工作区布局标记无效：' + str(exc))
    documents = checked = references = 0
    for document in _markdown_documents(root_path):
        documents += 1
        label = document.relative_to(root_path).as_posix()
        text = document.read_text(encoding='utf-8', errors='replace')
        for raw_target in _link_targets(text):
            target = raw_target[1:-1] if len(raw_target) > 1 and raw_target.startswith('<') and raw_target.endswith('>') else raw_target
            if _skip_target(target):
                continue
            path_part = target.partition('#')[0].split('?', 1)[0]
            if not path_part:
                continue
            if _is_pattern_target(path_part):
                patterns.append({'document': label, 'target': path_part})
                continue
            outcome = _classify_path(root_path, document.parent, path_part, label)
            if outcome['status'] == 'ok_local':
                checked += 1
            elif outcome['status'] == 'ok_external':
                external.append(outcome['record'])
                references += 1
            elif outcome['status'] == 'unresolved':
                unresolved.append(outcome['record'])
                errors.append(outcome['message'])
            else:
                errors.append(outcome['message'])
    routes = _validate_context_routes(root_path, require_local=generated)
    reading = _validate_reading_plans(root_path)
    errors.extend(routes['errors'])
    errors.extend(reading['errors'])
    errors.extend(_structure_errors(root_path, generated, routes, reading))
    truncated = len(errors) > MAX_LINK_ERRORS
    return {'ok': not errors, 'errors': errors[:MAX_LINK_ERRORS], 'errors_truncated': truncated,
            'documents': documents, 'links_checked': checked, 'external_links': external,
            'framework_references': references, 'unresolved': unresolved, 'patterns': patterns,
            'routes_manifests': routes['manifests'], 'route_paths_checked': routes['paths_checked'],
            'reading_manifests': reading['manifests'], 'reading_paths_checked': reading['paths_checked'],
            'read_only': True,
            'scope': '只检查 Markdown 本地链接、绝对路径、context_routes.json 的布尔与路径、'
                     'reading_required.json 的路径；不计入工作区本地链接的外部引用单独列出；'
                     '含尖括号的命名模式（如 <热件>_cold）只登记不判错；未检查宿主是否加载。'}


def _classify_path(root, containing_directory, path_text, label):
    """把一个链接/清单路径分类：本地可打开、仅外部引用、缺失、越界或无法解析。"""
    absolute = _is_absolute_target(path_text)
    try:
        candidate = Path(path_text) if absolute else (Path(containing_directory) / path_text)
        resolved = candidate.resolve()
    except (OSError, ValueError):
        return {'status': 'unresolved',
                'record': {'document': label, 'target': path_text, 'reason': '路径在当前平台无法解析'},
                'message': label + '：路径无法解析 ' + path_text}
    if not resolved.exists():
        return {'status': 'missing', 'message': label + '：链接打不开 ' + path_text + '（目标不存在）'}
    if resolved.is_relative_to(root):
        return {'status': 'ok_local'}
    framework = _framework_root_for(root, resolved)
    if absolute:
        if framework is None:
            return {'status': 'escape',
                    'message': label + '：绝对路径不属于已声明的框架仓库 ' + path_text
                              + '（解析到 ' + str(resolved) + '）'}
        return {'status': 'ok_external', 'record': {'document': label, 'target': path_text,
                                                    'resolved': str(resolved),
                                                    'framework': str(framework)}}
    lexical = Path(os.path.normpath(str(Path(containing_directory) / path_text)))
    if lexical.is_relative_to(root):
        return {'status': 'escape',
                'message': label + '：链接经符号链接越出工作区 ' + path_text + '（解析到 ' + str(resolved) + '）'}
    if framework is None:
        return {'status': 'escape',
                'message': label + '：链接越出工作区且不属于框架仓库 ' + path_text + '（解析到 ' + str(resolved) + '）'}
    return {'status': 'ok_external', 'record': {'document': label, 'target': path_text,
                                                'resolved': str(resolved), 'framework': str(framework)}}


def _structure_errors(root, generated, routes, reading):
    """模板与生成项目都必须有且只有一份路由清单；生成项目还要有根入口与阅读登记。"""
    errors = []
    if routes['manifests'] == 0:
        errors.append('缺少 context_routes.json：模板与生成项目都必须各有一份机器可读路由清单')
    elif routes['manifests'] > 1:
        errors.append('发现多份 context_routes.json（' + str(routes['manifests'])
                      + ' 份）：只允许一份，多份时无法确定现行入口')
    if not generated:
        return errors
    for name in GENERATED_ROOT_DOCUMENTS:
        if not (root / name).is_file():
            errors.append('生成项目缺少根入口文件：' + name)
    if reading['manifests'] < 1:
        errors.append('生成项目缺少 reading_required.json：阅读登记没有落地')
    return errors


def _validate_context_routes(root, require_local=False):
    """校验组织路由清单：布尔边界与每条 entry/required 都能打开。"""
    errors, manifests, checked = [], 0, 0
    for document in _named_documents(root, CONTEXT_ROUTES_NAME):
        manifests += 1
        label = document.relative_to(root).as_posix()
        data = _load_json(document.read_text(encoding='utf-8', errors='replace'))
        if not isinstance(data, dict):
            errors.append(label + '：不是合法 JSON 对象')
            continue
        if data.get('full_document') is not True:
            errors.append(label + '：full_document 必须是 true（登记只接受整篇读取）')
        if data.get('history_is_current_instruction') is not False:
            errors.append(label + '：history_is_current_instruction 必须是 false（历史不是现行指令）')
        routes = data.get('routes')
        if not isinstance(routes, list) or not routes:
            errors.append(label + '：routes 必须是非空数组')
            continue
        for index, route in enumerate(routes):
            if not isinstance(route, dict):
                errors.append(label + '：routes[' + str(index) + '] 必须是对象')
                continue
            route_label = label + ' routes[' + str(route.get('id') or index) + ']'
            if route.get('full_document') is not None and route.get('full_document') is not True:
                errors.append(route_label + '：full_document 必须是 true')
            checked += _manifest_path(root, root, route.get('entry'), route_label + ' entry',
                                      errors, require_local)
            required = route.get('required')
            if not isinstance(required, list):
                errors.append(route_label + '：required 必须是字符串数组')
                continue
            for position, value in enumerate(required):
                checked += _manifest_path(root, root, value,
                                          route_label + ' required[' + str(position) + ']',
                                          errors, require_local)
    return {'errors': errors, 'manifests': manifests, 'paths_checked': checked}


def _validate_reading_plans(root):
    """校验阅读登记：``entries[].path`` 相对所在框架目录必须存在。"""
    errors, manifests, checked = [], 0, 0
    for document in _named_documents(root, READING_PLAN_NAME):
        manifests += 1
        label = document.relative_to(root).as_posix()
        framework = document.parent.parent
        data = _load_json(document.read_text(encoding='utf-8', errors='replace'))
        if not isinstance(data, dict):
            errors.append(label + '：不是合法 JSON 对象')
            continue
        entries = data.get('entries')
        if not isinstance(entries, list):
            errors.append(label + '：entries 必须是数组')
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                errors.append(label + '：entries[' + str(index) + '] 必须是对象')
                continue
            checked += _manifest_path(root, framework, entry.get('path'),
                                      label + ' entries[' + str(entry.get('id') or index) + '].path', errors)
    return {'errors': errors, 'manifests': manifests, 'paths_checked': checked}


def _manifest_path(root, base, value, label, errors, require_local=False):
    """校验清单里的一个路径；返回是否真的打开（用于计数）。"""
    if not isinstance(value, str) or not value.strip():
        errors.append(label + '：必须是非空字符串')
        return 0
    if _SCHEME.match(value) and not _is_absolute_target(value):
        errors.append(label + '：必须是本地相对路径，不能带 scheme：' + value)
        return 0
    path_part = value.partition('#')[0].split('?', 1)[0]
    outcome = _classify_path(root, base, path_part, label)
    if outcome['status'] == 'ok_local':
        return 1
    if outcome['status'] == 'ok_external':
        if require_local:
            errors.append(label + '：生成项目的路径必须是工作区本地路径，不能用框架外部路径：' + value)
            return 0
        return 1
    errors.append(outcome.get('message', label + '：路径不可用 ' + value))
    return 0


def _documents(root):
    """列出参与校验的常规文件（跳过符号链接、缓存与状态目录），按相对路径排序。"""
    documents = []
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        base = Path(current)
        dirnames[:] = sorted(name for name in dirnames
                             if name not in EXCLUDED_DIR_NAMES and not (base / name).is_symlink())
        for name in sorted(filenames):
            path = base / name
            if path.is_symlink() or not path.is_file():
                continue
            documents.append(path)
    documents.sort(key=lambda path: path.relative_to(root).as_posix())
    return documents


def _markdown_documents(root):
    """参与链接校验的 Markdown 文件。"""
    return [path for path in _documents(root) if path.suffix.lower() in MARKDOWN_SUFFIXES]


def _named_documents(root, name):
    """按文件名找机器可读清单（``context_routes.json`` / ``reading_required.json``）。"""
    return [path for path in _documents(root) if path.name == name]


def _link_targets(text):
    """按出现顺序给出 Markdown 行内链接的原始目标文本。"""
    return [match.group('target') for match in _LINK.finditer(text)]


def _skip_target(target):
    """锚点、协议相对链接与网络 scheme 不是本地路径；绝对路径要单独校验，不能跳过。"""
    if not target or target.startswith('#') or target.startswith('//'):
        return True
    if _is_absolute_target(target):
        return False
    return bool(_SCHEME.match(target))


def _is_absolute_target(value):
    """绝对路径：POSIX 根、Windows 盘符路径或 UNC 路径。"""
    return value.startswith('/') or value.startswith('\\') or bool(re.match(r'^[A-Za-z]:[\\/]', value))


def _is_pattern_target(value):
    """含尖括号的命名模式（如 ``<热件>_cold/INDEX.md``）是写法示例，不是真实路径。"""
    return '<' in value or '>' in value


def _looks_like_framework(path):
    """结构上像框架仓库：有 ``kernel/contracts`` 与 ``profiles``；只是名字像不算。"""
    try:
        return (Path(path) / 'kernel' / 'contracts').is_dir() and (Path(path) / 'profiles').is_dir()
    except OSError:
        return False


def _framework_roots(root):
    """已声明的框架仓库根。

    回执里的 ``framework_root`` 只有在结构上确实像框架仓库时才计入，避免用一条清单
    字段无限授权任意外部路径；含 ``kernel/contracts`` 与 ``profiles`` 的祖先始终计入。
    """
    roots = []
    manifest = Path(root) / STATE_DIRNAME / MANIFEST_NAME
    if manifest.is_file():
        data = _load_json(manifest.read_text(encoding='utf-8', errors='replace'))
        if isinstance(data, dict) and isinstance(data.get('framework_root'), str):
            declared = Path(data['framework_root'])
            if declared.is_dir() and _looks_like_framework(declared):
                roots.append(declared.resolve())
    for ancestor in (Path(root), *Path(root).parents):
        if _looks_like_framework(ancestor) and ancestor not in roots:
            roots.append(ancestor)
    return roots


def _framework_root_for(root, target):
    """``target`` 落在哪个已声明框架仓库内；都不在返回 None。"""
    for candidate in _framework_roots(root):
        try:
            if target.is_relative_to(candidate):
                return candidate
        except (OSError, ValueError):
            continue
    return None
