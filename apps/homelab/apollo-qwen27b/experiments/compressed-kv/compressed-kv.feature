# Draft behavior contract; unautomated. No step bindings or GPU checks exist yet.
# Seed: homelab/compressed-kv-hypotheses.md, SHA256
# 918f51209232fec0f18bb69213851ecc8f5544f72b70ae5944db0e9657d60fc1.
# Plan: homelab/docs/proposals/2026-09-13-apollo-compressed-kv-experiments.md.
# Intended binding: test_compressed_kv.py, stdlib unittest; CKV IDs map to slices.
# Numeric verdict thresholds remain proposed until contract acceptance.

Feature: Reproducible compressed KV experiments on Apollo
  Every requested variant receives evidence even when another variant loses.
  Invalid or unsupported experiments remain visible without becoming passed tests.

  @CKV-01 @S0
  Scenario Outline: A bounded trial refuses an invalid preflight
    Given a trial using devices selected from the two approved RTX 5060 Ti UUIDs
    And the preflight condition is "<condition>"
    When the operator requests the trial
    Then no experiment container starts
    And the receipt records "<reason>"

    Examples:
      | condition                       | reason                |
      | a requested UUID is the 3070     | excluded-device       |
      | another owner occupies the GPU  | ownership-conflict    |
      | model revision is unpinned      | missing-provenance    |
      | the deadline has expired        | expired-trial         |
      | per-device memory exceeds limit | blocked-resource      |
      | artifacts exceed storage budget | blocked-resource      |
      | local disk headroom is too low  | blocked-resource      |
      | another launcher holds the lock | ownership-conflict    |
      | a host fault remains uncleared  | host-health-blocked   |

  @CKV-02 @S2
  Scenario: Packed storage accounting includes fixed and variable costs
    Given 256 BF16 codebook entries of eight components each
    And every learned segment has two endpoints
    When the byte ledger is generated
    Then one endpoint table occupies 4096 bytes
    And a two-endpoint segment family occupies 8192 bytes
    And indices, coefficients, scales, headers, padding and norm tables are counted
    And replicated tables, protected layers and adapters are counted
    And device peak, host peak and wire bytes are reported separately
    And packing and unpacking reproduce every integer field including a partial page

  @CKV-02 @S2
  Scenario Outline: A malformed cache block is rejected before use
    Given a packed cache block with "<defect>"
    When the experiment attempts to restore it
    Then restoration is refused before allocation from unvalidated lengths
    And the existing request cache remains unchanged

    Examples:
      | defect                 |
      | truncated payload      |
      | mismatched model ID    |
      | mismatched codebook ID |
      | incompatible shape     |
      | invalid payload length |

  @CKV-03 @S1 @S2 @S3
  Scenario: Frozen codecs receive independent quality evidence
    Given calibration, comparator selection and final evaluation documents are disjoint
    And fitting queries and evaluation queries are disjoint
    And weights, tokenizer, masks, attention scaling and generation budgets are fixed
    When K-only, V-only and combined codecs process teacher-forced and free generation
    Then newly generated token caches use the selected packed representation
    And each of three fit seeds has an independent recorded result
    And English, Portuguese, retrieval, extraction, aggregation and code scores remain separate
    And a failed baseline task does not count as evidence of quality preservation
    And overlapping document IDs invalidate the comparison

  @CKV-04 @S4
  Scenario Outline: Optimized attention agrees with its reconstructed codec
    Given a packed cache with causal tails, GQA and the model's actual RoPE mapping
    And the model's configured attention multiplier is preserved
    When attention uses "<path>"
    Then its output matches explicit reconstructed attention within the frozen tolerance
    And normalization, softmax and zero-norm edge cases use the same convention
    And invalid results cannot receive a valid performance verdict

    Examples:
      | path                         |
      | full reconstruction          |
      | tiled fused reconstruction   |
      | query-to-key score table      |
      | value coefficient aggregation |

  @CKV-04 @S4
  Scenario: Normalized interpolation does not shrink midpoint key scores
    Given two unit endpoints separated by 90 degrees
    And the interpolation coefficient is one half
    When the normalized segment is scored through the query lookup table
    Then the raw midpoint norm is approximately 0.70710678
    And the inverse-norm factor restores unit direction before radius scaling
    And a changed query rebuilds its dependent lookup entries
    And near-antipodal endpoints follow the declared minimum-norm or rejection rule

  @CKV-05 @S5
  Scenario Outline: Memory transport preserves the selected codec
    Given two request caches with distinct context markers and the same frozen codec
    When the experiment runs "<mode>"
    Then every required attended block is present for each decode step
    And transferred packed bytes are unchanged across host and device
    And continuation agrees with the same codec's resident reference
    And logical token counts are not multiplied by tensor-parallel ranks
    And encoder, lookup, transfer and reconstruction costs appear in the receipt

    Examples:
      | mode                          |
      | GPU-resident decode           |
      | cold-prefix restore           |
      | active full-context streaming |

  @CKV-05 @S5
  Scenario: An external-prefix hit cannot be inferred from native reuse
    Given a known request prefix stored in the external cache
    When the native GPU prefix cache is reset successfully and the request is repeated
    Then the exact context marker is correct
    And external retrieval counters identify the logical restored tokens
    And configured capacity is reported separately from actual prompt occupancy

  @CKV-06 @S3
  Scenario: Hybrid and shared-cache model state remains valid
    Given a model with recurrent, partial-RoPE, sliding or shared-cache state
    When its adapter appends tokens and restores a packed attention block
    Then recurrent state retains the baseline precision and update behavior
    And shared aliases and sliding eviction follow the original model
    And no unsupported state layout silently falls back to a different model or codec

  @CKV-07 @S6
  Scenario: Frozen transfer and adaptation remain distinct experiments
    Given MiniCPM and Granite have different layer and head dimensions
    When a frozen global K/V dictionary transfers through the agreed eight-component convention
    Then its dictionary bytes and transform remain unchanged
    And specialized tables without a declared mapping are recorded as unsupported
    And independently refitted tables are labeled refitted
    And fixed reconstruction correction, fitting objectives and trained adapters have distinct IDs
    And trained adapters are compared with an equally trained uncompressed control

  @CKV-08 @S0 @S5
  Scenario Outline: Trial interruption cleans up only its own resources
    Given a trial has unique process, container, buffer and output ownership
    When "<event>" occurs during a pending transfer
    Then the trial stops using owned buffers before releasing them
    And only owned processes and listeners are terminated
    And shared dependency mounts and original model files are preserved
    And the receipt records cleanup and baseline host-health outcomes
    And a shared host or GPU fault blocks all further GPU trials until health is requalified

    Examples:
      | event                   |
      | trial deadline expires  |
      | operator interrupts     |
      | device allocation fails |
      | a new GPU error occurs  |

  @CKV-09 @S0 @S6
  Scenario: Negative scientific results do not prune requested variants
    Given H2 fails its quality threshold and P1 is slower than INT4
    When the campaign schedules remaining H1 through H9 and P1 through P5 cases
    Then H7, H9, all listed models and all remaining named variants stay scheduled
    And every result or blocker retains its original evidence
    And correctness and resource blockers do not count as measured negatives
    And an exhausted uncertainty budget produces an inconclusive verdict
    And the campaign cannot report complete with missing or unaccepted blocked cells
    And resume refuses changed model, dataset, code or threshold identities
