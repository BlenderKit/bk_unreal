#include "BlendkitViewportModule.h"

#include "SBlendkitAssetBar.h"

#include "Framework/Docking/TabManager.h"
#include "Widgets/Docking/SDockTab.h"
#include "WorkspaceMenuStructure.h"
#include "WorkspaceMenuStructureModule.h"

#if PLATFORM_MAC
#include "BlendkitViewportScrollGuard.h"
#endif

#define LOCTEXT_NAMESPACE "Blendkit"

namespace
{
	const FName BlendkitAssetBarTabId(TEXT("BlendkitAssetBar"));

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
#if PLATFORM_MAC
	BlendkitScrollGuard::Install();
#endif

	FGlobalTabmanager::Get()->RegisterNomadTabSpawner(
			BlendkitAssetBarTabId,
			FOnSpawnTab::CreateStatic(&SpawnBlendkitAssetBarTab))
		.SetDisplayName(LOCTEXT("BlendkitAssetBarTab", "Blendkit Asset Bar"))
		.SetTooltipText(LOCTEXT("BlendkitAssetBarTabTip", "Browse and drag Blendkit assets (native)"))
		.SetGroup(WorkspaceMenu::GetMenuStructure().GetToolsCategory());
}

void FBlendkitViewportModule::ShutdownModule()
{
	if (FGlobalTabmanager::Get()->HasTabSpawner(BlendkitAssetBarTabId))
	{
		FGlobalTabmanager::Get()->UnregisterNomadTabSpawner(BlendkitAssetBarTabId);
	}

#if PLATFORM_MAC
	BlendkitScrollGuard::Remove();
#endif
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FBlendkitViewportModule, BlendkitViewport)
