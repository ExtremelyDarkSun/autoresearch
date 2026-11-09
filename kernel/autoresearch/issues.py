# GENERATED-BY: portable functional migration
# LAST-EDIT: 2026-11-09 E2/E4：问题与证据检查关联 exp_id；旧记录不改写；显式 linked_exp 事件
# PURPOSE: Persist first failures, deduplication, ownership, fixes and verification before resuming work.
# ORIGIN: framework expectations section 9 + E2/E4
# CUMULATIVE-DELTA: 2026-11-09 修四个语义洞 —— ① `assigned` 必须带显式 `owner` 字段（说明文字不算负责人）；
#   ② `verified` 必须给可核 argv + 退出码 + 证据（`passed` 由退出码推出，不是自述布尔）；
#   ③ 归档只认「这次结案」一次，重复归档拒；同名不同内容换名，⛔ 不静默覆盖；写失败留 `archive_failed` 诊断事件；
#   ④ 查重可给显式 `dedupe_key`（不做模糊合并），并把追加链校验放进同一个写事务。
#   本轮（E2/E4）：⑤ `record` 收 `exp_id` 并把它**纳入查重范围**（同命令不同实验不再糊成一个）；
#   ⑥ 旧记录（payload 里没有 `exp_id` 键）明确投影成 `exp_link='unknown'`、显式 None 投影成 `unlinked`，
#       ⛔ 不猜、⛔ 不改写历史；关联只用追加 `linked_exp` 事件（允许显式跨 exp 链接）。
"""问题台账（框架行为预期 §9）。

这一层只回答四件事：**同一个错有没有被重复登记**、**现在归谁**、**改了什么**、**复验跑的是哪条命令、
退出码多少**。⛔ 它不回答「这个科学结论对不对」：

* `verified` 只证明「有人用这条 argv 跑出了这个退出码，并留下了证据文件」；
* 证据文件的内容真实性、命令是否真针对这个 bug，仍由 agent / 人核；
* 台账是**完成记录**，不是科学判断。

存储
====

`<workspace>/.autoresearch/issues.sqlite3` 一张表：

    issue_events(seq PK, issue_id, kind, payload, previous, digest)

每条记录带前一条的 `digest` 形成哈希链；`BEFORE UPDATE/DELETE` 触发器禁止改历史。
`events()` 会重算链（序号连续 + previous 对上 + digest 对上），链断就整本判「损坏」。

查重
====

默认按 `{command, scope, exp_id}` **精确**哈希查重：同一条命令 + 同一个受影响范围 + 同一个实验 ⇒ 同一个 issue。
命令里带临时路径（`temp/xxx/`、`/tmp/...`）会让精确查重失效 —— 这时**显式**给 `dedupe_key`。
⛔ 不做模糊/相似合并：那会把两个不同的错并成一个，比漏合并更坏。
⛔ `exp_id` 在查重键里：同一个命令在两个实验里各崩一次是**两个** issue，不能糊成一个。

⚠️ **不给 `exp_id` 时仍是旧版的 canonical**（只有 `{command, scope}`，或不带 exp 的
`{dedupe_key, scope}`）：历史记录的哈希与查重键一个都不变，旧 `opened` 同命令复现仍然命中
同一个 issue。只有真的给了 `exp_id` 才把它加进 canonical。

实验关联（E2/E4）
=================

每条事件都可以带 `exp_id`（登记时给）。读取时投影出：

    exp_id       本次登记时写下的实验号（旧记录没有 ⇒ None）
    exp_link     'linked'（有实验号）/ 'unlinked'（显式没给）/ 'unknown'（旧记录，键都没有）
    exp_links    后续显式 `linked_exp` 事件（跨实验链接），每条带 actor / reason / 源码证据
    effective_exps  exp_id 与所有链接目标的并集

⛔ 旧记录一条都不改写：要把它算进某个实验，就追加一条 `linked_exp`，并留证据和理由。

状态
====

    open --fix--> fixed --verify(exitcode==0)--> closed --再次同命令--> open（recurred）
                          `--verify(exitcode!=0)--> open

`resume_allowed` 只在 `closed` 时为 True；归档不动状态（仍是 `closed`），也不删热件历史。
"""

from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

KINDS = ('opened', 'recurred', 'assigned', 'fixed', 'verified', 'reopened', 'archived',
         'archive_failed', 'linked_exp')
UPDATE_KINDS = ('assigned', 'fixed', 'verified', 'reopened')

#: 实验关联的三种投影。⛔ 缺关联不许读成「不限实验」。
EXP_LINKS = ('linked', 'unlinked', 'unknown')

#: 复验结论的出处只有这一个：命令本身跑出来的退出码。
VERIFICATION_NOTE = ('台账只记录这次复验的命令与退出码（完成记录），不代表科学结论成立；'
                     '证据内容与针对性仍由 agent / 人核')


class IssueRuleError(ValueError):
    """**可判**的规则拒绝（状态不对、重复归档、结论与退出码不一致、只读写）。

    与「输入/数据错误」分开，CLI 才能给出不同的退出码（规则拒绝 9，输入错误 2）。
    它是 `ValueError` 的子类，旧的 `except ValueError` 写法仍然成立。
    """


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _exp_ref(value):
    """实验号：`None` / 空串 ⇒ `None`（明确没关联）；否则必须是**单个非空词**。

    ⛔ 不接受带空白的串（那会把说明文字塞进查重键），⛔ 不做大小写折叠之外的改写。
    """
    if value is None:
        return None
    if isinstance(value, Path):
        value = str(value)
    if not isinstance(value, str):
        raise ValueError(f'exp_id 必须是非空字符串或 None，实际 {value!r}')
    text = value.strip()
    if not text:
        return None
    if any(char.isspace() for char in text):
        raise ValueError('exp_id 不能含空白（只写实验号本身，不要塞说明）')
    if len(text) > 128:
        raise ValueError('exp_id 过长（>128 字符）')
    return text


def _argv(value, label='command'):
    """显式 argv 数组：非空、全是字符串、不许空串（⛔ 不接受字符串命令行）。"""
    if isinstance(value, str) or not isinstance(value, list) or not value:
        raise ValueError(f'{label} 必须是显式 argv 数组（非空、全为字符串），⛔ 不接受字符串命令行')
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f'{label} 的每一项都必须是非空字符串')
    return [str(item) for item in value]


def _exitcode(value):
    if type(value) is not int:
        raise ValueError(f'退出码必须是整数（信号终止用负值），实际 {value!r}')
    return int(value)


class Issues:
    def __init__(self, workspace, *, readonly=False):
        self.root = Path(workspace).resolve()
        self.db = self.root / '.autoresearch/issues.sqlite3'
        self.readonly = readonly
        if not readonly:
            if not (self.root / '.autoresearch').is_dir():
                raise ValueError('工作区未初始化')
            with closing(sqlite3.connect(self.db)) as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS issue_events(seq INTEGER PRIMARY KEY, issue_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL)')
                for op in ('UPDATE', 'DELETE'):
                    conn.execute(f"CREATE TRIGGER IF NOT EXISTS issue_no_{op} BEFORE {op} ON issue_events BEGIN SELECT RAISE(ABORT,'issue events are append-only'); END")
                conn.commit()

    # -- 基础 ---------------------------------------------------------------

    def _path(self, value):
        if isinstance(value, Path):
            value = str(value)
        if not _text(value):
            raise ValueError('证据须给 path（工作区内相对路径）')
        p = (self.root / value).resolve()
        if p == self.root or not p.is_relative_to(self.root):
            raise ValueError('问题证据路径越出工作区')
        return p

    def _evidence(self, sources):
        if not isinstance(sources, list) or not sources:
            raise ValueError('缺原始证据来源')
        rows = []
        for source in sources:
            if not isinstance(source, dict) or not _text(source.get('locator')):
                raise ValueError('证据须给path和locator')
            path = self._path(source.get('path'))
            if not path.is_file():
                raise ValueError(f'证据文件不存在：{source.get("path")}')
            data = path.read_bytes()
            rows.append({'path': path.relative_to(self.root).as_posix(), 'locator': str(source['locator']).strip(),
                         'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
        return rows

    @staticmethod
    def _events_from(conn):
        """从**给定连接**读事件并重算哈希链（写事务里也用同一条连接，避免跨连接读）。"""
        rows = conn.execute('SELECT seq,issue_id,kind,payload,previous,digest FROM issue_events ORDER BY seq').fetchall()
        events, previous = [], ''
        for seq, identity, kind, payload, prev, digest in rows:
            record = {'seq': seq, 'issue_id': identity, 'kind': kind, 'payload': json.loads(payload), 'previous': prev}
            if seq != len(events) + 1 or prev != previous or hashlib.sha256(canonical(record).encode()).hexdigest() != digest:
                raise ValueError('问题台账损坏，不能判定已修复')
            record['digest'] = digest
            events.append(record)
            previous = digest
        return events

    def events(self):
        if not self.db.is_file():
            return []
        with closing(sqlite3.connect(self.db.as_uri() + '?mode=ro', uri=True)) as conn:
            return self._events_from(conn)

    def _append(self, conn, rows, identity, kind, payload):
        record = {'seq': len(rows) + 1, 'issue_id': identity, 'kind': kind, 'payload': payload,
                  'previous': rows[-1]['digest'] if rows else ''}
        digest = hashlib.sha256(canonical(record).encode()).hexdigest()
        conn.execute('INSERT INTO issue_events VALUES(?,?,?,?,?,?)',
                     (record['seq'], identity, kind, canonical(payload), record['previous'], digest))
        return dict(record, digest=digest)

    def _write(self, operation):
        if self.readonly:
            raise IssueRuleError('只读问题台账')
        with closing(sqlite3.connect(self.db, timeout=30)) as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                result = operation(conn, self._events_from(conn))
                conn.commit()
                return result
            except BaseException:
                conn.rollback()
                raise

    def _history(self, issue_id):
        return [row for row in self.events() if row['issue_id'] == issue_id]

    # -- 只读投影 -----------------------------------------------------------

    def status(self, issue_id=None, *, exp_id=None):
        """问题当前状态（只读投影）。

        每条多出四个实验关联字段（E2/E4）：

        * `exp_id` / `exp_link`：登记时写下的实验号与三种投影（`linked`/`unlinked`/`unknown`）；
          ⛔ 旧记录没有 `exp_id` 键 ⇒ `unknown`，**不猜**它属于哪个实验；
        * `exp_links`：后续显式 `linked_exp` 事件（跨实验链接，带 actor / reason / 证据）；
        * `effective_exps`：`exp_id` 与所有链接目标的并集（`exp_id=` 过滤按它判）。

        给了 `exp_id` ⇒ 只返回**明确关联**到该实验的问题；⛔ 不把 `unlinked`/`unknown` 算进来
        （那些是「未入台账」，调用方要另外点名）。
        """
        states = {}
        links = {}
        for row in self.events():
            item = states.setdefault(row['issue_id'],
                                     {'issue_id': row['issue_id'], 'history': [],
                                      '_has_exp_key': False})
            item['history'].append(row)
            if 'exp_id' in row['payload']:
                item['_has_exp_key'] = True
            item.update(row['payload'])
            item['last_kind'] = row['kind']
            if row['kind'] == 'linked_exp':
                links.setdefault(row['issue_id'], []).append({
                    'seq': row['seq'], 'exp_id': row['payload'].get('linked_exp'),
                    'actor': row['payload'].get('actor'),
                    'reason': row['payload'].get('text'),
                    'sources': row['payload'].get('sources'),
                    'at': row['payload'].get('time')})
        wanted = _exp_ref(exp_id)
        out = []
        for identity, item in states.items():
            primary = item.get('exp_id')
            primary = primary.strip() if isinstance(primary, str) and primary.strip() else None
            if item.pop('_has_exp_key'):
                exp_link = 'linked' if primary else 'unlinked'
            else:
                exp_link = 'unknown'
            item['exp_id'] = primary
            item['exp_link'] = exp_link
            item['exp_links'] = links.get(identity, [])
            effective = {value for value in [primary] + [link['exp_id'] for link in item['exp_links']]
                         if isinstance(value, str) and value.strip()}
            item['effective_exps'] = sorted(effective)
            item['resume_allowed'] = item.get('state') == 'closed'
            item['event_count'] = len(item['history'])
            out.append(item)
        if issue_id is not None:
            for item in out:
                if item['issue_id'] == issue_id:
                    if wanted is not None and wanted not in item['effective_exps']:
                        raise ValueError(f'问题 {issue_id} 没有关联到实验 {wanted}')
                    return item
            raise ValueError('问题不存在')
        if wanted is not None:
            return [item for item in out if wanted in item['effective_exps']]
        return out

    def archive_index(self):
        """冷件索引 = 台账里的 `archived` 事件（append-only，不会被后一次写覆盖）。"""
        index = []
        for row in self.events():
            if row['kind'] != 'archived':
                continue
            payload = row['payload']
            index.append({'issue_id': row['issue_id'], 'seq': row['seq'],
                          'archive_path': payload.get('archive_path'),
                          'archive_sha256': payload.get('archive_sha256'),
                          'bytes': payload.get('bytes'), 'at': payload.get('time'),
                          'actor': payload.get('actor')})
        return index

    # -- 首错登记 -----------------------------------------------------------

    def record(self, *, summary, owner, command, exitcode, sources, scope, actor='agent',
               dedupe_key=None, exp_id=None):
        """登记首错。同一 `{command, scope, exp_id}`（或同一显式 `dedupe_key` + scope + exp_id）复现 ⇒ 同一个 issue。

        `dedupe_key` 是给「命令里带临时路径、精确查重必然漏」的场景用的**显式**归并键；
        ⛔ 不给就不猜、不做模糊合并。

        `exp_id` 是这条证据/检查所属的实验号（E2）。它**进查重键**：同一个命令在两个实验
        里各崩一次是**两个** issue。`None` 明确表示「没关联」（`exp_link='unlinked'`）。
        """
        for label, value in (('问题', summary), ('负责人', owner), ('受影响范围', scope), ('登记者', actor)):
            if not _text(value):
                raise ValueError(f'须给{label}')
        argv = _argv(command, '首错命令')
        code = _exitcode(exitcode)
        if dedupe_key is not None and not _text(dedupe_key):
            raise ValueError('dedupe_key 必须是非空字符串（不给就按 command+scope 精确查重）')
        experiment = _exp_ref(exp_id)
        evidence = self._evidence(sources)
        # 查重键要**向后兼容**：不给 exp_id 时仍是旧的 `{command, scope}` canonical（旧哈希不变），
        # 只在确实给了 exp 时才把 exp_id 加进 canonical。⛔ 不许给历史记录换哈希。
        if dedupe_key is None:
            core = {'command': argv, 'scope': scope}
            if experiment is not None:
                core['exp_id'] = experiment
            key = hashlib.sha256(canonical(core).encode()).hexdigest()
            key_source = 'command+scope+exp_id' if experiment else 'command+scope'
        else:
            core = {'dedupe_key': dedupe_key.strip(), 'scope': scope}
            if experiment is not None:
                core['exp_id'] = experiment
            key = hashlib.sha256(canonical(core).encode()).hexdigest()
            key_source = 'explicit+exp_id' if experiment else 'explicit'

        def op(conn, rows):
            previous = [(index, r) for index, r in enumerate(rows)
                        if r['kind'] in ('opened', 'recurred') and r['payload'].get('dedupe_key') == key]
            old = previous[0][1] if previous else None
            identity = old['issue_id'] if old else f'ISSUE-{sum(r["kind"] == "opened" for r in rows) + 1:06d}'
            payload = {'summary': summary, 'owner': owner.strip(), 'command': argv, 'exitcode': code,
                       'sources': evidence, 'scope': scope, 'actor': actor,
                       'dedupe_key': key, 'dedupe_source': key_source,
                       'state': 'open', 'time': time.time(), 'resume_allowed': False,
                       'passed': None, 'verification': None, 'exp_id': experiment}
            return self._append(conn, rows, identity, 'recurred' if old else 'opened', payload)
        return self._write(op)

    # -- 处置 ---------------------------------------------------------------

    def update(self, issue_id, *, kind, actor, text, sources, owner=None,
               command=None, exitcode=None, passed=None):
        """记一条处置。

        * `assigned`：必须给**显式 `owner` 字段**（`text` 里的名字不算负责人）；
        * `fixed`：给说明 + 证据；可选带这次修复用的 argv + 退出码（两样一起给）；
        * `verified`：必须已有 `fixed`，并给**可核 argv + 退出码 + 证据**；`passed` 由退出码推出
          （给了也必须与退出码一致，否则拒）。⛔ 这只证明完成记录，不证明科学结论；
        * `reopened`：结案/已修复后又出问题，需说明 + 证据。
        """
        if kind not in UPDATE_KINDS:
            raise ValueError(f'更新动作必须是 {"/".join(UPDATE_KINDS)}')
        if not _text(actor) or not _text(text):
            raise ValueError('更新须含合法动作、执行者与说明')
        if kind == 'assigned':
            if not _text(owner):
                raise ValueError('定负责人必须显式给 owner 字段（说明文字不算负责人）')
            if command is not None or exitcode is not None or passed is not None:
                raise ValueError('assign 不接受命令 / 退出码 / 复验结论')
        emitted_command = None
        if command is not None or exitcode is not None:
            if command is None or exitcode is None:
                raise ValueError('命令与退出码必须一起给')
            emitted_command = _argv(command)
            emitted_code = _exitcode(exitcode)
        else:
            emitted_code = None
        if kind == 'verified':
            if emitted_command is None:
                raise ValueError('复验必须给可核命令 argv 与真实退出码，⛔ 不接受只写一个 passed 布尔')
            derived = emitted_code == 0
            if passed is not None and (type(passed) is not bool or passed != derived):
                raise IssueRuleError('passed 与退出码不一致：复验结论以实际退出码为准')
        else:
            derived = None
            if passed is not None:
                raise ValueError('passed 只用于 verified；其它动作请写 text')
        evidence = self._evidence(sources)

        def op(conn, rows):
            history = [r for r in rows if r['issue_id'] == issue_id]
            if not history:
                raise ValueError('问题不存在')
            state = history[-1]['payload']['state']
            if kind == 'fixed':
                if state != 'open':
                    raise IssueRuleError(f'只有 open 的问题能记修复（当前 {state}）')
                new_state = 'fixed'
            elif kind == 'verified':
                if state != 'fixed':
                    raise IssueRuleError('必须先记录修复，复验才有对象')
                new_state = 'closed' if derived else 'open'
            elif kind == 'reopened':
                if state not in ('closed', 'fixed'):
                    raise IssueRuleError(f'只有 closed / fixed 能重开（当前 {state}）')
                new_state = 'open'
            else:  # assigned：只改负责人，不改状态
                new_state = state
            payload = {'state': new_state, 'actor': actor.strip(), 'text': text.strip(),
                       'sources': evidence, 'time': time.time(), 'resume_allowed': new_state == 'closed',
                       'passed': None, 'verification': None}
            if kind == 'assigned':
                payload['owner'] = owner.strip()
            if emitted_command is not None:
                payload['command'] = emitted_command
                payload['exitcode'] = emitted_code
            if kind == 'verified':
                payload['passed'] = derived
                payload['verification'] = {'command': emitted_command, 'exitcode': emitted_code,
                                           'passed': derived, 'note': VERIFICATION_NOTE}
            return self._append(conn, rows, issue_id, kind, payload)
        return self._write(op)

    # -- 实验关联（E2/E4） ---------------------------------------------------

    def link(self, issue_id, *, exp_id, actor, reason, sources):
        """把一个问题**显式**关联到另一个实验（追加 `linked_exp` 事件）。

        * 旧记录一条都不改写：链接是**新事件**，读的时候由 `status()` 投影成
          `exp_links` / `effective_exps`；
        * 必须给理由（`reason`）与源码证据（`sources`）：⛔ 不做「悄悄改归属」；
        * 已经关联过这个实验（登记时的 `exp_id` 或此前的链接）⇒ 拒，不产生重复事件；
        * 只追加，不改状态：`open` 的仍是 `open`，`closed` 的仍是 `closed`。
        """
        if not _text(actor):
            raise ValueError('关联实验须给执行者')
        if not _text(reason):
            raise ValueError('关联实验须给理由（⛔ 不静默改归属）')
        if not _text(issue_id):
            raise ValueError('关联实验须给 issue_id')
        experiment = _exp_ref(exp_id)
        if experiment is None:
            raise ValueError('关联实验必须给非空 exp_id（要取消关联请另开一次显式动作）')
        evidence = self._evidence(sources)

        def op(conn, rows):
            history = [r for r in rows if r['issue_id'] == issue_id]
            if not history:
                raise ValueError('问题不存在')
            primary = history[0]['payload'].get('exp_id')
            primary = primary.strip() if isinstance(primary, str) and primary.strip() else None
            linked = {row['payload'].get('linked_exp') for row in history
                      if row['kind'] == 'linked_exp'}
            if experiment == primary:
                raise IssueRuleError(f'{issue_id} 登记时就关联在 {experiment}：不用再链接')
            if experiment in linked:
                raise IssueRuleError(f'{issue_id} 已经显式关联过 {experiment}：不重复链接')
            state = history[-1]['payload'].get('state')
            return self._append(conn, rows, issue_id, 'linked_exp',
                                {'state': state, 'linked_exp': experiment,
                                 'actor': actor.strip(), 'text': reason.strip(),
                                 'sources': evidence, 'time': time.time(),
                                 'resume_allowed': state == 'closed'})
        return self._write(op)

    # -- 归档 ---------------------------------------------------------------

    def _archive_dir(self, moment):
        return self._path('.autoresearch/issues/archive/' + time.strftime('%Y-%m-%d', time.gmtime(moment)))

    def _archive_target(self, issue_id, order, data, moment):
        """挑一个**不覆盖任何已存在文件**的路径：同内容复用，不同内容换名。"""
        directory = self._archive_dir(moment)
        directory.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, 100):
            name = f'{issue_id}-{order}.json' if attempt == 1 else f'{issue_id}-{order}-{attempt}.json'
            path = directory / name
            if not path.exists():
                return path, False
            if path.stat().st_size == len(data) and path.read_bytes() == data:
                return path, True  # 同内容已在盘上：复用，⛔ 不重写
        raise ValueError(f'归档文件名连试 99 次都被占用：{issue_id}-{order}')

    def _archive_failure(self, issue_id, actor, target, error):
        """写失败要留**可诊断**记录：单独一个事务追加 `archive_failed`，状态仍是 closed。"""
        def op(conn, rows):
            history = [r for r in rows if r['issue_id'] == issue_id]
            if not history:
                raise ValueError('问题不存在')
            return self._append(conn, rows, issue_id, 'archive_failed',
                                {'state': history[-1]['payload']['state'], 'actor': actor.strip(),
                                 'reason': 'archive_write_failed',
                                 'intended_path': target.relative_to(self.root).as_posix(),
                                 'error': f'{type(error).__name__}: {error}',
                                 'time': time.time(),
                                 'resume_allowed': history[-1]['payload']['state'] == 'closed'})
        return self._write(op)

    def archive(self, issue_id, *, actor):
        """把**整段**已结案历史复制成冷件，并把字节哈希与索引写回台账。

        * 只认「这次结案」一次：已归档过（最后一条就是 `archived`）⇒ 拒，不产生第二条；
        * 结案后又复发、再修复再复验 ⇒ 那是**新的**结案，允许再归档一次；
        * 同名文件不同内容 ⇒ 换名，**绝静默覆盖**；写失败 ⇒ 留 `archive_failed` 再抛错；
        * 热件历史一条都不删（append-only）；状态仍是 `closed`，`resume_allowed` 仍为 True。
        """
        if self.readonly:
            raise IssueRuleError('只读问题台账')
        if not _text(actor):
            raise ValueError('归档须给执行者')
        if not _text(issue_id):
            raise ValueError('归档须给 issue_id')
        history = self._history(issue_id)
        if not history:
            raise ValueError('问题不存在')
        if history[-1]['kind'] == 'archived':
            raise IssueRuleError('这次结案已经归过冷件：不重复归档（结案后复发并再次修复才算新的结案）')
        if history[-1]['payload'].get('state') != 'closed':
            raise IssueRuleError('未结案问题不能归冷件')
        moment = time.time()
        data = (json.dumps(history, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        intended = self._archive_dir(moment) / f'{issue_id}-{len(history)}.json'
        try:
            target, reused = self._archive_target(issue_id, len(history), data, moment)
            if not reused:
                with target.open('xb') as stream:  # 'x'：文件已存在就失败，⛔ 不覆盖
                    stream.write(data)
        except OSError as exc:
            # 目录建不出来 / 写不进去 / 名字被占：都要留可诊断记录，⛔ 不静默吞掉、⛔ 不覆盖。
            self._archive_failure(issue_id, actor, intended, exc)
            raise ValueError(f'归档写入失败（已在台账记 archive_failed）：{exc}') from None
        payload = {'state': 'closed', 'actor': actor.strip(),
                   'archive_path': target.relative_to(self.root).as_posix(),
                   'archive_sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                   'event_count': len(history), 'reused': reused,
                   'resume_allowed': True, 'time': moment}

        def op(conn, rows):
            current = [r for r in rows if r['issue_id'] == issue_id]
            if not current:
                raise ValueError('问题不存在')
            if current[-1]['kind'] == 'archived':
                raise IssueRuleError('这次结案已经归过冷件：不重复归档')
            if current[-1]['payload'].get('state') != 'closed':
                raise IssueRuleError('未结案问题不能归冷件')
            if len(current) != len(history):
                # 归档文件是照 len(history) 条事件写的；台账在期间变了就不能拿它当这次的冷件。
                raise IssueRuleError('台账在归档期间有新事件，冷件会与台账不符：重试 archive')
            return self._append(conn, rows, issue_id, 'archived', payload)
        return self._write(op)
