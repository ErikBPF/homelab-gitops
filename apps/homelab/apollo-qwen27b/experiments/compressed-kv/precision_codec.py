"""Shared grid and causal-group seams; assertion RED before implementation."""


def log_levels(span):
    return None


def grid_roundtrip(tensor, levels, clip=1.0, legacy_linear=False):
    return None


def grouped_roundtrip(tensor, codec, layout='token'):
    return None


class CausalGroupedCache:
    def __init__(self, config, codec, mode, k_layout='token', tail_tokens=32, table_bytes=None):
        pass

    def update(self, key_states, value_states, layer_idx, *args, **kwargs):
        return None

    def storage(self):
        return None


def evaluate_grouped(model, token_ids, codec, mode, k_layout='token', tail_tokens=32,
                     chunk_size=32, table_bytes=None):
    return None
