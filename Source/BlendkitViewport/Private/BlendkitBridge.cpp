#include "BlendkitBridge.h"

#include "Framework/Docking/TabManager.h"
#include "UObject/StrongObjectPtr.h"

namespace
{
	TStrongObjectPtr<UBlendkitBridge> GBlendkitBridge;
}

UBlendkitBridge* UBlendkitBridge::Get()
{
	if (!GBlendkitBridge.IsValid())
	{
		UBlendkitBridge* Bridge = NewObject<UBlendkitBridge>(GetTransientPackage(), TEXT("BlendkitBridge"));
		Bridge->AddToRoot(); // survives GC for the editor session
		GBlendkitBridge.Reset(Bridge);
	}
	return GBlendkitBridge.Get();
}

void UBlendkitBridge::OpenTab()
{
	FGlobalTabmanager::Get()->TryInvokeTab(FName(TEXT("BlendkitAssetBar")));
}

void UBlendkitBridge::OpenSettingsTab()
{
	OnSettingsRequested.Broadcast();
	FGlobalTabmanager::Get()->TryInvokeTab(FName(TEXT("BlendkitSettings")));
}

void UBlendkitBridge::SetSettings(const FBlendkitSettings& InSettings)
{
	Settings = InSettings;
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

void UBlendkitBridge::AddReferencedObjects(UObject* InThis, FReferenceCollector& Collector)
{
	Super::AddReferencedObjects(InThis, Collector);
	UBlendkitBridge* This = CastChecked<UBlendkitBridge>(InThis);

	This->BoundCallables.Reset();
	auto CollectDelegate = [This](const FMulticastScriptDelegate& Delegate)
	{
		for (UObject* Obj : Delegate.GetAllObjects())
		{
			if (Obj)
			{
				This->BoundCallables.Add(Obj);
			}
		}
	};

	CollectDelegate(This->OnSearchRequested);
	CollectDelegate(This->OnAssetDragStarted);
	CollectDelegate(This->OnAssetActivated);
	CollectDelegate(This->OnBookmarkToggled);
	CollectDelegate(This->OnLoadMoreRequested);
	CollectDelegate(This->OnSettingsRequested);
	CollectDelegate(This->OnSettingsSaved);
	CollectDelegate(This->OnLoginRequested);
	CollectDelegate(This->OnLogoutRequested);
	CollectDelegate(This->OnManualKeyRequested);
	CollectDelegate(This->OnBlenderDetectRequested);

	Collector.AddReferencedObjects(This->BoundCallables, This);
}
