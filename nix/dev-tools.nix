# Local, model-free developer tooling. Runtime state and MCP enrollment belong
# to the project-local launcher, never to these immutable package inputs.
{ inputs, system }:
{
  ichnora = inputs.ichnora.packages.${system}.ichnora;
  # Compatibility hold for this consumer: the followed Nixpkgs exports
  # LIBSQLITE3_SYS_USE_PKG_CONFIG=1 from its Cargo setup hook, while ast-index
  # at this lock declares no system SQLite and relies on rusqlite's bundled
  # copy. Without this the link fails with "cannot find -lsqlite3". Drop the
  # override once ast-index-nix declares its SQLite source itself.
  ast-index = inputs.ast-index.packages.${system}.ast-index.overrideAttrs (old: {
    preBuild = (old.preBuild or "") + ''
      unset LIBSQLITE3_SYS_USE_PKG_CONFIG
    '';
  });
  agent-evidence = inputs.agent-evidence.packages.${system}.agent-evidence;
  agent-tool-meter = inputs.agent-tool-meter.packages.${system}.agent-tool-meter;
  tabula = inputs.tabula.packages.${system}.tabula;
}
