{
  pkgs,
  unix-skills,
  private-skills,
  typesafe-skills,
}:
let
  selected = {
    goal-workflow = unix-skills + "/skills/agent/goal-workflow";
    context-management = unix-skills + "/skills/agent/context-management";
    tool-use = unix-skills + "/skills/agent/tool-use";
    lean-code = unix-skills + "/skills/code/lean-code";
    fast-checks = unix-skills + "/skills/code/fast-checks";
    test-freshness = unix-skills + "/skills/code/test-freshness";
    repository-evidence = private-skills + "/skills/code/repository-evidence";
    ichnora-memory = private-skills + "/skills/agent/ichnora-memory";
    ast-navigation = private-skills + "/skills/code/ast-navigation";
    hypothesis-testing = private-skills + "/skills/code/hypothesis-testing";
    workload-measurement = private-skills + "/skills/agent/workload-measurement";
    python-task-scripts = private-skills + "/skills/code/python-task-scripts";
    typesafe-ai = typesafe-skills + "/skills/typesafe-ai";
  };
in
pkgs.linkFarm "hackathon-skills" (
  pkgs.lib.mapAttrsToList (name: path: {
    name = "share/hackathon-skills/${name}";
    inherit path;
  }) selected
)
