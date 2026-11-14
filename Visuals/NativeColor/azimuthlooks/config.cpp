class CfgPatches
{
    class Azimuth_AvalonLooks
    {
        units[] = {};
        weapons[] = {};
        requiredVersion = 0.1;
        requiredAddons[] = {"DZ_Data"};
    };
};

class CfgMods
{
    class Azimuth_AvalonLooks
    {
        dir = "azimuthlooks";
        name = "Azimuth Avalon Looks";
        type = "mod";
        dependencies[] = {"Game", "Mission"};

        class defs
        {
            class gameScriptModule
            {
                value = "";
                files[] = {"azimuthlooks/scripts/3_game"};
            };
            class missionScriptModule
            {
                value = "";
                files[] = {"azimuthlooks/scripts/5_mission"};
            };
        };
    };
};
