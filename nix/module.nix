{
  tools,
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
      description = "Expose a focused selection from the user's pinned skill catalogs.";
    };
  };
  config = lib.mkIf config.hackathon.enable {
    environment.systemPackages =
      tools
      ++ lib.optional config.hackathon.skills.enable skills
      ++ lib.optional config.hackathon.bob.enable bob;
    environment.sessionVariables = {
      PYTHONNOUSERSITE = "1";
      UV_PYTHON_DOWNLOADS = "never";
      UV_PYTHON = "${builtins.head tools}/bin/python3.12";
    }
    // lib.optionalAttrs config.hackathon.skills.enable {
      HACKATHON_SKILLS_DIR = "${skills}/share/hackathon-skills";
    };
  };
}
