{
  description = "Efficient local agents: reproducible Python workbench and portable QEMU VM";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    vm-cli = {
      url = "git+https://github.com/timfewi/vm-cli.git?ref=main";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    bobshell = {
      url = "git+https://github.com/timfewi/bobshell.nix.git?ref=main";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # TypeSafe's own MIT-licensed guidance for building against Jev. Reviewed
    # at the locked revision: instructions only, no scripts.
    typesafe-skills = {
      url = "git+https://github.com/typesafe-ai/skills.git?ref=main";
      flake = false;
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      bobshell,
      typesafe-skills,
      vm-cli,
    }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfreePredicate = package: nixpkgs.lib.getName package == "bobshell";
      };
      workbench = import ./nix/tools.nix { inherit pkgs; };
      inherit (workbench) python;
      tools = workbench.packages;
      skills = import ./nix/skills.nix { inherit pkgs typesafe-skills; };
      bob = bobshell.packages.${system}.bobshell;
      mkShell =
        withBob:
        pkgs.mkShell {
          packages = tools ++ [ skills ] ++ nixpkgs.lib.optional withBob bob;
          HACKATHON_SKILLS_DIR = "${skills}/share/hackathon-skills";
          UV_PYTHON_DOWNLOADS = "never";
          UV_PYTHON = python.interpreter;
          shellHook = ''
            export PYTHONNOUSERSITE=1
            # Lets binary wheels (numpy, pandas, pymupdf, duckdb) find libstdc++ and zlib.
            export LD_LIBRARY_PATH=${nixpkgs.lib.makeLibraryPath workbench.wheelLibraries}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
            printf '%s\n' 'Python ${python.pythonVersion} workbench ready. Run: just ready; just practice'
          '';
        };
      mkVM =
        withBob:
        nixpkgs.lib.nixosSystem {
          inherit system;
          modules = [
            (nixpkgs + "/nixos/modules/virtualisation/qemu-vm.nix")
            self.nixosModules.default
            ./nix/vm.nix
            {
              hackathon.bob.enable = withBob;
              _module.args.hackathonSource = self;
            }
          ];
        };
      vm = mkVM false;
      vmWithBob = mkVM true;
      # A synthetic receiving host for checking the host module's contract.
      hostFixture = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          self.nixosModules.host
          {
            services.hacklab-vm = {
              enable = true;
              operatorName = "alice";
            };
            users.users.alice.isNormalUser = true;
            fileSystems."/" = {
              device = "/dev/disk/by-label/fixture";
              fsType = "ext4";
            };
            boot.loader.grub.enable = false;
            system.stateVersion = "26.05";
          }
        ];
      };
    in
    {
      devShells.${system} = {
        default = mkShell false;
        with-bob = mkShell true;
      };
      packages.${system} = {
        default = vm.config.system.build.vm;
        vm = vm.config.system.build.vm;
        vm-with-bob = vmWithBob.config.system.build.vm;
        tools = pkgs.buildEnv {
          name = "hackathon-tools";
          paths = tools;
        };
        inherit skills;
      };
      apps.${system} = {
        default = self.apps.${system}.vm;
        vm = {
          type = "app";
          program = "${vm.config.system.build.vm}/bin/run-hackathon-vm";
          meta.description = "Run the efficient local agents QEMU VM";
        };
        vm-with-bob = {
          type = "app";
          program = "${vmWithBob.config.system.build.vm}/bin/run-hackathon-vm";
          meta.description = "Run the QEMU VM with the existing IBM Bob package";
        };
      };
      nixosModules.default = import ./nix/module.nix {
        inherit
          tools
          python
          skills
          bob
          ;
      };
      # For a machine that hosts the VM; `default` configures a guest.
      nixosModules.host = import ./nix/host.nix {
        inherit self;
        vmCliModule = vm-cli.nixosModules.default;
      };
      nixosConfigurations = {
        hackathon = vm;
        hackathon-with-bob = vmWithBob;
      };
      checks.${system} = {
        # Where Ruff is not on PATH, scripts/ruff fetches this version: a
        # laptop formats and lints exactly as the Nix shell and the VM do.
        ruff-pin = pkgs.runCommand "hacklab-ruff-pin" { } ''
          grep -Fx 'version=${pkgs.ruff.version}' ${./scripts/ruff}
          touch "$out"
        '';
        vm-contract = pkgs.runCommand "hackathon-vm-contract" { } ''
          test '${toString vm.config.virtualisation.memorySize}' = 8192
          test '${toString vm.config.virtualisation.cores}' = 4
          test '${toString vm.config.virtualisation.diskSize}' = 40960
          test '${toString vm.config.services.openssh.enable}' = ""
          test '${toString (builtins.length vm.config.virtualisation.forwardPorts)}' = 0
          test '${toString vm.config.hackathon.bob.enable}' = ""
          test '${vm.config.environment.sessionVariables.UV_PYTHON}' = '${python}/bin/python3.15'
          test '${toString vm.config.programs.nix-ld.enable}' = 1
          # Rootless containers for testing the submission image; no daemon, no root group.
          test '${toString vm.config.virtualisation.podman.enable}' = 1
          test '${toString vm.config.virtualisation.docker.enable}' = ""
          touch "$out"
        '';
        host-contract =
          let
            unit = hostFixture.config.systemd.user.units."hacklab-vm.service".unit + "/hacklab-vm.service";
            manager = nixpkgs.lib.findFirst (
              package: nixpkgs.lib.getName package == "vm-cli"
            ) null hostFixture.config.environment.systemPackages;
            inventory =
              pkgs.writeText "hacklab-vm-inventory.json"
                hostFixture.config.environment.etc."vm-cli/config.json".text;
            customState =
              (hostFixture.extendModules {
                modules = [ { services.hacklab-vm.stateDirectory = "/home/alice/custom-state"; } ];
              }).config;
          in
          assert manager != null;
          assert hostFixture.config.programs.vm.vms.hacklab-vm.scope == "user";
          assert hostFixture.config.programs.vm.vms.hacklab-vm.unit == "hacklab-vm.service";
          assert hostFixture.config.programs.vm.vms.hacklab-vm.view == "console";
          assert hostFixture.config.programs.vm.vms.hacklab-vm.consoleSocket == "hacklab-vm/run/console.sock";
          assert hostFixture.config.systemd.user.services.hacklab-vm.wantedBy == [ ];
          assert
            customState.programs.vm.vms.hacklab-vm.consoleSocket == "/home/alice/custom-state/run/console.sock";
          pkgs.runCommand "hacklab-vm-host-contract" { } ''
            grep -F 'ConditionUser=alice' ${unit}
            grep -F '%S/hacklab-vm' ${unit}
            grep -F 'ExecStop=' ${unit}
            # Bob is opt-in: the default runner is the guest without it.
            test '${toString hostFixture.config.services.hacklab-vm.bob.enable}' = ""
            test '${hostFixture.config.services.hacklab-vm.runner}' = '${vm.config.system.build.vm}'
            # Off by default: nothing may pull the VM into a session start.
            ! grep -F 'WantedBy=' ${unit}
            printf 'q\n' | ${nixpkgs.lib.getExe manager} --config ${inventory} manage > menu
            grep -F 'hacklab-vm (user service)' menu
            touch "$out"
          '';
      };
      formatter.${system} = pkgs.nixfmt-tree;
    };
}
