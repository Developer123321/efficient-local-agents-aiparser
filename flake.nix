{
  description = "Efficient local agents: reproducible Python workbench and portable QEMU VM";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    bobshell = {
      url = "git+https://github.com/timfewi/bobshell.nix.git?ref=main";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    unix-skills = {
      url = "git+https://github.com/timfewi/unix-agent-skills.git?ref=main";
      flake = false;
    };
    private-skills = {
      url = "git+https://github.com/timfewi/private-skills-catalog.git?ref=main";
      flake = false;
    };
    # TypeSafe's own MIT-licensed guidance for building against Jev. Reviewed
    # at the locked revision: instructions only, no scripts.
    typesafe-skills = {
      url = "git+https://github.com/typesafe-ai/skills.git?ref=main";
      flake = false;
    };
    # Only the local developer tools are composed here, not another assistant
    # deployment or its browser/model/service configuration.
    project-check = {
      url = "git+https://github.com/timfewi/project-check-nix.git?ref=main";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    ichnora = {
      url = "git+https://github.com/timfewi/ichnora.git?ref=main";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.project-check.follows = "project-check";
    };
    ast-index = {
      url = "git+https://github.com/timfewi/ast-index-nix.git?ref=main";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    agent-evidence = {
      url = "git+https://github.com/timfewi/agent-evidence-nix.git?ref=main";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.project-check.follows = "project-check";
    };
    agent-tool-meter = {
      url = "git+https://github.com/timfewi/agent-tool-meter-nix.git?ref=main";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.project-check.follows = "project-check";
    };
    tabula = {
      url = "git+https://github.com/timfewi/tabula-repo-nix.git?ref=main";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    inputs@{
      self,
      nixpkgs,
      bobshell,
      unix-skills,
      private-skills,
      typesafe-skills,
      ...
    }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfreePredicate = package: nixpkgs.lib.getName package == "bobshell";
      };
      workbench = import ./nix/tools.nix { inherit pkgs; };
      inherit (workbench) python;
      devTools = import ./nix/dev-tools.nix { inherit inputs system; };
      tools = workbench.packages ++ builtins.attrValues devTools;
      skills = import ./nix/skills.nix {
        inherit
          pkgs
          unix-skills
          private-skills
          typesafe-skills
          ;
      };
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
            printf '%s\n' 'Python ${python.pythonVersion} workbench ready. Run: uv sync --locked; just doctor; just benchmark'
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
    in
    {
      devShells.${system} = {
        default = mkShell false;
        with-bob = mkShell true;
      };
      packages.${system} = devTools // {
        default = vm.config.system.build.vm;
        vm = vm.config.system.build.vm;
        vm-with-bob = vmWithBob.config.system.build.vm;
        tools = pkgs.buildEnv {
          name = "hackathon-tools";
          paths = tools;
        };
        dev-tools = pkgs.buildEnv {
          name = "hackathon-dev-tools";
          paths = builtins.attrValues devTools;
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
      nixosConfigurations = {
        hackathon = vm;
        hackathon-with-bob = vmWithBob;
      };
      checks.${system}.vm-contract = pkgs.runCommand "hackathon-vm-contract" { } ''
        test '${toString vm.config.virtualisation.memorySize}' = 8192
        test '${toString vm.config.virtualisation.cores}' = 4
        test '${toString vm.config.virtualisation.diskSize}' = 40960
        test '${toString vm.config.services.openssh.settings.PasswordAuthentication}' = ""
        test '${toString vm.config.hackathon.bob.enable}' = ""
        test '${vm.config.environment.sessionVariables.UV_PYTHON}' = '${python}/bin/python3.15'
        test '${toString vm.config.programs.nix-ld.enable}' = 1
        touch "$out"
      '';
      formatter.${system} = pkgs.nixfmt-tree;
    };
}
