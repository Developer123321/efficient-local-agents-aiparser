# Guidance linked for the coding assistant. Only TypeSafe's own skill for
# building against Jev is selected; project conventions live in AGENTS.md.
{ pkgs, typesafe-skills }:
pkgs.linkFarm "hackathon-skills" [
  {
    name = "share/hackathon-skills/typesafe-ai";
    path = typesafe-skills + "/skills/typesafe-ai";
  }
]
