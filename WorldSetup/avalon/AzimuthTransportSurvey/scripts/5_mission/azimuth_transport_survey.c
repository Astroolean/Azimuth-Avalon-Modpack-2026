// Read-only terrain/geometry survey. No vehicle, character or inventory object
// is created, moved or deleted. The manager imports completed sites next start.
class AzimuthSurveyCandidate
{
    string route;
    float x;
    float z;
    float a;
};
class AzimuthSurveyPoint
{
    float x;
    float y;
    float z;
    float a;
    string surface;
    float footprint_radius_m;
    int sample_count;
    float min_depth_m;
    float max_height_delta_m;
    float obstacle_clearance_m;
};
class AzimuthSurveyRequest
{
    int schema_version;
    int algorithm_version;
    string world_name;
    string request_id;
    ref array<ref AzimuthSurveyCandidate> candidates;
    ref array<ref AzimuthSurveyCandidate> excluded;
};
class AzimuthSurveyResult
{
    int schema_version = 1;
    int algorithm_version = 1;
    string world_name;
    string request_id;
    string status;
    float world_size;
    ref array<ref AzimuthSurveyPoint> large_water = new array<ref AzimuthSurveyPoint>();
    ref array<ref AzimuthSurveyPoint> airship = new array<ref AzimuthSurveyPoint>();
    ref array<ref AzimuthSurveyPoint> air = new array<ref AzimuthSurveyPoint>();
};
class AzimuthTransportSurvey
{
    static const string REQUEST = "$profile:Azimuth/Transport_Placement_Request.json";
    static const string REPORT = "$profile:Azimuth/Transport_Placement_Survey.json";
    static ref AzimuthSurveyRequest s_Request;
    static ref AzimuthSurveyResult s_Result;
    static int s_Index;

    static bool Finite(float v)
    {
        return v == v && Math.AbsFloat(v) < 1000000;
    }

    static bool Distant(float x, float z, array<ref AzimuthSurveyPoint> points)
    {
        foreach (AzimuthSurveyPoint point : points)
        {
            float dx = x - point.x;
            float dz = z - point.z;
            if (dx * dx + dz * dz < 122500)
                return false;
        }
        return true;
    }

    static bool Separated(float x, float z)
    {
        foreach (AzimuthSurveyCandidate other : s_Request.excluded)
        {
            float dx = x - other.x;
            float dz = z - other.z;
            if (dx * dx + dz * dz < 122500)
                return false;
        }
        return Distant(x, z, s_Result.large_water) && Distant(x, z, s_Result.airship) && Distant(x, z, s_Result.air);
    }

    static AzimuthSurveyPoint Inspect(AzimuthSurveyCandidate candidate)
    {
        bool sea = candidate.route == "large_water";
        float radius = 30;
        float clearance = 50;
        if (sea)
        {
            radius = 100;
            clearance = 100;
        }
        else if (candidate.route == "airship")
        {
            radius = 40;
            clearance = 80;
        }
        else if (candidate.route != "air")
            return null;
        if (!Finite(candidate.x) || !Finite(candidate.z) || !Finite(candidate.a) || candidate.x <= radius || candidate.z <= radius || candidate.x >= s_Result.world_size - radius || candidate.z >= s_Result.world_size - radius)
            return null;
        if (!Separated(candidate.x, candidate.z))
            return null;
        float seaLevel = GetGame().SurfaceGetSeaLevel();
        float minimumSea = GetGame().SurfaceGetSeaLevelMin();
        float low = 100000;
        float high = -100000;
        float minimumDepth = 100000;
        // Entire square, including its boundary; collision uses the same square.
        for (int ix = -4; ix <= 4; ix++)
        {
            for (int iz = -4; iz <= 4; iz++)
            {
                float px = candidate.x + ix * radius / 4.0;
                float pz = candidate.z + iz * radius / 4.0;
                float ground = GetGame().SurfaceY(px, pz);
                if (!Finite(ground) || GetGame().SurfaceIsHole(px, pz))
                    return null;
                if (sea)
                {
                    if (!GetGame().SurfaceIsSea(px, pz) || !Finite(minimumSea) || minimumSea - ground < 12)
                        return null;
                    minimumDepth = Math.Min(minimumDepth, minimumSea - ground);
                }
                else
                {
                    if (GetGame().SurfaceIsSea(px, pz) || GetGame().SurfaceIsPond(px, pz))
                        return null;
                    vector normal = GetGame().SurfaceGetNormal(px, pz);
                    if (normal[1] < 0.995)
                        return null;
                }
                low = Math.Min(low, ground);
                high = Math.Max(high, ground);
                if (!sea && high - low > 0.5)
                    return null;
            }
        }
        float bottom = low - 0.2;
        float volumeHeight = clearance + high - low + 0.2;
        if (sea)
        {
            if (!Finite(seaLevel))
                return null;
            bottom = minimumSea - 10;
            volumeHeight = clearance;
        }
        ref array<Object> excluded = new array<Object>();
        ref array<Object> collisions = new array<Object>();
        vector center = Vector(candidate.x, bottom + volumeHeight / 2, candidate.z);
        vector size = Vector(radius * 2, volumeHeight, radius * 2);
        if (GetGame().IsBoxCollidingGeometry(center, "0 0 0", size, ObjIntersectGeom, ObjIntersectGeom, excluded, collisions))
            return null;
        ref array<Man> people = new array<Man>();
        GetGame().GetPlayers(people);
        foreach (Man player : people)
        {
            vector pos = player.GetPosition();
            if (Math.AbsFloat(pos[0] - candidate.x) < radius + 20 && Math.AbsFloat(pos[2] - candidate.z) < radius + 20)
                return null;
        }
        AzimuthSurveyPoint accepted = new AzimuthSurveyPoint();
        accepted.x = candidate.x;
        accepted.z = candidate.z;
        accepted.a = candidate.a;
        accepted.y = high + 0.3;
        accepted.surface = "land";
        accepted.min_depth_m = 0;
        if (sea)
        {
            accepted.y = seaLevel + 0.3;
            accepted.surface = "sea";
            accepted.min_depth_m = minimumDepth;
        }
        accepted.footprint_radius_m = radius;
        accepted.sample_count = 81;
        accepted.max_height_delta_m = high - low;
        accepted.obstacle_clearance_m = clearance;
        return accepted;
    }

    static void Finish(string status)
    {
        s_Result.status = status;
        string error;
        if (!JsonFileLoader<AzimuthSurveyResult>.SaveFile(REPORT, s_Result, error))
            Print("[Azimuth transport] Terrain survey save failed: " + error);
        else
            Print("[Azimuth transport] Automatic terrain survey " + status + "; large-water=" + s_Result.large_water.Count().ToString() + ", airship=" + s_Result.airship.Count().ToString() + ", aircraft=" + s_Result.air.Count().ToString());
        s_Request = null;
    }

    static void Step()
    {
        if (!GetGame() || !s_Request || !s_Result)
            return;
        if (s_Index >= s_Request.candidates.Count())
        {
            Finish("complete");
            return;
        }
        AzimuthSurveyCandidate candidate = s_Request.candidates[s_Index];
        s_Index++;
        bool need = candidate && ((candidate.route == "large_water" && s_Result.large_water.Count() < 4) || (candidate.route == "airship" && s_Result.airship.Count() < 2) || (candidate.route == "air" && s_Result.air.Count() < 4));
        if (need)
        {
            AzimuthSurveyPoint point = Inspect(candidate);
            if (point)
            {
                if (candidate.route == "large_water") s_Result.large_water.Insert(point);
                else if (candidate.route == "airship") s_Result.airship.Insert(point);
                else s_Result.air.Insert(point);
            }
        }
        // Bounded one-site-per-tick work; avoid delaying a whole server frame.
        GetGame().GetCallQueue(CALL_CATEGORY_SYSTEM).CallLater(AzimuthTransportSurvey.Step, 25, false);
    }

    static void Begin()
    {
        if (!GetGame() || !GetGame().IsServer() || !FileExist(REQUEST))
            return;
        string error;
        if (!JsonFileLoader<AzimuthSurveyRequest>.LoadFile(REQUEST, s_Request, error) || !s_Request || s_Request.schema_version != 1 || s_Request.algorithm_version != 1 || s_Request.request_id.Length() != 64 || !s_Request.candidates || s_Request.candidates.Count() > 2048 || !s_Request.excluded || s_Request.excluded.Count() > 128)
            return;
        string actualWorld = GetGame().GetWorldName();
        actualWorld.ToLower();
        string expectedWorld = s_Request.world_name;
        expectedWorld.ToLower();
        if (actualWorld != expectedWorld)
            return;
        foreach (AzimuthSurveyCandidate avoid : s_Request.excluded)
        {
            if (!avoid || !Finite(avoid.x) || !Finite(avoid.z))
                return;
        }
        AzimuthSurveyResult previous;
        if (FileExist(REPORT) && JsonFileLoader<AzimuthSurveyResult>.LoadFile(REPORT, previous, error) && previous && previous.request_id == s_Request.request_id && previous.status == "complete")
            return;
        s_Result = new AzimuthSurveyResult();
        s_Result.world_name = s_Request.world_name;
        s_Result.request_id = s_Request.request_id;
        s_Result.world_size = GetGame().GetWorld().GetWorldSize();
        if (!Finite(s_Result.world_size) || s_Result.world_size < 1000 || s_Result.world_size > 100000)
        {
            Finish("failed");
            return;
        }
        s_Index = 0;
        Step();
    }
};
modded class MissionServer
{
    override void OnMissionStart()
    {
        super.OnMissionStart();
        GetGame().GetCallQueue(CALL_CATEGORY_SYSTEM).CallLater(AzimuthTransportSurvey.Begin, 1000, false);
    }

    override void OnMissionFinish()
    {
        GetGame().GetCallQueue(CALL_CATEGORY_SYSTEM).Remove(AzimuthTransportSurvey.Step);
        GetGame().GetCallQueue(CALL_CATEGORY_SYSTEM).Remove(AzimuthTransportSurvey.Begin);
        super.OnMissionFinish();
    }
};
