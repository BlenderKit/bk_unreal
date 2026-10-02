using UnrealBuildTool;

public class BlendkitViewport : ModuleRules
{
	public BlendkitViewport(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
		});

		// Editor-only: UnrealEd/LevelEditor expose GEditor, FEditorViewportClient
		// and the active level viewport - none of this exists in cooked builds.
		// The module Type is "Editor" in Blendkit.uplugin so it is only ever
		// compiled/loaded for editor targets.
		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"UnrealEd",
			"LevelEditor",
			"Slate",
			"SlateCore",
			// Native (Slate) asset-bar prototype:
			"InputCore",          // key/mouse enums for drag detection
			"ImageWrapper",       // decode downloaded thumbnails into Slate brushes
			"WorkspaceMenuStructure", // dockable tab menu group
		});

		// The macOS scroll guard (BlendkitViewportScrollGuardMac.mm) uses AppKit
		// to intercept scroll events over our external Qt windows.
		if (Target.Platform == UnrealTargetPlatform.Mac)
		{
			PublicFrameworks.Add("AppKit");
		}
	}
}
