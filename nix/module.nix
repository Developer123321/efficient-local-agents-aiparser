{
  tools,
  python,
  skills,
  bob,
}:
{ config, lib, ... }:
{
  options.hackathon = {
    enable = lib.mkEnableOption "the efficient local agents workbench";
    bob.enable = lib.mkEnableOption "the existing IBM Bob Shell package";
    skills.enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Expose TypeSafe's pinned skill for building against Jev.";
    };
  };
  config = lib.mkIf config.hackathon.enable {
    environment.systemPackages =
      tools
      ++ lib.optional config.hackathon.skills.enable skills
      ++ lib.optional config.hackathon.bob.enable bob;
    # A stable path keeps linked skills valid when the system is rebuilt.
    environment.pathsToLink = lib.optional config.hackathon.skills.enable "/share/hackathon-skills";
    environment.sessionVariables = {
      PYTHONNOUSERSITE = "1";
      UV_PYTHON_DOWNLOADS = "never";
      UV_PYTHON = python.interpreter;
    }
    // lib.optionalAttrs config.hackathon.skills.enable {
      HACKATHON_SKILLS_DIR = "/run/current-system/sw/share/hackathon-skills";
    };
  };
}
