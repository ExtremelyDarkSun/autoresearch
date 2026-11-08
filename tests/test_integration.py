# GENERATED-BY: portable extraction / repository bootstrap, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Portable project entrypoint or verification.
# ORIGIN: none
# CUMULATIVE-DELTA: Independent kernel and target profile integration.
"""Integration checks for generic execution and complete instruction handling."""
import tempfile
import unittest
from pathlib import Path
from kernel.autoresearch.demo import run_demo
from kernel.autoresearch.context import instruction_manifest, render_bundle, verify_injection
from kernel.autoresearch.runtime.execution import RunSpec, Execution, ResultBundle, digest, canonical

ROOT=Path(__file__).resolve().parents[1]

class IntegrationTests(unittest.TestCase):
    def test_empty_state_demo_completes_once(self):
        with tempfile.TemporaryDirectory() as directory:
            result=run_demo(Path(directory)/'demo')
            self.assertEqual(result['state'],'completed')
            self.assertEqual(result['submit_calls'],1)
            self.assertEqual(result['spent_units'],7)
            with self.assertRaises(FileExistsError):
                run_demo(Path(directory)/'demo')

    def test_kernel_does_not_require_agent_patch_or_trace(self):
        config={'backend':'synthetic'}
        spec=RunSpec('e','r','a'*64,'b'*64,'c'*64,'/unit','/unit/code','/unit/env','/unit/input','/unit/output',
                     ('case',),'reservation','lease',True,False,
                     execution_config=config,execution_config_fingerprint=digest(canonical(config).encode()))
        spec.validate(require_execution=True)
        self.assertEqual(spec.artifact_checklist(),('results.json',))
        content=b'{"metric": 1}'
        self.assertEqual(Execution.validate_results(spec,ResultBundle({'results.json':content},{'results.json':digest(content)})),())
        bad=ResultBundle({'results.json':content,'../escape':b'x'}, {'results.json':digest(content),'../escape':digest(b'x')})
        self.assertIn('unsafe_path:../escape',Execution.validate_results(spec,bad))

    def test_complete_bundle_and_omission_are_distinct(self):
        manifest=instruction_manifest(ROOT,ROOT/'profiles/agent-harness')
        bundle=render_bundle(manifest)
        self.assertTrue(verify_injection(manifest,bundle)['complete'])
        self.assertGreater(manifest['suggested_max_bytes'],manifest['bundle_bytes'])
        missing=verify_injection(manifest,bundle.replace(manifest['documents'][0]['content'],''))
        self.assertFalse(missing['complete'])
        omitted=verify_injection(manifest,bundle+'\nWorkspace instruction budget 65536 bytes: omitted other/AGENTS.md\n')
        self.assertFalse(omitted['complete'])
        self.assertEqual(len(omitted['omission_lines']),1)

    def test_initialized_workspace_and_governance_reads_are_readonly(self):
        import contextlib, io, json
        from kernel.autoresearch.cli import main
        from kernel.autoresearch.governance_cli import main as governance_main
        with tempfile.TemporaryDirectory() as directory:
            workspace=Path(directory)/'workspace'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['init','--workspace',str(workspace)]),0)
            db=workspace/'.autoresearch/runtime.sqlite3'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(governance_main(['--db',str(db),'--commit','freeze','--file',str(ROOT/'kernel/templates/plan.template.json')]),0)
            before=db.read_bytes()
            out=io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(governance_main(['--db',str(db),'plan','--id','<plan-id>']),0)
            self.assertEqual(json.loads(out.getvalue())['plan']['id'],'<plan-id>')
            self.assertEqual(before,db.read_bytes())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(governance_main(['--db',str(db),'plan','--id','absent']),2)
            fixture=workspace/'intent.json'
            fixture.write_text(json.dumps({'intent_id':'I1','quote':'retain regression coverage','source_session':'synthetic','seq':1}),encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                for command in ('sync-user','intent-new'):
                    self.assertEqual(governance_main(['--db',str(db),'--commit',command,'--file',str(fixture)]),0)
                self.assertEqual(governance_main(['--db',str(db),'intent-state','--id','I1']),0)
            fixture.write_text('{}',encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(governance_main(['--db',str(db),'--commit','intent-new','--file',str(fixture)]),2)
            missing=workspace/'missing.sqlite3'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(governance_main(['--db',str(missing),'lessons']),2)
            self.assertFalse(missing.exists())

    def test_unconfigured_profile_never_builds_evaluation_command(self):
        import contextlib, io
        from kernel.autoresearch.cli import main
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['profile','--tasks','synthetic.jsonl','--output','synthetic-output']),2)

if __name__=='__main__': unittest.main()
