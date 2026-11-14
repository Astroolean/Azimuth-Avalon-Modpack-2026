class AzimuthAvalonGradeRequester : PPERequesterBase
{
    // Vanilla tint layers begin at 50. Keep this grade below them.
    static const int DAYLIGHT_LAYER = 10;

    override protected void OnStart(Param par = null)
    {
        super.OnStart(par);

        SetTargetValueColor(PostProcessEffectType.Glow, PPEGlow.PARAM_COLORIZATIONCOLOR,
            {1.0, 0.94, 0.83, 0.10}, DAYLIGHT_LAYER, PPOperators.SET);

        // Contrast spans 0-2. Add 0.015 of that range to the underlying value.
        SetTargetValueFloat(PostProcessEffectType.Glow, PPEGlow.PARAM_CONTRAST,
            true, 0.015, DAYLIGHT_LAYER, PPOperators.ADD);
    }
}

modded class PPERequesterRegistrations
{
    override protected void RegisterAdditionalRequesters()
    {
        super.RegisterAdditionalRequesters();
        PPERequesterBank.RegisterRequester(AzimuthAvalonGradeRequester);
    }
}
