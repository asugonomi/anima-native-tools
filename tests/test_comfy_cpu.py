"""Run with ComfyUI portable Python; COMFYUI_PATH points at its ComfyUI folder."""
import os,sys,pathlib
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
sys.path.insert(0,os.environ["COMFYUI_PATH"])
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import comfy.cli_args
comfy.cli_args.args.cpu=True
import torch
from ant.negpip import anima_extra_conds_negpip_wrapper,cosmos_attn2_negpip,NEGPIP_MASK_KEY
from ant.teacache import AnimaTeaCacheRuntime
from comfy.model_base import Anima
seen={}
def base(**kwargs): seen.update(kwargs); return {}
out=anima_extra_conds_negpip_wrapper(base)(t5xxl_weights=torch.tensor([1.,-2.,0.]))
assert seen['t5xxl_weights'].tolist()==[1.,2.,0.]
mask=out['c_ppm_negpip_mask'].cond
assert mask.shape==(1,512,1)
assert mask[0,:3,0].tolist()==[1,-1,1]
v=torch.ones(1,512,2)
assert cosmos_attn2_negpip(v,v,v,extra_options={NEGPIP_MASK_KEY:mask})['v'][0,1,0].item()==-1

class Block:
    use_adaln_lora=False
    def __init__(self): self.calls=0
    def adaln_modulation_self_attn(self,t): return torch.zeros(1,1,3)
    def layer_norm_self_attn(self,x): return x
    def __call__(self,x,*args,**kwargs): self.calls+=1; return x+1
class Model:
    patch_temporal=1; patch_spatial=1
    def __init__(self):
        self.blocks=[Block(),Block()]
        self.t_embedder=[lambda t:t,lambda t:(torch.zeros(1,1,1),torch.zeros(1,1,3))]
    def t_embedding_norm(self,x): return x
    def prepare_embedded_sequence(self,x,**kw): return x,torch.zeros(1),None
    def final_layer(self,x,*args,**kw): return x
    def unpatchify(self,x): return x
m=Model(); x=torch.ones(1,1,1,1,1); context=torch.ones(1,512,1)
r=AnimaTeaCacheRuntime(0,0,1,None,{1},0,1); r.replay_active=True
result=r.forward(m,x,torch.ones(1),context)
assert result.item()==4 and [b.calls for b in m.blocks]==[1,2]
r.replay_active=False
result=r.forward(m,x,torch.ones(1),context)
assert result.item()==3
assert not torch.cuda.is_initialized()
print("PASS: installed ComfyUI imports; signed mask and attention; replay ON/OFF numerical calls; CUDA uninitialized")

