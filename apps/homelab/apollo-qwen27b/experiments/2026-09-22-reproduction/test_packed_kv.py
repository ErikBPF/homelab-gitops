"""Guard the vLLM 0.30 packed attention page geometry used on Apollo."""

from dataclasses import dataclass

import torch
from lmcache.integration.vllm.kv_cache_group_edits import apply_kv_cache_group_edits
from vllm.v1.kv_cache_interface import FullAttentionSpec


@dataclass
class Group:
    layer_names: list[str]
    kv_cache_spec: FullAttentionSpec


@dataclass
class Config:
    kv_cache_groups: list[Group]
    has_mamba_layers: bool = True


def main():
    # Apollo: 864-token logical block, 32-token kernel page, packed K/V.
    spec = FullAttentionSpec(
        block_size=864, num_kv_heads=2, head_size=256, dtype=torch.uint8
    )
    for input_shape, hint, output_shape in (
        ((54, 32, 2, 512), "NHD", (2, 864, 1, 1024)),
        ((54, 32, 2, 512), "HND", (2, 1, 864, 1024)),
    ):
        pages = (torch.arange(54 * 32 * 2 * 512) % 251).to(torch.uint8).reshape(input_shape)
        result = apply_kv_cache_group_edits(
            Config([Group(["layer.0"], spec)]),
            {"layer.0": pages},
            {"kv_layout": hint},
        )["layer.0"]
        assert result.shape == output_shape, result.shape
        assert result.data_ptr() == pages.data_ptr()
        assert result.numel() == pages.numel()
        assert result.is_contiguous()
        assert torch.equal(result.flatten(), pages.flatten())

    # vLLM presents an NHD cache as a K/V-first, noncontiguous tensor.
    packed = (torch.arange(54 * 32 * 2 * 512) % 251).to(torch.uint8).reshape(54, 32, 2, 512)
    pages = packed.transpose(1, 2)
    result = apply_kv_cache_group_edits(
        Config([Group(["layer.0"], spec)]),
        {"layer.0": pages},
        {"kv_layout": "NHD"},
    )["layer.0"]
    assert result.shape == (2, 864, 1, 1024), result.shape
    assert result.data_ptr() == pages.data_ptr()
    assert result.numel() == pages.numel()
    assert result.is_contiguous()
    assert torch.equal(result.flatten(), packed.flatten())
    print("PACKED_KV_OK")


if __name__ == "__main__":
    main()
