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
  };

  outputs =
    {
      self,
      nixpkgs,
      bobshell,
      unix-skills,
      private-skills,
      ...
    }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfreePredicate = package: nixpkgs.lib.getName package == "bobshell";
      };
      tools = import ./nix/tools.nix { inherit pkgs; };
      skills = import ./nix/skills.nix { inherit pkgs unix-skills private-skills; };
      bob = bobshell.packages.${system}.bobshell;
      mkShell =
        withBob:
        pkgs.mkShell {
          packages = tools ++ [ skills ] ++ nixpkgs.lib.optional withBob bob;
          HACKATHON_SKILLS_DIR = "${skills}/share/hackathon-skills";
          UV_PYTHON_DOWNLOADS = "never";
          UV_PYTHON = "${pkgs.python312}/bin/python3.12";
          shellHook = ''
            export PYTHONNOUSERSITE=1
            printf '%s\n' 'Python 3.12 workbench ready. Run: uv sync --frozen; just doctor; just benchmark'
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
      nixosModules.default = import ./nix/module.nix { inherit tools skills bob; };
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
        touch "$out"
      '';
      formatter.${system} = pkgs.nixfmt-tree;
    };
}
