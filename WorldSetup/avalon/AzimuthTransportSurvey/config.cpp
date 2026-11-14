class CfgPatches
{
    class Azimuth_TransportSurvey
    {
        units[] = {}; weapons[] = {}; requiredVersion = 0.1;
        requiredAddons[] = {"DZ_Data"};
    };
};
class CfgMods
{
    class Azimuth_TransportSurvey
    {
        dir = "azimuthtransportsurvey";
        name = "Azimuth Transport Terrain Survey";
        type = "mod";
        dependencies[] = {"Mission"};
        class defs
        {
            class missionScriptModule
            {
                value = "";
                files[] = {"azimuthtransportsurvey/scripts/5_mission"};
            };
        };
    };
};
