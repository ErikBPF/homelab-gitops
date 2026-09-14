"""Teacher-forced cache propagation; implementation follows the RED anchor."""

import os
import sys

os.environ['USE_TF'] = '0'
os.environ['USE_FLAX'] = '0'
sys.path.append('/host-deps')

from transformers import DynamicCache


class QuantizedCache(DynamicCache):
    def __init__(self, config, codec, mode):
        super().__init__(config=config)


def evaluate_teacher_forced(model, token_ids, codec, mode):
    return None


def logit_metrics(reference, actual, targets):
    return None
