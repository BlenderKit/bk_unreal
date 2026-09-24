#include "BlendkitBridge.h"

#include "Framework/Docking/TabManager.h"

namespace
{
	TWeakObjectPtr<UBlendkitBridge> GBlendkitBridge;
}

UBlendkitBridge* UBlendkitBridge::Get()
{
	if (!GBlendkitBridge.IsValid())
	{
		UBlendkitBridge* Bridge = NewObject<UBlendkitBridge>(GetTransientPackage(), TEXT("BlendkitBridge"));
		Bridge->AddToRoot(); // survives GC for the editor session
		GBlendkitBridge = Bridge;
	}
	return GBlendkitBridge.Get();
}

void UBlendkitBridge::OpenTab()
{
	FGlobalTabmanager::Get()->TryInvokeTab(FName(TEXT("BlendkitAssetBar")));
}

void UBlendkitBridge::SetResults(const TArray<FBlendkitAssetItem>& InItems)
{
	Results = InItems;
	OnResultsChanged.Broadcast();
}

void UBlendkitBridge::SetThumbnail(const FString& AssetId, const FString& Path)
{
	for (FBlendkitAssetItem& Item : Results)
	{
		if (Item.AssetId == AssetId)
		{
			Item.ThumbnailPath = Path;
			break;
		}
	}
	OnThumbnailChanged.Broadcast(AssetId, Path);
}

void UBlendkitBridge::SetStatus(const FString& Text)
{
	Status = Text;
	OnStatusChanged.Broadcast(Text);
}
