{
  config,
  lib,
  pkgs,
  hackathonSource,
  ...
}:
let
  wheelLibraries = (import ./tools.nix { inherit pkgs; }).wheelLibraries;
in
{
  system.stateVersion = "26.05";
  networking = {
    hostName = "hackathon";
    firewall = {
      enable = true;
      # QEMU user networking maps 10.0.2.2 and fec0::2 to the host's loopback
      # services. The guest needs them only as a next hop and for DHCP, so
      # nothing in the guest may open connections to the host itself.
      extraCommands = ''
        for tables in iptables ip6tables; do
          $tables -N hackathon-host 2>/dev/null || $tables -F hackathon-host
          $tables -A hackathon-host -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
          $tables -A hackathon-host -p tcp -j REJECT
          $tables -A hackathon-host -p udp -j REJECT
        done
        iptables -I hackathon-host -p udp --dport 67 -j RETURN
        iptables -C OUTPUT -d 10.0.2.2 -j hackathon-host 2>/dev/null ||
          iptables -A OUTPUT -d 10.0.2.2 -j hackathon-host
        ip6tables -C OUTPUT -d fec0::2 -j hackathon-host 2>/dev/null ||
          ip6tables -A OUTPUT -d fec0::2 -j hackathon-host
      '';
      extraStopCommands = ''
        iptables -D OUTPUT -d 10.0.2.2 -j hackathon-host 2>/dev/null || true
        ip6tables -D OUTPUT -d fec0::2 -j hackathon-host 2>/dev/null || true
      '';
    };
  };
  hackathon.enable = true;
  virtualisation = {
    memorySize = 8192;
    cores = 4;
    diskSize = 40960;
    graphics = false;
    # No port is forwarded: the guest is reachable only through its console.
    forwardPorts = [ ];
    # The caller's HOME/workspace is never mounted into this guest.
    sharedDirectories = lib.mkForce { };
    # Nothing is shared, so the fixed-size shared memory backend is not needed;
    # without it `scripts/vm-run --memory` can resize the guest.
    qemu.enableSharedMemory = false;
    useNixStoreImage = true;
    writableStore = true;
    # The submission runs in a container. Rootless Podman builds and runs the
    # same Dockerfile without a daemon and without a root-equivalent group;
    # `docker` is an alias for it.
    podman = {
      enable = true;
      dockerCompat = true;
    };
  };
  services = {
    getty.autologinUser = "hackathon";
    # No SSH daemon and no listener of any kind; work happens on the console.
    openssh.enable = false;
  };
  users.users.hackathon = {
    isNormalUser = true;
    uid = 1000;
    extraGroups = [ "wheel" ];
    # An empty initial console password permits the first ordinary passwd call.
    # Nothing can log in over the network; set a password for sudo on first boot.
    initialHashedPassword = "";
  };
  users.users.root.initialHashedPassword = "!";
  security.sudo.wheelNeedsPassword = true;
  time.timeZone = "Europe/Vienna";
  nix.settings = {
    experimental-features = [
      "nix-command"
      "flakes"
    ];
    max-jobs = 2;
    cores = 4;
    trusted-users = [ "root" ];
  };

  # Prebuilt executables from PyPI wheels (ruff) and editor remote servers expect
  # the generic Linux loader. The guest and these libraries share one Nixpkgs.
  programs.nix-ld = {
    enable = true;
    libraries = wheelLibraries;
  };
  environment.sessionVariables.LD_LIBRARY_PATH = lib.makeLibraryPath wheelLibraries;

  systemd.services = {
    # Seeding at boot keeps the first console login free of setup work.
    hackathon-seed = {
      description = "Seed the reviewed project source into the workspace once";
      wantedBy = [ "multi-user.target" ];
      unitConfig.ConditionPathExists = "!/home/hackathon/workspace/hacklab-vm";
      path = [ pkgs.git ];
      serviceConfig = {
        Type = "oneshot";
        User = "hackathon";
      };
      script = ''
        project="$HOME/workspace/hacklab-vm"
        mkdir -p "$HOME/workspace"
        mkdir "$project"
        cp -R --no-preserve=mode,ownership /etc/hackathon-source/. "$project/"
        chmod -R u+w "$project"
        # Establish the Git filtering boundary before the user adds runtime data.
        git -C "$project" init -q -b main
        git -C "$project" remote add origin https://github.com/timfewi/hacklab-vm.git
        git -C "$project" add -- .
        # Bob reads project skills from .bob/skills; the link is Git-ignored.
        if ! HACKATHON_SKILLS_DIR=/run/current-system/sw/share/hackathon-skills \
          ${pkgs.runtimeShell} "$project/scripts/vm-skills" \
          "$project/${if config.hackathon.bob.enable then ".bob" else ".agents"}/skills"; then
          echo "Skill linking failed; run 'bash scripts/vm-skills' in the project." >&2
        fi
      '';
    };
  };

  environment.etc."hackathon-help".text = ''
    Welcome to the efficient local agents workbench.
    Source is seeded into ~/workspace/hacklab-vm at first boot.
    Set a console password: passwd
    This guest runs no SSH daemon and forwards no port; use this console.
    After resizing the terminal window, type: fit
    Run: cd ~/workspace/hacklab-vm; just ready; just practice
    Test the submission image offline: just container-test
    Join the team history: gh auth login; git fetch origin; git reset origin/main
    Keep API keys in runtime .env or your existing secret manager, never in Nix.
  '';
  # A Git-backed flake supplies reviewed versioned source, excluding ignored
  # runtime credentials, virtual environments, datasets and benchmark traces.
  environment.etc."hackathon-source".source = hackathonSource;
  programs.bash.interactiveShellInit = ''
    # A serial console cannot report its size; ask the terminal instead.
    fit() {
      local rows cols
      [ -t 0 ] || return 0
      printf '\e7\e[999;999H\e[6n\e8' >/dev/tty
      IFS='[;' read -rs -d R -t 1 _ rows cols </dev/tty || return 0
      case "$rows$cols" in
        *[!0-9]* | "") return 0 ;;
      esac
      stty rows "$rows" cols "$cols"
    }
    # The host service's console; the foreground runner and the smoke test use ttyS0.
    if [ "$(tty)" = /dev/hvc0 ]; then fit; fi
    if [ ! -e "$HOME/.hackathon-welcome" ]; then
      cat /etc/hackathon-help
      touch "$HOME/.hackathon-welcome"
    fi
  '';
  documentation.enable = false;
  environment.systemPackages = [
    pkgs.cacert
    # Files reach an SSH-less guest through the network: `wormhole receive`.
    pkgs.magic-wormhole
  ];
}
