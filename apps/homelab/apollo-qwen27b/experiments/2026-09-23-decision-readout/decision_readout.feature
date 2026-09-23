# Behavior contract for the decision readout sidecar (S1/S2).
# Automated: bound to test_decision_sidecar.py (stdlib unittest), which was
# observed failing (ModuleNotFoundError) before decision_sidecar.py existed.
# Scenarios marked @unautomated are contract only; no step binding exists yet.
# Seed: homelab/docs/plans/apollo-decision-model/one-pager.md, SHA256
# recorded on the plan; seed quote: "add a small lora layer to this model or a
# sidecar to also serve it as a decision model".
# Plan: homelab/docs/plans/apollo-decision-model/one-pager.md.
# Wire format: apolinario/decision-index @ 52a698928a9ae5bdf16b75687c903871db29c6e5.
# Readout echoes the chat-rendered context, then echoes each option key after it
# and sums the log-probabilities of the key's own tokens. The remaining ceiling
# is context capacity: a question whose options do not fit is refused (422),
# never truncated.
# DR-07 receipt: Apollo results/s2b-kit-results.jsonl (7/7 ok on a synthetic
# suite-shaped sample), produced by run-s2-kit.sh.

Feature: Decision readout sidecar over the served Apollo model
  A Decision Index request is answered with a typed distribution over exactly the
  supplied options, read from the model's own answer-slot log-probabilities.
  A question the engine cannot answer is refused, never guessed.

  @DR-01
  Scenario: A choice question returns a valid and correct distribution
    Given a state that names a colour and a two-option colour question
    When the readout sidecar answers the question
    Then the response model is echoed from the request
    And the answer type is choice
    And the probability keys equal the question criteria keys
    And the probabilities sum to one
    And the most likely option is the named colour

  @DR-02
  Scenario: A noul question returns a true-probability
    Given a state that names a colour and a yes-or-no question
    When the readout sidecar answers the question
    Then the answer type is noul
    And the noul value is a probability between zero and one

  @DR-03
  Scenario: A multi-token option key is scored over all of its tokens
    Given a question whose option key tokenizes to more than one token
    When the readout sidecar answers the question
    Then the multi-token option can be the most likely answer
    And its probability reflects the sum over the key's tokens
    And the probabilities still sum to one

  @DR-04
  Scenario: A capacity rejection passes through unchanged
    Given a state that exceeds the model's maximum context length
    When the readout sidecar answers the question
    Then the sidecar returns 422
    And the model's capacity message is preserved in the error

  @DR-05
  Scenario: A question type the engine cannot answer is refused
    Given a question of an unsupported type
    When the readout sidecar answers the question
    Then the sidecar returns 422
    And no answer is invented for that question

  @DR-06 @unautomated @S3
  Scenario: The readout is scored on the frozen suite
    Given the pinned decision-index-suite rows file
    When the reproduction kit runs its http engine against the sidecar
    Then every response passes the kit's validate
    And the Decision Index score and calibration error are recorded
    And no score is reported without the pinned suite hash

  @DR-07 @unautomated @S2
  Scenario: The authoritative kit accepts the sidecar's wire responses
    Given the reproduction kit's http engine pointed at the sidecar
    When the kit runs over a suite-shaped sample
    Then every response passes the kit's validate
    And each recorded status is ok
