#include "BlendkitViewportModule.h"

#include "SBlendkitAssetBar.h"
#include "SBlendkitSettings.h"

#include "Framework/Docking/TabManager.h"
#include "Widgets/Docking/SDockTab.h"
#include "WorkspaceMenuStructure.h"
#include "WorkspaceMenuStructureModule.h"

#define LOCTEXT_NAMESPACE "Blendkit"

namespace
{
	const FName BlendkitAssetBarTabId(TEXT("BlendkitAssetBar"));
	const FName BlendkitSettingsTabId(TEXT("BlendkitSettings"));

	TSharedRef<SDockTab> SpawnBlendkitSettingsTab(const FSpawnTabArgs&)
	{
		return SNew(SDockTab)
			.TabRole(ETabRole::NomadTab)
			[
				SNew(SBlendkitSettings)
			];
	}

	TSharedRef<SDockTab> SpawnBlendkitAssetBarTab(const FSpawnTabArgs&)
	{
		return SNew(SDockTab)
			.TabRole(ETabRole::NomadTab)
			[
				SNew(SBlendkitAssetBar)
			];
	}
}

void FBlendkitViewportModule::StartupModule()
{
	FGlobalTabmanager::Get()->RegisterNomadTabSpawner(
			BlendkitAssetBarTabId,
			FOnSpawnTab::CreateStatic(&SpawnBlendkitAssetBarTab))
		.SetDisplayName(LOCTEXT("BlendkitAssetBarTab", "Blendkit Asset Bar"))
		.SetTooltipText(LOCTEXT("BlendkitAssetBarTabTip", "Browse and drag Blendkit assets (native)"))
		.SetGroup(WorkspaceMenu::GetMenuStructure().GetToolsCategory());

	FGlobalTabmanager::Get()->RegisterNomadTabSpawner(
			BlendkitSettingsTabId,
			FOnSpawnTab::CreateStatic(&SpawnBlendkitSettingsTab))
		.SetDisplayName(LOCTEXT("BlendkitSettingsTab", "Blendkit Settings"))
		.SetGroup(WorkspaceMenu::GetMenuStructure().GetToolsCategory());
}

void FBlendkitViewportModule::ShutdownModule()
{
	if (FGlobalTabmanager::Get()->HasTabSpawner(BlendkitSettingsTabId))
	{
		FGlobalTabmanager::Get()->UnregisterNomadTabSpawner(BlendkitSettingsTabId);
	}
	if (FGlobalTabmanager::Get()->HasTabSpawner(BlendkitAssetBarTabId))
	{
		FGlobalTabmanager::Get()->UnregisterNomadTabSpawner(BlendkitAssetBarTabId);
	}

}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FBlendkitViewportModule, BlendkitViewport)
