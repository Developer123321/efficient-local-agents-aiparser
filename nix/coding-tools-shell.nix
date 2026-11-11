# Optional composition inside a consumer that already pins coding-agents-nix.
# Runtime services, model credentials and coding-agent profiles remain host-owned.
{
  workbench,
  coding-agents,
  system ? "x86_64-linux",
}:
let
  pkgs = workbench.inputs.nixpkgs.legacyPackages.${system};
in
pkgs.mkShell {
  inputsFrom = [ workbench.devShells.${system}.default ];
  packages = coding-agents.lib.${system}.toolPackages;
}
