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
      # nothing in the guest may open connections to the host itself. Replies
      # stay allowed: forwarded SSH arrives from that same address.
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
    # QEMU's user network forwards only a loopback listener on the receiving host.
    forwardPorts = [
      {
        from = "host";
        host.address = "127.0.0.1";
        host.port = 2222;
        guest.port = 22;
      }
    ];
    # The caller's HOME/workspace is never mounted into this guest.
    sharedDirectories = lib.mkForce { };
    # Nothing is shared, so the fixed-size shared memory backend is not needed;
    # without it `scripts/vm-run --memory` can resize the guest.
    qemu.enableSharedMemory = false;
    useNixStoreImage = true;
    writableStore = true;
  };
  services = {
    getty.autologinUser = "hackathon";
    openssh = {
      enable = true;
      openFirewall = true;
      # scripts/vm-run passes one public key per boot; see hackathon-ssh-key.
      authorizedKeysFiles = [ "/run/hackathon-ssh/%u" ];
      settings = {
        PasswordAuthentication = false;
        KbdInteractiveAuthentication = false;
        PermitRootLogin = "no";
      };
    };
  };
  users.users.hackathon = {
    isNormalUser = true;
    uid = 1000;
    extraGroups = [ "wheel" ];
    # An empty initial console password permits the first ordinary passwd call.
    # SSH rejects all password authentication; set a password on first boot.
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
    # The key lives on tmpfs: it follows the host's current choice at every boot
    # and no host file is mounted. A public key is not a secret.
    hackathon-ssh-key = {
      description = "Install the SSH public key passed by scripts/vm-run";
      wantedBy = [ "multi-user.target" ];
      before = [ "sshd.service" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ImportCredential = "hackathon.authorized_keys";
        RuntimeDirectory = "hackathon-ssh";
      };
      script = ''
        key="''${CREDENTIALS_DIRECTORY:-}/hackathon.authorized_keys"
        if [ -s "$key" ]; then
          install -m 0644 "$key" /run/hackathon-ssh/hackathon
        fi
      '';
    };
    # Seeding at boot instead of at the first interactive shell makes the project
    # available to SSH commands and editor remote sessions as well.
    hackathon-seed = {
      description = "Seed the reviewed project source into the workspace once";
      wantedBy = [ "multi-user.target" ];
      unitConfig.ConditionPathExists = "!/home/hackathon/workspace/python-vm";
      path = [ pkgs.git ];
      serviceConfig = {
        Type = "oneshot";
        User = "hackathon";
      };
      script = ''
        project="$HOME/workspace/python-vm"
        mkdir -p "$HOME/workspace"
        mkdir "$project"
        cp -R --no-preserve=mode,ownership /etc/hackathon-source/. "$project/"
        chmod -R u+w "$project"
        # Establish the Git filtering boundary before the user adds runtime data.
        git -C "$project" init -q -b main
        git -C "$project" remote add origin https://github.com/timfewi/python-vm.git
        git -C "$project" add -- .
        # Bob reads project skills from .bob/skills; the links are Git-ignored.
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
    Source is seeded into ~/workspace/python-vm at first boot.
    Set a console password: passwd
    Host connection: ssh -p 2222 hackathon@127.0.0.1
      scripts/vm-run passes your host's public SSH key at every boot.
    Run: cd ~/workspace/python-vm; just ready; just benchmark
    Join the team history: gh auth login; git fetch origin; git reset origin/main
    To update source later, use scripts/vm-copy from the host.
    Keep API keys in runtime .env or your existing secret manager, never in Nix.
  '';
  # A Git-backed flake supplies reviewed versioned source, excluding ignored
  # runtime credentials, virtual environments, datasets and benchmark traces.
  environment.etc."hackathon-source".source = hackathonSource;
  programs.bash.interactiveShellInit = ''
    if [ ! -e "$HOME/.hackathon-welcome" ]; then
      cat /etc/hackathon-help
      touch "$HOME/.hackathon-welcome"
    fi
  '';
  documentation.enable = false;
  environment.systemPackages = [ pkgs.cacert ];
}
