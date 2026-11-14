class CfgPatches
{
    class Azimuth_AvalonLogout
    {
        units[] = {};
        weapons[] = {};
        requiredVersion = 0.1;
        requiredAddons[] = {"DZ_Data"};
    };
};

class CfgMods
{
    class Azimuth_AvalonLogout
    {
        dir = "azimuthlogout";
        name = "Azimuth Avalon Logout";
        type = "mod";
        dependencies[] = {"Mission"};

        class defs
        {
            class missionScriptModule
            {
                value = "";
                files[] = {"azimuthlogout/scripts/5_mission"};
            };
        };
    };
};
