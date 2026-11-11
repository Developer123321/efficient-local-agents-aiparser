# Local, model-free developer tooling. Runtime state and MCP enrollment belong
# to the project-local launcher, never to these immutable package inputs.
{ inputs, system }:
{
  ichnora = inputs.ichnora.packages.${system}.ichnora;
  ast-index = inputs.ast-index.packages.${system}.ast-index;
  agent-evidence = inputs.agent-evidence.packages.${system}.agent-evidence;
  agent-tool-meter = inputs.agent-tool-meter.packages.${system}.agent-tool-meter;
  tabula = inputs.tabula.packages.${system}.tabula;
}
