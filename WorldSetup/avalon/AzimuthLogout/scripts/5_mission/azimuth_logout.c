modded class MissionServer
{
    override bool ShouldPlayerBeKilled(PlayerBase player)
    {
        // Keep the regular save and disconnect flow, including for unconscious or restrained players.
        return false;
    }
}
