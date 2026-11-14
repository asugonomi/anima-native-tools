# Third-party source notices

- `ant/negpip.py`: adapted from `src/negpip/anima_negpip.py` in [pamparamm/ComfyUI-ppm](https://github.com/pamparamm/ComfyUI-ppm), under AGPL-3.0-or-later. Changes isolate transformer options and clear stale sign masks.
- `ant/teacache.py`: adapted from `nodes.py` in [CocyNoric/ComfyUI-Anima-TeaCache](https://github.com/CocyNoric/ComfyUI-Anima-TeaCache), under Apache-2.0. The full license is retained in `licenses/Apache-2.0-TeaCache.txt`. Changes add per-sampling cleanup, conditioning invalidation, and Layer Replay.
- `scripts/anima_native.py`: Forge Neo adaptation of the NegPiP / Anima TeaCache workflow, including the sampler preset table and coefficient table from ComfyUI-Anima-TeaCache.

The combined source distribution is provided under AGPL-3.0-or-later; see `LICENSE`. Preserve the above attribution and the included license texts when redistributing. Source snapshot SHA-256 hashes are recorded in `SOURCE_MANIFEST.json`.

No model weights, LoRA files, prompts, user settings, or training data are included.
