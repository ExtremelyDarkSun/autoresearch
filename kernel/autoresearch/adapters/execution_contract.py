# GENERATED-BY: portable extraction, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Reusable research mechanism or offline validation.
# ORIGIN: agent-result-bundle contract extracted from mixed runtime
# CUMULATIVE-DELTA: Instance history removed; state roots and target contracts separated.
from dataclasses import dataclass
from ..runtime.execution import RunSpec, Execution, ResultBundle, digest, sha_valid
from ..runtime.service import Runtime
import json, posixpath
@dataclass(frozen=True)
class AgentRunSpec(RunSpec):
    required_artifacts: tuple[str, ...] = ('task_results.jsonl', 'sweep_results.jsonl', 'SUMMARY.json')
    def validate(self, *, require_execution=False):
        super().validate(require_execution=require_execution)
        if require_execution and (not isinstance(self.model_id, str) or not self.model_id.strip()):
            raise ValueError('agent evaluation requires explicit model_id')
        if not {'task_results.jsonl', 'sweep_results.jsonl', 'SUMMARY.json'}.issubset(self.required_artifacts):
            raise ValueError('agent bundle requires task, sweep and summary results')
        return self

    def artifact_checklist(self):
        return tuple(self.required_artifacts) + tuple((path for tid in self.task_ids for path in (f'traces/trace_{tid}.json', f'patches/{tid}.patch')))

class AgentExecution(Execution):
    spec_type = AgentRunSpec
    @staticmethod
    def validate_results(spec, bundle):
        errors = []
        if not isinstance(bundle, ResultBundle):
            raise ValueError('adapter必须返回ResultBundle')
        for path in spec.artifact_checklist():
            content = bundle.artifacts.get(path)
            if content is None:
                errors.append('missing:' + path)
            elif not isinstance(content, bytes) or (not content and (not path.startswith('patches/'))):
                errors.append('empty:' + path)
            elif bundle.manifest.get(path) != digest(content):
                errors.append('hash_mismatch:' + path)
        for path, content in bundle.artifacts.items():
            if not isinstance(content, bytes) or not sha_valid(bundle.manifest.get(path)) or bundle.manifest.get(path) != digest(content):
                errors.append('invalid_manifest:' + path)
            if path.startswith('/') or posixpath.normpath(path) != path or path.startswith('../') or (path in {'', '.', '..'}):
                errors.append('unsafe_path:' + path)
        ids = []
        content = bundle.artifacts.get('task_results.jsonl', b'')
        if isinstance(content, bytes):
            try:
                for line in content.decode('utf-8-sig').splitlines():
                    if line.strip():
                        record = json.loads(line)
                        if not isinstance(record, dict) or not record.get('instance_id') or 'resolved' not in record or (type(record['resolved']) is not bool):
                            raise ValueError('结果行必须包含题号和布尔resolved')
                        ids.append(record['instance_id'])
            except (UnicodeError, ValueError, TypeError):
                errors.append('invalid_results_rows')
        if not ids:
            errors.append('no_results')
        if len(ids) != len(set(ids)):
            errors.append('duplicate_results')
        if set(ids) != set(spec.task_ids):
            errors.append('task_set_mismatch')
        sweep_ids = []
        try:
            for line in bundle.artifacts.get('sweep_results.jsonl', b'').decode('utf-8-sig').splitlines():
                if line.strip():
                    record = json.loads(line)
                    tid = record.get('instance_id') or record.get('task_id')
                    if not isinstance(tid, str):
                        raise ValueError('sweep结果缺题号')
                    sweep_ids.append(tid)
            if len(sweep_ids) != len(set(sweep_ids)) or set(sweep_ids) != set(spec.task_ids):
                errors.append('sweep_task_set_mismatch')
        except (AttributeError, UnicodeError, ValueError, TypeError):
            errors.append('invalid_sweep_rows')
        return tuple(sorted(set(errors)))

class AgentRuntime(Runtime):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('execution_class', AgentExecution)
        super().__init__(*args, **kwargs)
