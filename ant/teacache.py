# Adapted from CocyNoric/ComfyUI-Anima-TeaCache (Apache-2.0).
# Modified: scoped resets, conditioning invalidation, integrated layer replay.
import math
from dataclasses import dataclass
from typing import Any

import torch


ANIMA_COEFFICIENTS = (
    16.440769544691957,
    -21.14123745515793,
    13.006701290921894,
    -1.6731977662609427,
    0.08632681059623949,
)
_STEP_INFO_KEY = "anima_teacache_step_info"
_WRAPPER_KEY = "anima_teacache"


def poly1d(coefficients, value):
    result = 0.0
    for coefficient in coefficients:
        result = result * value + coefficient
    return result


@dataclass
class CacheEntry:
    previous_modulated_input: torch.Tensor | None = None
    previous_residual: torch.Tensor | None = None
    accumulated_distance: float = 0.0
    signature: tuple[Any, ...] | None = None


@dataclass
class CacheDecision:
    should_calc: bool
    cacheable: bool
    keys: list[Any]
    slices: list[slice]


class AnimaTeaCacheRuntime:
    def __init__(self, rel_l1_thresh, start_percent, end_percent, cache_device, replay_blocks=(), replay_start=0.5, replay_end=0.6):
        self.replay_blocks = set(replay_blocks)
        self.replay_start = replay_start
        self.replay_end = replay_end
        self.context_key = None
        self.replay_active = False
        self.rel_l1_thresh = rel_l1_thresh
        self.start_percent = start_percent
        self.end_percent = end_percent
        self.cache_device = cache_device
        self.entries: dict[Any, CacheEntry] = {}
        self.schedule_key = None
        self.last_step_index = None

    def reset(self):
        self.entries.clear()
        self.schedule_key = None
        self.last_step_index = None

    def sample_wrapper(self, executor, *args, **kwargs):
        self.reset()
        self.context_key = None
        try:
            return executor(*args, **kwargs)
        finally:
            self.reset()
            self.context_key = None

    def diffusion_model_wrapper(self, executor, *args, **kwargs):
        diffusion_model = executor.class_obj
        transformer_options = dict(kwargs.get("transformer_options", {}))
        kwargs["transformer_options"] = transformer_options
        context = args[2] if len(args) > 2 else kwargs["context"]
        mask = kwargs.get("c_ppm_negpip_mask")
        old = self.context_key
        changed = old is None or not torch.equal(old[0], context) or ((old[1] is None) != (mask is None))
        if not changed and mask is not None:
            changed = not torch.equal(old[1], mask)
        if changed:
            self.entries.clear()
            self.context_key = (context.detach().clone(), None if mask is None else mask.detach().clone())
        step_info = self._build_step_info(
            transformer_options.get("sigmas"),
            transformer_options.get("sample_sigmas"),
        )
        replay = step_info is not None and self.replay_start <= step_info["percent"] <= self.replay_end
        if replay != self.replay_active:
            self.entries.clear()
        self.replay_active = replay
        if step_info is not None:
            transformer_options[_STEP_INFO_KEY] = step_info
        executor.original = lambda *inner_args, **inner_kwargs: self.forward(
            diffusion_model, *inner_args, **inner_kwargs
        )
        return executor(*args, **kwargs)

    def _build_step_info(self, current_sigmas, sample_sigmas):
        if (
            not torch.is_tensor(current_sigmas)
            or not torch.is_tensor(sample_sigmas)
        ):
            return None

        sigmas = sample_sigmas.detach().flatten()
        total_steps = sigmas.numel() - 1
        if total_steps <= 0:
            return None

        current_sigma = current_sigmas.detach().flatten()[0].to(
            device=sigmas.device, dtype=sigmas.dtype
        )
        step_index = int((sigmas[:-1] - current_sigma).abs().argmin().item())
        percent = step_index / total_steps
        return {
            "index": step_index,
            "total": total_steps,
            "percent": percent,
            "schedule_key": tuple(sigmas.float().cpu().tolist()),
        }

    def _split_layout(self, tensor, transformer_options):
        labels = transformer_options.get("cond_or_uncond")
        uuids = transformer_options.get("uuids")
        if not labels:
            return [0], [slice(0, tensor.shape[0])]
        if tensor.shape[0] % len(labels) != 0:
            return None

        chunk_size = tensor.shape[0] // len(labels)
        occurrences = {}
        keys = []
        slices = []
        for index, label in enumerate(labels):
            uuid = uuids[index] if uuids and len(uuids) == len(labels) else None
            identity = (label, uuid)
            occurrence = occurrences.get(identity, 0)
            occurrences[identity] = occurrence + 1
            keys.append((label, uuid, occurrence))
            slices.append(slice(index * chunk_size, (index + 1) * chunk_size))
        return keys, slices

    def decide(self, modulated_input, transformer_options):
        step_info = transformer_options.get(_STEP_INFO_KEY)
        layout = self._split_layout(modulated_input, transformer_options)
        if step_info is None or layout is None:
            self.reset()
            return CacheDecision(True, False, [], [])

        step_index = step_info["index"]
        schedule_key = step_info["schedule_key"]
        if (
            self.schedule_key != schedule_key
            or (step_index == 0 and self.last_step_index != 0)
            or (self.last_step_index is not None and step_index < self.last_step_index)
        ):
            self.reset()
            self.schedule_key = schedule_key
        self.last_step_index = step_index

        keys, slices = layout
        in_range = self.start_percent <= step_info["percent"] <= self.end_percent
        boundary = step_index == 0 or step_index == step_info["total"] - 1
        force_full = not in_range or boundary or self.rel_l1_thresh <= 0

        current_inputs = []
        for key, section in zip(keys, slices):
            current = modulated_input[section].detach().to(self.cache_device)
            current_inputs.append(current)
            entry = self.entries.setdefault(key, CacheEntry())
            signature = (tuple(current.shape), current.dtype, tuple(modulated_input[section].stride()))

            if entry.signature != signature:
                entry.previous_modulated_input = None
                entry.previous_residual = None
                entry.accumulated_distance = 0.0
                entry.signature = signature
                force_full = True

            previous = entry.previous_modulated_input
            if previous is None or entry.previous_residual is None:
                force_full = True
            elif not force_full:
                denominator = previous.abs().mean()
                if not torch.isfinite(denominator) or denominator.item() <= 0.0:
                    force_full = True
                else:
                    relative_l1 = ((current - previous).abs().mean() / denominator).float().item()
                    scaled = poly1d(ANIMA_COEFFICIENTS, relative_l1)
                    if not math.isfinite(scaled):
                        force_full = True
                    else:
                        entry.accumulated_distance += scaled
                        if entry.accumulated_distance >= self.rel_l1_thresh:
                            force_full = True

        for key, current in zip(keys, current_inputs):
            self.entries[key].previous_modulated_input = current

        if force_full:
            for key in keys:
                self.entries[key].accumulated_distance = 0.0

        return CacheDecision(force_full, True, keys, slices)

    def commit_residual(self, decision, residual):
        if not decision.cacheable:
            return
        for key, section in zip(decision.keys, decision.slices):
            self.entries[key].previous_residual = residual[section].detach().to(self.cache_device)

    def reuse_residual(self, decision, hidden_states):
        residuals = []
        for key in decision.keys:
            residual = self.entries[key].previous_residual
            if residual is None:
                raise RuntimeError("Anima TeaCache residual is not initialized")
            residuals.append(residual.to(device=hidden_states.device, dtype=hidden_states.dtype))
        return hidden_states + torch.cat(residuals, dim=0)

    def _first_block_modulated_input(
        self,
        diffusion_model,
        hidden_states,
        timestep_embedding,
        adaln_lora,
        extra_pos_emb,
    ):
        block = diffusion_model.blocks[0]
        block_input = hidden_states
        if extra_pos_emb is not None:
            block_input = block_input + extra_pos_emb

        modulation = block.adaln_modulation_self_attn(timestep_embedding)
        if block.use_adaln_lora:
            modulation = modulation + adaln_lora
        shift, scale, _ = modulation.chunk(3, dim=-1)
        shift = shift[:, :, None, None, :]
        scale = scale[:, :, None, None, :]
        return block.layer_norm_self_attn(block_input) * (1 + scale) + shift

    def forward(
        self,
        diffusion_model,
        x,
        timesteps,
        context,
        fps=None,
        padding_mask=None,
        **kwargs,
    ):
        import comfy.ldm.common_dit

        original_shape = list(x.shape)
        x = comfy.ldm.common_dit.pad_to_patch_size(
            x,
            (
                diffusion_model.patch_temporal,
                diffusion_model.patch_spatial,
                diffusion_model.patch_spatial,
            ),
        )
        hidden_states, rope_embedding, extra_pos_emb = diffusion_model.prepare_embedded_sequence(
            x, fps=fps, padding_mask=padding_mask
        )

        if timesteps.ndim == 1:
            timesteps = timesteps.unsqueeze(1)
        sinusoidal_timestep = diffusion_model.t_embedder[0](timesteps).to(hidden_states.dtype)
        timestep_embedding, adaln_lora = diffusion_model.t_embedder[1](sinusoidal_timestep)
        timestep_embedding = diffusion_model.t_embedding_norm(timestep_embedding)

        diffusion_model.affline_scale_log_info = {
            "t_embedding_B_T_D": timestep_embedding.detach()
        }
        diffusion_model.affline_emb = timestep_embedding
        diffusion_model.crossattn_emb = context

        if extra_pos_emb is not None:
            assert hidden_states.shape == extra_pos_emb.shape

        block_kwargs = {
            "rope_emb_L_1_1_D": rope_embedding.unsqueeze(1).unsqueeze(0),
            "adaln_lora_B_T_3D": adaln_lora,
            "extra_per_block_pos_emb": extra_pos_emb,
            "transformer_options": kwargs.get("transformer_options", {}),
        }
        if hidden_states.dtype == torch.float16:
            hidden_states = hidden_states.float()

        modulated_input = self._first_block_modulated_input(
            diffusion_model,
            hidden_states,
            timestep_embedding,
            adaln_lora,
            extra_pos_emb,
        )
        transformer_options = block_kwargs["transformer_options"]
        decision = self.decide(modulated_input, transformer_options)

        if decision.should_calc:
            original_hidden_states = hidden_states
            for index, block in enumerate(diffusion_model.blocks):
                hidden_states = block(
                    hidden_states,
                    timestep_embedding,
                    context,
                    **block_kwargs,
                )
                if self.replay_active and index in self.replay_blocks:
                    hidden_states = block(hidden_states, timestep_embedding, context, **block_kwargs)
            self.commit_residual(decision, hidden_states - original_hidden_states)
        else:
            hidden_states = self.reuse_residual(decision, hidden_states)

        output = diffusion_model.final_layer(
            hidden_states.to(context.dtype),
            timestep_embedding,
            adaln_lora_B_T_3D=adaln_lora,
        )
        output = diffusion_model.unpatchify(output)
        return output[
            :,
            :,
            : original_shape[-3],
            : original_shape[-2],
            : original_shape[-1],
        ]

