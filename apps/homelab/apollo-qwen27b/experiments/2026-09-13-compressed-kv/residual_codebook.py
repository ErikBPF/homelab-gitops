"""Cartesian residual codebooks and frozen-attention fitting weights."""

import math

import torch

from cartesian_probe import _nearest


def _finite(tensor):
    if (not isinstance(tensor, torch.Tensor) or tensor.dtype != torch.float32
            or tensor.device.type not in ('cpu', 'cuda') or not tensor.numel()
            or not torch.isfinite(tensor).all()):
        raise ValueError('expected nonempty finite CPU/GPU FP32 tensor')


def _vectors(tensor):
    _finite(tensor)
    if tensor.ndim != 2 or tensor.shape[1] != 8:
        raise ValueError('expected eight-component vectors')


def _weights(weights):
    _finite(weights)
    if weights.ndim != 1 or (weights < 0).any():
        raise ValueError('expected nonnegative vector weights')


def normalize_weights(weights):
    """Floor relative to the positive mean, then normalize; report zero fallback."""
    _weights(weights)
    positive = weights[weights > 0].double()
    if not positive.numel():
        return torch.ones_like(weights), True
    bounded = weights.double().clamp_min(positive.mean() * 1e-4)
    normalized = (bounded / bounded.mean()).float()
    _finite(normalized)
    return normalized, False


def fit_centers(samples, weights, initial, iterations=12):
    """Use Euclidean assignments and weighted centroids, retaining empty centers."""
    _vectors(samples)
    _vectors(initial)
    _weights(weights)
    if (weights.shape != (len(samples),) or weights.device != samples.device
            or initial.device != samples.device or not (weights > 0).any()
            or type(iterations) is not int or iterations < 1):
        raise ValueError('invalid fitting weights, devices or iteration count')
    centers = initial.detach().clone()
    mass = weights.detach().double()
    weighted_samples = samples.detach().double() * mass[:, None]
    for _ in range(iterations):
        assigned = _nearest(samples, centers)
        totals = torch.zeros_like(centers, dtype=torch.float64).index_add_(0, assigned, weighted_samples)
        counts = torch.zeros(len(centers), dtype=torch.float64, device=samples.device).index_add_(0, assigned, mass)
        denominator = counts.masked_fill(counts == 0, 1)[:, None]
        centers = torch.where(counts[:, None] > 0, totals / denominator, centers.double()).float()
        _finite(centers)
    return centers.detach()


def fit_residual(samples, seed, stages, weights=None):
    """Fit four/five stages; losses include the initial and every stage's MSE."""
    _vectors(samples)
    if (not 256 <= len(samples) <= 8192 or type(seed) is not int
            or type(stages) is not int or stages not in (4, 5)):
        raise ValueError('expected 256..8192 samples, integer seed and four/five stages')
    if weights is None:
        weights = torch.ones(len(samples), device=samples.device)
    normalized, _ = normalize_weights(weights)
    if normalized.shape != (len(samples),) or normalized.device != samples.device:
        raise ValueError('sample weights must match the fitting rows and device')
    samples = samples.detach()
    generator = torch.Generator().manual_seed(seed)
    decoded = torch.zeros_like(samples)
    tables, losses = [], []

    def loss():
        error = (samples.double() - decoded.double()).square()
        result = ((error * normalized.double()[:, None]).sum() / (8 * normalized.double().sum())).item()
        if not math.isfinite(result):
            raise ValueError('nonfinite residual fitting objective')
        return result

    losses.append(loss())
    for _ in range(stages):
        residual = samples - decoded
        indices = torch.randperm(len(samples), generator=generator)[:256].to(samples.device)
        centers = fit_centers(residual, normalized, residual[indices])
        assigned = _nearest(residual, centers)
        decoded = decoded + centers[assigned]
        _finite(decoded)
        tables.append(centers)
        losses.append(loss())
    return {'tables': torch.stack(tables), 'losses': losses}


def roundtrip(tensor, tables):
    """Greedily encode residuals and reconstruct from the stored uint8 fields."""
    _finite(tensor)
    _finite(tables)
    if (tensor.ndim == 0 or tensor.shape[-1] % 8
            or tables.ndim != 3 or tables.shape[0] not in (4, 5)
            or tables.shape[1:] != (256, 8) or tables.device != tensor.device):
        raise ValueError('invalid residual tensor/table shape or device')
    vectors = tensor.reshape(-1, 8)
    decoded, payload = torch.zeros_like(vectors), []
    for centers in tables:
        stored = _nearest(vectors - decoded, centers).to(torch.uint8)
        decoded = decoded + centers[stored.long()]
        _finite(decoded)
        payload.append(stored)
    return decoded.reshape(tensor.shape), torch.stack(payload, dim=1), tensor.new_empty(0)


def attention_sensitivity(q, k, v, scale):
    """Return the diagonal of the frozen-output Jacobian Gram matrix for K/V."""
    for tensor in (q, k, v):
        _finite(tensor)
    if (q.ndim != 4 or k.ndim != 4 or v.shape != k.shape
            or q.shape[0] != k.shape[0] or q.shape[2] != 1
            or q.shape[-1] != k.shape[-1] or q.shape[1] % k.shape[1]
            or q.device != k.device or v.device != k.device
            or not isinstance(scale, (int, float)) or not math.isfinite(scale)):
        raise ValueError('invalid final-query GQA shape, device or attention scale')
    batch, heads, tokens, dimension = k.shape
    grouped_q = q.reshape(batch, heads, q.shape[1] // heads, dimension)
    scores = torch.einsum('bhgd,bhtd->bhgt', grouped_q, k) * scale
    _finite(scores)
    probabilities = scores.softmax(dim=-1)
    output = torch.einsum('bhgt,bhtd->bhgd', probabilities, v)
    distance = (v[:, :, None] - output[:, :, :, None]).square().sum(dim=-1)
    key = torch.einsum('bhgt,bhgd->bhtd', probabilities.square() * (scale ** 2) * distance, grouped_q.square())
    value = probabilities.square().sum(dim=2)[..., None].expand(batch, heads, tokens, dimension).clone()
    _finite(key)
    _finite(value)
    return key, value
