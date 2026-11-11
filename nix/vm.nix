{
  lib,
  pkgs,
  hackathonSource,
  ...
}:
{
  system.stateVersion = "26.05";
  networking = {
    hostName = "hackathon";
    firewall.enable = true;
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
    useNixStoreImage = true;
    writableStore = true;
  };
  services = {
    getty.autologinUser = "hackathon";
    openssh = {
      enable = true;
      openFirewall = true;
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
  environment.etc."hackathon-help".text = ''
    Welcome to the efficient local agents workbench.
    Source is seeded into ~/workspace/python-vm at first login.
    Set a password: passwd
    Install an SSH public key in ~/.ssh/authorized_keys (chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys).
    Host connection: ssh -p 2222 hackathon@127.0.0.1
    Run: cd ~/workspace/python-vm; uv sync --frozen; just doctor; just benchmark
    To update source later, use scripts/vm-copy from the host.
    Keep API keys in runtime .env or your existing secret manager, never in Nix.
  '';
  # A Git-backed flake supplies reviewed versioned source, excluding ignored
  # runtime credentials, virtual environments, datasets and benchmark traces.
  environment.etc."hackathon-source".source = hackathonSource;
  programs.bash.interactiveShellInit = ''
    if [ ! -e "$HOME/workspace/python-vm" ]; then
      mkdir -p "$HOME/workspace"
      if mkdir "$HOME/workspace/python-vm" 2>/dev/null; then
        cp -R --no-preserve=mode,ownership /etc/hackathon-source/. "$HOME/workspace/python-vm/"
        chmod -R u+w "$HOME/workspace/python-vm"
        # Establish the Git filtering boundary before the user adds runtime data.
        git -C "$HOME/workspace/python-vm" init -q
        git -C "$HOME/workspace/python-vm" add -- .
      fi
    fi
    if [ ! -e "$HOME/.hackathon-welcome" ]; then
      cat /etc/hackathon-help
      touch "$HOME/.hackathon-welcome"
    fi
  '';
  documentation.enable = false;
  environment.systemPackages = [ pkgs.cacert ];
}
