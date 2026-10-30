@unautomated @disposable
Feature: Observe Alloy permission reduction and authenticated scraping in a local replica
  # Draft contract, not an executed test. Intended binding:
  # tests/replica/alloy-security.nix and its GitOps-owned test script.
  # Slice A and B are separate bounded native test runs.

  Background:
    Given the target is the named disposable local Alloy replica
    And its chart, image, substrate and rendered payload are pinned
    And a fresh run ID is bound to the test derivation and observed cluster
    And no production endpoint or credential is present
    And the retained node proxy permission is recorded as a limitation

  Scenario: Refuse an unknown endpoint before any guest starts
    Given an extra target address has been added outside the reviewed transformation allowlist
    When the fixture payload is prepared
    Then preparation fails
    And no guest or Kubernetes client starts

  Scenario: Detect broad grants before proving their removal
    Given the former chart grants are bound to the replica scraper account
    And the fixture Secret, ConfigMap and pod log already exist with known synthetic contents
    When the account reads a fixture Secret, ConfigMap and pod log
    Then all three reads return HTTP 200 with their expected contents
    And the direct-read denial validator fails
    When the scoped chart is applied without removing those grants
    Then the same denial validator still fails
    When the exact former role and binding are removed
    Then all three direct reads return HTTP 403
    And missing objects, authentication failures and timeouts cannot satisfy denial
    And the account can discover nodes and Services in traefik and argocd
    And it cannot read Services in the unrelated fixture namespace
    And no inference is made about denial of indirect node proxy operations

  Scenario: Deliver fresh metrics through the configured scraper
    Given the scoped scraper runs the pinned image with projected local credentials
    And the isolated receiver has no prior samples or independent scrape jobs
    When the configured discovery and API proxy scrapes run
    Then both replica nodes produce fresh cAdvisor CPU and memory series
    And both nodes produce fresh kubelet name and running-pod series
    And local Traefik and Argo Service discovery deliver their fixture series
    And readiness without those series cannot pass the fixture

  Scenario Outline: Reject an untrusted server before sending the synthetic token
    Given each actual API proxy scrape block accepts the trusted matching control endpoint
    When the same pinned image connects to a <certificate> endpoint
    Then a TLS attempt and verification error are observed
    And the endpoint receives no authenticated HTTP request
    And no fresh successful scrape sample is accepted

    Examples:
      | certificate |
      | wrong-CA    |
      | wrong-name  |

  Scenario: Prove the TLS denial validator detects disabled verification
    Given the wrong-CA and wrong-name controls reject the current configuration
    When verification is disabled only in the disposable mutation
    Then both endpoints receive the synthetic authenticated request
    And the denial validator fails
    When the reviewed TLS settings are restored
    Then the trusted positive control works again
    And both untrusted controls are rejected

  Scenario: Stop a failed replica without touching existing infrastructure
    Given a replica assertion fails or its bounded deadline expires
    When the native driver exits
    Then both test guests are stopped
    And only test-owned temporary state is removed
    And no success marker is emitted
    And existing host contexts and clusters are unchanged
