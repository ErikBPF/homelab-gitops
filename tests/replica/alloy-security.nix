{ runId, imageArchive }:
let
  pins = builtins.fromJSON (builtins.readFile ./artifacts.json);
  desktop = builtins.getFlake "github:ErikBPF/desktop-nixos/${pins.desktop_revision}";
  pkgs = desktop.inputs.nixpkgs.legacyPackages.x86_64-linux;
  lib = pkgs.lib;
  python = pkgs.python3.withPackages (p: [ p.pyyaml ]);
  replica = lib.cleanSourceWith {
    src = ./.;
    filter = path: type: builtins.baseNameOf path != "__pycache__";
  };
  chartSource = lib.cleanSourceWith {
    src = ../../platform/alloy-metrics;
    filter = path: type: builtins.baseNameOf path != "charts";
  };
  chartArchive = pkgs.fetchurl {
    url = "https://github.com/grafana/helm-charts/releases/download/alloy-${pins.chart_version}/alloy-${pins.chart_version}.tgz";
    sha256 = pins.chart_sha256;
  };
  archive = builtins.path {
    path = imageArchive;
    name = "alloy-replica.oci.tar";
  };
  payload =
    pkgs.runCommand "alloy-replica-payload-${runId}"
      {
        nativeBuildInputs = [
          python
          pkgs.kubernetes-helm
        ];
      }
      ''
        cp -r ${chartSource} chart
        chmod -R u+w chart
        mkdir -p chart/charts
        cp ${chartArchive} chart/charts/alloy-1.14.3.tgz
        python3 ${replica}/prepare_payload.py chart "$out" ${lib.escapeShellArg runId}
      '';
  test = desktop.checks.x86_64-linux.k3s-smoke.extend {
    modules = [
      ({ config, lib, ... }: {
        name = lib.mkForce "homelab-gitops-alloy-replica-r21-${runId}";
        globalTimeout = 1800;
        passthru.payload = payload;
        nodes.server = {
          virtualisation.restrictNetwork = true;
          virtualisation.forwardPorts = lib.mkForce [ ];
          environment.systemPackages = [
            python
            pkgs.prometheus
          ];
        };
        nodes.agent = {
          virtualisation.restrictNetwork = true;
          virtualisation.forwardPorts = lib.mkForce [ ];
        };
        testScript = lib.mkForce ''
          RUN_ID = "${runId}"
          PAYLOAD = "${payload}"
          ARCHIVE = "${archive}"
          REPLICA = "${replica}"
          SERVER_IP = "${config.nodes.server.networking.primaryIPAddress}"
          ${builtins.readFile ./test_script.py}
        '';
      })
    ];
  };
in
assert builtins.match "[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}" runId != null;
assert builtins.hashFile "sha256" imageArchive == pins.image_archive_sha256;
test.overrideTestDerivation (_: {
  preferLocalBuild = true;
  allowSubstitutes = false;
  QEMU_OPTS = "";
  QEMU_NET_OPTS = "";
})
