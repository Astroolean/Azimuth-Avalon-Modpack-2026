modded class MissionGameplay
{
    private const float AZIMUTH_GRADE_CHECK_INTERVAL = 0.15;
    private const ref array<int> m_AzimuthVisualEffects = {
        PPERequesterBank.REQ_BLOODLOSS,
        PPERequesterBank.REQ_DEATHEFFECTS,
        PPERequesterBank.REQ_UNCONEFFECTS,
        PPERequesterBank.REQ_TUNELVISSION,
        PPERequesterBank.REQ_BURLAPSACK,
        PPERequesterBank.REQ_FEVEREFFECTS,
        PPERequesterBank.REQ_FLASHBANGEFFECTS,
        PPERequesterBank.REQ_SHOCKHITEFFECTS,
        PPERequesterBank.REQ_HEALTHHITEFFECTS,
        PPERequesterBank.REQ_CONTAMINATEDAREA,
        PPERequesterBank.REQ_SPOOKYAREA,
        PPERequesterBank.REQ_DROWNING,
        PPERequesterBank.REQ_HMP_GHOST,
        PPERequesterBank.REQ_UNDERGROUND,
        PPERequesterBank.REQ_SANDSTORMEFFECT,
        PPERequesterBank.REQ_CAMERANV,
        PPERequesterBank.REQ_CAMERAADS,
        PPERequesterBank.REQ_GLASSESAVIATOR,
        PPERequesterBank.REQ_GLASSESSPORTBLACK,
        PPERequesterBank.REQ_GLASSESSPORTBLUE,
        PPERequesterBank.REQ_GLASSESSPORTGREEN,
        PPERequesterBank.REQ_GLASSESSPORTORANGE,
        PPERequesterBank.REQ_GLASSESDESIGNER,
        PPERequesterBank.REQ_GLASSESTACTICAL,
        PPERequesterBank.REQ_MOTOHELMETBLACK,
        PPERequesterBank.REQ_GLASSESWELDING
    };

    private float m_AzimuthGradeElapsed;
    private PPERequesterBase m_AzimuthDaylightGrade;

    override void OnMissionStart()
    {
        super.OnMissionStart();
        if (g_Game.IsDedicatedServer())
            return;

        string worldName = g_Game.GetWorldName();
        worldName.ToLower();
        if (worldName == "avalon")
            m_AzimuthDaylightGrade = PPERequesterBank.GetRequester(AzimuthAvalonGradeRequester);
    }

    override void OnUpdate(float timeslice)
    {
        super.OnUpdate(timeslice);
        if (!m_AzimuthDaylightGrade)
            return;

        m_AzimuthGradeElapsed += timeslice;
        if (m_AzimuthGradeElapsed < AZIMUTH_GRADE_CHECK_INTERVAL)
            return;

        m_AzimuthGradeElapsed = 0;
        bool shouldGrade = AzimuthCanGradeDaylight();
        bool isGrading = m_AzimuthDaylightGrade.IsRequesterRunning();
        if (shouldGrade && !isGrading)
            m_AzimuthDaylightGrade.Start();
        else if (!shouldGrade && isGrading)
            m_AzimuthDaylightGrade.Stop();
    }

    override void OnMissionFinish()
    {
        if (m_AzimuthDaylightGrade && m_AzimuthDaylightGrade.IsRequesterRunning())
            m_AzimuthDaylightGrade.Stop();

        m_AzimuthDaylightGrade = null;
        m_AzimuthGradeElapsed = 0;
        super.OnMissionFinish();
    }

    private bool AzimuthCanGradeDaylight()
    {
        PlayerBase player = PlayerBase.Cast(g_Game.GetPlayer());
        World currentWorld = g_Game.GetWorld();
        if (!currentWorld || !player || !player.IsAlive() || player.IsUnconscious() || currentWorld.IsNight())
            return false;
        if (player.IsSoundInsideBuilding())
            return false;

        int year, month, day, hour, minute;
        currentWorld.GetDate(year, month, day, hour, minute);
        if (hour < 8 || hour >= 17)
            return false;

        foreach (int effectId : m_AzimuthVisualEffects)
        {
            if (PPERequesterBank.IsRequesterRunning(effectId))
                return false;
        }
        return true;
    }
}
