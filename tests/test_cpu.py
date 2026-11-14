import sys, unittest, importlib.util, pathlib, ast
sys.argv = [sys.argv[0], "--cpu"]
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import torch
from ant.teacache import AnimaTeaCacheRuntime, CacheDecision, _STEP_INFO_KEY
from ant.nodes import AnimaNativeTools

class RuntimeTests(unittest.TestCase):
    def runtime(self): return AnimaTeaCacheRuntime(.2, 0., 1., None)
    def opts(self, i): return {_STEP_INFO_KEY: {"index": i, "total": 10, "percent": i/10, "schedule_key": (10,9,8,7,6,5,4,3,2,1,0)}, "cond_or_uncond": [0]}
    def test_syntax(self):
        for file in ROOT.rglob("*.py"): ast.parse(file.read_text(encoding="utf-8"))
    def test_schema(self):
        self.assertEqual(AnimaNativeTools.RETURN_TYPES, ("MODEL", "CLIP"))
        self.assertIn("negpip", AnimaNativeTools.INPUT_TYPES()["required"])
    def test_first_last_full(self):
        r=self.runtime(); x=torch.ones(1,2,2)
        for i in (0,9): self.assertTrue(r.decide(x,self.opts(i)).should_calc)
    def test_cache_reuse(self):
        r=self.runtime(); x=torch.ones(1,2,2)
        d=r.decide(x,self.opts(0)); r.commit_residual(d,x*2)
        d=r.decide(x,self.opts(1)); self.assertFalse(d.should_calc)
        self.assertTrue(torch.equal(r.reuse_residual(d,x),x*3))
    def test_disabled_cache(self):
        r=self.runtime(); r.rel_l1_thresh=0; x=torch.ones(1,2,2)
        d=r.decide(x,self.opts(0)); r.commit_residual(d,x)
        self.assertTrue(r.decide(x,self.opts(1)).should_calc)
    def test_missing_schedule(self):
        r=self.runtime(); self.assertFalse(r.decide(torch.ones(1,2,2),{}).cacheable)
    def test_shape_change(self):
        r=self.runtime(); x=torch.ones(1,2,2); d=r.decide(x,self.opts(0)); r.commit_residual(d,x)
        self.assertTrue(r.decide(torch.ones(1,3,2),self.opts(1)).should_calc)
    def test_zero_denominator(self):
        r=self.runtime(); x=torch.zeros(1,2,2); d=r.decide(x,self.opts(0)); r.commit_residual(d,x)
        self.assertTrue(r.decide(x,self.opts(1)).should_calc)
    def test_reset_on_exception(self):
        r=self.runtime(); r.entries[1]='stale'
        def fail(*a,**k): r.entries[2]='new'; raise RuntimeError('expected')
        with self.assertRaises(RuntimeError): r.sample_wrapper(fail)
        self.assertEqual(r.entries,{})
    def test_layout_separation(self):
        r=self.runtime(); keys,slices=r._split_layout(torch.ones(4,2),{"cond_or_uncond":[0,1]})
        self.assertNotEqual(keys[0],keys[1]); self.assertEqual(slices,[slice(0,2),slice(2,4)])
    def test_cpu_only(self): self.assertFalse(torch.cuda.is_initialized())

if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]],verbosity=2)
