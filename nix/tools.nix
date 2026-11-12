{ pkgs }:
let
  python = pkgs.python315;
  # Escape hatch for an event-day dependency that has no CPython 3.15 wheel:
  # `export UV_PYTHON=3.14`, then sync. Only the versioned name is exposed, so `python3`
  # stays unambiguous.
  pythonFallback = pkgs.runCommand "python3.14-fallback" { } ''
    mkdir -p "$out/bin"
    ln -s ${pkgs.python314}/bin/python3.14 "$out/bin/python3.14"
  '';
in
{
  inherit python;
  # Binary wheels such as numpy, pandas, pymupdf and duckdb load these at import
  # time. The Nix interpreter does not search a global library directory.
  wheelLibraries = [
    pkgs.stdenv.cc.cc.lib
    pkgs.zlib
  ];
  packages = with pkgs; [
    python
    pythonFallback
    uv
    ruff
    git
    gh
    just
    jq
    yq-go
    ripgrep
    fd
    curl
    openssh
    sqlite
    duckdb
    poppler-utils
    # English, German and orientation data instead of every language model.
    (tesseract.override {
      enableLanguages = [
        "eng"
        "deu"
        "osd"
      ];
    })
    pandoc
    p7zip
    unzip
    file
    tree
    zstd
    tmux
    nano
    vim
    htop
    util-linux
    hyperfine
    time
    shellcheck
    nixfmt
  ];
}
