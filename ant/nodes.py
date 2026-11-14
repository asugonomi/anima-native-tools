"""ComfyUI entry point. No Forge imports or external node dependencies."""
class AnimaNativeTools:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("MODEL",), "clip": ("CLIP",),
            "negpip": ("BOOLEAN", {"default": True}),
            "teacache": ("BOOLEAN", {"default": False}),
            "threshold": ("FLOAT", {"default": 0.15, "min": 0.001, "max": 1., "step": 0.001}),
            "cache_start": ("FLOAT", {"default": 0., "min": 0., "max": 1.}),
            "cache_end": ("FLOAT", {"default": 1., "min": 0., "max": 1.}),
            "layer_replay": ("BOOLEAN", {"default": False}),
            "blocks": ("STRING", {"default": "3,4,5"}),
            "replay_start": ("FLOAT", {"default": 0.5, "min": 0., "max": 1.}),
            "replay_end": ("FLOAT", {"default": 0.6, "min": 0., "max": 1.}),
        }}
    RETURN_TYPES = ("MODEL", "CLIP")
    FUNCTION = "apply"
    CATEGORY = "Anima Native Tools"

    def apply(self, model, clip, negpip=True, teacache=False, threshold=.15,
              cache_start=0., cache_end=1., layer_replay=False, blocks="3,4,5",
              replay_start=.5, replay_end=.6):
        import torch
        import comfy.patcher_extension as pe
        from comfy.model_base import Anima
        from .negpip import patch_anima_negpip
        from .teacache import AnimaTeaCacheRuntime
        if not isinstance(model.model, Anima):
            raise TypeError("Anima Native Tools requires ComfyUI native Anima")
        if model.model_options.get("anima_native_tools"):
            raise ValueError("Use one Anima Native Tools node per model path; do not stack nodes")
        for start, end in ((cache_start, cache_end), (replay_start, replay_end)):
            if not 0 <= start <= end <= 1:
                raise ValueError("Ranges must satisfy 0 <= start <= end <= 1")
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        ids = set()
        if layer_replay:
            parts = [v.strip() for v in blocks.split(',')]
            count = len(model.get_model_object("diffusion_model").blocks)
            if not parts or any(not v.isascii() or not v.isdigit() or int(v) >= count for v in parts):
                raise ValueError(f"Block IDs must be comma-separated integers in 0..{count-1}")
            ids = {int(v) for v in parts}
        m, c = model.clone(), clip.clone()
        m.model_options["anima_native_tools"] = True
        if negpip:
            if m.model_options.get("ppm_negpip"):
                raise ValueError("Remove the separate PPM NegPiP node before enabling Native Tools NegPiP")
            patch_anima_negpip(m)
            m.model_options["ppm_negpip"] = True
            c.patcher.model_options["ppm_negpip"] = True
        if teacache or layer_replay:
            # The tensors stay on the model device; no additional CUDA initialization.
            runtime = AnimaTeaCacheRuntime(threshold if teacache else 0., cache_start, cache_end,
                                          None, ids, replay_start, replay_end)
            m.add_wrapper_with_key(pe.WrappersMP.DIFFUSION_MODEL, "anima_native_tools", runtime.diffusion_model_wrapper)
            m.add_wrapper_with_key(pe.WrappersMP.OUTER_SAMPLE, "anima_native_tools", runtime.sample_wrapper)
        return (m, c)

NODE_CLASS_MAPPINGS = {"AnimaNativeTools": AnimaNativeTools}
NODE_DISPLAY_NAME_MAPPINGS = {"AnimaNativeTools": "Anima Native Tools (NegPiP / TeaCache / Replay)"}
