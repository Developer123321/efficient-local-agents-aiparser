# Host side of the workbench VM: an on-demand user service and its `hacklab-vm`
# command. The guest keeps its own pinned package set; nothing of the host's
# home, network configuration or package policy changes.
{ self }:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.hacklab-vm;
  system = "x86_64-linux";
  # systemd expands %S to the user's state directory when no path is configured.
  unitState = if cfg.stateDirectory == null then "%S/hacklab-vm" else cfg.stateDirectory;
  start = pkgs.writeShellApplication {
    name = "hacklab-vm-start";
    runtimeInputs = [
      pkgs.bash
      pkgs.coreutils
      pkgs.util-linux
    ];
    text = ''
      state=''${1:?state directory}
      umask 077
      mkdir -p -- "$state"
      export HACKATHON_VM_STATE="$state"
      export HACKATHON_VM_RUNNER=${cfg.runner}
      # Two private sockets in the state directory, no network listener: one
      # lets `stop` ask the guest to power off cleanly, the other carries the
      # guest console that `view` attaches to.
      export QEMU_OPTS="-qmp unix:qmp.sock,server=on,wait=off -chardev socket,id=console,path=console.sock,server=on,wait=off -device virtio-serial-pci -device virtconsole,chardev=console"
      exec bash ${../scripts/vm-run} --memory ${toString cfg.memoryMiB} --cpus ${toString cfg.vcpu}
    '';
  };
  stop = pkgs.writeShellApplication {
    name = "hacklab-vm-stop";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.socat
    ];
    text = ''
      state=''${1:?state directory}
      pid=''${2:?main process}
      # A relative address keeps the socket path within the kernel's length limit.
      if cd -- "$state/run" 2>/dev/null && [[ -S qmp.sock ]]; then
        {
          printf '%s\n' '{"execute":"qmp_capabilities"}' '{"execute":"system_powerdown"}'
          sleep 1
        } | socat - UNIX-CONNECT:qmp.sock >/dev/null || true
      fi
      for _ in $(seq 60); do
        kill -0 "$pid" 2>/dev/null || exit 0
        sleep 1
      done
      echo 'The guest did not power off within a minute; systemd stops it now.' >&2
    '';
  };
  command = pkgs.writeShellApplication {
    name = "hacklab-vm";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.socat
      pkgs.systemd
    ];
    text = ''
      readonly configured_state=${
        lib.escapeShellArg (if cfg.stateDirectory == null then "" else cfg.stateDirectory)
      }
    ''
    + builtins.readFile ../scripts/hacklab-vm.sh;
  };
in
{
  options.services.hacklab-vm = {
    enable = lib.mkEnableOption "the on-demand hacklab workbench VM and its hacklab-vm command";
    operatorName = lib.mkOption {
      type = lib.types.strMatching "[a-z_][a-z0-9_-]*";
      description = "Existing host account that owns and runs the VM.";
    };
    stateDirectory = lib.mkOption {
      type = lib.types.nullOr (lib.types.strMatching "/[^%[:space:]]*");
      default = null;
      example = "/home/alice/.local/state/hacklab-vm";
      description = ''
        Directory for the guest disk, the per-boot store image and control sockets.
        Null uses the operator's XDG state directory. Keep it on persistent
        storage; the disk holds credentials and work.
      '';
    };
    memoryMiB = lib.mkOption {
      type = lib.types.ints.between 2048 65536;
      default = 8192;
    };
    vcpu = lib.mkOption {
      type = lib.types.ints.between 1 32;
      default = 4;
    };
    bob.enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Run the guest variant that contains the unfree IBM Bob Shell package.";
    };
    runner = lib.mkOption {
      type = lib.types.package;
      readOnly = true;
      default = self.packages.${system}.${if cfg.bob.enable then "vm-with-bob" else "vm"};
      defaultText = lib.literalMD "this flake's `vm-with-bob` or `vm` package";
      description = ''
        The guest runner this host starts. A host that overrides this flake's
        inputs gets a different guest than the one tested here; build this
        option and pass it to `scripts/vm-smoke` as `HACKATHON_VM_RUNNER`.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = pkgs.stdenv.hostPlatform.system == system;
        message = "hacklab-vm currently supports x86_64-linux only.";
      }
      {
        assertion =
          builtins.hasAttr cfg.operatorName config.users.users
          && config.users.users.${cfg.operatorName}.isNormalUser;
        message = "services.hacklab-vm.operatorName must identify an existing normal host user.";
      }
    ];
    environment.systemPackages = [ command ];
    # No wantedBy: the VM runs only after `hacklab-vm start`.
    systemd.user.services.hacklab-vm = {
      description = "Hacklab workbench VM";
      unitConfig.ConditionUser = cfg.operatorName;
      serviceConfig = {
        ExecStart = "${lib.getExe start} ${unitState}";
        ExecStop = "${lib.getExe stop} ${unitState} $MAINPID";
        TimeoutStopSec = 90;
        MemoryMax = "${toString (cfg.memoryMiB + 2048)}M";
        TasksMax = 1024;
      };
    };
  };
}
